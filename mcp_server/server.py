"""MCP server: expose the grounded support agent as a governed tool for an orchestrator.

This mirrors the itsoc-mcp design deliberately. The MCP layer is a thin *client* of the
decision engine and computes nothing itself: it cannot answer, only relay the engine's
resolve-or-escalate verdict, and every response carries the same provenance block. That
makes the agent safe to drop into a multi-agent system (for example Fin's Agent API, which
now supports multi-agent architectures) as a component that will never fabricate a
resolution on another agent's behalf.

Host workflow (four paths):

    understand  list_topics / get_evidence / kb:// resources
    resolve     resolve_or_escalate
    test        tests/test_mcp_tools.py (every tool, by name)
    publish     server.json + PUBLISHING.md (human-gated)

Every tool declares all four MCP hints as explicit booleans so hosts can warn before
invoke, and so OpenAI's directory (which rejects missing/non-boolean hints) will accept
the contract. The values match handler behaviour: local closed-world KB, read-only,
deterministic, never destructive.

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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from core.resolver import Resolver, decision_to_json  # noqa: E402
from core.retriever import default_kb_dir  # noqa: E402

# The KB is resolved so the server works both in-repo and when pip/uvx-installed
# (default_kb_dir finds the bundled kb/ in the wheel). GSA_KB_DIR overrides it.
KB = os.environ.get("GSA_KB_DIR") or default_kb_dir()
_resolver = Resolver(KB)

TOP_K_MIN = 1
TOP_K_MAX = 20

# OpenAI's directory rejects a tool when any of these four is missing or non-boolean.
# Defaults in the spec are the opposite of this server (readOnlyHint false, etc.), so
# we declare every hint explicitly rather than relying on a client default.
HINT_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def _closed_world_read_hints(title: str) -> dict:
    """Hints for a tool that only reads the local bundled KB.

    readOnlyHint     True  — no create/update/delete, no network, no env mutation
    destructiveHint  False — no irreversible change (meaningful because we still declare it)
    idempotentHint   True  — same args, same KB => same payload (no wall-clock in output)
    openWorldHint    False — closed set of local markdown passages, not the open web
    """
    return {
        "title": title,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


TOOL_CONTRACT = {
    "list_topics": {
        "description": (
            "Understand coverage: list every knowledge-base document and heading this "
            "server can ground an answer in. Call this to see what the agent knows "
            "before asking a question. Does not answer questions and does not make a "
            "resolve/escalate decision. Read-only, closed-world, deterministic."
        ),
        "input": {},
        "output": {
            "kb_sha256_16": "fingerprint of the loaded knowledge base",
            "topics": "[{doc, passages: [{heading, passage_id}]}]",
        },
        "annotations": _closed_world_read_hints("List knowledge-base topics"),
        "when_to_use": "To understand what the KB covers, before resolve_or_escalate.",
        "when_not_to_use": "To answer a customer question (call resolve_or_escalate) or to rank passages for one question (call get_evidence).",
    },
    "get_evidence": {
        "description": (
            "Understand a single question: return the ranked knowledge-base passages "
            "considered for it, including passage text, for a human reviewer. Does not "
            "make a resolve/escalate decision and never invents text. Read-only, "
            "closed-world, deterministic. Use resolve_or_escalate when you need the verdict."
        ),
        "input": {"question": "string", "top_k": "int (optional, default 3, min 1, max 20)"},
        "output": {
            "kb_sha256_16": "fingerprint of the loaded knowledge base",
            "citations": "[{passage_id, doc, heading, score, text}]",
        },
        "annotations": _closed_world_read_hints("Get evidence"),
        "when_to_use": "A reviewer wants the passages behind a question without a decision attached.",
        "when_not_to_use": "To produce a customer-facing verdict (call resolve_or_escalate) or to list all topics (call list_topics).",
    },
    "resolve_or_escalate": {
        "description": (
            "Resolve a customer support question ONLY if the knowledge base can ground "
            "it; otherwise return an honest escalation. Never fabricates an answer. "
            "Relays the deterministic engine verdict with citations and provenance. "
            "Read-only, closed-world, deterministic. Use get_evidence to inspect "
            "passages without a decision; use list_topics to see coverage."
        ),
        "input": {"question": "string", "top_k": "int (optional, default 3, min 1, max 20)"},
        "output": {
            "outcome": "resolved | escalated",
            "reason": "grounded | low_confidence | insufficient_coverage | no_match",
            "confidence": "0..1 (knowledge-base coverage of the question)",
            "answer": "grounded answer text, or null when escalated",
            "citations": "[{passage_id, doc, heading, score}]",
            "handoff_note": "context for the human, or null when resolved",
            "provenance": "{kb_sha256_16, retriever, thresholds, top_score, top_coverage, ...}",
        },
        "annotations": _closed_world_read_hints("Resolve or escalate"),
        "guarantees": [
            "computes no answer of its own; relays the engine verdict",
            "no answer is returned without at least one citation",
            "out-of-scope questions are escalated, never resolved",
            "does not modify the knowledge base, disk, or any external system",
        ],
        "when_to_use": "The customer asked a support question and you need a governed verdict.",
        "when_not_to_use": "To browse the KB (list_topics) or to inspect ranked passages with no decision (get_evidence).",
    },
}

TOOL_NAMES = tuple(TOOL_CONTRACT.keys())


def _require_question(question: str) -> str:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    return question.strip()


def _require_top_k(top_k: int) -> int:
    try:
        k = int(top_k)
    except (TypeError, ValueError):
        raise ValueError("top_k must be an integer") from None
    if k < TOP_K_MIN or k > TOP_K_MAX:
        raise ValueError("top_k must be between {} and {}".format(TOP_K_MIN, TOP_K_MAX))
    return k


def _strip_wall_clock(payload: dict) -> dict:
    """Drop asked_at so MCP output is byte-stable across calls (idempotentHint)."""
    prov = payload.get("provenance")
    if isinstance(prov, dict) and "asked_at" in prov:
        prov = dict(prov)
        prov.pop("asked_at", None)
        payload = dict(payload)
        payload["provenance"] = prov
    return payload


def list_topics() -> dict:
    """List every knowledge-base document and heading this server can ground.

    Use this to understand coverage before calling resolve_or_escalate. Does not
    answer questions and does not make a resolve/escalate decision.
    """
    by_doc: dict[str, list] = {}
    for p in _resolver.passages:
        by_doc.setdefault(p.doc, []).append(
            {"heading": p.heading, "passage_id": p.passage_id}
        )
    return {
        "kb_sha256_16": _resolver.kb_hash,
        "topics": [
            {"doc": doc, "passages": passages}
            for doc, passages in sorted(by_doc.items())
        ],
    }


def get_evidence(question: str, top_k: int = 3) -> dict:
    """Return ranked KB passages (with text) for a question, with no decision.

    For a human reviewer. Does not resolve or escalate. Never invents passage text.
    """
    question = _require_question(question)
    top_k = _require_top_k(top_k)
    hits = _resolver.bm25.search(question, top_k=top_k)
    return {
        "kb_sha256_16": _resolver.kb_hash,
        "citations": [
            {
                "passage_id": p.passage_id,
                "doc": p.doc,
                "heading": p.heading,
                "score": round(s, 3),
                "text": p.text,
            }
            for p, s in hits
        ],
    }


def resolve_or_escalate(question: str, top_k: int = 3) -> dict:
    """Answer a support question only if the KB can ground it; else escalate.

    Relays the engine verdict. Never fabricates. Same question + KB => same payload.
    """
    question = _require_question(question)
    top_k = _require_top_k(top_k)
    d = _resolver.resolve(question, top_k=top_k)
    return _strip_wall_clock(json.loads(decision_to_json(d)))


def _read_kb_doc(doc: str) -> str:
    """Read one bundled markdown file. Rejects anything that is not a kb/*.md name."""
    if not isinstance(doc, str) or not doc.endswith(".md"):
        raise ValueError("doc must be a knowledge-base markdown filename")
    if "/" in doc or "\\" in doc or ".." in doc or doc.startswith("."):
        raise ValueError("doc must be a knowledge-base markdown filename")
    kb_real = os.path.realpath(KB)
    path = os.path.realpath(os.path.join(kb_real, doc))
    if not path.startswith(kb_real + os.sep) or not os.path.isfile(path):
        raise ValueError("unknown knowledge-base document: {}".format(doc))
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_app():
    """Construct the FastMCP app with tools, resources, and the triage prompt.

    Imported only on the stdio / SDK path so `python3 mcp_server/server.py --contract`
    still works with no third-party package.
    """
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    app = FastMCP("grounded-support-agent")

    # Hints are inlined at each registration so source-side AST (M8ven, OpenAI
    # directory) sees every tool name next to all four explicit booleans.
    app.tool(
        name="list_topics",
        title="List knowledge-base topics",
        description=TOOL_CONTRACT["list_topics"]["description"],
        annotations=ToolAnnotations(
            title="List knowledge-base topics",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )(list_topics)

    app.tool(
        name="get_evidence",
        title="Get evidence",
        description=TOOL_CONTRACT["get_evidence"]["description"],
        annotations=ToolAnnotations(
            title="Get evidence",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )(get_evidence)

    app.tool(
        name="resolve_or_escalate",
        title="Resolve or escalate",
        description=TOOL_CONTRACT["resolve_or_escalate"]["description"],
        annotations=ToolAnnotations(
            title="Resolve or escalate",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )(resolve_or_escalate)

    app.resource(
        "kb://document/{doc}",
        name="kb_document",
        title="Knowledge-base document",
        description="Read one bundled support knowledge-base markdown file by filename (for example password.md). Read-only.",
        mime_type="text/markdown",
    )(_read_kb_doc)

    def support_triage(question: str) -> str:
        return (
            "Customer question:\n{question}\n\n"
            "Triage with the grounded support agent. Stay on this path:\n"
            "1. Understand — call list_topics if you need coverage, or get_evidence "
            "to inspect ranked passages (with text) and no decision.\n"
            "2. Resolve — call resolve_or_escalate for the verdict. Relay its answer "
            "and citations when outcome is resolved; add no facts of your own.\n"
            "3. If outcome is escalated, do not invent an answer. Hand off with the "
            "handoff_note and citations.\n"
            "The server is read-only and closed-world. It never writes, and it never "
            "reaches the network."
        ).format(question=question)

    app.prompt(
        name="support_triage",
        title="Support triage",
        description="Understand then resolve a customer question with the grounded support agent, without fabricating.",
    )(support_triage)

    return app


def _serve_stdio():
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        from mcp.types import ToolAnnotations  # noqa: F401
    except ImportError:
        print("The `mcp` SDK is not installed. Install with `pip install mcp`, or inspect the "
              "tool contract with:  python3 mcp_server/server.py --contract", file=sys.stderr)
        raise SystemExit(1)
    build_app().run()


def main(argv=None):
    """Console-script entry point (`grounded-support-agent`).

    `--contract` prints the tool contract as JSON and exits with NO SDK required;
    otherwise the server speaks MCP over stdio (needs the `mcp` SDK).
    """
    argv = sys.argv[1:] if argv is None else argv
    if "--contract" in argv:
        print(json.dumps(TOOL_CONTRACT, indent=2))
        return 0
    _serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
