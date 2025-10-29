# MCP (Model Context Protocol) Integration Examples

FlowGentic's integration with MCP servers using official `langchain-mcp-adapters` library and AsyncFlow execution.

## Examples

### `toy/toy-mcp-demo.py` - Getting Started

Learn MCP basics with a demo server (echo, add, multiply tools).
```bash
python toy/toy-mcp-demo.py
```

Shows: Tool discovery, direct invocation, basic MCP usage

---

### `sequential/sales_analytics/main.py` - Production Sequential Pipeline

Real-world sales analytics workflow with MCP database integration.
```bash
make examples-mcp-sales-analytics
```

**Workflow:**
```
Validation → Data Extraction (MCP SQLite) → Analytics Context Prep 
→ Analytics Agent → Report Context Prep → Report Generation → Finalize
```

Shows: MCP SQLite specialist, multi-agent coordination, context prep between stages, full telemetry

---

## Quick Start

### Prerequisites
```bash
echo "OPENROUTER_API_KEY=your_key" > .env
pip install langchain-mcp-adapters
```

### Basic Usage
```python
@integration.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
    mcp_servers={
        "sqlite": {
            "command": "uvx",
            "args": ["mcp-sqlite", str(db_path)],
            "transport": "stdio"
        }
    }
)
async def discover_tools():
    pass

tools = await discover_tools()
```

## Configuration Examples

**Demo Server:**
```python
mcp_servers={"demo": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"], "transport": "stdio"}}
```

**SQLite Server:**
```python
mcp_servers={"sqlite": {"command": "uvx", "args": ["mcp-sqlite", str(db_path)], "transport": "stdio"}}
```

**Multiple Servers:**
```python
mcp_servers={"db": {...}, "weather": {...}}
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Server not starting | Check `node`/`npx` or `uv` installed |
| No tools found | Verify server command/args, use absolute paths |
| Agent ignores tools | Improve tool descriptions, use better LLM |

**Always use absolute paths:**
```python
db_path = str(Path(__file__).parent / "data.db").resolve()
```

## Resources

- **Find MCP Servers:** [PulseMCP](https://pulsemcp.com/servers)
- **MCP Docs:** [modelcontextprotocol.io](https://modelcontextprotocol.io)
- **LangChain Guide:** [python.langchain.com/docs/integrations/tools/mcp](https://python.langchain.com/docs/integrations/tools/mcp/)

---

**Next Steps:** Run toy → Run sales analytics → Build your own!