"""Knowledge base package.

The support KB is a set of markdown files (one topic per file). Making `kb` an
import package lets the markdown ship inside the built wheel and be located via
`importlib.resources` when the agent is installed with pip/uvx, so the MCP server
finds its KB with no repo checkout. The markdown files are the KB; this module has
no runtime logic. See core.retriever.default_kb_dir().
"""
