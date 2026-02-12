"""One-time Outlook authentication setup — device code flow.

Run: python -m server.setup_outlook_auth
"""

import sys
from pathlib import Path

# Ensure the parent directory is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import config  # noqa: E402
from server.outlook import _get_app, _save_cache, SCOPES, get_access_token  # noqa: E402


def main():
    if not config.OUTLOOK_CLIENT_ID:
        print("Error: OUTLOOK_CLIENT_ID not set.")
        print("1. Register an app at portal.azure.com → App registrations")
        print("2. Set OUTLOOK_CLIENT_ID in server/.env")
        sys.exit(1)

    # Check if already authenticated
    token = get_access_token()
    if token:
        print("Already authenticated! Token is valid.")
        print("To re-authenticate, delete server/.outlook_token_cache.bin and run again.")
        return

    app = _get_app()
    if not app:
        print("Error: Failed to initialize MSAL app.")
        sys.exit(1)

    # Initiate device code flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"Error: Could not create device flow — {flow.get('error_description', 'unknown error')}")
        sys.exit(1)

    print()
    print(flow["message"])
    print()

    # Wait for user to complete auth
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" in result:
        _save_cache()
        print()
        print("Authentication successful! Token cached.")
        print("Conduit can now read your Outlook inbox.")
    else:
        print(f"Authentication failed: {result.get('error_description', result.get('error', 'unknown'))}")
        sys.exit(1)


if __name__ == "__main__":
    main()
