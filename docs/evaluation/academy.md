# Academy substitution check

This check is the evidence for option D of decision E1, for 0.5.0: keep
`flowgentic.academy`, or replace it with a recipe. As the
[substitution experiment](substitution.md) did for the LangGraph adapter, it
runs one workflow twice, once through `AcademyIntegration` and once with direct
Academy calls inside AsyncFlow tasks, and records what the module demonstrably
supplies. A capability counts for the module only when the module supplies it
and the direct version does not.

## Setup

| | Flowgentic version | Direct version |
| --- | --- | --- |
| Workflow | Example 1's concurrent counters: ten `Counter` agents; for each, one function task increments the counter by its own amount and a second reads it back | The same |
| Integration | `AcademyIntegration()`; `create_agent_task` per action, awaited inside the function tasks | `Manager.from_exchange_factory(LocalExchangeFactory())`; `manager.launch` for each agent; a `Handle` bound to the manager's exchange client; a helper that submits each call to the manager's event loop |
| Code | `flowgentic_counters` | `direct_counters` |

Both functions are in `tests/integration/test_academy_substitution.py`, which
CI runs with the rest of `tests/`; locally,
`uv run --extra dev pytest -q tests/integration/test_academy_substitution.py`.
Versions: academy-py 1.0.0, radical-asyncflow 0.6.0, Python 3.10, 3.11 and
3.12, on Academy's local exchange and AsyncFlow's `LocalExecutionBackend` over
a `ThreadPoolExecutor`. Both versions run their agents on the manager's event
loop, Academy's default.

## Results

| # | Criterion | Flowgentic version | Direct version | Verdict | Evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | Results | `Counter 1: 2` to `Counter 10: 20`; 20 AsyncFlow function tasks | Identical | Both supply it | `test_both_versions_return_the_same_results`, and `test_both_versions_submit_the_same_asyncflow_tasks`: each version registers 10 `increment_counter` and 10 `get_counter_result` tasks, and nothing else. |
| 2 | Lifecycle: launch once, shut down | Launches each agent on the first call for its `agent_id`, waits for its ping, and shuts every agent down when the context exits | Launches the ten agents before any task runs; the manager's context shuts them down | Both supply it. The module's launch on first use, once under concurrent first calls, matters only to an application that does not know its agents before its tasks run. | `test_both_versions_launch_and_shut_down_each_agent_once`: 10 agents start and 10 stop in each version. `tests/unit/test_academy.py::test_concurrent_first_calls_launch_one_agent`: ten concurrent first calls for one `agent_id` launch one agent, from the manager's loop and from function tasks. |
| 3 | Calls from worker threads | Every launch and action runs on the manager's event loop, through handles bound to its exchange client | The same, written in the application: a 3-line helper around `asyncio.run_coroutine_threadsafe` and a bound `Handle` per agent | Both supply it. The module hides two traps that the direct version must know. | A function task runs in a worker thread under its own event loop. `test_handle_from_launch_fails_in_a_function_task`: the handle that `manager.launch` returns raises `ExchangeClientNotFoundError` there, because it finds its exchange client through Academy's `exchange_context` variable, which the worker thread does not carry. `test_bound_handle_on_the_worker_loop_waits_for_that_loop_to_wake`: a handle bound to the exchange client but called on the worker's loop gets its answer only when that loop next wakes, here after the 1 s timer of `wait_for`. Without a timer, the call hung: it had not returned after 20 s, when the run was stopped (observed in a probe run, not tested). |
| 4 | Where the agent's work runs | On the manager's event loop, in the main thread. The function task only waits for the result. | The same | Both lack it. Neither version runs an agent's work on an AsyncFlow backend, and a process-pool or remote backend cannot reach the manager. | `test_actions_run_on_the_managers_loop_not_in_the_task`: in both versions every action runs in the main thread, not in the backend's worker threads. `test_actions_need_no_asyncflow_task`: the same workflow, with the actions awaited directly in the manager's context, gives the same results with no AsyncFlow task, no bound handle and no helper. |
| 5 | Application code | 21 code lines (27 lines) | 27 code lines (39 lines), including the helper and the handle binding | Both supply it: the direct version has 6 more code lines. The module adds 186 lines to maintain, and an optional dependency. | `test_application_code_lines` counts each function's lines that hold code: no blank lines, comments or docstrings. The direct version also needs to know about Academy's `exchange_context` and the cross-loop rule above; the module's guide and these tests document both. |

## Summary for E1

In this workflow `flowgentic.academy` adds no capability over direct Academy
calls: results, AsyncFlow tasks and the agents' lifecycle are the same, with 6
fewer lines of application code. What the module supplies is knowledge: how a
call from a worker thread reaches Academy. Without it, a call fails
(`ExchangeClientNotFoundError`) or hangs. Its one behaviour the direct version
does not need here is launching an agent on first use.

Neither version places an agent's work on an AsyncFlow backend. An AsyncFlow
task around an Academy action only waits for an agent that runs on the
manager's event loop, and the same actions give the same results without any
task. Wrapping the action in a task gains AsyncFlow's task records and its
dependency handling between task futures, not placement.

The verdict supports option D in 0.5.0: a recipe page that carries the direct
version's helper and handle binding, with a tested example, gives the same
results. The module stays in 0.4.0, repaired, under E1 = A.

## Not tested

- Academy's Redis, HTTP and Globus exchanges, and agents launched on
  executors other than the manager's event loop.
- AsyncFlow's process-pool and remote backends, which cannot reach the
  manager in either version.
- Agents whose actions block the event loop. Academy runs such work through
  `Agent.agent_run_sync`; neither version exercises it.
