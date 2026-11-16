"""Tools for agents at scale.

``flowgentic.tools.service`` is the tool service: one process that calls MCP
tools for many agent processes, over queues the caller provides.
``flowgentic.tools.mcp`` is its MCP client, a supervised pool of connections.
The agent processes' side of the service needs no extra; the service's MCP
client needs the ``mcp`` extra.
"""
