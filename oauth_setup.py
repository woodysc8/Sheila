import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]

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


def main():
    print("Sheila Google OAuth setup")
    print("=" * 50)

    if not os.path.exists(GAUTH_FILE):
        raise SystemExit(f"Missing {GAUTH_FILE}")

    with open(GAUTH_FILE, "r", encoding="utf-8") as f:
        client_config = json.load(f)

    # Google's installed-app flow automatically selects an available
    # localhost port and starts a temporary callback server.
    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=SCOPES,
    )

    print()
    print("Opening Google authorization in your browser...")
    print("Sign in as: sam@streetcredpr.com")
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

    with open(CREDENTIAL_FILE, "w", encoding="utf-8") as f:
        f.write(credentials.to_json())

    accounts = {
        "accounts": [
            {
                "email": "sam@streetcredpr.com",
                "account_type": "work",
                "extra_info": "",
            }
        ]
    }

    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)

    print()
    print(f"Saved credentials to {CREDENTIAL_FILE}")
    print(f"Created {ACCOUNTS_FILE}")
    print()
    print("Sheila Google authentication is complete.")


if __name__ == "__main__":
    main()