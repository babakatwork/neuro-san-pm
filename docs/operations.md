# Operations

## Processes

The initial production shape has two long-lived processes:

- `neuro-san`: HTTP service plus core `PeriodicEventInitiator` and
  `EventWorkMonitor`;
- `slack-bridge`: optional Socket Mode event listener.

The Playwright extension point is optional and is not provisioned by this
permanent stack.

The container launcher runs `scripts/check_config.py` before replacing itself
with the Neuro SAN process. Missing credentials, unsafe boolean typos, request
logging, worker-count, timeout, schedule, or registry errors therefore cause a
fail-closed restart instead of a silently ineffective scheduler.
The sample also fixes the internal HTTP port at 8080 so the health check and
Slack bridge cannot drift from the server listener.

## Scheduling rules

The default cron expression is `*/15 * * * *`, evaluated in the server's local
timezone. Override it with `COLLEAGUE_CRON_SCHEDULE` and restart.

Keep the smallest interval greater than `COLLEAGUE_MAX_RUN_SECONDS`. This sample
fixes that setting at 600 seconds to match `max_execution_seconds`; the config
checker rejects a mismatch and enforces the interval for consecutive cron
firings. Neuro SAN does not catch up missed firings after downtime, and schedule
changes are not currently hot reloaded.

Use exactly one HTTP worker and one server replica. Each process starts its own
scheduler. The state lease suppresses overlaps that share the same state file,
but it is not a substitute for single scheduler ownership across hosts.

## State

`COLLEAGUE_STATE_PATH` holds:

- the most recent normalized board snapshot and digest;
- the last processed Slack timestamp;
- the last report time and notified digest;
- the active run lease.

The lease expires after `COLLEAGUE_MAX_RUN_SECONDS + 60`, so a crashed run does
not block the colleague forever. `slack_delivery.json` holds recent exact-message
hashes. `slack_inbox_batches.json` binds request timestamps/threads to delivery
receipts without storing message bodies. `slack_wake_events.json` holds pending
Socket Mode references, completed tombstones, and body-free dead letters. The
Compose named volume persists these files across restarts and is mounted by both
long-lived services.

On first startup, Slack history bootstraps from the configured lookback window.
Large trusted backlogs drain in bounded batches. Inspect quarantined Socket
events with `python -m scripts.slack_event_admin list`; use its `requeue` or
`drop` action only after reviewing the event metadata and Slack logs.

Back up state if avoiding duplicate baselines after disaster recovery matters.
It is safe to delete state during development; the next run behaves as a first
baseline.

## Health and logs

The Compose health check calls `/api/v1/list`. The colleague is intentionally
`public=false`, so an HTTP 200 with an empty agents array is healthy.

Keep `AGENT_REQUEST_LOGGING_INPUT_SLICE=0` in every deployment. Compose sets it
explicitly; the offline configuration check rejects a missing or different
value so Slack request text is not copied into Neuro SAN request logs.

Monitor for:

- `Found 1 periodic agent interactions` at startup;
- `Starting EventWorkMonitor` and `Starting PeriodicEventInitiator`;
- repeated `state_error`, `slack_post` failures, or missing run finishes in the
  audit log;
- GitHub GraphQL authentication/scope errors;
- Slack `missing_scope`, `not_in_channel`, or rate-limit responses.

## Deployment

```bash
docker compose --profile slack up -d --build
docker compose ps
docker compose logs -f neuro-san slack-bridge
```

The Neuro SAN endpoint is reachable inside the Compose network but is not
published to the host. To exercise it from inside the running service, use:

```bash
docker compose exec neuro-san python scripts/trigger_event.py
```

Use `docker compose --profile slack down` to stop without deleting the named
state volume. Add `-v` only when intentionally resetting state.

## Upgrade runbook

1. Read Neuro SAN core release notes, especially scheduler, event, and MCP changes.
2. Update the exact pin in `requirements.txt` and `pyproject.toml` together.
3. Rebuild a fresh `.venv`.
4. Run `make validate`.
5. Boot with a far-future cron schedule and Slack writes disabled.
6. Confirm one periodic interaction and one scheduler process.
7. Restore the schedule, deploy one replica, then re-enable Slack writes.
