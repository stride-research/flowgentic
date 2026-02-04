import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

logger = logging.getLogger(__name__)


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
			elif experiment_key.startswith("weak_scaling"):
				self._plot_weak_scaling(experiment_key, records)
			# Future: overhead, throughput plots

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

		# Create subdirectory for strong scaling plots
		scaling_subdir = "strong_scaling"

		# Plot speedup
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=speedups,
			title=f"Strong Scaling: Speedup\n({n_agents} agents, {n_tools} tool calls/agent)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Speedup (T₁/Tₚ)",
			filename="speedup.png",
			subdirectory=scaling_subdir,
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
			subdirectory=scaling_subdir,
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
			subdirectory=scaling_subdir,
		)

		logger.info(f"Generated strong scaling plots in {scaling_subdir}/")

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

		# Create subdirectory for weak scaling plots
		scaling_subdir = "weak_scaling"

		# Plot efficiency
		self._create_scaling_plot(
			x_values=backend_slots,
			y_values=efficiencies,
			title=f"Weak Scaling: Efficiency\n({n_agents} agents, workload ∝ p)",
			xlabel="Number of Backend Slots (p)",
			ylabel="Efficiency (T₁/Tₚ)",
			filename="efficiency.png",
			subdirectory=scaling_subdir,
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
			subdirectory=scaling_subdir,
			ideal_line=backend_slots,
			ideal_label="Ideal (linear)",
		)

		logger.info(f"Generated weak scaling plots in {scaling_subdir}/")

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

	# Future methods for overhead and throughput plots
	def _plot_overhead(
		self, experiment_name: str, records: List[Dict[Any, Any]]
	) -> None:
		"""Plot coordination overhead metrics using events data."""
		# TODO: Implement overhead analysis from events
		# Events contain tool_exec_start/tool_exec_end timestamps
		pass

	def _plot_throughput(
		self, experiment_name: str, records: List[Dict[Any, Any]]
	) -> None:
		"""Plot throughput metrics. To be implemented when data is available."""
		# TODO: Implement throughput plotting
		pass
