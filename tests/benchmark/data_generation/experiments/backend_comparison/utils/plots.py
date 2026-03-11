import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np

from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter

logger = logging.getLogger(__name__)


ENGINE_COLORS = {
    "asyncflow": "#2196F3",
    "parsl": "#FF9800",
}

ENGINE_LABELS = {
    "asyncflow": "AsyncFlow",
    "parsl": "Parsl",
}

_SET2 = plt.cm.Set2.colors
COMP_COLORS = {
    "D_wrap": _SET2[0],
    "D_overhead": _SET2[1],
    "D_backend": _SET2[2],
}
COMP_LABELS = {
    "D_wrap": "D_wrap (amortized)",
    "D_overhead": "D_overhead (D_resolve + D_collect)",
    "D_backend": "D_backend",
}


def _extract_new_durations(events: List[Dict]) -> Dict:
	"""Extract durations from the updated event model"""
	wrap_starts: Dict = {}
	wrap_ends: Dict = {}
	inv: Dict = {}

	for e in events:
		etype = e["event"]
		if etype == "tool_wrap_start":
			wrap_starts[(e["tool_name"], e["wrap_id"])] = e["ts"]
		elif etype == "tool_wrap_end":
			wrap_ends[(e["tool_name"], e["wrap_id"])] = e["ts"]
		elif etype in ("tool_invoke_start", "tool_resolve_end",
					   "tool_collect_start", "tool_invoke_end"):
			iid = e["invocation_id"]
			if iid not in inv:
				inv[iid] = {}
			inv[iid][etype] = e["ts"]
			if "tool_name" in e:
				inv[iid]["tool_name"] = e["tool_name"]

	# D_wrap per tool name
	d_wrap_by_tool: Dict[str, List[float]] = {}
	for (tool_name, wrap_id), ts_start in wrap_starts.items():
		if (tool_name, wrap_id) in wrap_ends:
			d_wrap_by_tool.setdefault(tool_name, []).append(
				wrap_ends[(tool_name, wrap_id)] - ts_start
			)

	# Per-invocation durations
	d_resolve, d_backend, d_collect, d_total = [], [], [], []
	inv_by_tool: Dict[str, int] = {}
	required = ("tool_invoke_start", "tool_resolve_end",
				 "tool_collect_start", "tool_invoke_end")
	for idata in inv.values():
		if all(k in idata for k in required):
			s = idata["tool_invoke_start"]
			r = idata["tool_resolve_end"]
			c = idata["tool_collect_start"]
			end = idata["tool_invoke_end"]
			d_resolve.append(r - s)
			d_backend.append(c - r)
			d_collect.append(end - c)
			d_total.append(end - s)
			if "tool_name" in idata:
				tn = idata["tool_name"]
				inv_by_tool[tn] = inv_by_tool.get(tn, 0) + 1

	return {
		"d_wrap_by_tool": d_wrap_by_tool,
		"d_wrap": [v for vals in d_wrap_by_tool.values() for v in vals],
		"d_resolve": d_resolve,
		"d_backend": d_backend,
		"d_collect": d_collect,
		"d_total": d_total,
		"inv_by_tool": inv_by_tool,
	}


def _amortized_wrap_ms(d_wrap_by_tool: Dict, inv_by_tool: Dict, total_inv: int) -> float:
	"""D_wrap amortized per invocation (ms)"""
	total_wrap = sum(
		sum(d_wrap_by_tool.get(tool, []))
		for tool, n in inv_by_tool.items()
		if n > 0
	)
	return (total_wrap / total_inv * 1000) if total_inv > 0 else 0.0


class BackendComparisonPlotter(BasePlotter):
	"""Generates comparison plots for backend-adaptive execution experiment."""

	def __init__(self, plots_dir: Optional[Path] = None) -> None:
		super().__init__()
		self.plots_dir = plots_dir

	def plot_results(self, data: Dict[Any, Any]) -> None:
		engines = list(data.keys())
		if not engines:
			logger.warning("No data to plot.")
			return

		sample = data[engines[0]]
		self._subtitle = (
			f"({sample['n_of_agents']} agents, "
			f"{sample['n_of_tool_calls_per_agent']} tool calls/agent, "
			f"{sample['n_of_backend_slots']} slots)"
		)

		self._plot_makespan(data, engines, "makespan")
		self._plot_invocation_breakdown(data, engines, "overhead")
		self._plot_invocation_proportional(data, engines, "overhead")
		self._plot_results_table(data, engines)
		self._plot_overhead_vs_backend(data, engines, "overhead")
		self._plot_overhead_vs_backend_proportional(data, engines, "overhead")


	def _plot_makespan(self, data: Dict, engines: List[str], subdir: str) -> None:
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

	def _plot_invocation_breakdown(self, data: Dict, engines: List[str], subdir: str) -> None:
		"""Stacked bar: D_wrap (amortized) + D_overhead + D_backend per engine.Absolute mean time per invocation (ms)"""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		x = np.arange(len(engines))
		width = 0.5
		components = ["D_wrap", "D_overhead", "D_backend"]
		values: Dict[str, List[float]] = {c: [] for c in components}

		for e in engines:
			d = _extract_new_durations(data[e]["events"])
			n_inv = len(d["d_resolve"]) or 1
			d_overhead = [r + c for r, c in zip(d["d_resolve"], d["d_collect"])]
			values["D_wrap"].append(_amortized_wrap_ms(d["d_wrap_by_tool"], d["inv_by_tool"], n_inv))
			values["D_overhead"].append(np.mean(d_overhead) * 1000 if d_overhead else 0.0)
			values["D_backend"].append(np.mean(d["d_backend"]) * 1000 if d["d_backend"] else 0.0)

		bottom = np.zeros(len(engines))
		for comp in components:
			vals = np.array(values[comp])
			ax.bar(x, vals, width, label=COMP_LABELS[comp], bottom=bottom, color=COMP_COLORS[comp])
			bottom += vals

		for i, total in enumerate(bottom):
			ax.text(x[i], total, f"{total:.3f}ms", ha="center", va="bottom", fontsize=10)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Mean Time per Invocation (ms)", fontsize=12)
		ax.set_title(f"Invocation Breakdown (incl. D_wrap amortized)\n{self._subtitle}", fontsize=14)
		ax.set_xticks(x)
		ax.set_xticklabels(labels)
		ax.legend(loc="best")
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "invocation_breakdown.png", subdir)
		plt.close(fig)

	def _plot_invocation_proportional(self, data: Dict, engines: List[str], subdir: str) -> None:
		"""Stacked bar: D_wrap (amortized) + D_overhead + D_backend normalized to 100%"""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		x = np.arange(len(engines))
		width = 0.5
		components = ["D_wrap", "D_overhead", "D_backend"]
		raw: Dict[str, List[float]] = {c: [] for c in components}

		for e in engines:
			d = _extract_new_durations(data[e]["events"])
			n_inv = len(d["d_resolve"]) or 1
			d_overhead = [r + c for r, c in zip(d["d_resolve"], d["d_collect"])]
			raw["D_wrap"].append(_amortized_wrap_ms(d["d_wrap_by_tool"], d["inv_by_tool"], n_inv))
			raw["D_overhead"].append(np.mean(d_overhead) * 1000 if d_overhead else 0.0)
			raw["D_backend"].append(np.mean(d["d_backend"]) * 1000 if d["d_backend"] else 0.0)

		totals = [sum(raw[c][i] for c in components) for i in range(len(engines))]
		values = {
			c: [raw[c][i] / totals[i] * 100 if totals[i] > 0 else 0 for i in range(len(engines))]
			for c in components
		}

		bottom = np.zeros(len(engines))
		for comp in components:
			vals = np.array(values[comp])
			ax.bar(x, vals, width, label=COMP_LABELS[comp], bottom=bottom, color=COMP_COLORS[comp])
			for i, (v, b) in enumerate(zip(vals, bottom)):
				if v > 5:
					ax.text(x[i], b + v / 2, f"{v:.1f}%", ha="center", va="center", fontsize=9)
			bottom += vals

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("% of Effective Invocation Cost", fontsize=12)
		ax.set_title(f"Invocation Breakdown Proportional\n{self._subtitle}", fontsize=14)
		ax.set_xticks(x)
		ax.set_xticklabels(labels)
		ax.legend(loc="best")
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(0, 100)

		plt.tight_layout()
		self._save_plot(fig, "invocation_proportional.png", subdir)
		plt.close(fig)
	
	
	def _plot_overhead_vs_backend(self, data: Dict, engines: List[str], subdir: str) -> None:
		"""Stacked bar: D_overhead + D_backend per engine. No D_wrap. Mean time per invocation (ms)"""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		x = np.arange(len(engines))
		width = 0.5
		components = ["D_overhead", "D_backend"]
		values: Dict[str, List[float]] = {c: [] for c in components}

		for e in engines:
			d = _extract_new_durations(data[e]["events"])
			d_overhead = [r + c for r, c in zip(d["d_resolve"], d["d_collect"])]
			values["D_overhead"].append(sum(d_overhead) * 1000 if d_overhead else 0.0)
			values["D_backend"].append(sum(d["d_backend"]) * 1000 if d["d_backend"] else 0.0)

		bottom = np.zeros(len(engines))
		for comp in components:
			vals = np.array(values[comp])
			ax.bar(x, vals, width, label=COMP_LABELS[comp], bottom=bottom, color=COMP_COLORS[comp])
			bottom += vals

		for i, total in enumerate(bottom):
			ax.text(x[i], total, f"{total:.3f}ms", ha="center", va="bottom", fontsize=10)

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("Total Time (ms)", fontsize=12)
		ax.set_title(f"Overhead vs Backend Total (= D_total)\n{self._subtitle}", fontsize=14)
		ax.set_xticks(x)
		ax.set_xticklabels(labels)
		ax.legend(loc="best")
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(bottom=0)

		plt.tight_layout()
		self._save_plot(fig, "overhead_vs_backend.png", subdir)
		plt.close(fig)

	def _plot_overhead_vs_backend_proportional(self, data: Dict, engines: List[str], subdir: str) -> None:
		"""Stacked bar: D_overhead + D_backend normalized to 100%. No D_wrap."""
		fig, ax = plt.subplots(figsize=(8, 6))

		labels = [ENGINE_LABELS.get(e, e) for e in engines]
		x = np.arange(len(engines))
		width = 0.5
		components = ["D_overhead", "D_backend"]
		raw: Dict[str, List[float]] = {c: [] for c in components}

		for e in engines:
			d = _extract_new_durations(data[e]["events"])
			d_overhead = [r + c for r, c in zip(d["d_resolve"], d["d_collect"])]
			raw["D_overhead"].append(sum(d_overhead) * 1000 if d_overhead else 0.0)
			raw["D_backend"].append(sum(d["d_backend"]) * 1000 if d["d_backend"] else 0.0)

		totals = [sum(raw[c][i] for c in components) for i in range(len(engines))]
		values = {
			c: [raw[c][i] / totals[i] * 100 if totals[i] > 0 else 0 for i in range(len(engines))]
			for c in components
		}

		bottom = np.zeros(len(engines))
		for comp in components:
			vals = np.array(values[comp])
			ax.bar(x, vals, width, label=COMP_LABELS[comp], bottom=bottom, color=COMP_COLORS[comp])
			for i, (v, b) in enumerate(zip(vals, bottom)):
				if v > 5:
					ax.text(x[i], b + v / 2, f"{v:.1f}%", ha="center", va="center", fontsize=9)
			bottom += vals

		ax.set_xlabel("Backend Engine", fontsize=12)
		ax.set_ylabel("% of Total D_total", fontsize=12)
		ax.set_title(f"Overhead vs Backend Proportional\n{self._subtitle}", fontsize=14)
		ax.set_xticks(x)
		ax.set_xticklabels(labels)
		ax.legend(loc="best")
		ax.grid(True, alpha=0.3, axis="y")
		ax.set_ylim(0, 100)

		plt.tight_layout()
		self._save_plot(fig, "overhead_vs_backend_proportional.png", subdir)
		plt.close(fig)

	def _plot_results_table(self, data: Dict, engines: List[str]) -> None:
		"""Summary table with totals and per-invocation means, grouped by section."""
		fig, ax = plt.subplots(figsize=(12, 7))
		ax.axis("off")

		stats: Dict[str, Dict] = {}
		for e in engines:
			d = _extract_new_durations(data[e]["events"])
			n_inv = len(d["d_resolve"]) or 1
			d_overhead_per_inv = [r + c for r, c in zip(d["d_resolve"], d["d_collect"])]
			overhead_fractions = [
				(r + c) / t
				for r, c, t in zip(d["d_resolve"], d["d_collect"], d["d_total"])
				if t > 0
			]
			stats[e] = {
				"makespan": data[e]["total_makespan"],
				"n_inv": n_inv,
				# totals
				"d_total_total": sum(d["d_total"]) * 1000,
				"d_backend_total": sum(d["d_backend"]) * 1000,
				"d_overhead_total": sum(d_overhead_per_inv) * 1000,
				"d_wrap_total": sum(d["d_wrap"]) * 1000,
				# per-invocation means
				"d_total_mean": np.mean(d["d_total"]) * 1000 if d["d_total"] else 0.0,
				"d_backend_mean": np.mean(d["d_backend"]) * 1000 if d["d_backend"] else 0.0,
				"d_overhead_mean": np.mean(d_overhead_per_inv) * 1000 if d_overhead_per_inv else 0.0,
				"d_wrap_amortized": _amortized_wrap_ms(d["d_wrap_by_tool"], d["inv_by_tool"], n_inv),
				"overhead_fraction": np.mean(overhead_fractions) if overhead_fractions else 0.0,
			}

		def _fmt(v: float) -> str:
			return f"{v:.3f}"

		SECTION = True
		DATA = False
		row_defs = [
			("Overview", None, SECTION),
			("Makespan (s)", lambda e: f"{stats[e]['makespan']:.3f}", DATA),
			("N invocations", lambda e: str(stats[e]["n_inv"]), DATA),
			("Totals (sum across all invocations)", None, SECTION),
			("D_total total (ms)", lambda e: _fmt(stats[e]["d_total_total"]), DATA),
			("D_backend total (ms)", lambda e: _fmt(stats[e]["d_backend_total"]), DATA),
			("D_overhead total (ms)", lambda e: _fmt(stats[e]["d_overhead_total"]), DATA),
			("D_wrap total (ms)", lambda e: _fmt(stats[e]["d_wrap_total"]), DATA),
			("Per Invocation (mean)", None, SECTION),
			("D_total mean (ms)", lambda e: _fmt(stats[e]["d_total_mean"]), DATA),
			("D_backend mean (ms)", lambda e: _fmt(stats[e]["d_backend_mean"]), DATA),
			("D_overhead mean (ms)", lambda e: _fmt(stats[e]["d_overhead_mean"]), DATA),
			("D_wrap amortized (ms)", lambda e: _fmt(stats[e]["d_wrap_amortized"]), DATA),
			("Overhead fraction", lambda e: f"{stats[e]['overhead_fraction']:.4f}", DATA),
		]

		col_labels = [ENGINE_LABELS.get(e, e) for e in engines]
		rows = [
			[label] + ([""] * len(engines) if fn is None else [fn(e) for e in engines])
			for label, fn, _ in row_defs
		]

		table = ax.table(
			cellText=rows,
			colLabels=["Metric"] + col_labels,
			loc="center",
			cellLoc="center",
		)
		table.auto_set_font_size(False)
		table.set_fontsize(10)
		table.scale(1.5, 1.9)

		# Header row
		for j in range(len(engines) + 1):
			cell = table[0, j]
			cell.set_facecolor("#2C3E50")
			cell.set_text_props(color="white", fontweight="bold")

		# Data rows
		data_row_idx = 0
		for i in range(len(row_defs)):
			is_section = row_defs[i][2]
			row_i = i + 1  
			if is_section:
				for j in range(len(engines) + 1):
					cell = table[row_i, j]
					cell.set_facecolor("#4A6FA5")
					cell.set_text_props(color="white", fontweight="bold")
					cell.set_edgecolor("#2C3E50")
			else:
				color = "#F2F2F2" if data_row_idx % 2 == 0 else "#FFFFFF"
				for j in range(len(engines) + 1):
					table[row_i, j].set_facecolor(color)
					table[row_i, j].set_edgecolor("#CCCCCC")
				data_row_idx += 1

		ax.set_title(f"Results Summary\n{self._subtitle}", fontsize=14, pad=20)
		plt.tight_layout()
		self._save_plot(fig, "results_table.png", None)
		plt.close(fig)

	def _save_plot(self, fig: plt.Figure, filename: str, subdirectory: Optional[str] = None) -> None:
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
