"""Non-interactive Gmail API client construction for colleague tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class GmailConfigurationError(ValueError):
    """A safe-to-return Gmail configuration failure."""


def build_gmail_service(required_scopes: set[str]) -> Any:
    """Build an authorized Gmail service without starting interactive OAuth."""
    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", ".secrets/gmail-token.json"))
    if not token_path.is_file():
        raise GmailConfigurationError("GMAIL_TOKEN_PATH does not point to an authorized token file")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - packaging error
        raise GmailConfigurationError("Gmail API dependencies are not installed") from exc

    try:
        credentials = Credentials.from_authorized_user_file(str(token_path))
    except (OSError, ValueError) as exc:
        raise GmailConfigurationError("Gmail token file is invalid or unreadable") from exc
    granted = set(credentials.scopes or [])
    if not required_scopes.issubset(granted):
        raise GmailConfigurationError("Gmail token lacks the required OAuth scope")
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise GmailConfigurationError("Gmail token refresh failed") from exc
    if not credentials.valid:
        raise GmailConfigurationError("Gmail token is not valid")
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)
