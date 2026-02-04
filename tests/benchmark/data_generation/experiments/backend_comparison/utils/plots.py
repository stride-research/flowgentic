import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter
from tests.benchmark.data_generation.experiments.synthethic_adaptive.utils.plots import (
	_extract_event_durations,
)

logger = logging.getLogger(__name__)


ENGINE_COLORS = {
	"asyncflow": "#2196F3",  
	"parsl": "#FF9800",      
}

ENGINE_LABELS = {
	"asyncflow": "AsyncFlow",
	"parsl": "Parsl",
}


class BackendComparisonPlotter(BasePlotter):
	"""Generates comparison plots for backend-adaptive execution experiment."""

	def __init__(self, plots_dir: Optional[Path] = None) -> None:
		super().__init__()
		self.plots_dir = plots_dir

	def plot_results(self, data: Dict[Any, Any]) -> None:
		"""
		Generate all comparison plots.

		Data structure expected:
		{
			"asyncflow": {...single BenchmarkedRecord with engine_id...},
			"parsl": {...single BenchmarkedRecord with engine_id...},
		}
		"""
		engines = list(data.keys())
		if not engines:
			logger.warning("No data to plot.")
			return

		# Get metadata from first record for subtitles
		sample = data[engines[0]]
		self._subtitle = (
			f"({sample['n_of_agents']} agents, "
			f"{sample['n_of_tool_calls_per_agent']} tool calls/agent, "
			f"{sample['n_of_backend_slots']} slots)"
		)


		self._plot_makespan(data, engines, "makespan")
		self._plot_compilation_total(data, engines, "overhead")
		self._plot_compilation_breakdown(data, engines, "overhead")
		self._plot_overhead_percentage(data, engines, "overhead")
		self._plot_throughput(data, engines, "throughput")
		self._plot_submission_rate(data, engines, "throughput")

	# MAkespan plots

	def _plot_makespan(
		self, data: Dict, engines: List[str], subdir: str
	) -> None:
		"""Total execution time (makespan) per engine."""
		fig, ax = plt.subplots(figsize=(8, 6))

		makespans = [data[e]["total_makespan"] for e in engines]
		colors = [ENGINE_COLORS.get(e, "#999") for e in engines]
		labels = [ENGINE_LABELS.get(e, e) for e in engines]

		bars = ax.bar(labels, makespans, color=colors, width=0.5)

		for bar in bars:
			ax.text(
				bar.get_x() + bar.get_width() / 2, bar.get_height(),
				f"{bar.get_height():.3f}s",
				ha="center", va="bottom", fontsize=10,
			)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Makespan (seconds)", fontsize=12)
		ax.set_title(f"Makespan Comparison\n{self._subtitle}", fontsize=14)
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "makespan.png", subdir)
		plt.close(fig)

	# Overhead plots

	def _plot_compilation_total(
		self, data: Dict, engines: List[str], subdir: str
	) -> None:
		"""Total compilation time (task wrapping + block wrapping) per engine."""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		colors = [ENGINE_COLORS.get(e, "#999") for e in engines]

		compilation_times = []
		for e in engines:
			durations = _extract_event_durations(data[e]["events"])
			total = sum(durations["task_wrap"]) + sum(durations["block_wrap"])
			compilation_times.append(total * 1000)  # ms

		bars = ax.bar(labels, compilation_times, color=colors, width=0.5)

		for bar in bars:
			ax.text(
				bar.get_x() + bar.get_width() / 2, bar.get_height(),
				f"{bar.get_height():.2f}ms",
				ha="center", va="bottom", fontsize=10,
			)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Total Compilation Time (ms)", fontsize=12)
		ax.set_title(f"Compilation Overhead\n{self._subtitle}", fontsize=14)
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "compilation_total.png", subdir)
		plt.close(fig)

	def _plot_compilation_breakdown(
		self, data: Dict, engines: List[str], subdir: str
	) -> None:
		"""Stacked bar: task wrapping vs block wrapping per engine."""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		task_wrap_vals = []
		block_wrap_vals = []

		for e in engines:
			durations = _extract_event_durations(data[e]["events"])
			task_wrap_vals.append(sum(durations["task_wrap"]) * 1000)
			block_wrap_vals.append(sum(durations["block_wrap"]) * 1000)

		x = np.arange(len(engines))
		width = 0.5

		ax.bar(x, task_wrap_vals, width, label="Task Wrapping", color="#4CAF50", alpha=0.8)
		ax.bar(
			x, block_wrap_vals, width, bottom=task_wrap_vals,
			label="Block Wrapping", color="#9C27B0", alpha=0.8,
		)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Time (ms)", fontsize=12)
		ax.set_title(f"Compilation Overhead Breakdown\n{self._subtitle}", fontsize=14)
		ax.set_xticks(x)
		ax.set_xticklabels(labels)
		ax.legend()
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "compilation_breakdown.png", subdir)
		plt.close(fig)

	def _plot_overhead_percentage(
		self, data: Dict, engines: List[str], subdir: str
	) -> None:
		"""Framework overhead as % of total makespan per engine."""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		colors = [ENGINE_COLORS.get(e, "#999") for e in engines]

		overhead_pcts = []
		for e in engines:
			durations = _extract_event_durations(data[e]["events"])
			compilation = sum(durations["task_wrap"]) + sum(durations["block_wrap"])
			makespan = data[e]["total_makespan"]
			pct = (compilation / makespan * 100) if makespan > 0 else 0
			overhead_pcts.append(pct)

		bars = ax.bar(labels, overhead_pcts, color=colors, width=0.5)

		for bar in bars:
			ax.text(
				bar.get_x() + bar.get_width() / 2, bar.get_height(),
				f"{bar.get_height():.2f}%",
				ha="center", va="bottom", fontsize=10,
			)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Overhead (% of makespan)", fontsize=12)
		ax.set_title(f"Framework Overhead Percentage\n{self._subtitle}", fontsize=14)
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "overhead_percentage.png", subdir)
		plt.close(fig)

	# Throughput plots

	def _plot_throughput(
		self, data: Dict, engines: List[str], subdir: str
	) -> None:
		"""Tasks completed per second per engine."""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		colors = [ENGINE_COLORS.get(e, "#999") for e in engines]

		throughputs = []
		for e in engines:
			durations = _extract_event_durations(data[e]["events"])
			total_tasks = len(durations["task_exec"])
			makespan = data[e]["total_makespan"]
			throughputs.append(total_tasks / makespan if makespan > 0 else 0)

		bars = ax.bar(labels, throughputs, color=colors, width=0.5)

		for bar in bars:
			ax.text(
				bar.get_x() + bar.get_width() / 2, bar.get_height(),
				f"{bar.get_height():.2f}",
				ha="center", va="bottom", fontsize=10,
			)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Throughput (tasks/second)", fontsize=12)
		ax.set_title(f"Task Throughput\n{self._subtitle}", fontsize=14)
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "throughput.png", subdir)
		plt.close(fig)

	def _plot_submission_rate(
		self, data: Dict, engines: List[str], subdir: str
	) -> None:
		"""Ensemble submission rate: tasks submitted per second per engine."""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		colors = [ENGINE_COLORS.get(e, "#999") for e in engines]

		submission_rates = []
		for e in engines:
			start_timestamps = [
				ev["ts"] for ev in data[e]["events"] if ev["event"] == "task_exec_start"
			]
			total_tasks = len(start_timestamps)
			if total_tasks > 1:
				submission_window = max(start_timestamps) - min(start_timestamps)
				rate = total_tasks / submission_window if submission_window > 0 else 0.0
			else:
				rate = 0.0
			submission_rates.append(rate)

		bars = ax.bar(labels, submission_rates, color=colors, width=0.5)

		for bar in bars:
			ax.text(
				bar.get_x() + bar.get_width() / 2, bar.get_height(),
				f"{bar.get_height():.2f}",
				ha="center", va="bottom", fontsize=10,
			)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Submission Rate (tasks/second)", fontsize=12)
		ax.set_title(f"Ensemble Submission Rate\n{self._subtitle}", fontsize=14)
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "submission_rate.png", subdir)
		plt.close(fig)

	# Helpers

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
				plot_path.parent.mkdir(parents=True, exist_ok=True)

			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved plot: {plot_path}")
		else:
			logger.warning(f"No plots_dir set, cannot save {filename}")
