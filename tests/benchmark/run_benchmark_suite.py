#!/usr/bin/env python3
"""
Benchmark Suite Runner

This script runs the FlowGentic benchmark suite with multiple configurations.
Each configuration gets a unique run_name so results are not overwritten.

Usage:
    python -m tests.benchmark.run_benchmark_suite

Configuration:
    - N (total tool invocations) = A * k
    - A = number of agents
    - k = tools per agent (fixed at 64)
    - p = backend slots {1, 2, 4, 8, ..., 512}

For strong scaling, N is fixed while p varies.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import yaml


# ============================================================================
# BENCHMARK CONFIGURATIONS
# ============================================================================

# Tools per agent (fixed)
TOOLS_PER_AGENT = 64

# Tool execution duration (seconds) - 0 for noop, >0 for synthetic work
TOOL_EXECUTION_DURATION = 3

# Backend slots: max exponent for 2^x (9 means up to 2^9 = 512 slots)
# This generates p in {1, 2, 4, 8, 16, 32, 64, 128, 256, 512}
MAX_BACKEND_SLOTS_EXPONENT = 9

# REPETITIONS 
N_OF_ITERATIONS = 1

# Workload sizes (N = total tool invocations)
# N = 2^10 = 1024, 2^14 = 16384, 2^17 = 131072
WORKLOAD_SIZES = [
	2**14,  # 16384 - uncomment for full suite
	2**13,  # 16384 - uncomment for full suite
	2**12,  # 1024 - start with this
]

# Number of repetitions per configuration (for variance)
# NOTE: Multi-run is commented out for now - uncomment when needed
# REPETITIONS = 1


# ============================================================================
# SCRIPT LOGIC
# ============================================================================

CONFIG_PATH = Path("tests/benchmark/config.yml")
WORKLOAD_ID = "langgraph_asyncflow"


def calculate_agents_for_workload(n_total_tools: int, tools_per_agent: int) -> int:
	"""Calculate number of agents needed for a given total workload N."""
	return n_total_tools // tools_per_agent


def generate_config(
	run_name: str,
	n_agents: int,
	n_tools_per_agent: int,
	n_backend_slots_exp: int,
	tool_duration: int,
) -> dict:
	"""Generate a config.yml dictionary."""
	return {
		"run_name": run_name,
		"run_description": f"Strong scaling: {n_agents} agents x {n_tools_per_agent} tools = {n_agents * n_tools_per_agent} total invocations",
		"environment": {
			"n_of_agents": n_agents,
			"n_of_tool_calls_per_agent": n_tools_per_agent,
			"n_of_backend_slots": n_backend_slots_exp,
			"tool_execution_duration_time": tool_duration,
		},
		"workload_id": WORKLOAD_ID,
	}


def write_config(config: dict) -> None:
	"""Write config to config.yml."""
	with open(CONFIG_PATH, "w") as f:
		yaml.dump(config, f, default_flow_style=False)


def run_benchmark() -> int:
	"""Run the benchmark and return exit code."""
	cmd = [sys.executable, "-m", "tests.benchmark.data_generation.run_experiments"]
	result = subprocess.run(cmd, cwd=Path.cwd())
	return result.returncode


def main():
	"""Main entry point for the benchmark suite."""
	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

	print("=" * 60)
	print("FlowGentic Benchmark Suite")
	print("=" * 60)
	print(f"Tools per agent (k): {TOOLS_PER_AGENT}")
	print(f"Tool duration: {TOOL_EXECUTION_DURATION}s")
	print(
		f"Max backend slots: 2^{MAX_BACKEND_SLOTS_EXPONENT} = {2**MAX_BACKEND_SLOTS_EXPONENT}"
	)
	print(f"Workload sizes (N): {WORKLOAD_SIZES}")
	print("=" * 60)

	total_runs = len(WORKLOAD_SIZES)
	completed = 0
	failed = []
	for run_version in range(1, N_OF_ITERATIONS+1):
		for n_total in WORKLOAD_SIZES:
			n_agents = calculate_agents_for_workload(n_total, TOOLS_PER_AGENT)

			if n_agents == 2:
				return 

			# Generate unique run name
			run_name = f"strong-N{n_total}-k{TOOLS_PER_AGENT}-version{run_version}-{timestamp}"

			print(f"\n{'=' * 60}")
			print(f"RUN {completed + 1}/{total_runs*N_OF_ITERATIONS}")
			print(f"{'=' * 60}")
			print(f"  Run name: {run_name}")
			print(f"  Total tool invocations (N): {n_total}")
			print(f"  Agents (A): {n_agents}")
			print(f"  Tools per agent (k): {TOOLS_PER_AGENT}")
			print(f"  Backend slots (p): 1 to {2**MAX_BACKEND_SLOTS_EXPONENT}")
			print(f"  Results will be in: tests/benchmark/results/{run_name}/")
			print("-" * 60)

			# Generate and write config
			config = generate_config(
				run_name=run_name,
				n_agents=n_agents,
				n_tools_per_agent=TOOLS_PER_AGENT,
				n_backend_slots_exp=MAX_BACKEND_SLOTS_EXPONENT,
				tool_duration=TOOL_EXECUTION_DURATION,
			)
			write_config(config)

			print(f"Config written. Starting benchmark...")
			print("-" * 60)

			# Run benchmark
			exit_code = run_benchmark()

			if exit_code == 0:
				print(f"\n✓ Run completed successfully!")
				print(f"  Results available at: tests/benchmark/results/{run_name}/")
				completed += 1
			else:
				print(f"\n✗ Run failed with exit code {exit_code}")
				failed.append(run_name)

	# Summary
	print("\n" + "=" * 60)
	print("BENCHMARK SUITE COMPLETE")
	print("=" * 60)
	print(f"Completed: {completed}/{total_runs}")
	if failed:
		print(f"Failed runs: {failed}")
	print("=" * 60)

	return 0 if not failed else 1


if __name__ == "__main__":
	sys.exit(main())
