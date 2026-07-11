# Computer-use policy

The product colleague does not need UI automation for GitHub or Slack; their
APIs/MCP tools are safer and more deterministic. Computer use is supplied only
as a disabled extension point for sites without a suitable interface.

## Start Playwright MCP

Local development:

```bash
npx -y @playwright/mcp@0.0.77 --headless --isolated \
  --block-service-workers \
  --allowed-origins "https://github.com;https://*.githubusercontent.com" \
  --port 8931
```

The version is pinned so a permanent agent does not silently gain tools or
behavior on restart. Update it deliberately after review. The browser uses an
ephemeral profile and a narrow initial GitHub origin list. Playwright documents
that origin filtering does not cover redirects and is not a security boundary,
so this command is for a separately isolated development environment—not the
permanent colleague Compose stack. A production browser worker needs enforced
egress controls outside Playwright.

## Enable the optional network

After reviewing the policy, add this entry to `registries/manifest.hocon`:

```hocon
"optional/computer_use_researcher.hocon": {
    serve = true
    public = false
    mcp = false
}
```

Do not add this network as a tool of the scheduled product colleague. Invoke it
only for a specific operator request.

## Initial capability set

The MCP allowlist contains only:

- `browser_navigate`;
- `browser_snapshot`;
- `browser_take_screenshot`.

It excludes typing, form submission, downloads, file access, and arbitrary code.
Even observation-only navigation can trigger server-side effects or expose
private data. Run the browser without signed-in sessions, sensitive mounts, or
access to cloud metadata/private networks.

When interaction is eventually needed, add one capability at a time behind a
host-enforced domain allowlist and explicit human approval. Playwright MCP's own
documentation states that it is not a security boundary.
