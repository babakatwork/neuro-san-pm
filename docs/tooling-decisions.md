# Tooling decisions

## Gmail

Studio's existing `gmail_toolkit` exposes search, reading, drafts, and sending
together. The sample does not attach that broad toolkit to an autonomous loop.
It uses the official Google API client behind three coded boundaries instead:
bounded host-prefixed search, bounded plain-text read, and a separately scoped,
lease-bound, exact-recipient-allowlisted sender that starts in dry-run mode.

## Neuro SAN runtime

This project follows the current `ns init` project shape and uses Studio 0.3.9 /
core 0.6.76. Scheduling is not a custom Python loop: `periodic.interactions` in
the manifest activates the core scheduler, and the front agent is an `event`.

## Existing GitHub MCP entry

The older local `mcp_info.hocon` used `headers`; current neuro-san expects
`http_headers`. It also exposed the remote server's default tools, which do not
include GitHub Projects. The replacement uses explicit Projects/Issues/PR
read-only URLs, the current header key, and per-tool allowlists.

Read-only is not resource scoping: a raw MCP tool can still read any project or
repository authorized by its token. The sample therefore does not attach those
MCP tools. `GitHubProjectReader` takes no owner/project arguments and uses one
constant query against host-owned coordinates. The MCP entries remain templates
for future networks that add an equivalent validating boundary.

## Existing `slack.py`

The cited Agentic RAG Slack tool only reads channel history. Its synchronous
method is empty, it cannot post proactive updates, and its missing-dependency
path returns hard-coded sample business content. A permanent teammate must fail
closed, so this project replaces it with bounded Slack Web API tools that have
no fabricated fallback.

## Existing Slack app

The Studio Socket Mode app is a useful interactive reference, but it accepts
arbitrary network names and `sly_data`, keeps context only in memory, and does
not provide an agent-callable proactive send boundary. The new bridge is fixed
to `product_colleague`, one channel, and allowlisted users. It sends only a wake
signal; scheduled and event runs converge on the same paginated durable inbox.
Proactive delivery is a separate fixed-destination coded tool.

## Slack/Codex skills and connectors

Codex connectors and skills help an operator work in an interactive Codex task;
they are not runtime tools available inside a separately deployed Neuro SAN
process. Runtime Slack access therefore uses a dedicated Slack app and bot
token.

## Tochiro and computer use

The useful pattern from Tochiro is locally scoped coded tools whose authority is
defined by host state rather than model text. General computer use is not
inherited from that sample. This project provides a Playwright MCP extension
point, disabled by default and left outside the permanent Compose stack until
host-enforced egress isolation exists, while keeping GitHub and Slack on their
purpose-built interfaces.
