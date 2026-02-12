#!/usr/bin/env python3
"""One-time OAuth2 setup for Google Calendar.

Usage:
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Enable the Google Calendar API
  3. Create OAuth2 "Desktop app" credentials
  4. Download the client ID and secret
  5. Run this script:
       python scripts/gcal_auth.py --client-id YOUR_ID --client-secret YOUR_SECRET
  6. A browser window will open — sign in and authorize
  7. Copy the refresh token into server/.env as GOOGLE_REFRESH_TOKEN
"""

import argparse
import http.server
import json
import sys
import urllib.parse
import urllib.request

REDIRECT_PORT = 8085
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
SCOPES = "https://www.googleapis.com/auth/calendar"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main():
    parser = argparse.ArgumentParser(description="Get Google Calendar OAuth2 refresh token")
    parser.add_argument("--client-id", required=True, help="OAuth2 client ID")
    parser.add_argument("--client-secret", required=True, help="OAuth2 client secret")
    args = parser.parse_args()

    # Step 1: Build authorization URL
    auth_params = urllib.parse.urlencode({
        "client_id": args.client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{AUTH_URL}?{auth_params}"

    print(f"\nOpening browser for Google authorization...\n")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")

    import webbrowser
    webbrowser.open(auth_url)

    # Step 2: Start local server to catch the redirect
    auth_code = None

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)

            if "code" in params:
                auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Authorization successful!</h2><p>You can close this tab.</p>")
            else:
                error = params.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h2>Authorization failed: {error}</h2>".encode())

        def log_message(self, fmt, *a):
            pass  # Suppress server logs

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), Handler)
    print(f"Waiting for authorization callback on port {REDIRECT_PORT}...")
    server.handle_request()  # Handle one request

    if not auth_code:
        print("Error: No authorization code received.")
        sys.exit(1)

    # Step 3: Exchange code for tokens
    print("Exchanging authorization code for tokens...")
    token_data = urllib.parse.urlencode({
        "code": auth_code,
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=token_data)
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("Error: No refresh token in response. Try again with prompt=consent.")
        print(f"Response: {json.dumps(tokens, indent=2)}")
        sys.exit(1)

    print(f"\nSuccess! Add these to server/.env:\n")
    print(f"GOOGLE_CLIENT_ID={args.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={args.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
    print(f"GOOGLE_CALENDAR_ID=primary")
    print()


if __name__ == "__main__":
    main()
