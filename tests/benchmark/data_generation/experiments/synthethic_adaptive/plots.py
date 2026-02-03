import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import numpy as np

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

logger = logging.getLogger(__name__)


class SyntheticAdaptivePlotter(BasePlotter):
	"""Handles saving benchmark results and generating plots"""

	def __init__(self) -> None: ...

	def plot_results(self, data: Dict[Any, Any]):
		logger.debug(f"Received this data: {data}")

	# def __init__(self, data_dir: Path):
	# 	self.data_dir = data_dir
	# 	self.plots_dir = data_dir.parent / "plots"
	# 	self.plots_dir.mkdir(parents=True, exist_ok=True)

	# def save_and_plot(self, results: Dict[str, List[Dict]]):
	# 	"""Save results to JSON and generate plots"""
	# 	# Step 1: Save all JSON files
	# 	for name, data in results.items():
	# 		output_file = self.data_dir / f"{name}.json"
	# 		with open(output_file, "w") as f:
	# 			json.dump(data, f, indent=2)
	# 		logger.info(f"✓ Results saved to {output_file}")

	# 	# Step 2: Group experiments by base name (without noop/not_noop)
	# 	grouped_experiments = self._group_experiments(results)

	# 	# Step 3: Generate combined plots for each group
	# 	for base_name, experiments in grouped_experiments.items():
	# 		self._plot_scaling_combined(base_name, experiments)

	# 	logger.info(f"✓ All results and plots saved to {self.data_dir.parent}")

	# def _group_experiments(
	# 	self, results: Dict[str, List[Dict]]
	# ) -> Dict[str, Dict[str, List[Dict]]]:
	# 	"""Group experiments by base name (e.g., group noop and not_noop together)"""
	# 	grouped = {}

	# 	for name, data in results.items():
	# 		# Extract base name by removing noop/not_noop suffix
	# 		if name.endswith("-noop"):
	# 			base_name = name[:-5]  # Remove "-noop"
	# 			variant = "noop"
	# 		elif name.endswith("-not_noop"):
	# 			base_name = name[:-9]  # Remove "-not_noop"
	# 			variant = "not_noop"
	# 		else:
	# 			base_name = name
	# 			variant = "default"

	# 		if base_name not in grouped:
	# 			grouped[base_name] = {}

	# 		grouped[base_name][variant] = data

	# 	return grouped

	# def _plot_scaling_combined(
	# 	self, base_name: str, experiments: Dict[str, List[Dict]]
	# ):
	# 	"""Generate combined speedup and efficiency plots for multiple experiment variants"""
	# 	scaling_type = base_name.split("-")[
	# 		0
	# 	]  # e.g., "strong_scaling" or "weak_scaling"

	# 	# Create subdirectories
	# 	if scaling_type == "strong_scaling":
	# 		speedup_dir = self.plots_dir / "strong_scaling" / "speedup"
	# 		efficiency_dir = self.plots_dir / "strong_scaling" / "efficiency"
	# 		speedup_dir.mkdir(parents=True, exist_ok=True)
	# 		efficiency_dir.mkdir(parents=True, exist_ok=True)
	# 	elif scaling_type == "weak_scaling":
	# 		makespan_dir = self.plots_dir / "weak_scaling" / "makespan"
	# 		efficiency_dir = self.plots_dir / "weak_scaling" / "efficiency"
	# 		makespan_dir.mkdir(parents=True, exist_ok=True)
	# 		efficiency_dir.mkdir(parents=True, exist_ok=True)
	# 	else:
	# 		speedup_dir = self.plots_dir / scaling_type
	# 		efficiency_dir = self.plots_dir / scaling_type
	# 		speedup_dir.mkdir(parents=True, exist_ok=True)

	# 	# Prepare data for plotting
	# 	series_data = {}
	# 	for variant, data in experiments.items():
	# 		p_values = np.array([d["n_of_backend_slots"] for d in data])
	# 		metric_values = np.array([d["makespan"] for d in data])

	# 		if scaling_type == "weak_scaling":
	# 			# For weak scaling: efficiency = how close makespan stays to baseline
	# 			T1 = metric_values[0]
	# 			# Weak scaling efficiency: T(1) / T(p)
	# 			# (should be ~1.0 if makespan stays constant)
	# 			weak_efficiency = T1 / metric_values

	# 			series_data[variant] = {
	# 				"p_values": p_values,
	# 				"makespan": metric_values,
	# 				"efficiency": weak_efficiency,  # This stays near 1.0
	# 			}
	# 		else:
	# 			# Strong scaling
	# 			T1 = metric_values[0]
	# 			speedup = T1 / metric_values
	# 			efficiency = speedup / p_values

	# 			series_data[variant] = {
	# 				"p_values": p_values,
	# 				"makespan": metric_values,
	# 				"speedup": speedup,
	# 				"efficiency": efficiency,
	# 			}

	# 	# Define colors and markers for different variants
	# 	colors = {"noop": "blue", "not_noop": "green", "default": "gray"}
	# 	markers = {"noop": "o", "not_noop": "s", "default": "^"}
	# 	labels = {
	# 		"noop": "NOOP (overhead only)",
	# 		"not_noop": "Real Work (4s tools)",
	# 		"default": "Default",
	# 	}
	# 	first_p_values = next(iter(series_data.values()))["p_values"]

	# 	# Plot based on scaling type
	# 	if scaling_type == "weak_scaling":
	# 		# For weak scaling: plot makespan (should be flat)
	# 		fig, ax = plt.subplots(figsize=(10, 6))

	# 		for variant, data_dict in series_data.items():
	# 			ax.plot(
	# 				data_dict["p_values"],
	# 				data_dict["makespan"],
	# 				marker=markers.get(variant, "o"),
	# 				linestyle="-",
	# 				label=labels.get(variant, variant),
	# 				linewidth=2,
	# 				markersize=8,
	# 				color=colors.get(variant, "gray"),
	# 			)

	# 		# Add ideal line (flat at first value)
	# 		ideal_makespan = (
	# 			np.ones_like(first_p_values, dtype=float)
	# 			* series_data[next(iter(series_data))]["makespan"][0]
	# 		)
	# 		ax.plot(
	# 			first_p_values,
	# 			ideal_makespan,
	# 			"--",
	# 			label="Ideal (Constant)",
	# 			linewidth=2,
	# 			alpha=0.7,
	# 			color="black",
	# 		)

	# 		# Set y-axis limits to show proper context (0 to ~1.5x max value)
	# 		all_makespan_values = np.concatenate(
	# 			[data_dict["makespan"] for data_dict in series_data.values()]
	# 		)
	# 		max_makespan = np.max(all_makespan_values)
	# 		ax.set_ylim(0, max_makespan * 1.5)

	# 		ax.set_xlabel("Backend Slots (p)", fontsize=12)
	# 		ax.set_ylabel("Makespan (seconds)", fontsize=12)
	# 		ax.set_title(f"Weak Scaling - Makespan: {base_name}", fontsize=14)
	# 		ax.legend(fontsize=10)
	# 		ax.set_xscale("log", base=2)
	# 		ax.grid(True, alpha=0.3)
	# 		plt.tight_layout()
	# 		plt.savefig(makespan_dir / f"{base_name}-makespan-combined.png", dpi=150)
	# 		plt.close()
	# 	else:
	# 		# For strong scaling: plot speedup
	# 		fig, ax = plt.subplots(figsize=(10, 6))

	# 		for variant, data_dict in series_data.items():
	# 			ax.plot(
	# 				data_dict["p_values"],
	# 				data_dict["speedup"],
	# 				marker=markers.get(variant, "o"),
	# 				linestyle="-",
	# 				label=labels.get(variant, variant),
	# 				linewidth=2,
	# 				markersize=8,
	# 				color=colors.get(variant, "gray"),
	# 			)

	# 		# Add ideal line (using first series' p_values)
	# 		ideal_speedup = first_p_values.astype(float)
	# 		ax.plot(
	# 			first_p_values,
	# 			ideal_speedup,
	# 			"--",
	# 			label="Ideal (Linear)",
	# 			linewidth=2,
	# 			alpha=0.7,
	# 			color="black",
	# 		)

	# 		ax.set_xlabel("Backend Slots (p)", fontsize=12)
	# 		ax.set_ylabel("Speedup S(p)", fontsize=12)
	# 		ax.set_title(f"Speedup Comparison: {base_name}", fontsize=14)
	# 		ax.legend(fontsize=10)
	# 		ax.set_xscale("log", base=2)
	# 		ax.grid(True, alpha=0.3)
	# 		plt.tight_layout()
	# 		plt.savefig(speedup_dir / f"{base_name}-speedup-combined.png", dpi=150)
	# 		plt.close()

	# 	# Plot combined efficiency
	# 	fig, ax = plt.subplots(figsize=(10, 6))

	# 	for variant, data_dict in series_data.items():
	# 		ax.plot(
	# 			data_dict["p_values"],
	# 			data_dict["efficiency"],
	# 			marker=markers.get(variant, "o"),
	# 			linestyle="-",
	# 			label=labels.get(variant, variant),
	# 			linewidth=2,
	# 			markersize=8,
	# 			color=colors.get(variant, "gray"),
	# 		)

	# 	# Add ideal line
	# 	ideal_efficiency = np.ones_like(first_p_values, dtype=float)
	# 	ax.plot(
	# 		first_p_values,
	# 		ideal_efficiency,
	# 		"--",
	# 		label="Ideal (100%)",
	# 		linewidth=2,
	# 		alpha=0.7,
	# 		color="black",
	# 	)

	# 	ax.set_xlabel("Backend Slots (p)", fontsize=12)
	# 	ax.set_ylabel("Efficiency E(p)", fontsize=12)
	# 	ax.set_title(f"Efficiency Comparison: {base_name}", fontsize=14)
	# 	ax.legend(fontsize=10)
	# 	ax.set_xscale("log", base=2)
	# 	ax.set_ylim(0, 1.1)
	# 	ax.grid(True, alpha=0.3)
	# 	plt.tight_layout()
	# 	plt.savefig(efficiency_dir / f"{base_name}-efficiency-combined.png", dpi=150)
	# 	plt.close()

	# 	logger.info(f"✓ Combined plots saved for {base_name}")
