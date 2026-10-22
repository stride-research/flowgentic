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

- [x] 1. **Pages.** Write and audit the pages in the table.
    - From W3 R8: `docs/index.md`, `docs/design_patterns/chatbot.md`, `sequential.md` and `supervisor.md` show `ConcurrentExecutionBackend`, which AsyncFlow 0.3.0 and later do not export; the examples use `LocalExecutionBackend` since R8. `sequential.md` and `supervisor.md` also show `create_react_agent`, which the examples replaced with `langchain.agents.create_agent`.

    Result (2026-10-22, PR C): every page in the table written or audited. Python code blocks are `pymdownx.snippets` includes of marked sections in tested files; shell blocks are commands that were run, with their output. Home rewritten; `sequential.md` and `supervisor.md` include their examples (`LocalExecutionBackend`, `create_agent`); `chatbot.md` removed (interactive example, no test) and `hierachical.md` removed (no example); `features/overview.md` and `miscellaneous.md` merged into the LangGraph guide. The design-pattern examples need OpenRouter: both linked ones ran end to end offline with a scripted chat model; no live run (Matteo's call at G2). PR C also fixes two examples the pages show: the mutable-graph example's conditional routing (found by running it; red-then-green test) and the supervisor toy's `agent_B` key (found by the page check). `/code-review` found 10 issues and a fresh page-vs-code check 15; all are fixed in PR C's docs, and the three memory-strategy bugs they exposed in `src/` are documented as known limitations, not fixed. `grep -rn 'ConcurrentExecutionBackend\|create_react_agent' README.md docs` matches only the R5 report's account of R8.
- [x] 2. **Removals.**
    - Remove `docs/features/mcp.md` and the `AGENT_TOOL_AS_MCP` node in `docs/architecture.md`. That flow type exists only on `feature/mcp`, which W2 archives; the status page names `archive/feature/mcp`.
    - Remove `docs/phased_rollout.md`, which shows the retired framework × backend matrix.

    Result: both removed; the status page names `archive/feature/mcp` and notes the retired matrix.
- [x] 3. **Strict-build fixes**, unless D9 moved them to R3: the missing annotations at `src/flowgentic/academy.py:146`, `src/flowgentic/langGraph/mutable_graph.py:307` and `src/flowgentic/utils/llm_providers.py:25`, and the 4 broken links in `docs/design_patterns/chatbot.md` and `docs/features/memory.md`.

    Result: D9 moved them to PR B (R3). Confirmed on `main` at `9949f5e7`: `uv run --extra dev mkdocs build --strict` exits 0 with no warnings. PR C also turns on MkDocs link and anchor validation, so broken links now fail the strict build.
- [x] 4. **`mkdocs.yml`.** Set `site_url` to `https://stride-research.github.io/flowgentic/` and `repo_name` to `stride-research/flowgentic`.

    Result: both set.
- [x] 5. **Repository README.** Rewrite `README.md` at the repo root to match Home: status, the one-row support matrix, install, the citation updated to 0.3.0 and 2026 (authors per D4), and a PyPI badge only if D2 says yes.

    Result: rewritten; no PyPI badge (D2 open). `CITATION.cff` added with D4's authors (`uvx cffconvert --validate` passes); the README cites 0.3.0 and 2026 with the same authors.

Done-checks for PR C:

- `uv run --extra dev mkdocs build --strict` finishes with 0 warnings.
- `grep -rniE 'crewai|ag2|pegasus|parsl|openai agents' README.md docs` matches only the status page's note on the retired matrix.
- After PR C merges, the deployed site shows the new navigation and the 0.3.0 statement.
- README and Home give the same status and install commands.

Results (2026-10-22, PR C's body holds the outputs): the strict build exits 0 with no warnings; the grep matches only `docs/status.md`'s note on the retired matrix; a script that compares the README's and Home's Status and Install sections, link targets dropped, prints "identical" for both. The deployed-site check waits for the merge (G2); locally the built site has the new navigation and Home's "This documentation describes Flowgentic 0.3.0".
