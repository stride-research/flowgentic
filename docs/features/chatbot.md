# Chatbot

Build a production-ready chatbot with minimal glue.

- Works with LangChain providers (OpenAI, Ollama, community)
- Memory and tools integration
- Swappable runtimes for dev vs HPC

## Example

See `examples/langgraph-integration/chatbot/main.py` for a minimal build.

## Architecture

```mermaid
sequenceDiagram
  participant U as User
  participant A as Agent
  participant L as LLM Provider
  U->>A: Message
  A->>L: Prompt + Tools
  L-->>A: Completion
  A-->>U: Response
```
