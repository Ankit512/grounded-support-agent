# Publishing `grounded-support-agent` — DRAFT (human-gated)

This is a **draft** describing how to publish. The build and local verification
below **have been run**; the **credentialed steps have not**. **Do not** run
`twine upload`, `mcp-publisher login`, `mcp-publisher publish`, push to any remote,
or open any PR without a human decision — those steps are intentionally left to a
person with the PyPI token and the GitHub OAuth login.

> **The official path is the MCP Registry (`registry.modelcontextprotocol.io`).**
> It is metadata-only and driven by the `mcp-publisher` CLI plus a `server.json`
> manifest (see [`server.json`](server.json), validated against the current
> `2025-12-11` schema). The old "open a PR against `modelcontextprotocol/servers`"
> path is **obsolete** — that repo redirects to the official registry. Do not open
> a servers-repo PR.

## Server identity

- **Registry name:** `io.github.Ankit512/grounded-support-agent` (GitHub-namespaced;
  matches the `name` in `server.json` **and** the `mcp-name:` marker in the PyPI
  README). **DEFAULT — the human may rename before publishing.**
- **PyPI package:** `grounded-support-agent`
- **Version:** `0.2.0` (matches across `pyproject.toml` and `server.json`; must also
  match the PyPI upload on every release).
- **One-line description:** Governed customer-support MCP server: resolves only
  KB-grounded questions with citations, honestly escalates the rest, never
  fabricates a resolution.
- **Homepage:** https://github.com/Ankit512/grounded-support-agent
- **License:** MIT (holder: Ankit Kumar) · **Transport:** stdio
- **Tools (read-only, all four hints explicit):** `list_topics` (understand
  coverage), `get_evidence` (ranked passages with text, no decision),
  `resolve_or_escalate` (the verdict, with citations + provenance). Each tool
  declares `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`,
  `openWorldHint: false`.

## Self-contained by design (verified)

Unlike a server that proxies a separate backend, this package is **fully
self-contained**: the decision engine (`core/`), the MCP wrapper (`mcp_server/`)
and the knowledge base (`kb/`) all ship in the wheel, and the core needs no
network and no model. `uvx grounded-support-agent` runs the **complete** tool set
with no repo checkout and nothing else running. The installed server resolves its
bundled KB via `core.retriever.default_kb_dir()` (importlib.resources, with an
in-repo `__file__` fallback); `GSA_KB_DIR` overrides it.

## Publish flow (human-run, in order)

Ownership of the `io.github.Ankit512/*` namespace is proven two ways: the
`mcp-name: io.github.Ankit512/grounded-support-agent` marker that ships in the PyPI
long-description (already in [`README.md`](README.md), confirmed present in the
built `dist/*` METADATA/PKG-INFO), and a GitHub OAuth login.

1. **PyPI upload FIRST** (the registry only references an already-published
   package). From the repo root, with the maintainer's PyPI token:

   ```sh
   python -m build                 # -> dist/grounded_support_agent-0.2.0-{whl,tar.gz}
   twine upload dist/*             # human's PyPI token
   ```

   The build embeds the `mcp-name:` marker in the package long-description — this
   is what the registry checks to verify PyPI ownership.

2. **Authenticate to the registry** (interactive GitHub OAuth; proves you own the
   `io.github.Ankit512` namespace):

   ```sh
   mcp-publisher login github
   ```

3. **Publish the manifest** (metadata-only; points at the PyPI package from step 1):

   ```sh
   mcp-publisher publish          # reads ./server.json
   ```

Run steps 2–3 from the repo root (the directory containing `server.json`). On each
new release, bump the version in `pyproject.toml` **and** `server.json` together,
re-upload to PyPI, then `mcp-publisher publish` again.

## Install (once published)

```sh
uvx grounded-support-agent        # or: pipx run grounded-support-agent
```

No repo checkout, no external backend, no API key — the KB ships in the wheel and
every tool works standalone.

## Paste-ready MCP client registration (stdio)

For Claude Desktop (`claude_desktop_config.json`) or Claude Code (`.mcp.json`).

**Standalone (recommended, once published to PyPI):**

```json
{
  "mcpServers": {
    "grounded-support-agent": {
      "command": "uvx",
      "args": ["grounded-support-agent"]
    }
  }
}
```

**From a repo checkout** (replace `/ABSOLUTE/PATH/TO/grounded-support-agent`):

```json
{
  "mcpServers": {
    "grounded-support-agent": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/ABSOLUTE/PATH/TO/grounded-support-agent"
    }
  }
}
```

Optional `env`: `"GSA_KB_DIR": "/path/to/a/custom/kb"` to point the server at a
different knowledge base directory.

## Pre-publish checklist (for the human)

Boxes are checked ONLY where the step was actually run and its result observed.

- [ ] `python -m build` from the repo root succeeds → `dist/grounded_support_agent-0.2.0-{whl,tar.gz}`.
- [ ] `twine check dist/*` for the 0.2.0 artifacts.
- [x] `mcp-name:` marker still in [`README.md`](README.md) (`io.github.Ankit512/grounded-support-agent`).
- [x] `kb/*.md` (all six topics) + `core/` + `mcp_server/` ship in the wheel;
      console-script entry point `grounded-support-agent = mcp_server.server:main`.
- [x] Versions match: `pyproject.toml` (`0.2.0`) == `server.json` (`0.2.0`).
- [ ] **Clean out-of-repo venv for 0.2.0:** install the wheel into a fresh venv,
      run `grounded-support-agent --contract`, confirm `list_topics` /
      `get_evidence` / `resolve_or_escalate` all appear with the four boolean
      hints, and that `--contract` still works with the SDK absent.
- [x] In-repo: `python3 eval/run_eval.py` → RESULT: PASS, 0 hallucinations;
      `python3 -m unittest discover -s tests -v` → OK (named tests for
      `list_topics`, `get_evidence`, `resolve_or_escalate`, including FastMCP
      `tools/list` annotations when the SDK is installed).
- [ ] **HUMAN:** confirm the PyPI/registry name (`grounded-support-agent` /
      `io.github.Ankit512/grounded-support-agent`) and the LICENSE holder
      ("Ankit Kumar"), or change them, before publishing.
- [ ] **HUMAN:** register the PyPI project / have a PyPI token ready.
- [ ] **HUMAN, only then, in order:** `twine upload dist/*` →
      `mcp-publisher login github` → `mcp-publisher publish`.

> **Schema note:** the registry schema is versioned by date
> (`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`).
> If the CLI reports a schema mismatch on publish, re-scaffold with
> `mcp-publisher init` (no login required) and re-apply these field values.
