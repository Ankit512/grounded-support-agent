# Security policy

This server is a **local, closed-world, read-only** MCP tool. It answers from a
bundled markdown knowledge base and never reaches the network.

## What it does not do

- No outbound HTTP, DNS, or socket calls from `core/` or `mcp_server/`.
- No credentials, API keys, or OAuth. The only optional env var is `GSA_KB_DIR`,
  which points at a local directory of markdown files.
- No writes: tools never create, update, or delete files, tickets, or accounts.
- No shell, eval, or dynamic code execution on tool arguments.

## Tool behaviour (hints)

Every tool declares all four MCP hints as explicit booleans:

| Hint | Value | Why |
| --- | --- | --- |
| `readOnlyHint` | `true` | handlers only read the loaded KB |
| `destructiveHint` | `false` | no irreversible change is possible |
| `idempotentHint` | `true` | same arguments + same KB → same payload |
| `openWorldHint` | `false` | closed set of local passages, not the open web |

Hosts can auto-approve these tools. Treat a different annotation as a bug.

## Reporting a vulnerability

Open a private GitHub security advisory on
[Ankit512/grounded-support-agent](https://github.com/Ankit512/grounded-support-agent)
or email the maintainer listed on the repository. Please do not file a public
issue for an unfixed vulnerability.
