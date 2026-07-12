# First run and product-manager tuning

This guide describes what to expect after enabling live Slack delivery and how
to tune `registries/product_colleague.hocon` into a useful member of the team.

## What the colleague does

The colleague runs on the native Neuro SAN periodic schedule, every 15 minutes
by default. Each run reads eligible Slack requests, inspects the configured
GitHub Project, compares the current deterministic snapshot with durable state,
and decides whether the team needs an update.

It should remain quiet unless one of these conditions applies:

- this is the first successful board baseline;
- the board changed materially;
- a blocker or stale item needs attention;
- an allowlisted teammate asked it a question;
- the daily report interval elapsed.

GitHub access is read-only. The colleague can analyze tickets and recommend
actions, but it cannot edit tickets, move cards, assign people, or claim that it
did so. Routine runs do not inspect Gmail.

Run the core server and periodic scheduler with:

```bash
make run
```

Run the Slack Socket Mode bridge separately for immediate mention-triggered
wake-ups:

```bash
make slack-bridge
```

Without the bridge, eligible Slack requests are still discovered by the next
periodic run. With `COLLEAGUE_SLACK_REQUIRE_MENTION=true`, channel requests must
mention the bot. The channel and allowed users are fixed by host configuration.

## Example scenarios

### First successful run

The colleague records its initial board snapshot and normally posts a baseline:

> Initial board baseline: 34 items, 8 in progress, 3 blocked, and 6 without
> owners. The main delivery risk is the authentication milestone, where two
> high-priority items have not changed in 12 days.

Later scheduled runs should be silent if nothing important changed. A fresh
Slack checkpoint considers at most the configured initial lookback window,
which defaults to 24 hours.

### A teammate requests prioritization

An allowlisted teammate writes:

> @Colleague What should we prioritize before Friday's release?

The bridge wakes the network immediately, or periodic polling finds the request.
The colleague reads the current board and replies in the originating Slack
thread with a recommendation and relevant GitHub links. Each trusted request is
answered separately.

### The board develops a new risk

A high-priority issue becomes blocked, an in-progress item goes stale, or a
milestone gains unowned work. On its next run, the colleague compares the new
snapshot with the last delivered state and may post a short change report with
counts, risks, and suggested decisions. Cosmetic or irrelevant changes should
not create channel noise.

## First-day calibration

Treat the first day as a calibration period. Watch:

- message frequency and whether silence is used appropriately;
- relevance of reported risks and false alarms;
- whether factual observations are separated from recommendations;
- tone, brevity, and usefulness of Slack replies;
- whether links and counts make reports easy to verify;
- whether requests are answered in the correct thread.

Operational checkpoints and secret-free audit records live under `.state/` in a
local run. Keep Slack writes enabled only after the destination and message
format have been verified.

## Initial prompt improvements

The highest-value early changes to `product_colleague.hocon` are usually:

1. **Product context.** State the current release, product goals, deadlines,
   strategic priorities, and what success means for the team.
2. **Escalation thresholds.** Define when stale work, missing ownership, blocked
   dependencies, and priority mismatches deserve a message.
3. **A stable report format.** For example: `Changes`, `Risks`, `Decisions
   needed`, and `Suggested next actions`.
4. **Evidence discipline.** Require the colleague to distinguish board facts,
   inferences, and recommendations, and to ask one focused question when the
   available evidence is insufficient.
5. **Silence rules.** Explicitly identify low-value changes that should not
   produce a Slack message.
6. **Team vocabulary and ownership.** Add repository names, milestones, release
   conventions, domain terms, and the roles responsible for each product area.
7. **Triage criteria.** Check for missing owner, priority, status, acceptance
   criteria, dependencies, and alignment with the current milestone.

Make one or two prompt changes at a time and observe several real runs before
adding broader authority. Keep GitHub writes as a future, separate capability
with narrow arguments and explicit human approval.
