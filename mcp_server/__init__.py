"""mcp_server — a thin MCP wrapper around the Grounded Support Agent's decision
engine.

Mirrors the itsoc-mcp design: the MCP layer is a thin CLIENT of core/ and
computes no verdict of its own, so it can sit inside a multi-agent system as a
component that will never fabricate a resolution. Every tool response carries the
same provenance block the CLI produces. server.py is the only module that imports
the optional `mcp` SDK; the tool logic here runs and is fully testable with no
SDK installed."""
