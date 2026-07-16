# Slack setup and behavior

## App configuration

1. Create a Slack app and a bot user.
2. Add `chat:write`.
3. Add exactly one history scope matching the configured conversation:
   `channels:history`, `groups:history`, or `im:history`.
4. Add `app_mentions:read` if channel mentions should wake the colleague.
5. Invite the bot to the selected channel.
6. Copy the bot token to `SLACK_BOT_TOKEN`.
7. Copy the bot member's stable ID to `SLACK_BOT_USER_ID`.
8. Copy the stable channel ID to `SLACK_CHANNEL_ID`.
9. Put only trusted teammate IDs who may direct the agent in
   `SLACK_ALLOWED_USER_IDS`. Other human messages are visible as ambient
   context but cannot become reply-required requests.
10. Keep `COLLEAGUE_SLACK_REQUIRE_MENTION=true` unless this is a dedicated DM
    or bot-only channel.

For Socket Mode event wake-ups:

1. Enable Socket Mode.
2. Create an app-level token with `connections:write` and store it as
   `SLACK_APP_TOKEN`.
3. Subscribe to `app_mention`.
4. Subscribe to `message.im` only if the configured channel is a DM.
5. Avoid broad ambient `message.channels` subscriptions.

Reinstall the app after changing scopes or event subscriptions.

## Outbound behavior

`SlackPost` has no channel parameter. It can post only to
`SLACK_CHANNEL_ID`. SlackInbox returns an opaque `inbox_batch_id`; to answer a
request the agent supplies that ID and the request's `ts`. Host-owned batch state
resolves the actual thread and rejects invented or cross-run handles. `sly_data`
is ignored. The tool also requires the active run lease ID.

Accepted replies are also recorded in a body-free, durable
`.state/slack_reply_ledger.json` ledger. The key is derived from the configured
channel and the original Slack request timestamp. This gives each teammate
request at-most-once reply semantics for 30 days: a later run does not show an
answered request to the agent, and the posting boundary rejects a second answer
even when its wording differs. Unanswered requests remain eligible for retry.

Messages are capped at 3,500 characters. Exact messages to the same channel and
thread are suppressed for `COLLEAGUE_SLACK_DEDUPE_SECONDS` (six hours by
default). Model-produced angle brackets are escaped, Slack formatting is
disabled, and link/media unfurls are disabled, so ticket text cannot create a
mass mention or attacker-controlled preview. Outbound delivery is disabled
unless `COLLEAGUE_SLACK_WRITE_ENABLED=true`. A dry-run preview does not mark a
request delivered, so ColleagueState refuses to consume its inbox checkpoint.

Fixed lifecycle notices are separately gated by
`COLLEAGUE_SLACK_AVAILABILITY_ENABLED`, which defaults to `false`. Leave it
disabled to run `make run`, `make up`, and `make down` without online/offline
channel posts while retaining ordinary agent updates and replies. Set both this
flag and `COLLEAGUE_SLACK_WRITE_ENABLED=true` to enable lifecycle notices.

## Inbox behavior

`SlackInbox` calls `conversations.history` with fixed lower/upper timestamps and
paginates inside host code. On a new state file it starts from
`COLLEAGUE_SLACK_INITIAL_LOOKBACK_HOURS` (24 hours by default), rather than trying
to read the channel from its creation date. For ambient context, it drops:

- bot messages;
- malformed timestamps;
- content from any channel other than `SLACK_CHANNEL_ID`.

The `channel_context` result contains all remaining bounded human messages.
The separate `messages` result contains only reply-required requests: they must
come from `SLACK_ALLOWED_USER_IDS` and, while mention filtering is enabled,
mention `SLACK_BOT_USER_ID`. Ambient context informs product-management
judgment but is never automatically answered.

The tool creates a body-free inbox batch. If more trusted requests exist than
the per-run bound, it returns the oldest batch and a partial high-water mark;
later runs drain the rest without loss. ColleagueState accepts that checkpoint
only when every request in the batch has a host-recorded Slack delivery or
dedupe receipt. If a run, post, or dry run stops first, the requests remain
eligible on the next run.

## Event wake-up

`apps.slack_bridge` accepts an event only when both the channel and user IDs are
allowlisted. It places body-free event metadata in the shared state volume and
posts a body-free `MINIMAL` wake request to
`/api/v1/product_colleague/streaming_chat`. Both event and scheduled runs then
read the same SlackInbox/checkpoint, so retries and restarts do not create a
second message-processing path. Pending metadata also lets SlackInbox fetch a
mention inside a thread with `conversations.replies`; it remains queued until a
delivery-gated ColleagueState checkpoint consumes the batch. Unresolvable events
retry a bounded number of times and then move to a body-free dead-letter list,
so a deleted message cannot poison all future Kanban runs. A failed dispatch is
logged and its reservation is released; the bridge never bypasses the outbound
write gate with a direct `say()` call.

Inspect or resolve quarantined events explicitly:

```bash
python -m scripts.slack_event_admin list
python -m scripts.slack_event_admin requeue Ev123
python -m scripts.slack_event_admin drop Ev123
```

No token, teammate message body, or user-provided `sly_data` passes from Slack
into Neuro SAN. The bridge creates the only accepted private metadata itself.
