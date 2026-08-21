#!/usr/bin/env python3
"""server.py — MCP wiring for the Grounded Support Agent: tool schemas + dispatch.

This is the ONLY module that imports the `mcp` SDK, and it does so lazily inside
build_server(), so the tool logic (the TOOLS registry and its handlers) runs and
is fully testable with NO SDK and no pip install. Inspect the contract with:

    python3 mcp_server/server.py --contract      # no SDK required
    pip install mcp && python3 -m mcp_server.server   # speak MCP over stdio

Design (mirrors itsoc-mcp): this layer is a thin CLIENT of core/. It computes no
verdict itself — it calls core.resolver, which owns the resolve-or-escalate
decision — so it can never fabricate a resolution. Every response carries the
provenance block (KB sha256, retriever + params, thresholds, score, coverage).

Two tools:
  resolve_or_escalate(question)  -> the verdict, with citation + provenance
  get_evidence(question, k)      -> ranked passages for a human, NO decision
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.resolver import Resolver  # noqa: E402

DEFAULT_KB = os.path.join(ROOT, "kb")


def _resolver(kb_dir=None):
    return Resolver.from_kb(kb_dir or os.environ.get("GSA_KB_DIR") or DEFAULT_KB)


# --- tool handlers (SDK-free, pure functions of the resolver) --------------
def resolve_or_escalate(resolver, question):
    """The governed verdict. Returns core's decision dict verbatim — verdict,
    citation (on RESOLVE), ranked evidence, and provenance. This layer adds
    nothing to the decision."""
    if not (question or "").strip():
        return {"ok": False, "error": "a non-empty 'question' is required"}
    decision = resolver.resolve_or_escalate(question)
    return {"ok": True, **decision}


def get_evidence(resolver, question, k=3):
    """The ranked passages for a human reviewer, with NO decision attached. Use
    when a person wants to see what the KB has without the agent taking a
    position. Still carries provenance so the evidence can be tied to the KB."""
    if not (question or "").strip():
        return {"ok": False, "error": "a non-empty 'question' is required"}
    ranked = resolver.retriever.search(question, k=max(1, int(k)))
    prov = resolver.retriever.provenance()
    evidence = [{
        "passage_id": r["passage"].id,
        "topic": r["passage"].topic,
        "heading": r["passage"].heading,
        "score": r["bm25"],
        "coverage": r["coverage"],
        "matched_terms": r["matched"],
        "text": r["passage"].text,
    } for r in ranked]
    return {
        "ok": True,
        "question": question,
        "decision": None,  # explicit: this tool makes no resolve/escalate call
        "evidence": evidence,
        "provenance": prov,
    }


# --- tool registry ---------------------------------------------------------
TOOLS = [
    {
        "name": "resolve_or_escalate",
        "description": (
            "Decide whether the support KB can answer a customer question, and "
            "if so return a grounded answer WITH its cited source passage. The "
            "verdict (RESOLVE or ESCALATE) is owned by the deterministic decision "
            "engine, not by any model: an out-of-scope question always escalates "
            "and is never resolved. The response carries a provenance block (KB "
            "sha256, retriever + params, thresholds, score, coverage) so the "
            "decision can be audited. Never fabricates a resolution."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The customer's question, verbatim.",
                },
            },
            "required": ["question"],
        },
        "handler": lambda r, args: resolve_or_escalate(
            r, question=args.get("question", "")),
    },
    {
        "name": "get_evidence",
        "description": (
            "Return the top ranked KB passages for a question, for a HUMAN "
            "reviewer, with NO resolve/escalate decision attached. Read-only. "
            "Use to inspect what the KB contains without the agent taking a "
            "position. Carries the same provenance block so the evidence is tied "
            "to the exact KB that produced it. Passages are verbatim KB text, "
            "never fabricated."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to gather evidence for.",
                },
                "k": {
                    "type": "integer",
                    "description": "How many passages to return (default 3).",
                    "default": 3,
                },
            },
            "required": ["question"],
        },
        "handler": lambda r, args: get_evidence(
            r, question=args.get("question", ""), k=args.get("k", 3)),
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def contract():
    """The tool contract as a plain dict — the same shape an MCP client sees
    from list_tools, minus the SDK. Printed by --contract."""
    resolver = _resolver()
    return {
        "server": "grounded-support-agent",
        "description": "Governed customer-support resolver (resolve or escalate).",
        "kb_sha256": resolver.retriever.fingerprint,
        "kb_passages": resolver.retriever.num_passages,
        "tools": [
            {"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]}
            for t in TOOLS
        ],
    }


def build_server(resolver=None):
    """Construct the MCP Server with tools registered. `resolver` is injectable
    so a harness can supply one over a test KB; production builds one over kb/."""
    import asyncio
    from mcp.server import Server        # imported lazily so the tool logic stays SDK-free
    import mcp.types as types

    resolver = resolver or _resolver()
    server = Server("grounded-support-agent")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=t["name"], description=t["description"],
                           inputSchema=t["inputSchema"]) for t in TOOLS]

    @server.call_tool()
    async def call_tool(name, arguments):
        tool = _BY_NAME.get(name)
        if tool is None:
            payload = {"ok": False, "error": "unknown tool: {}".format(name)}
        else:
            payload = await asyncio.to_thread(tool["handler"], resolver, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

    return server


def _serve_stdio():
    import asyncio
    from mcp.server.stdio import stdio_server

    async def _amain():
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream,
                             server.create_initialization_options())

    asyncio.run(_amain())


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Grounded Support Agent MCP server.")
    parser.add_argument("--contract", action="store_true",
                        help="print the tool contract as JSON and exit (no SDK needed)")
    args = parser.parse_args(argv)

    if args.contract:
        print(json.dumps(contract(), indent=2))
        return 0

    try:
        _serve_stdio()
    except ImportError:
        sys.stderr.write(
            "The `mcp` SDK is not installed. Install it to speak MCP over stdio:\n"
            "    pip install mcp && python3 -m mcp_server.server\n"
            "To inspect the tool contract without the SDK:\n"
            "    python3 mcp_server/server.py --contract\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
