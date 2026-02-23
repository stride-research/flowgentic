import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

logger = logging.getLogger(__name__)


class ThroughputSaturationPlotter(BasePlotter):
	"""
	Generates throughput saturation plots:
	1. Throughput vs Offered Load (saturation curve)
	2. p95 Latency vs Offered Load (tail latency under contention)

	Each series is an ensemble size (n_of_backend_slots).
	"""

	def __init__(self, plots_dir: Optional[Path] = None) -> None:
		super().__init__()
		self.plots_dir = plots_dir

	def plot_results(self, data: Dict[Any, Any]) -> None:
		"""Generate both throughput and latency plots."""
		if not data:
			logger.warning("No data to plot.")
			return

		self._plot_throughput(data)
		self._plot_latency(data)
		self._plot_combined(data)

	def _plot_throughput(self, data: Dict[Any, Any]) -> None:
		"""Plot throughput vs offered load (saturation curve)."""
		fig, ax = plt.subplots(figsize=(10, 7))

		sorted_series = sorted(
			data.items(), key=lambda item: item[1][0]["ensemble_size"]
		)

		for series_key, records in sorted_series:
			if not records:
				continue

			ensemble_size = records[0]["ensemble_size"]
			offered_loads = [r["offered_load"] for r in records]
			throughputs = [r["throughput"] for r in records]

			ax.plot(
				offered_loads,
				throughputs,
				marker="o",
				linewidth=2,
				markersize=8,
				label=f"S={ensemble_size}",
			)

		ax.set_xlabel("Offered Load (invocations/s)", fontsize=13)
		ax.set_ylabel("Sustained Throughput (invocations/s)", fontsize=13)
		ax.set_title(
			"Coordination Throughput vs Invocation Rate\n(Saturation Curve)",
			fontsize=14,
		)
		ax.legend(loc="lower right", fontsize=11)
		ax.grid(True, alpha=0.3)
		ax.set_xlim(left=0)
		ax.set_ylim(bottom=0)

		plt.tight_layout()

		if self.plots_dir:
			plot_path = self.plots_dir / "throughput_vs_load.png"
			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved plot: {plot_path}")

		plt.close(fig)

	def _plot_latency(self, data: Dict[Any, Any]) -> None:
		"""
		Plot p95 latency vs offered load.

		This reveals how the system behaves under contention. While throughput
		characterizes the maximum completion rate, p95 latency shows the
		latency experienced by the slowest 5% of invocations, which is
		sensitive to queueing, scheduling jitter, and control-plane contention.
		"""
		fig, ax = plt.subplots(figsize=(10, 7))

		sorted_series = sorted(
			data.items(), key=lambda item: item[1][0]["ensemble_size"]
		)

		for series_key, records in sorted_series:
			if not records:
				continue

			ensemble_size = records[0]["ensemble_size"]
			tool_duration = records[0].get("tool_execution_duration_time", 2)
			offered_loads = [r["offered_load"] for r in records]
			latencies_p95 = [r.get("latency_p95", 0) for r in records]

			ax.plot(
				offered_loads,
				latencies_p95,
				marker="s",
				linewidth=2,
				markersize=8,
				label=f"S={ensemble_size}",
			)

		# Add reference line for tool duration (theoretical minimum latency)
		if sorted_series:
			first_records = sorted_series[0][1]
			if first_records:
				tool_duration = first_records[0].get("tool_execution_duration_time", 2)
				max_load = max(r["offered_load"] for r in first_records)
				ax.axhline(
					y=tool_duration,
					color="gray",
					linestyle="--",
					alpha=0.7,
					label=f"Tool duration D={tool_duration}s",
				)

		ax.set_xlabel("Offered Load (invocations/s)", fontsize=13)
		ax.set_ylabel("p95 Latency (seconds)", fontsize=13)
		ax.set_title(
			"Tail Latency vs Invocation Rate\n(p95 End-to-End Latency)",
			fontsize=14,
		)
		ax.legend(loc="upper left", fontsize=11)
		ax.grid(True, alpha=0.3)
		ax.set_xlim(left=0)
		ax.set_ylim(bottom=0)

		plt.tight_layout()

		if self.plots_dir:
			plot_path = self.plots_dir / "latency_vs_load.png"
			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved plot: {plot_path}")

		plt.close(fig)

	def _plot_combined(self, data: Dict[Any, Any]) -> None:
		"""
		Generate combined 2-panel figure with throughput and latency side-by-side.

		This provides a complete view of system behavior:
		- Left: Where does throughput saturate?
		- Right: How does latency degrade as we approach saturation?
		"""
		fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

		sorted_series = sorted(
			data.items(), key=lambda item: item[1][0]["ensemble_size"]
		)

		colors = plt.cm.tab10.colors

		for idx, (series_key, records) in enumerate(sorted_series):
			if not records:
				continue

			ensemble_size = records[0]["ensemble_size"]
			tool_duration = records[0].get("tool_execution_duration_time", 2)
			offered_loads = [r["offered_load"] for r in records]
			throughputs = [r["throughput"] for r in records]
			latencies_p95 = [r.get("latency_p95", 0) for r in records]

			color = colors[idx % len(colors)]

			# Throughput plot
			ax1.plot(
				offered_loads,
				throughputs,
				marker="o",
				linewidth=2,
				markersize=8,
				color=color,
				label=f"S={ensemble_size}",
			)

			# Latency plot
			ax2.plot(
				offered_loads,
				latencies_p95,
				marker="s",
				linewidth=2,
				markersize=8,
				color=color,
				label=f"S={ensemble_size}",
			)

		# Throughput plot formatting
		ax1.set_xlabel("Offered Load (invocations/s)", fontsize=12)
		ax1.set_ylabel("Throughput (invocations/s)", fontsize=12)
		ax1.set_title("Saturation Curve", fontsize=13)
		ax1.legend(loc="lower right", fontsize=10)
		ax1.grid(True, alpha=0.3)
		ax1.set_xlim(left=0)
		ax1.set_ylim(bottom=0)

		# Latency plot formatting
		if sorted_series:
			first_records = sorted_series[0][1]
			if first_records:
				tool_duration = first_records[0].get("tool_execution_duration_time", 2)
				ax2.axhline(
					y=tool_duration,
					color="gray",
					linestyle="--",
					alpha=0.7,
					label=f"D={tool_duration}s",
				)

		ax2.set_xlabel("Offered Load (invocations/s)", fontsize=12)
		ax2.set_ylabel("p95 Latency (seconds)", fontsize=12)
		ax2.set_title("Tail Latency Under Load", fontsize=13)
		ax2.legend(loc="upper left", fontsize=10)
		ax2.grid(True, alpha=0.3)
		ax2.set_xlim(left=0)
		ax2.set_ylim(bottom=0)

		plt.suptitle(
			"Flowgentic Throughput Saturation Analysis",
			fontsize=14,
			fontweight="bold",
		)
		plt.tight_layout()

		if self.plots_dir:
			plot_path = self.plots_dir / "saturation_analysis.png"
			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved combined plot: {plot_path}")

		plt.close(fig)
