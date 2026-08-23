"""
Run this after adding or updating files in knowledge/ to (re)index them for
vector search. Picks up PDFs, Word docs, and text files from config.KNOWLEDGE_DIRS
(e.g. User Background, StreetCred Sourcebook_MASTER).

Usage:
    python ingest.py
"""

import os
import glob
import json
import knowledge
import config

SUPPORTED = config.SHARED_DRIVE_EXTENSIONS if config.SHARED_DRIVE_EXTENSIONS else (".txt", ".md", ".docx", ".pdf")


def _drive_api_available() -> bool:
    if not config.GOOGLE_DRIVE_FOLDER_ID.strip():
        return False
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        return True
    except Exception:
        return False


def _get_drive_service():
    service_account_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT", config.GOOGLE_SERVICE_ACCOUNT).strip()
    creds_json = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", config.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON).strip()
    creds_file = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", config.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE).strip()

    if not (service_account_path or creds_json or creds_file):
        raise RuntimeError("Google Drive service account credentials are not configured. Set GOOGLE_SERVICE_ACCOUNT to the JSON file path.")

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if service_account_path:
        creds = service_account.Credentials.from_service_account_file(
            service_account_path,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
    elif creds_json:
        data = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            data,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_google_drive_files(folder_id: str) -> list[str]:
    if not _drive_api_available():
        return []

    service = _get_drive_service()
    results = []
    seen = set()

    try:
        service.files().get(fileId=folder_id, fields="id, name, mimeType, driveId", supportsAllDrives=True).execute()
    except Exception as exc:
        print(f"[ingest] Google Drive folder ID {folder_id!r} could not be opened. Check that the authorized Google account has access to the shared company folder. Error: {exc}")
        return []

    def walk(folder):
        query = f"'{folder}' in parents and trashed = false"
        try:
            resp = service.files().list(
                q=query,
                pageSize=1000,
                fields="nextPageToken, files(id, name, mimeType, parents)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        except Exception as exc:
            print(f"[ingest] Google Drive lookup failed for folder {folder!r}: {exc}")
            return

        items = resp.get("files", [])
        if not items:
            print(f"[ingest] Google Drive folder {folder!r} returned no files. Check that the authorized Google account has access to the shared company folder.")

        for item in items:
            item_id = item.get("id")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            mime_type = item.get("mimeType", "")
            if mime_type == "application/vnd.google-apps.folder":
                walk(item_id)
            else:
                results.append(f"drive://{item_id}/{item.get('name', 'untitled')}")

    walk(folder_id)
    return sorted(results)


def _read_drive_file(path: str) -> str:
    if not path.startswith("drive://"):
        return ""

    file_id = path.split("drive://", 1)[1].split("/", 1)[0]
    service = _get_drive_service()
    meta = service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
    mime_type = meta.get("mimeType", "")
    name = meta.get("name", "drive_file")

    try:
        if mime_type == "application/vnd.google-apps.document":
            body = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        elif mime_type == "application/vnd.google-apps.spreadsheet":
            body = service.files().export(fileId=file_id, mimeType="text/csv").execute()
        elif mime_type == "application/vnd.google-apps.presentation":
            body = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        else:
            body = service.files().get_media(fileId=file_id).execute()

        if isinstance(body, bytes):
            text = body.decode("utf-8", errors="ignore")
        else:
            text = str(body)
        if not text.strip():
            print(f"[ingest] {name} appears to have no extractable text -- skipping.")
            return ""
        return text
    except Exception as exc:
        print(f"[ingest] Failed to read Drive file {name}: {exc}")
        return ""


def _read_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _read_docx(path):
    import docx
    doc = docx.Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def _read_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _collect_files() -> list[str]:
    seen = set()
    paths = []
    for docs_dir in config.KNOWLEDGE_DIRS:
        if not os.path.isdir(docs_dir):
            continue
        for ext in SUPPORTED:
            for path in glob.glob(os.path.join(docs_dir, f"*{ext}")):
                if os.path.isfile(path) and path not in seen:
                    seen.add(path)
                    paths.append(path)

    if config.SHARED_DRIVE_PATH:
        if os.path.isdir(config.SHARED_DRIVE_PATH):
            for root, _, files in os.walk(config.SHARED_DRIVE_PATH):
                for name in files:
                    if os.path.splitext(name)[1].lower() not in SUPPORTED:
                        continue
                    path = os.path.join(root, name)
                    if path not in seen:
                        seen.add(path)
                        paths.append(path)
        else:
            print(f"[ingest] Shared drive path not found: {config.SHARED_DRIVE_PATH}")

    if config.GOOGLE_DRIVE_FOLDER_ID and _drive_api_available():
        for drive_path in _list_google_drive_files(config.GOOGLE_DRIVE_FOLDER_ID):
            if drive_path not in seen:
                seen.add(drive_path)
                paths.append(drive_path)
    elif config.GOOGLE_DRIVE_FOLDER_ID:
        print("[ingest] Google Drive API is configured but the required Python packages or credentials are missing.")

    return sorted(paths)


def main():
    for docs_dir in config.KNOWLEDGE_DIRS:
        os.makedirs(docs_dir, exist_ok=True)

    files = _collect_files()
    if not files:
        print("[ingest] No supported files found in:")
        for d in config.KNOWLEDGE_DIRS:
            print(f"  {d}")
        print("[ingest] Add User Background, StreetCred Sourcebook, etc., then rerun.")
        return

    print(f"[ingest] Found {len(files)} file(s) to index.")
    for path in files:
        name = os.path.basename(path)
        try:
            if path.startswith("drive://"):
                drive_id = path.split("drive://", 1)[1].split("/", 1)[0]
                name = path.split("/", 1)[1] if "/" in path else path
                text = _read_drive_file(path)
                if not text.strip():
                    continue
                knowledge.add_document(doc_id=f"drive_{drive_id}", text=text, source_name=name)
                continue

            ext = os.path.splitext(path)[1].lower()
            if ext in (".txt", ".md"):
                text = _read_txt(path)
            elif ext == ".docx":
                text = _read_docx(path)
            elif ext == ".pdf":
                text = _read_pdf(path)
            else:
                continue

            if not text.strip():
                print(f"[ingest] {name} appears to have no extractable text -- skipping.")
                continue

            knowledge.add_document(doc_id=name, text=text, source_name=name)
        except Exception as e:
            print(f"[ingest] Failed on {name}: {e}")

    print("[ingest] Done. Iris can now reference these documents.")


if __name__ == "__main__":
    main()
