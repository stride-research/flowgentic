import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

# Silence matplotlib's verbose font manager DEBUG logs
logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _extract_event_durations(events: List[Dict]) -> Dict[str, List[float]]:
	"""
	Match start/end events by ID and compute durations.

	Returns dict with keys:
	- 'task_wrap': list of task wrapping durations
	- 'block_wrap': list of block wrapping durations
	- 'task_exec': list of task execution durations
	"""
	starts = {}
	ends = {}

	for e in events:
		event_type = e["event"]

		if event_type == "task_wrap_start":
			starts[("task_wrap", e["wrap_id"])] = e["ts"]
		elif event_type == "task_wrap_end":
			ends[("task_wrap", e["wrap_id"])] = e["ts"]
		elif event_type == "block_wrap_start":
			starts[("block_wrap", e["wrap_id"])] = e["ts"]
		elif event_type == "block_wrap_end":
			ends[("block_wrap", e["wrap_id"])] = e["ts"]
		elif event_type == "task_exec_start":
			starts[("task_exec", e["exec_id"])] = e["ts"]
		elif event_type == "task_exec_end":
			ends[("task_exec", e["exec_id"])] = e["ts"]

	durations = {"task_wrap": [], "block_wrap": [], "task_exec": []}

	for key, start_ts in starts.items():
		if key in ends:
			duration = ends[key] - start_ts
			durations[key[0]].append(duration)

	return durations


def _compute_overhead_metrics(records: List[Dict]) -> Dict[str, List]:
	"""
	Compute overhead metrics across all records.

	Returns dict with parallel lists indexed by record:
	- backend_slots, makespans
	- total_compilation_time, task_wrap_times, block_wrap_times
	- exec_durations (list of lists), mean_exec_duration
	- total_tasks
	"""
	metrics = {
		"backend_slots": [],
		"makespans": [],
		"total_compilation_time": [],
		"task_wrap_times": [],
		"block_wrap_times": [],
		"exec_durations": [],  # List of lists
		"mean_exec_duration": [],
		"total_tasks": [],
	}

	for r in records:
		durations = _extract_event_durations(r["events"])

		task_wrap_total = sum(durations["task_wrap"])
		block_wrap_total = sum(durations["block_wrap"])
		compilation_total = task_wrap_total + block_wrap_total

		metrics["backend_slots"].append(r["n_of_backend_slots"])
		metrics["makespans"].append(r["total_makespan"])
		metrics["total_compilation_time"].append(compilation_total)
		metrics["task_wrap_times"].append(task_wrap_total)
		metrics["block_wrap_times"].append(block_wrap_total)
		metrics["exec_durations"].append(durations["task_exec"])
		metrics["mean_exec_duration"].append(
			np.mean(durations["task_exec"]) if durations["task_exec"] else 0
		)
		metrics["total_tasks"].append(len(durations["task_exec"]))

	return metrics


class SyntheticAdaptivePlotter(BasePlotter):
	"""Handles saving benchmark results and generating plots for synthetic adaptive experiments."""

	def __init__(self, plots_dir: Optional[Path] = None) -> None:
		super().__init__()
		self.plots_dir = plots_dir

	def set_plots_dir(self, plots_dir: Path) -> None:
		"""Set the plots directory after initialization."""
		self.plots_dir = plots_dir

	def plot_results(self, data: Dict[Any, Any]) -> None:
		"""
		Generate all plots from experiment data.

		Data structure expected:
		{
			'strong_scaling-op-work': [...],  # List of BenchmarkedRecord dicts
			'weak_scaling-...': [...],        # Future
		}
		"""
		logger.debug(f"Received this data: {data}")

		for experiment_key, records in data.items():
			if experiment_key.startswith("strong_scaling"):
				self._plot_strong_scaling(experiment_key, records)
				self._plot_overhead(experiment_key, records, "strong_scaling")
				self._plot_throughput(experiment_key, records, "strong_scaling")
			elif experiment_key.startswith("weak_scaling"):
				self._plot_weak_scaling(experiment_key, records)
				self._plot_overhead(experiment_key, records, "weak_scaling")
				self._plot_throughput(experiment_key, records, "weak_scaling")

	def _plot_strong_scaling(
		self, experiment_name: str, records: List[Dict[Any, Any]]
	) -> None:
		"""
		Generate strong scaling plots: speedup and efficiency.

		Strong scaling: fixed workload, increasing backend slots.
		- Speedup = T(1) / T(p)
		- Efficiency = Speedup / p = T(1) / (p * T(p))
		"""
		if not records:
			logger.warning(f"No records for {experiment_name}, skipping plots.")
			return

		# Sort by backend slots to ensure correct ordering
		sorted_records = sorted(records, key=lambda r: r["n_of_backend_slots"])

		# Extract data
		backend_slots = [r["n_of_backend_slots"] for r in sorted_records]
		makespans = [r["total_makespan"] for r in sorted_records]

		# Calculate speedup and efficiency
		t1 = makespans[0]  # Baseline: makespan with 1 slot
		speedups = [t1 / t_p for t_p in makespans]
		efficiencies = [s / p for s, p in zip(speedups, backend_slots)]

		# Get metadata for titles
		run_name = sorted_records[0].get("run_name", "unknown")
		n_agents = sorted_records[0].get("n_of_agents", "?")
		n_tools = sorted_records[0].get("n_of_tool_calls_per_agent", "?")

		# Create subdirectory for strong scaling makespan plots
		makespan_subdir = "strong_scaling/makespan"

		# Plot speedup
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=speedups,
			title=f"Strong Scaling: Speedup\n({n_agents} agents, {n_tools} tool calls/agent)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Speedup (T₁/Tₚ)",
			filename="speedup.png",
			subdirectory=makespan_subdir,
			ideal_line=backend_slots,  # Ideal speedup = p (linear)
			ideal_label="Ideal (linear)",
		)

		# Plot efficiency
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=efficiencies,
			title=f"Strong Scaling: Efficiency\n({n_agents} agents, {n_tools} tool calls/agent)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Efficiency (Speedup/p)",
			filename="efficiency.png",
			subdirectory=makespan_subdir,
			ideal_line=[1.0] * len(backend_slots),  # Ideal efficiency = 1
			ideal_label="Ideal (100%)",
			y_max=1.1,
		)

		# Also plot raw makespan for reference
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=makespans,
			title=f"Strong Scaling: Makespan\n({n_agents} agents, {n_tools} tool calls/agent)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Makespan (seconds)",
			filename="makespan.png",
			subdirectory=makespan_subdir,
		)

		logger.info(f"Generated strong scaling makespan plots in {makespan_subdir}/")

	def _plot_weak_scaling(
		self, experiment_name: str, records: List[Dict[Any, Any]]
	) -> None:
		"""
		Generate weak scaling plots: speedup and efficiency.

		Weak scaling: workload increases proportionally with backend slots.
		- Efficiency = T(1) / T(p)  (ideally stays at 1)
		- Scaled Speedup = (p * T₁) / Tₚ
		"""
		if not records:
			logger.warning(f"No records for {experiment_name}, skipping plots.")
			return

		# Sort by backend slots
		sorted_records = sorted(records, key=lambda r: r["n_of_backend_slots"])

		# Extract data
		backend_slots = [r["n_of_backend_slots"] for r in sorted_records]
		makespans = [r["total_makespan"] for r in sorted_records]

		# Calculate weak scaling metrics
		t1 = makespans[0]
		# Weak scaling efficiency: T(1)/T(p) - should stay near 1 if scaling well
		efficiencies = [t1 / t_p for t_p in makespans]
		# Scaled speedup: how much faster we are vs sequential execution of scaled workload
		scaled_speedups = [(p * t1) / t_p for p, t_p in zip(backend_slots, makespans)]

		# Get metadata for titles
		run_name = sorted_records[0].get("run_name", "unknown")
		n_agents = sorted_records[0].get("n_of_agents", "?")

		# Create subdirectory for weak scaling makespan plots
		makespan_subdir = "weak_scaling/makespan"

		# Plot efficiency
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=efficiencies,
			title=f"Weak Scaling: Efficiency\n({n_agents} agents, workload ∝ p)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Efficiency (T₁/Tₚ)",
			filename="efficiency.png",
			subdirectory=makespan_subdir,
			ideal_line=[1.0] * len(backend_slots),
			ideal_label="Ideal (100%)",
			y_max=1.1,
		)

		# Plot scaled speedup
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=scaled_speedups,
			title=f"Weak Scaling: Scaled Speedup\n({n_agents} agents, workload ∝ p)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Scaled Speedup (p·T₁/Tₚ)",
			filename="speedup.png",
			subdirectory=makespan_subdir,
			ideal_line=backend_slots,
			ideal_label="Ideal (linear)",
		)

		logger.info(f"Generated weak scaling makespan plots in {makespan_subdir}/")

	def _create_scaling_plot(
		self,
		x_values: List[float],
		y_values: List[float],
		title: str,
		xlabel: str,
		ylabel: str,
		filename: str,
		subdirectory: Optional[str] = None,
		ideal_line: Optional[List[float]] = None,
		ideal_label: str = "Ideal",
		y_max: Optional[float] = None,
	) -> None:
		"""Create a single scaling plot with optional ideal reference line."""
		fig, ax = plt.subplots(figsize=(8, 6))

		# Plot actual values
		ax.plot(x_values, y_values, "bo-", linewidth=2, markersize=8, label="Measured")

		# Plot ideal line if provided
		if ideal_line is not None:
			ax.plot(
				x_values, ideal_line, "r--", linewidth=1.5, alpha=0.7, label=ideal_label
			)

		ax.set_xlabel(xlabel, fontsize=12)
		ax.set_ylabel(ylabel, fontsize=12)
		ax.set_title(title, fontsize=14)
		ax.grid(True, alpha=0.3)
		ax.legend(loc="best")

		# Set x-axis to show actual slot values
		ax.set_xticks(x_values)
		ax.set_xticklabels([str(x) for x in x_values])

		if y_max is not None:
			ax.set_ylim(bottom=0, top=y_max)
		else:
			ax.set_ylim(bottom=0)

		plt.tight_layout()

		# Save plot
		if self.plots_dir:
			if subdirectory:
				# Create subdirectory if it doesn't exist
				subdir_path = self.plots_dir / subdirectory
				subdir_path.mkdir(parents=True, exist_ok=True)
				plot_path = subdir_path / filename
			else:
				plot_path = self.plots_dir / filename

			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved plot: {plot_path}")
		else:
			logger.warning(f"No plots_dir set, cannot save {filename}")

		plt.close(fig)

	# ==================== OVERHEAD PLOTS ====================

	def _plot_overhead(
		self, experiment_name: str, records: List[Dict[Any, Any]], scaling_type: str
	) -> None:
		"""
		Plot overhead metrics from event data.

		Generates plots in {scaling_type}/overhead/:
		1. compilation_total.png - Total compilation time vs slots
		2. compilation_breakdown.png - Task vs block wrapping time breakdown
		3. exec_overhead_mean.png - Mean per-task execution duration
		4. exec_overhead_distribution.png - Box plot of execution durations
		5. overhead_percentage.png - Framework overhead as % of makespan
		"""
		if not records:
			return

		sorted_records = sorted(records, key=lambda r: r["n_of_backend_slots"])
		metrics = _compute_overhead_metrics(sorted_records)
		overhead_subdir = f"{scaling_type}/overhead"

		n_agents = sorted_records[0].get("n_of_agents", "?")
		n_tools = sorted_records[0].get("n_of_tool_calls_per_agent", "?")
		subtitle = f"({n_agents} agents, {n_tools} tool calls/agent)"

		# 1. Total compilation time vs backend slots
		# INTERPRETATION: Shows setup cost - should be constant regardless of slots
		# If it increases with slots, there's scaling overhead in wrapping
		self._create_scaling_plot(
			x_values=metrics["backend_slots"],
			y_values=[t * 1000 for t in metrics["total_compilation_time"]],  # ms
			title=f"Compilation Overhead\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Total Compilation Time (ms)",
			filename="compilation_total.png",
			subdirectory=overhead_subdir,
		)

		# 2. Compilation breakdown: task wrapping vs block wrapping
		# INTERPRETATION: Identifies which component dominates setup cost
		# High task_wrap suggests many small tasks; high block_wrap suggests complex coordination
		self._create_stacked_bar_plot(
			x_values=metrics["backend_slots"],
			y_stacks={
				"Task Wrapping": [t * 1000 for t in metrics["task_wrap_times"]],
				"Block Wrapping": [t * 1000 for t in metrics["block_wrap_times"]],
			},
			title=f"Compilation Overhead Breakdown\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Time (ms)",
			filename="compilation_breakdown.png",
			subdirectory=overhead_subdir,
		)

		# 3. Mean execution duration per task
		# INTERPRETATION: Should be ~constant (= configured task duration)
		# Deviation indicates scheduling/queueing overhead in the execution path
		self._create_scaling_plot(
			x_values=metrics["backend_slots"],
			y_values=metrics["mean_exec_duration"],
			title=f"Mean Task Execution Duration\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Mean Duration (seconds)",
			filename="exec_overhead_mean.png",
			subdirectory=overhead_subdir,
		)

		# 4. Execution duration distribution (box plot)
		# INTERPRETATION: Variance reveals consistency of task execution
		# High variance suggests contention or uneven scheduling
		self._create_box_plot(
			data=metrics["exec_durations"],
			labels=[str(s) for s in metrics["backend_slots"]],
			title=f"Task Execution Duration Distribution\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Duration (seconds)",
			filename="exec_overhead_distribution.png",
			subdirectory=overhead_subdir,
		)

		# 5. Framework overhead as percentage of total makespan
		# INTERPRETATION: Key metric - how much time is "wasted" on framework overhead
		# Should decrease with more parallelism as compilation is amortized
		overhead_pct = [
			(comp / ms) * 100 if ms > 0 else 0
			for comp, ms in zip(metrics["total_compilation_time"], metrics["makespans"])
		]
		self._create_scaling_plot(
			x_values=metrics["backend_slots"],
			y_values=overhead_pct,
			title=f"Framework Overhead Percentage\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Overhead (% of makespan)",
			filename="overhead_percentage.png",
			subdirectory=overhead_subdir,
		)

		logger.info(f"Generated overhead plots in {overhead_subdir}/")

	# ==================== THROUGHPUT PLOTS ====================

	def _plot_throughput(
		self, experiment_name: str, records: List[Dict[Any, Any]], scaling_type: str
	) -> None:
		"""
		Plot throughput metrics from event data.

		Generates plots in {scaling_type}/throughput/:
		1. throughput.png - Tasks completed per second vs slots
		2. throughput_per_slot.png - Throughput divided by slot count
		3. throughput_scaling.png - Actual vs ideal throughput scaling
		"""
		if not records:
			return

		sorted_records = sorted(records, key=lambda r: r["n_of_backend_slots"])
		metrics = _compute_overhead_metrics(sorted_records)
		throughput_subdir = f"{scaling_type}/throughput"

		n_agents = sorted_records[0].get("n_of_agents", "?")
		n_tools = sorted_records[0].get("n_of_tool_calls_per_agent", "?")
		subtitle = f"({n_agents} agents, {n_tools} tool calls/agent)"

		# Calculate throughput metrics
		# throughput = total_tasks / makespan
		throughputs = [
			n / ms if ms > 0 else 0
			for n, ms in zip(metrics["total_tasks"], metrics["makespans"])
		]

		# 1. Aggregate throughput vs backend slots
		# INTERPRETATION: Shows system capacity - should increase with slots
		# Flattening indicates saturation or bottleneck
		self._create_scaling_plot(
			x_values=metrics["backend_slots"],
			y_values=throughputs,
			title=f"Task Throughput\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Throughput (tasks/second)",
			filename="throughput.png",
			subdirectory=throughput_subdir,
		)

		# 2. Throughput per slot (utilization efficiency)
		# INTERPRETATION: Measures per-slot productivity
		# Decreasing values indicate diminishing returns from adding slots
		throughput_per_slot = [
			t / s if s > 0 else 0 for t, s in zip(throughputs, metrics["backend_slots"])
		]
		self._create_scaling_plot(
			x_values=metrics["backend_slots"],
			y_values=throughput_per_slot,
			title=f"Throughput per Backend Slot\n{subtitle}",
			xlabel="Number of Backend Slots (p)",
			ylabel="Throughput per Slot (tasks/second/slot)",
			filename="throughput_per_slot.png",
			subdirectory=throughput_subdir,
		)

		# 3. Throughput scaling factor: actual vs ideal
		# INTERPRETATION: Compares actual scaling to theoretical linear scaling
		# Gap represents parallelization inefficiency (Amdahl's law effects)
		if throughputs[0] > 0:
			baseline_throughput = throughputs[0]
			actual_scaling = [t / baseline_throughput for t in throughputs]
			ideal_scaling = [float(s) for s in metrics["backend_slots"]]

			self._create_scaling_plot(
				x_values=metrics["backend_slots"],
				y_values=actual_scaling,
				title=f"Throughput Scaling Factor\n{subtitle}",
				xlabel="Number of Backend Slots (p)",
				ylabel="Scaling Factor (relative to 1 slot)",
				filename="throughput_scaling.png",
				subdirectory=throughput_subdir,
				ideal_line=ideal_scaling,
				ideal_label="Ideal (linear)",
			)

		logger.info(f"Generated throughput plots in {throughput_subdir}/")

	# ==================== HELPER PLOT METHODS ====================

	def _create_stacked_bar_plot(
		self,
		x_values: List[float],
		y_stacks: Dict[str, List[float]],
		title: str,
		xlabel: str,
		ylabel: str,
		filename: str,
		subdirectory: Optional[str] = None,
	) -> None:
		"""Create a stacked bar chart."""
		fig, ax = plt.subplots(figsize=(8, 6))

		x_positions = np.arange(len(x_values))
		width = 0.6
		bottom = np.zeros(len(x_values))

		colors = plt.cm.Set2.colors
		for i, (label, values) in enumerate(y_stacks.items()):
			ax.bar(
				x_positions,
				values,
				width,
				label=label,
				bottom=bottom,
				color=colors[i % len(colors)],
			)
			bottom += np.array(values)

		ax.set_xlabel(xlabel, fontsize=12)
		ax.set_ylabel(ylabel, fontsize=12)
		ax.set_title(title, fontsize=14)
		ax.set_xticks(x_positions)
		ax.set_xticklabels([str(x) for x in x_values])
		ax.legend(loc="best")
		ax.grid(True, alpha=0.3, axis="y")

		plt.tight_layout()
		self._save_plot(fig, filename, subdirectory)
		plt.close(fig)

	def _create_box_plot(
		self,
		data: List[List[float]],
		labels: List[str],
		title: str,
		xlabel: str,
		ylabel: str,
		filename: str,
		subdirectory: Optional[str] = None,
	) -> None:
		"""Create a box plot for distribution visualization."""
		fig, ax = plt.subplots(figsize=(8, 6))

		# Filter out empty lists
		filtered_data = []
		filtered_labels = []
		for d, label in zip(data, labels):
			if d:
				filtered_data.append(d)
				filtered_labels.append(label)

		if not filtered_data:
			plt.close(fig)
			return

		bp = ax.boxplot(filtered_data, patch_artist=True)

		# Style the boxes
		for patch in bp["boxes"]:
			patch.set_facecolor("lightblue")
			patch.set_alpha(0.7)

		ax.set_xlabel(xlabel, fontsize=12)
		ax.set_ylabel(ylabel, fontsize=12)
		ax.set_title(title, fontsize=14)
		ax.set_xticklabels(filtered_labels)
		ax.grid(True, alpha=0.3, axis="y")

		plt.tight_layout()
		self._save_plot(fig, filename, subdirectory)
		plt.close(fig)

	def _save_plot(
		self, fig: plt.Figure, filename: str, subdirectory: Optional[str] = None
	) -> None:
		"""Save a plot to the configured directory."""
		if self.plots_dir:
			if subdirectory:
				subdir_path = self.plots_dir / subdirectory
				subdir_path.mkdir(parents=True, exist_ok=True)
				plot_path = subdir_path / filename
			else:
				plot_path = self.plots_dir / filename

			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved plot: {plot_path}")
		else:
			logger.warning(f"No plots_dir set, cannot save {filename}")
