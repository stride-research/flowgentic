import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

logger = logging.getLogger(__name__)


class ThroughputSaturationPlotter(BasePlotter):
	"""
	Generates throughput saturation plot: coordination throughput vs offered load.

	Each series is an ensemble size (n_of_backend_slots). Expected shape: a
	near-diagonal region that transitions into a flat plateau. Larger ensemble
	sizes produce a higher plateau.
	"""

	def __init__(self, plots_dir: Optional[Path] = None) -> None:
		super().__init__()
		self.plots_dir = plots_dir

	def plot_results(self, data: Dict[Any, Any]) -> None:
		if not data:
			logger.warning("No data to plot.")
			return

		fig, ax = plt.subplots(figsize=(10, 7))

		# Sort series by ensemble_size so legend order matches visual curve order
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
				label=f"Ensemble size {ensemble_size}",
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
		else:
			logger.warning("No plots_dir set, cannot save throughput_vs_load.png")

		plt.close(fig)
