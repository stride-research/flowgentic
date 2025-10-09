---
title: Flowgentic
---

# Flowgentic

Build and run modern agentic workflows on HPC with minimal overhead. Flowgentic bridges HPC workflow engines and agent orchestration frameworks, so you can prototype locally and scale to clusters without rewrites.

### What can I use this for?

- **HPC execution of agent workflows**: Run multiagent graphs (e.g., langraph) on HPC via multiple HPC workflow engines (e.g., Radical Asyncflow)
- **Concurrent tool and agent blocks**: Offload parallelizable work to HPC backends.
- **Production-oriented patterns**: Start from examples that implement sequential patterns with typed state, tool registries, and error handling.


## Quickstart
### 1) Installation
```bash
# 1) pyproject.toml
dependencies = [
    ...,
    "flowgentic @ git+https://github.com/stride-research/flowgentic/flowgentic.git@main",
    ...

]
# 2) pip3 
pip3 install "git+https://github.com/stride-research/flowgentic.git@main#egg=flowgentic"

# 3) cloning this repo
pip install '.' # no dev dependencies
pip install '.[dev]' # dev dependencies

```
Then install graphviz pluggins:
```bash
# For MacOS
brew install graphviz
sudo dot -c

```
### 2) Environmental variables
- **OPEN_ROUTER_API_KEY**: required if you use the OpenRouter-backed LLM provider.
- `.env` files are supported via `python-dotenv` if you call `load_dotenv()`.

```bash
export OPEN_ROUTER_API_KEY=sk-or-...
```

### 3) `config.yml`
- You can modify the `config.yml` at the top level of this repo in order to adjust different settings affecting the package (e.g., folders names for executions outputs)



## Architecture overview

```mermaid
flowchart LR
  User --> AgentLayer
  subgraph Flowgentic
    AgentLayer[Agents]
    Memory[Memory]
    Fault[Fault Tolerance]
    Utils[Utils]
  end
  AgentLayer -->|LangGraph| LG[LangGraph Runtime]
  AgentLayer -->|AsyncFlow| AF[Radical AsyncFlow]
```

Explore the sections on the left for patterns, features, API reference, and examples.


