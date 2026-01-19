import json
import logging
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)


class Analyse:
	"""Handles saving benchmark results and generating plots"""

	def __init__(self, data_dir: Path):
		self.data_dir = data_dir
		self.plots_dir = data_dir.parent / "plots"
		self.plots_dir.mkdir(parents=True, exist_ok=True)

	def save_and_plot(self, results: Dict[str, List[Dict]]):
		"""Save results to JSON and generate plots"""
		for name, data in results.items():
			# Save JSON
			output_file = self.data_dir / f"{name}.json"
			with open(output_file, "w") as f:
				json.dump(data, f, indent=2)
			logger.info(f"✓ Results saved to {output_file}")

			# Generate plots
			self._plot_scaling(name, data)

		logger.info(f"✓ All results and plots saved to {self.data_dir.parent}")

	def _plot_scaling(self, experiment_name: str, data: List[Dict]):
		"""Generate speedup and efficiency plots"""
		# Determine scaling type from experiment name
		scaling_type = experiment_name.split("-")[0]  # e.g., "strong_scaling"

		# Create subdirectories for strong_scaling
		if scaling_type == "strong_scaling":
			speedup_dir = self.plots_dir / "strong_scaling" / "speedup"
			efficiency_dir = self.plots_dir / "strong_scaling" / "efficiency"
			speedup_dir.mkdir(parents=True, exist_ok=True)
			efficiency_dir.mkdir(parents=True, exist_ok=True)
		else:
			speedup_dir = self.plots_dir / scaling_type
			efficiency_dir = self.plots_dir / scaling_type
			speedup_dir.mkdir(parents=True, exist_ok=True)

		# Extract p values and makespans
		p_values = np.array([d["n_of_backend_slots"] for d in data])
		makespans = np.array([d["makespan"] for d in data])

		# T(1) is the first measurement (p=1)
		T1 = makespans[0]

		# Speedup: S(p) = T(1) / T(p)
		speedup = T1 / makespans

		# Efficiency: E(p) = S(p) / p
		efficiency = speedup / p_values

		# Ideal lines
		ideal_speedup = p_values.astype(float)
		ideal_efficiency = np.ones_like(p_values, dtype=float)

		# Plot speedup
		fig, ax = plt.subplots(figsize=(8, 6))
		ax.plot(p_values, speedup, "o-", label="Measured", linewidth=2, markersize=8)
		ax.plot(p_values, ideal_speedup, "--", label="Ideal", linewidth=2, alpha=0.7)
		ax.set_xlabel("Backend Slots (p)")
		ax.set_ylabel("Speedup S(p)")
		ax.set_title(f"Speedup: {experiment_name}")
		ax.legend()
		ax.set_xscale("log", base=2)
		ax.grid(True, alpha=0.3)
		plt.tight_layout()
		plt.savefig(speedup_dir / f"{experiment_name}-speedup.png", dpi=150)
		plt.close()

		# Plot efficiency
		fig, ax = plt.subplots(figsize=(8, 6))
		ax.plot(p_values, efficiency, "o-", label="Measured", linewidth=2, markersize=8)
		ax.plot(p_values, ideal_efficiency, "--", label="Ideal", linewidth=2, alpha=0.7)
		ax.set_xlabel("Backend Slots (p)")
		ax.set_ylabel("Efficiency E(p)")
		ax.set_title(f"Efficiency: {experiment_name}")
		ax.legend()
		ax.set_xscale("log", base=2)
		ax.set_ylim(0, 1.1)
		ax.grid(True, alpha=0.3)
		plt.tight_layout()
		plt.savefig(efficiency_dir / f"{experiment_name}-efficiency.png", dpi=150)
		plt.close()

		logger.info(f"✓ Plots saved for {experiment_name}")
