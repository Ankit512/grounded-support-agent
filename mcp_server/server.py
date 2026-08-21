"""MCP server: expose the grounded support agent as a governed tool for an orchestrator.

This mirrors the itsoc-mcp design deliberately. The MCP layer is a thin *client* of the
decision engine and computes nothing itself: it cannot answer, only relay the engine's
resolve-or-escalate verdict, and every response carries the same provenance block. That
makes the agent safe to drop into a multi-agent system (for example Fin's Agent API, which
now supports multi-agent architectures) as a component that will never fabricate a
resolution on another agent's behalf.

Run:
    pip install mcp
    python3 -m mcp_server.server        # speaks MCP over stdio

If the `mcp` SDK is not installed, importing this module still succeeds enough to show the
tool contract via `python3 mcp_server/server.py --contract`, so the design is inspectable
without any dependency.
"""
from __future__ import annotations
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.resolver import Resolver, decision_to_json  # noqa: E402

KB = os.path.join(ROOT, "kb")
_resolver = Resolver(KB)

TOOL_CONTRACT = {
    "resolve_or_escalate": {
        "description": "Answer a customer support question ONLY if the knowledge base can "
                       "ground it; otherwise return an honest escalation. Never fabricates.",
        "input": {"question": "string", "top_k": "int (optional, default 3)"},
        "output": {
            "outcome": "resolved | escalated",
            "reason": "grounded | low_confidence | insufficient_coverage | no_match",
            "confidence": "0..1 (knowledge-base coverage of the question)",
            "answer": "grounded answer text, or null when escalated",
            "citations": "[{passage_id, doc, heading, score}]",
            "handoff_note": "context for the human, or null when resolved",
            "provenance": "{kb_sha256_16, retriever, thresholds, top_score, top_coverage, ...}",
        },
        "guarantees": [
            "computes no answer of its own; relays the engine verdict",
            "no answer is returned without at least one citation",
            "out-of-scope questions are escalated, never resolved",
        ],
    },
    "get_evidence": {
        "description": "Return the ranked knowledge-base passages considered for a question, "
                       "for a human reviewer, without making a resolve/escalate decision.",
        "input": {"question": "string", "top_k": "int (optional, default 3)"},
        "output": {"citations": "[{passage_id, doc, heading, score}]"},
    },
}


def resolve_or_escalate(question: str, top_k: int = 3) -> dict:
    d = _resolver.resolve(question, top_k=top_k)
    return json.loads(decision_to_json(d))


def get_evidence(question: str, top_k: int = 3) -> dict:
    hits = _resolver.bm25.search(question, top_k=top_k)
    return {"citations": [
        {"passage_id": p.passage_id, "doc": p.doc, "heading": p.heading, "score": round(s, 3)}
        for p, s in hits
    ]}


def _serve_stdio():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("The `mcp` SDK is not installed. Install with `pip install mcp`, or inspect the "
              "tool contract with:  python3 mcp_server/server.py --contract", file=sys.stderr)
        raise SystemExit(1)
    app = FastMCP("grounded-support-agent")
    app.tool()(resolve_or_escalate)
    app.tool()(get_evidence)
    app.run()


if __name__ == "__main__":
    if "--contract" in sys.argv:
        print(json.dumps(TOOL_CONTRACT, indent=2))
    else:
        _serve_stdio()
