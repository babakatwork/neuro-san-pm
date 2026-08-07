# Agentic development handoffs

The product colleague can propose small, well-defined Backlog or To Do issues
for the existing `neuro-san-coder`. A human must approve the exact issue in the
proposal's Slack thread before any delivery write is accepted.

GitHub is the durable conversation. The coder and PM alternate ownership, and
each assignment starts a fresh coder session using the issue, linked pull
request, and their comments as context. Neither agent can merge or formally
approve a pull request.

The top product colleague owns candidate discovery and product judgment using
its shared Kanban, Slack, ticket, PR, and source context. It passes one bounded
delivery brief to the downstream manager, which performs narrow eligibility,
approval, implementation, and review transitions. The manager does not scan for
unrelated work on every wake-up.

Slack is used for the initial coder authorization only. After approval, plans,
questions, answers, review findings, and handoffs stay in GitHub. If the agents
cannot answer a product question autonomously, they ask once on the issue and
wait for a human issue or PR comment rather than posting routine Slack updates.

## Install the pinned coder dependency

The coder is a git submodule because its current source revision is not
wheel-buildable as a pip VCS dependency. It is still pinned to an exact commit
and is used without modifications, another server, worker, or wrapper.

```bash
git submodule update --init --recursive
make setup
```

Verify `codex`, Git, and GitHub authentication as the same operating-system
user that runs the PM:

```bash
codex --version
codex login status
gh auth status
```

## Configure the boundary

Keep both write switches false initially. Copy the agentic-development block
from `.env.example` and configure:

- `GITHUB_DELIVERY_ALLOWED_REPOSITORIES`: exact `owner/repository` targets.
- `GITHUB_DELIVERY_PM_LOGIN`: a machine user with upstream Triage access and
  write access to the configured Project. PM upstream repository Write permission
  is allowed, but is not passed to the coder.
- `GITHUB_DELIVERY_CODER_LOGIN`: preferably a distinct machine user with Read
  access to the public upstream and a same-name fork it owns. It must not have
  upstream Write access. The configuration checker permits the PM and coder
  login to be the same for an initial trial, but emits a warning.
- `GITHUB_PM_TOKEN` and `GITHUB_CODER_TOKEN`: use distinct credentials when the
  PM account can push upstream. The checker permits the same non-empty
  credential with a warning, but the fork boundary still rejects it if the
  coder credential has upstream push permission. The PM token performs
  issue/PR/Project coordination; the bundled launcher exposes only the coder
  token to Codex.
- `GITHUB_DELIVERY_HUMAN_REVIEWERS`: comma-separated human GitHub logins. A
  reviewer overlapping the PM or coder is permitted with a warning, but a
  separate human reviewer is strongly recommended.
- `CODING_AGENT_ALLOWED_WORKSPACES`: one or more absolute roots separated by
  the platform path separator.
- `CODING_AGENT_PRIMARY_WORKSPACE`: an existing directory within those roots.
- `AGENTIC_DELIVERY_REQUIRED_LABEL`: use `pm-agentic-e2e` for the canary.

The PM run limit is 600 seconds. Keep `CODING_AGENT_TIMEOUT_SECONDS` at 480 or
lower so the existing coded tool can return and persist its GitHub handoff
before the outer run expires.

Before enabling delivery, sign in as the coder machine user and fork every
allowlisted public upstream repository into that user's namespace, preserving
the repository name. The configured local workspace may initially point at the
upstream or fork. On each run the host rewrites `origin` to
`https://github.com/<coder>/<repo>.git` and `upstream` to the allowlisted
source. The sanitized launcher removes PM and ambient GitHub credentials,
disables global/system Git credential configuration, and supplies only the
coder token through a non-interactive HTTPS helper.

Project status writes need the IDs for the configured project, its Status
field, and the In Progress/In Review options. Obtain them with GitHub CLI:

```bash
gh project view "$GITHUB_PROJECT_NUMBER" --owner "$GITHUB_PROJECT_OWNER" --format json
gh project field-list "$GITHUB_PROJECT_NUMBER" --owner "$GITHUB_PROJECT_OWNER" --format json
```

Set `GITHUB_PROJECT_ID`, `GITHUB_PROJECT_STATUS_FIELD_ID`, and
`GITHUB_PROJECT_STATUS_OPTIONS_JSON`, for example:

```dotenv
GITHUB_PROJECT_STATUS_OPTIONS_JSON={"In Progress":"option-id","In Review":"option-id"}
```

The PM token needs Projects write plus Issues and Pull requests write but the PM
account itself needs only upstream Triage. The coder token needs content write
on its own forks and enough public-repository access to open cross-fork PRs. It
must have no upstream Write permission. The bounded PM tools do not expose
merge, close, delete, source-file write, or PR approval operations.

## Verification-first canary

First run the offline contract and simulated workflow:

```bash
make agentic-test
```

Then perform the live canary:

1. Manually create a disposable, simple documentation or test-only issue in an
   allowlisted repository. Give it explicit acceptance criteria and the
   `pm-agentic-e2e` label.
2. Add it to the configured project in Backlog or To Do and leave it
   unassigned.
3. Set `COLLEAGUE_SLACK_WRITE_ENABLED=true`,
   `COLLEAGUE_AGENTIC_DEVELOPMENT_ENABLED=true`, and
   `GITHUB_DELIVERY_WRITE_ENABLED=true`.
4. Run `make check`, restart `make run`, and invoke `make trigger`. The Slack
   bridge is optional for this deterministic manual canary.
5. Confirm exactly one Slack proposal appears and that the issue has not
   changed. With the Slack bridge running, reply naturally in that thread; the
   reply itself wakes the next run. Use `make trigger` only when testing without
   the bridge. The triage agent interprets the
   response as approval, rejection, or unclear; the host independently verifies
   the exact message, thread, and allowlisted author before recording a
   decision. If the meaning is unclear, the agent asks one short follow-up.
6. Wait for the bridge-driven run (or use one `make trigger` fallback when the
   bridge is not running). Observe the issue plan,
   In Progress movement, coder assignment, PR creation, implementation/test
   comment, and assignment back to the PM.
7. For a revision, let the PM comment and reassign the ticket to the coder;
   the next wake must start a fresh coder session from those GitHub comments.
8. The canary passes only when the card is In Review, configured humans are
   requested on the open PR, and GitHub reports `merged=false`.

After the canary, remove `AGENTIC_DELIVERY_REQUIRED_LABEL` to consider normal
eligible tickets. Set either agentic development or GitHub delivery writes back
to false for an immediate kill switch.
