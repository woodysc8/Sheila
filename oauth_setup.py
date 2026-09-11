import argparse
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from google_auth_oauthlib.flow import InstalledAppFlow
import config

WORK_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]
PERSONAL_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
# Backward-compatible name for callers that used the original work setup.
SCOPES = WORK_SCOPES

GAUTH_FILE = ".gauth.json"
CREDENTIAL_FILE = ".oauth2.sam@streetcredpr.com.json"
ACCOUNTS_FILE = ".accounts.json"


class OAuthHandler(BaseHTTPRequestHandler):
    authorization_code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)

        if "code" in query:
            OAuthHandler.authorization_code = query["code"][0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()

            self.wfile.write(
                b"""
                <html>
                <head><title>Sheila Google Authorization</title></head>
                <body>
                    <h1>Google authorization successful.</h1>
                    <p>You can close this browser window and return to Sheila.</p>
                </body>
                </html>
                """
            )
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def _profile(profile: str) -> dict[str, object]:
    if profile == "work":
        return {"client_file": GAUTH_FILE, "credential_file": CREDENTIAL_FILE,
                "scopes": WORK_SCOPES, "account": "sam@streetcredpr.com", "write_accounts": True}
    return {"client_file": config.SHEILA_PERSONAL_GOOGLE_OAUTH_CLIENT_FILE,
            "credential_file": config.SHEILA_PERSONAL_GOOGLE_OAUTH_CREDENTIALS_FILE,
            "scopes": PERSONAL_CALENDAR_SCOPES, "account": "woodysc7@gmail.com", "write_accounts": False}


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Set up Sheila Google OAuth credentials.")
    parser.add_argument("--profile", choices=("work", "personal"), default="work")
    args = parser.parse_args(argv)
    profile = _profile(args.profile)

    if args.profile == "personal":
        if Path(str(profile["client_file"])).resolve() == Path(GAUTH_FILE).resolve():
            raise SystemExit("Personal OAuth client file must be separate from .gauth.json")
        if Path(str(profile["credential_file"])).resolve() == Path(CREDENTIAL_FILE).resolve():
            raise SystemExit("Personal OAuth credentials must be separate from work credentials")

    print(f"Sheila Google OAuth setup ({args.profile})")
    print("=" * 50)

    client_file = str(profile["client_file"])
    credential_file = str(profile["credential_file"])
    if not os.path.exists(client_file):
        raise SystemExit(f"Missing {client_file}")

    with open(client_file, "r", encoding="utf-8") as f:
        client_config = json.load(f)

    # Google's installed-app flow automatically selects an available
    # localhost port and starts a temporary callback server.
    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=profile["scopes"],
    )

    print()
    print("Opening Google authorization in your browser...")
    print(f"Sign in as: {profile['account']}")
    print()

    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        authorization_prompt_message="Please visit this URL: {url}",
        success_message="Google authorization successful. You can close this window.",
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )

    print()
    print("Google authorization successful.")

    with open(credential_file, "w", encoding="utf-8") as f:
        f.write(credentials.to_json())

    if profile["write_accounts"]:
        accounts = {"accounts": [{"email": "sam@streetcredpr.com", "account_type": "work", "extra_info": ""}]}
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=2)

    print()
    print(f"Saved credentials to {credential_file}")
    if profile["write_accounts"]:
        print(f"Created {ACCOUNTS_FILE}")
    print()
    print("Sheila Google authentication is complete.")


if __name__ == "__main__":
    main()
