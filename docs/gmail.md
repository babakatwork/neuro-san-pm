# Gmail capability

Gmail is optional and disabled by default. Dedicated coded tools keep reading
and sending as distinct security boundaries instead of exposing a broad combined
Gmail toolkit to the autonomous agent.

## Authorize read access

Enable the Gmail API in a Google Cloud project, create an OAuth desktop client,
and keep its downloaded JSON outside this repository. Then run:

```bash
.venv/bin/python scripts/setup_gmail.py --credentials /safe/path/credentials.json
```

Set `COLLEAGUE_GMAIL_ENABLED=true`. The default token is
`.secrets/gmail-token.json`; Compose mounts `.secrets` read-only into only the
agent service. The permanent process loads and refreshes this token but never
launches interactive OAuth or receives the client secret.

## Enable sending deliberately

Regenerate the token with the narrow send scope:

```bash
.venv/bin/python scripts/setup_gmail.py \
  --credentials /safe/path/credentials.json --enable-send
```

Set `GMAIL_ALLOWED_RECIPIENTS` to exact addresses. First inspect dry-run
previews, then deliberately set `COLLEAGUE_GMAIL_WRITE_ENABLED=true`. Sending
also requires the active colleague lease. Messages are plain text, bounded,
secret-free in the audit, and deduplicated for 24 hours. A trusted Slack user
may explicitly request a send. Separately, set `COLLEAGUE_DAILY_SUMMARY_TO` to
a comma-separated list of at most 20 addresses whose every address appears in
`GMAIL_ALLOWED_RECIPIENTS` to allow an autonomous summary when the board
changed after its initial baseline. The finalizer sends a separate message to
each recipient, so addresses are not exposed to one another, and permits at
most one complete summary per UTC day. If one delivery fails, the summary
remains pending; successful recipient deliveries are protected by the existing
24-hour dedupe when the next run retries. The finalizer never reads the mailbox
to prepare the summary. If no change is pending, the draft is ignored.

## Data boundary

`GMAIL_QUERY_PREFIX` is prepended by host code to every search and defaults to
`in:inbox newer_than:30d`. Search returns bounded headers and snippets. Reading
returns at most 20,000 characters of plain text; attachments and HTML are
excluded. All email content remains untrusted data.
