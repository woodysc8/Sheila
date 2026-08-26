"""Read-only Google Drive search and practical document-reading helpers."""

import re

from .google_auth import GoogleAuthError, build_service

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


def has_explicit_client_relationship(text: str, company: str) -> bool:
    """Return true only for direct client/account/customer relationship claims."""
    company_pattern = re.escape(company.strip())
    patterns = (
        rf"\b(?:client|account|customer)\s*[:\-]\s*{company_pattern}\b",
        rf"\b{company_pattern}\b\s+(?:is|are)\s+(?:a|an|one of (?:our|the))?\s*(?:current )?(?:client|account|customer)\b",
        rf"\b{company_pattern}\b\s+(?:is|are)\s+served by\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def normalize_file(item: dict) -> dict[str, str]:
    return {"id": item.get("id", ""), "name": item.get("name", ""), "mime_type": item.get("mimeType", ""),
            "modified_time": item.get("modifiedTime", ""), "web_view_link": item.get("webViewLink", ""), "description": item.get("description", "")}


def search_files(query: str, limit: int = 10) -> list[dict[str, str]]:
    try:
        service = build_service("drive", "v3")
        escaped = query.replace("'", "\\'")
        result = service.files().list(q=f"fullText contains '{escaped}' and trashed = false", pageSize=min(max(limit, 1), 20),
            fields="files(id,name,mimeType,modifiedTime,webViewLink,description)", orderBy="modifiedTime desc", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        return [normalize_file(item) for item in result.get("files", [])]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Drive search failed.") from exc


def get_file_metadata(file_id: str) -> dict[str, str]:
    try:
        return normalize_file(build_service("drive", "v3").files().get(fileId=file_id, fields="id,name,mimeType,modifiedTime,webViewLink,description", supportsAllDrives=True).execute())
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Drive file retrieval failed.") from exc


def read_google_document(file_id: str) -> str:
    """Export native Google Docs as plain text; no arbitrary file downloads."""
    try:
        service = build_service("drive", "v3")
        metadata = get_file_metadata(file_id)
        if metadata["mime_type"] != GOOGLE_DOC_MIME:
            return "This file can be found, but Sheila can currently read only native Google Docs."
        return service.files().export_media(fileId=file_id, mimeType="text/plain").execute().decode("utf-8", errors="replace")[:8000]
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError("Drive document reading failed.") from exc
