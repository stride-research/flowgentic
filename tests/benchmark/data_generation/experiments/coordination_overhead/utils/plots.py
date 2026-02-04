import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

logger = logging.getLogger(__name__)


class CoordinationOverheadPlotter(BasePlotter):
	"""Plotter for coordination overhead vs ensemble size."""

	def __init__(self, plots_dir: Optional[Path] = None) -> None:
		super().__init__()
		self.plots_dir = plots_dir

	def set_plots_dir(self, plots_dir: Path) -> None:
		self.plots_dir = plots_dir

	def plot_results(self, data: Dict[Any, Any]) -> None:
		if not data:
			logger.warning("No data provided for coordination overhead plot.")
			return

		ensemble_sizes = data.get("ensemble_sizes", [])
		adapter_series = data.get("adapters", {})

		if not ensemble_sizes or not adapter_series:
			logger.warning("Missing ensemble sizes or adapter series, skipping plot.")
			return

		fig, ax = plt.subplots(figsize=(8, 6))

		colors = plt.cm.Set2.colors
		for idx, (adapter_name, series) in enumerate(adapter_series.items()):
			overheads = series.get("overhead_ms", [])
			if not overheads:
				continue
			ax.plot(
				ensemble_sizes,
				overheads,
				marker="o",
				linewidth=2,
				color=colors[idx % len(colors)],
				label=adapter_name,
			)

		ax.set_xlabel("Ensemble Size (# invocations per ensemble)", fontsize=12)
		ax.set_ylabel(
			"Mean Coordination Overhead per Invocation (ms/invocation)", fontsize=12
		)
		ax.set_title(
			"Per-invocation Coordination Overhead vs Ensemble Size",
			fontsize=14,
		)
		ax.set_xticks(ensemble_sizes)
		ax.grid(True, alpha=0.3)
		ax.legend(loc="best")

		plt.tight_layout()
		self._save_plot(fig, "coordination_overhead.png")
		plt.close(fig)

	def _save_plot(self, fig: plt.Figure, filename: str) -> None:
		if self.plots_dir:
			self.plots_dir.mkdir(parents=True, exist_ok=True)
			plot_path = self.plots_dir / filename
			fig.savefig(plot_path, dpi=150, bbox_inches="tight")
			logger.info(f"Saved plot: {plot_path}")
		else:
			logger.warning(f"No plots_dir set, cannot save {filename}")