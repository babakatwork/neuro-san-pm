from coded_tools.colleague.gmail_recipients import parse_email_list
from coded_tools.colleague.gmail_recipients import validate_daily_summary_recipients


def test_email_list_is_normalized_deduplicated_and_ordered():
    assert parse_email_list("First@Example.com, second@example.com,first@example.com") == [
        "first@example.com",
        "second@example.com",
    ]


def test_daily_summary_list_requires_every_recipient_to_be_allowlisted():
    recipients, error = validate_daily_summary_recipients(
        "first@example.com,missing@example.com",
        "first@example.com,second@example.com",
    )

    assert recipients == ["first@example.com", "missing@example.com"]
    assert error == "COLLEAGUE_DAILY_SUMMARY_TO contains a recipient not in GMAIL_ALLOWED_RECIPIENTS"


def test_daily_summary_list_rejects_malformed_entries():
    recipients, error = validate_daily_summary_recipients(
        "first@example.com,Das",
        "first@example.com,Das",
    )

    assert recipients == ["first@example.com", "das"]
    assert error == "COLLEAGUE_DAILY_SUMMARY_TO contains an invalid email address"
