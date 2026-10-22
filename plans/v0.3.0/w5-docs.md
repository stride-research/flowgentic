# W5: Documentation (PR C)

The site documents exactly what v0.3.0 ships. Every page describes code present on `main`, and every code block is either included from a tested file or a command that was run. W5 lands as PR C from branch `docs/v0.3.0`, cut from `main` after PR B merges and opened after D1, because the status pages state D1's outcome.

Gates: G1 (push the branch, open or update PR C), G2 (merge PR C). Decisions: D1 and D4 before PR C.

The site builds from `docs/` with MkDocs and deploys from `main` through `.github/workflows/docs-deployment.yml` to https://stride-research.github.io/flowgentic/.

## Site structure

| Page | Source | Content |
| --- | --- | --- |
| Home | `docs/index.md` (rewrite) | What Flowgentic is, the D1 status, where it fits in RADICAL (the five compositions in the roadmap), install, and "This documentation describes Flowgentic 0.3.0". |
| Getting started | `docs/getting-started.md` (new) | Install with `uv sync --locked` or the release wheel, then run both offline demos and read their outputs. |
| Augment an AsyncFlow application | `docs/guides/augment-asyncflow-app.md` (new) | The `bind_agent` recipe from the augmented demo, plus a note that under ADR the decision belongs in `@decide`. |
| LangGraph on AsyncFlow with Flowgentic | `docs/guides/langgraph-on-asyncflow.md` (new) | `LangraphIntegration` usage, merging today's features overview and services pages. |
| LangGraph on AsyncFlow directly | `docs/guides/langgraph-direct.md` (new) | The direct recipe from R5. |
| Features | `docs/features/*` (audit) | Memory, fault tolerance, mutable graph, telemetry and services stay, each checked against the code. Fault tolerance states the defaults (3 attempts, 30 s each) and that they suit short tool calls, not long scientific tasks. |
| Design patterns | `docs/design_patterns/*` (audit) | Kept only where the linked example runs; broken links fixed. |
| Substitution experiment | `docs/evaluation/substitution.md` | The R5 report. |
| Project status | `docs/status.md` (new) | Maintenance status, the supported matrix (one row), the archived branches with their tags and how to restore one (w2-archive.md), and the known limitations (README.md). |
| Changelog | `docs/changelog.md` (new) | Includes `CHANGELOG.md` through `pymdownx.snippets`. |
| API reference | generated | Unchanged generator. |

## Steps

- [ ] 1. **Pages.** Write and audit the pages in the table.
- [ ] 2. **Removals.**
    - Remove `docs/features/mcp.md` and the `AGENT_TOOL_AS_MCP` node in `docs/architecture.md`. That flow type exists only on `feature/mcp`, which W2 archives; the status page names `archive/feature/mcp`.
    - Remove `docs/phased_rollout.md`, which shows the retired framework × backend matrix.
- [ ] 3. **Strict-build fixes**, unless D9 moved them to R3: the missing annotations at `src/flowgentic/academy.py:146`, `src/flowgentic/langGraph/mutable_graph.py:307` and `src/flowgentic/utils/llm_providers.py:25`, and the 4 broken links in `docs/design_patterns/chatbot.md` and `docs/features/memory.md`.
- [ ] 4. **`mkdocs.yml`.** Set `site_url` to `https://stride-research.github.io/flowgentic/` and `repo_name` to `stride-research/flowgentic`.
- [ ] 5. **Repository README.** Rewrite `README.md` at the repo root to match Home: status, the one-row support matrix, install, the citation updated to 0.3.0 and 2026 (authors per D4), and a PyPI badge only if D2 says yes.

Done-checks for PR C:

- `uv run --extra dev mkdocs build --strict` finishes with 0 warnings.
- `grep -rniE 'crewai|ag2|pegasus|parsl|openai agents' README.md docs` matches only the status page's note on the retired matrix.
- After PR C merges, the deployed site shows the new navigation and the 0.3.0 statement.
- README and Home give the same status and install commands.
