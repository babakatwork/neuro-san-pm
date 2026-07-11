# Security model

This sample is designed for a read-mostly first deployment. An LLM is a planner
inside explicit host-owned capability boundaries; it is not the security
boundary.

## Trust boundaries

| Input or capability | Trust | Enforcement |
| --- | --- | --- |
| GitHub item text and fields | Untrusted data | Constant query-only reader; owner/project are host environment, not model arguments |
| Slack messages | Trusted identity, untrusted text | Fixed IDs, mention filter, bounded batching, delivery-gated checkpoints |
| GitHub token | Secret | Agent environment only; never returned by RuntimeConfig |
| Slack tokens | Secret | Environment only; never accepted as tool arguments |
| Slack destination | Operator-owned | Fixed environment channel; no model-controlled channel argument |
| Gmail content | Untrusted data | Bounded plain-text reads; no attachments or link following |
| Gmail sending | Disabled by default | Separate OAuth scope, active lease, exact recipient allowlist, dedupe, live-write gate |
| Scheduled concurrency | Untrusted timing | One worker plus durable lease and message dedupe |
| Computer use | Disabled by default | Optional unserved network with observation-only tools |

## Credential policy

- Use dedicated bot/install credentials, not personal user tokens.
- Grant `read:project`, and only the repository/org read permissions needed.
- Keep GitHub MCP on `/readonly` endpoints. Do not merely rely on an agent
  instruction to avoid writes.
- Keep `.env`, `.state`, `.secrets`, and logs out of version control.
- Keep `AGENT_REQUEST_LOGGING_INPUT_SLICE=0`; Neuro SAN otherwise includes
  incoming request text in its request log marker.
- Rotate tokens after accidental disclosure and audit the Slack/GitHub app
  installation grants.
- Use a production secret manager instead of an `.env` file when deploying to
  shared infrastructure.

Compose injects the GitHub/OpenAI credentials only into the agent service and
the Socket Mode app token only into the bridge. The bot token is the sole shared
credential because both processes require Slack access.

`COLLEAGUE_SLACK_WRITE_ENABLED` and `COLLEAGUE_SLACK_REQUIRE_MENTION` accept
only recognized boolean spellings. Invalid values fail closed and make the
readiness check fail. Keep mention filtering enabled unless the configured
conversation is a dedicated DM or bot-only channel, and configure the bot's
stable `SLACK_BOT_USER_ID` rather than a display name.

## Prompt-injection policy

Ticket titles, bodies, comments, links, Slack text, and web pages may contain
instructions aimed at the model. They never change the system policy or grant
authority. The initial network intentionally sends only compact project fields
to the snapshot tool and exposes no GitHub write operation.

A future action such as editing an issue, moving a Project item, sending email,
or operating a signed-in browser should be a new narrow tool with:

1. a fixed resource allowlist;
2. structured validated arguments;
3. idempotency keys;
4. an explicit human approval state outside model text;
5. a secret-free audit record;
6. a deterministic rollback or compensation path where possible.

## Network policy

The permanent Compose stack does not publish the Neuro SAN port. The registry's
`public=false` setting only removes the network from discovery; it does not
authenticate a caller who knows the endpoint. If remote webhooks are added,
terminate TLS and authenticate at a reverse proxy, validate webhook signatures,
rate-limit requests, and keep the agent server on a private network.

The Playwright MCP server is not a security boundary, including when its
`allowed-origins` option is set. It is deliberately absent from the permanent
Compose stack. Run it in a disposable container/VM without credentials,
sensitive mounts, or unrestricted access to private networks.

## Audit and data retention

`.state/audit.jsonl` records event names, outcomes, timestamps, run IDs, and
message hashes. It intentionally omits tokens and Slack message text. Define a
retention/rotation policy before long-term deployment.
