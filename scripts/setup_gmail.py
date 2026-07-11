"""Explicitly authorize Gmail and persist a token for the unattended runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True, help="OAuth desktop-client JSON")
    parser.add_argument("--token", type=Path, default=Path(".secrets/gmail-token.json"))
    parser.add_argument("--enable-send", action="store_true", help="Also request gmail.send")
    args = parser.parse_args()
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [READ_SCOPE] + ([SEND_SCOPE] if args.enable_send else [])
    credentials = InstalledAppFlow.from_client_secrets_file(str(args.credentials), scopes).run_local_server(port=0)
    args.token.parent.mkdir(parents=True, exist_ok=True)
    args.token.write_text(credentials.to_json(), encoding="utf-8")
    args.token.chmod(0o600)
    print(f"Authorized {len(scopes)} scope(s); token stored at {args.token}")


if __name__ == "__main__":
    main()
