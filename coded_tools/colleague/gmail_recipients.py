"""Parse and validate host-configured Gmail recipient lists."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
MAX_DAILY_SUMMARY_RECIPIENTS = 20


def parse_email_list(value: str) -> list[str]:
    """Return normalized, order-preserving, de-duplicated comma-separated values."""
    recipients: list[str] = []
    seen: set[str] = set()
    for raw in value.split(","):
        recipient = raw.strip().lower()
        if recipient and recipient not in seen:
            recipients.append(recipient)
            seen.add(recipient)
    return recipients


def is_email_address(value: str) -> bool:
    """Return whether value is one syntactically bounded email address."""
    return bool(EMAIL_RE.fullmatch(value))


def validate_daily_summary_recipients(
    daily_value: str,
    allowed_value: str,
) -> tuple[list[str], str | None]:
    """Validate the complete daily list before any recipient receives a message."""
    recipients = parse_email_list(daily_value)
    if not recipients:
        return [], None
    if len(recipients) > MAX_DAILY_SUMMARY_RECIPIENTS:
        return recipients, (
            f"COLLEAGUE_DAILY_SUMMARY_TO exceeds the "
            f"{MAX_DAILY_SUMMARY_RECIPIENTS}-recipient safety limit"
        )
    if any(not is_email_address(recipient) for recipient in recipients):
        return recipients, "COLLEAGUE_DAILY_SUMMARY_TO contains an invalid email address"
    allowed = set(parse_email_list(allowed_value))
    if any(recipient not in allowed for recipient in recipients):
        return recipients, "COLLEAGUE_DAILY_SUMMARY_TO contains a recipient not in GMAIL_ALLOWED_RECIPIENTS"
    return recipients, None
