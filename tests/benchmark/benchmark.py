"""
FlowGentic Strong and Weak Scaling Benchmark
Measures FlowGentic overhead and scaling efficiency per Matteo Turilli's specifications.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, StateGraph
from radical.asyncflow import ConcurrentExecutionBackend
from pydantic import BaseModel

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration

logging.basicConfig(
	level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class TaskState(BaseModel):
	task_id: int


class BenchmarkConfig(BaseModel):
	"""Configuration for benchmark runs"""

	name: str
	p_values: List[int]  # Number of backend slots/workers
	n_fg_fixed: int = 1000  # Fixed number of tasks for strong scaling
	k_per_worker: int = 100  # Tasks per worker for weak scaling
	w_work_seconds: float = 0.0  # Work duration (0 = noop)


class BenchmarkResult(BaseModel):
	"""Results from a single benchmark run"""

	p: int  # Number of workers
	n_fg: int  # Number of FlowGentic calls
	w_work: float  # Work duration
	t_fg: float  # FlowGentic makespan in seconds

	def speedup(self, t_baseline: float) -> float:
		"""Calculate speedup: S(p) = T(1) / T(p)"""
		return t_baseline / self.t_fg if self.t_fg > 0 else 0

	def efficiency(self, t_baseline: float) -> float:
		"""Calculate efficiency: E(p) = T(1) / (p * T(p))"""
		return t_baseline / (self.p * self.t_fg) if self.t_fg > 0 else 0


class FlowGenticBenchmark:
	"""Benchmark harness for FlowGentic scaling tests"""

	def __init__(self, output_dir: Path = Path("tests/benchmark/results")):
		self.output_dir = output_dir
		# Structured output:
		# tests/benchmark/results/
		#   data/benchmark_results.json
		#   plots/{noop,work}/{strong,weak}/*.png
		self.data_dir = self.output_dir / "data"
		self.plots_dir = self.output_dir / "plots"
		self.output_dir.mkdir(parents=True, exist_ok=True)
		self.data_dir.mkdir(parents=True, exist_ok=True)
		self.plots_dir.mkdir(parents=True, exist_ok=True)
		self.results: Dict[str, List[BenchmarkResult]] = {}

	async def run_workload(self, agents_manager, n_fg: int, w_work: float) -> float:
		"""
		Execute n_fg FlowGentic calls with w_work seconds of work each.
		Returns makespan T_FG(p, n_fg, w_work).
		"""

		def create_benchmark_graph(agents_manager, w_work):
			@agents_manager.execution_wrappers.asyncflow(
				flow_type=AsyncFlowType.FUNCTION_TASK
			)
			async def work_node(state: TaskState) -> dict:
				await asyncio.sleep(w_work)
				return {"result": f"task_{state.task_id}_complete"}

			graph = StateGraph(TaskState)
			graph.add_node("work_node", work_node)
			graph.set_entry_point("work_node")
			graph.add_edge("work_node", END)
			return graph.compile()

		# Initial time
		t_start = time.perf_counter()

		# Submit all tasks
		app = create_benchmark_graph(agents_manager, w_work)
		tasks = [app.ainvoke(TaskState(task_id=i)) for i in range(n_fg)]
		await asyncio.gather(*tasks)

		# End timing: last FlowGentic call returning
		t_end = time.perf_counter()

		makespan = t_end - t_start
		return makespan

	async def run_strong_scaling(self, config: BenchmarkConfig):
		"""
		Strong scaling: fixed N_FG tasks, varying p workers.
		Tests T_FG(p, N_FG, W_work) for different p values.
		"""
		logger.info(f"=== STRONG SCALING: {config.name} ===")
		logger.info(
			f"Fixed tasks: {config.n_fg_fixed}, W_work: {config.w_work_seconds}s"
		)

		results = []

		for p in config.p_values:
			logger.info(f"\n--- Testing p={p} workers ---")

			# Create backend with p workers
			backend = await ConcurrentExecutionBackend(
				ThreadPoolExecutor(max_workers=p)
			)

			async with LangraphIntegration(backend=backend) as agents_manager:
				# Run workload
				t_fg = await self.run_workload(
					agents_manager, config.n_fg_fixed, config.w_work_seconds
				)

				result = BenchmarkResult(
					p=p, n_fg=config.n_fg_fixed, w_work=config.w_work_seconds, t_fg=t_fg
				)

				results.append(result)

				logger.info(f"T_FG({p}) = {t_fg:.3f}s")
				if len(results) > 1:
					speedup = result.speedup(results[0].t_fg)
					efficiency = result.efficiency(results[0].t_fg)
					logger.info(
						f"Speedup: {speedup:.2f}x, Efficiency: {efficiency:.3f}"
					)

		self.results[f"strong_{config.name}"] = results
		return results

	async def run_weak_scaling(self, config: BenchmarkConfig):
		"""
		Weak scaling: k*p tasks for p workers (constant tasks per worker).
		Tests T_FG(p, k*p, W_work) for different p values.
		"""
		logger.info(f"=== WEAK SCALING: {config.name} ===")
		logger.info(
			f"Tasks per worker: {config.k_per_worker}, W_work: {config.w_work_seconds}s"
		)

		results = []

		for p in config.p_values:
			n_fg = config.k_per_worker * p  # Scale tasks with workers
			logger.info(f"\n--- Testing p={p} workers, n_fg={n_fg} tasks ---")

			# Create backend with p workers
			backend = await ConcurrentExecutionBackend(
				ThreadPoolExecutor(max_workers=p)
			)

			async with LangraphIntegration(backend=backend) as agents_manager:
				# Run workload
				t_fg = await self.run_workload(
					agents_manager, n_fg, config.w_work_seconds
				)

				result = BenchmarkResult(
					p=p, n_fg=n_fg, w_work=config.w_work_seconds, t_fg=t_fg
				)

				results.append(result)

				logger.info(f"T_FG({p}, {n_fg}) = {t_fg:.3f}s")
				if len(results) > 1:
					efficiency = result.efficiency(results[0].t_fg)
					logger.info(f"Weak Efficiency: {efficiency:.3f}")

		self.results[f"weak_{config.name}"] = results
		return results

	def save_results(self):
		"""Save raw results to JSON"""
		output_file = self.data_dir / "benchmark_results.json"

		data = {
			name: [r.model_dump() for r in results]
			for name, results in self.results.items()
		}

		with open(output_file, "w") as f:
			json.dump(data, f, indent=2)

		logger.info(f"\n✓ Results saved to {output_file}")

	def generate_plots(self):
		"""Generate scaling plots"""
		try:
			import matplotlib.pyplot as plt
			import numpy as np
		except ImportError:
			logger.warning("matplotlib not available, skipping plots")
			return

		# Strong scaling plots
		for exp_name in ["strong_noop", "strong_work"]:
			if exp_name not in self.results:
				continue

			# exp_name format: "{strong|weak}_{noop|work}"
			exp_type, workload = exp_name.split("_", maxsplit=1)
			plot_dir = self.plots_dir / workload / exp_type
			plot_dir.mkdir(parents=True, exist_ok=True)

			results = self.results[exp_name]
			p_vals = [r.p for r in results]
			t_vals = [r.t_fg for r in results]
			t_baseline = results[0].t_fg

			# Plot 1: Makespan vs p
			fig, ax = plt.subplots(figsize=(10, 6))
			ax.plot(p_vals, t_vals, "o-", label="Measured", linewidth=2)

			# Ideal line: T(p) = T(1)/p
			ideal = [t_baseline / p for p in p_vals]
			ax.plot(p_vals, ideal, "--", label="Ideal", alpha=0.7)

			ax.set_xlabel("Number of Workers (p)")
			ax.set_ylabel("Makespan T_FG(p) [seconds]")
			ax.set_title(f"Strong Scaling Makespan - {exp_name}")
			ax.legend()
			ax.grid(True, alpha=0.3)
			ax.set_xscale("log")
			ax.set_yscale("log")

			plt.savefig(plot_dir / "makespan.png", dpi=150, bbox_inches="tight")
			plt.close()

			# Plot 2: Speedup vs p
			fig, ax = plt.subplots(figsize=(10, 6))
			speedups = [r.speedup(t_baseline) for r in results]
			ax.plot(p_vals, speedups, "o-", label="Measured", linewidth=2)
			ax.plot(p_vals, p_vals, "--", label="Ideal (S=p)", alpha=0.7)

			ax.set_xlabel("Number of Workers (p)")
			ax.set_ylabel("Speedup S(p) = T(1)/T(p)")
			ax.set_title(f"Strong Scaling Speedup - {exp_name}")
			ax.legend()
			ax.grid(True, alpha=0.3)
			ax.set_xscale("log")
			ax.set_yscale("log")

			plt.savefig(plot_dir / "speedup.png", dpi=150, bbox_inches="tight")
			plt.close()

			# Plot 3: Efficiency vs p
			fig, ax = plt.subplots(figsize=(10, 6))
			efficiencies = [r.efficiency(t_baseline) for r in results]
			ax.plot(p_vals, efficiencies, "o-", label="Measured", linewidth=2)
			ax.axhline(
				y=1.0, linestyle="--", color="gray", label="Ideal (E=1)", alpha=0.7
			)

			ax.set_xlabel("Number of Workers (p)")
			ax.set_ylabel("Efficiency E(p) = T(1)/(p·T(p))")
			ax.set_title(f"Strong Scaling Efficiency - {exp_name}")
			ax.legend()
			ax.grid(True, alpha=0.3)
			ax.set_xscale("log")
			ax.set_ylim(0, 1.2)

			plt.savefig(plot_dir / "efficiency.png", dpi=150, bbox_inches="tight")
			plt.close()

		# Weak scaling plots
		for exp_name in ["weak_noop", "weak_work"]:
			if exp_name not in self.results:
				continue

			exp_type, workload = exp_name.split("_", maxsplit=1)
			plot_dir = self.plots_dir / workload / exp_type
			plot_dir.mkdir(parents=True, exist_ok=True)

			results = self.results[exp_name]
			p_vals = [r.p for r in results]
			t_vals = [r.t_fg for r in results]
			t_baseline = results[0].t_fg

			# Weak efficiency plot
			fig, ax = plt.subplots(figsize=(10, 6))
			efficiencies = [t_baseline / r.t_fg for r in results]
			ax.plot(p_vals, efficiencies, "o-", label="Measured", linewidth=2)
			ax.axhline(
				y=1.0, linestyle="--", color="gray", label="Ideal (E=1)", alpha=0.7
			)

			ax.set_xlabel("Number of Workers (p)")
			ax.set_ylabel("Weak Efficiency E_weak(p) = T(1)/T(p)")
			ax.set_title(f"Weak Scaling Efficiency - {exp_name}")
			ax.legend()
			ax.grid(True, alpha=0.3)
			ax.set_xscale("log")
			ax.set_ylim(0, 1.2)

			plt.savefig(plot_dir / "efficiency.png", dpi=150, bbox_inches="tight")
			plt.close()

		logger.info(f"✓ Plots saved under {self.plots_dir}")


async def main():
	"""Run all benchmarks"""

	benchmark = FlowGenticBenchmark()

	# Scale points: Use smaller values for quick testing
	# Full spec: [1, 1024, 16384, 131072, 1048576]
	p_values = [1, 2, 4, 8, 16]  # Adjust based on your system

	# Experiment 1: Strong scaling with noop tasks (W_work = 0)
	config_strong_noop = BenchmarkConfig(
		name="noop", p_values=p_values, n_fg_fixed=1000, w_work_seconds=0.0
	)
	await benchmark.run_strong_scaling(config_strong_noop)

	# Experiment 2: Strong scaling with work (W_work > 0)
	config_strong_work = BenchmarkConfig(
		name="work",
		p_values=p_values,
		n_fg_fixed=1000,
		w_work_seconds=0.01,  # 10ms per task
	)
	await benchmark.run_strong_scaling(config_strong_work)

	# Experiment 3: Weak scaling with noop tasks
	config_weak_noop = BenchmarkConfig(
		name="noop", p_values=p_values, k_per_worker=100, w_work_seconds=0.0
	)
	await benchmark.run_weak_scaling(config_weak_noop)

	# Experiment 4: Weak scaling with work
	config_weak_work = BenchmarkConfig(
		name="work", p_values=p_values, k_per_worker=100, w_work_seconds=0.01
	)
	await benchmark.run_weak_scaling(config_weak_work)

	# Save and visualize
	benchmark.save_results()
	benchmark.generate_plots()

	logger.info("\n=== BENCHMARK COMPLETE ===")


if __name__ == "__main__":
	asyncio.run(main())
