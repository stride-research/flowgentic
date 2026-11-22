"""One agent process that streams its telemetry over OTLP with
``flowgentic.otlp.configure``, for the tests of the ``otlp`` extra.

    python otlp_process.py [--endpoint URL] [--agents N] [--failures N] ...

Its agents are ``otel_process.py``'s: on the loop, over a fleet of one
scripted replica whose server is down for its first ``--failures`` calls.
Each reply counts 100 prompt tokens and 10 completion tokens. Without
``--endpoint``, the exporters read the endpoint from the environment, as
OpenTelemetry's ``OTEL_EXPORTER_OTLP_ENDPOINT``. ``--rhapsody N`` then runs
N function tasks on a RHAPSODY session whose telemetry goes to the same
collector. Prints the fleet's counts as JSON.
"""

import argparse
import asyncio
import json
from pathlib import Path

from otel_process import run  # beside this script


def answer(x: int) -> int:
	return x + 1


async def rhapsody_tasks(telemetry, tasks: int) -> int:
	"""Run ``tasks`` function tasks on a RHAPSODY session whose telemetry
	streams with the process's; the tasks that completed."""
	from concurrent.futures import ThreadPoolExecutor

	from rhapsody import ComputeTask, Session
	from rhapsody.backends import ConcurrentExecutionBackend

	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(2))
	session = Session(backends=[backend])
	await session.start_telemetry(**telemetry.rhapsody_options())
	futures = await session.submit_tasks(
		[ComputeTask(function=answer, args=(i,)) for i in range(tasks)]
	)
	done = await asyncio.gather(*futures)
	await asyncio.sleep(0.12)  # an export with every task done
	await session.close()
	return len(done)


def main() -> None:
	p = argparse.ArgumentParser()
	p.add_argument("--endpoint")
	p.add_argument("--agents", type=int, default=50)
	p.add_argument("--failures", type=int, default=0)
	p.add_argument("--sample", type=float)
	p.add_argument("--exemplars", action="store_true")
	p.add_argument("--no-spans", action="store_true")
	p.add_argument("--files", type=Path)
	p.add_argument("--run")
	p.add_argument("--service")
	p.add_argument("--instance")
	p.add_argument("--interval", type=float, default=0.05)
	p.add_argument("--timeout", type=float)
	p.add_argument("--rhapsody", type=int, default=0)
	a = p.parse_args()

	from flowgentic.otlp import configure

	given = {"sample": a.sample, "timeout_s": a.timeout}
	telemetry = configure(
		a.endpoint,
		run=a.run,
		service=a.service,
		instance=a.instance,
		exemplars=a.exemplars,
		interval_s=a.interval,
		spans=not a.no_spans,
		files=a.files,
		**{k: v for k, v in given.items() if v is not None},
	)
	try:
		out = asyncio.run(run("agent-", a.agents, a.failures)) if a.agents else {}
		if a.rhapsody:
			out["rhapsody_tasks"] = asyncio.run(rhapsody_tasks(telemetry, a.rhapsody))
	finally:
		telemetry.shutdown()
	print(json.dumps(out))


if __name__ == "__main__":
	main()
