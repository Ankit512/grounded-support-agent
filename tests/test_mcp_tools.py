#!/usr/bin/env python3
"""MCP tool-contract tests. Every declared tool is referenced by name.

M8ven and OpenAI's directory require:
  * all four hints (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
    as explicit booleans on every tool
  * at least one test that names each tool

Standard library for the contract and handler tests:
    python3 tests/test_mcp_tools.py

The FastMCP registration tests run when the `mcp` SDK is installed (CI mcp-contract
job). They are skipped, not failed, when it is absent.
"""
from __future__ import annotations
import ast
import asyncio
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcp_server.server import (  # noqa: E402
    HINT_KEYS,
    TOOL_CONTRACT,
    TOOL_NAMES,
    get_evidence,
    list_topics,
    resolve_or_escalate,
)

SERVER_PY = os.path.join(ROOT, "mcp_server", "server.py")

try:
    from mcp.server.fastmcp import FastMCP  # noqa: F401
    HAS_MCP = True
except Exception:
    HAS_MCP = False


class ToolNameInventoryTests(unittest.TestCase):
    """The contract and the handlers agree on the tool names."""

    def test_contract_declares_list_topics(self):
        self.assertIn("list_topics", TOOL_CONTRACT)
        self.assertIn("list_topics", TOOL_NAMES)

    def test_contract_declares_get_evidence(self):
        self.assertIn("get_evidence", TOOL_CONTRACT)
        self.assertIn("get_evidence", TOOL_NAMES)

    def test_contract_declares_resolve_or_escalate(self):
        self.assertIn("resolve_or_escalate", TOOL_CONTRACT)
        self.assertIn("resolve_or_escalate", TOOL_NAMES)

    def test_every_tool_is_in_the_four_path_set(self):
        self.assertEqual(
            set(TOOL_NAMES),
            {"list_topics", "get_evidence", "resolve_or_escalate"},
        )

    def test_readme_names_every_tool(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for name in ("list_topics", "get_evidence", "resolve_or_escalate"):
            self.assertIn("`{}`".format(name), readme)


class ToolAnnotationTests(unittest.TestCase):
    """All four hints declared, explicit booleans, matching handler behaviour."""

    def _assert_closed_world_read(self, tool_name: str):
        spec = TOOL_CONTRACT[tool_name]
        hints = spec["annotations"]
        for key in HINT_KEYS:
            self.assertIn(key, hints, "{} missing {}".format(tool_name, key))
            self.assertIsInstance(hints[key], bool, "{} {} must be a boolean".format(tool_name, key))
        self.assertIs(hints["readOnlyHint"], True, tool_name)
        self.assertIs(hints["destructiveHint"], False, tool_name)
        self.assertIs(hints["idempotentHint"], True, tool_name)
        self.assertIs(hints["openWorldHint"], False, tool_name)
        self.assertTrue(hints.get("title"))

    def test_list_topics_has_all_four_hints(self):
        self._assert_closed_world_read("list_topics")

    def test_get_evidence_has_all_four_hints(self):
        self._assert_closed_world_read("get_evidence")

    def test_resolve_or_escalate_has_all_four_hints(self):
        self._assert_closed_world_read("resolve_or_escalate")

    def test_server_source_inlines_hints_on_list_topics(self):
        self._assert_source_inlines("list_topics")

    def test_server_source_inlines_hints_on_get_evidence(self):
        self._assert_source_inlines("get_evidence")

    def test_server_source_inlines_hints_on_resolve_or_escalate(self):
        self._assert_source_inlines("resolve_or_escalate")

    def _assert_source_inlines(self, tool_name: str):
        """Each app.tool(name=...) call must pass all four hints as boolean literals."""
        with open(SERVER_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kwargs = {k.arg: k.value for k in node.keywords if k.arg}
            name_node = kwargs.get("name")
            if not (isinstance(name_node, ast.Constant) and name_node.value == tool_name):
                continue
            found = True
            ann = kwargs.get("annotations")
            self.assertIsNotNone(ann, "{} registration has no annotations=".format(tool_name))
            self.assertIsInstance(ann, ast.Call)
            ann_kwargs = {k.arg: k.value for k in ann.keywords if k.arg}
            for key in HINT_KEYS:
                self.assertIn(key, ann_kwargs, "{} annotations missing {}".format(tool_name, key))
                val = ann_kwargs[key]
                self.assertIsInstance(val, ast.Constant, "{} {} is not a literal".format(tool_name, key))
                self.assertIsInstance(val.value, bool, "{} {} is not a boolean literal".format(tool_name, key))
        self.assertTrue(found, "no app.tool(name={!r}) registration in server.py".format(tool_name))


class ListTopicsHandlerTests(unittest.TestCase):
    def test_list_topics_returns_kb_documents(self):
        out = list_topics()
        self.assertIn("kb_sha256_16", out)
        docs = {t["doc"] for t in out["topics"]}
        self.assertIn("password.md", docs)
        self.assertIn("refunds.md", docs)
        self.assertTrue(out["topics"][0]["passages"][0]["passage_id"])

    def test_list_topics_is_idempotent(self):
        self.assertEqual(list_topics(), list_topics())

    def test_list_topics_has_no_verdict(self):
        out = list_topics()
        self.assertNotIn("outcome", out)
        self.assertNotIn("answer", out)


class GetEvidenceHandlerTests(unittest.TestCase):
    def test_get_evidence_returns_passages_with_text(self):
        out = get_evidence("how do I reset my password?")
        self.assertIn("citations", out)
        self.assertTrue(out["citations"])
        top = out["citations"][0]
        self.assertIn("password.md", top["passage_id"])
        self.assertTrue(top["text"])
        self.assertNotIn("outcome", out)
        self.assertNotIn("answer", out)

    def test_get_evidence_is_idempotent(self):
        q = "how do I reset my password?"
        self.assertEqual(get_evidence(q), get_evidence(q))

    def test_get_evidence_rejects_empty_question(self):
        with self.assertRaises(ValueError):
            get_evidence("   ")

    def test_get_evidence_rejects_out_of_range_top_k(self):
        with self.assertRaises(ValueError):
            get_evidence("how do I reset my password?", top_k=0)
        with self.assertRaises(ValueError):
            get_evidence("how do I reset my password?", top_k=99)


class ResolveOrEscalateHandlerTests(unittest.TestCase):
    def test_resolve_or_escalate_resolves_password(self):
        out = resolve_or_escalate("how do I reset my password?")
        self.assertEqual(out["outcome"], "resolved")
        self.assertTrue(out["answer"])
        self.assertTrue(out["citations"])
        self.assertIsNone(out["handoff_note"])
        self.assertNotIn("asked_at", out["provenance"])

    def test_resolve_or_escalate_escalates_out_of_scope(self):
        out = resolve_or_escalate(
            "do you integrate with Salesforce and migrate my Zendesk tickets?"
        )
        self.assertEqual(out["outcome"], "escalated")
        self.assertIsNone(out["answer"])
        self.assertTrue(out["handoff_note"])

    def test_resolve_or_escalate_is_idempotent(self):
        q = "which SSO providers are supported?"
        self.assertEqual(resolve_or_escalate(q), resolve_or_escalate(q))

    def test_resolve_or_escalate_rejects_empty_question(self):
        with self.assertRaises(ValueError):
            resolve_or_escalate("")


class ContractCliTests(unittest.TestCase):
    def test_contract_cli_lists_every_tool_and_hint(self):
        proc = subprocess.run(
            [sys.executable, os.path.join("mcp_server", "server.py"), "--contract"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(proc.stdout)
        for name in ("list_topics", "get_evidence", "resolve_or_escalate"):
            self.assertIn(name, data)
            hints = data[name]["annotations"]
            for key in HINT_KEYS:
                self.assertIsInstance(hints[key], bool)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed")
class FastMCPRegistrationTests(unittest.TestCase):
    """Wire-level tools/list: names, descriptions, and all four boolean hints."""

    @classmethod
    def setUpClass(cls):
        from mcp_server.server import build_app
        cls.app = build_app()
        cls.tools = {t.name: t for t in asyncio.run(cls.app.list_tools())}

    def test_fastmcp_lists_list_topics(self):
        self._assert_tool("list_topics")

    def test_fastmcp_lists_get_evidence(self):
        self._assert_tool("get_evidence")

    def test_fastmcp_lists_resolve_or_escalate(self):
        self._assert_tool("resolve_or_escalate")

    def test_fastmcp_tool_set_matches_contract(self):
        self.assertEqual(set(self.tools), set(TOOL_NAMES))

    def _assert_tool(self, name: str):
        self.assertIn(name, self.tools)
        tool = self.tools[name]
        self.assertTrue(tool.description)
        ann = tool.annotations
        self.assertIsNotNone(ann, "{} missing annotations on tools/list".format(name))
        self.assertIs(ann.readOnlyHint, True, name)
        self.assertIs(ann.destructiveHint, False, name)
        self.assertIs(ann.idempotentHint, True, name)
        self.assertIs(ann.openWorldHint, False, name)

    def test_fastmcp_call_resolve_or_escalate(self):
        result = asyncio.run(self.app.call_tool(
            "resolve_or_escalate",
            {"question": "how do I reset my password?"},
        ))
        self.assertTrue(result)

    def test_fastmcp_call_get_evidence(self):
        result = asyncio.run(self.app.call_tool(
            "get_evidence",
            {"question": "how do I reset my password?"},
        ))
        self.assertTrue(result)

    def test_fastmcp_call_list_topics(self):
        result = asyncio.run(self.app.call_tool("list_topics", {}))
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
