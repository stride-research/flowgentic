"""
Post-benchmark analysis: Generate summary statistics and insights.
Run after benchmark.py completes.
"""

import json
from pathlib import Path
from typing import Dict, List
import sys


def load_results(results_dir: Path) -> Dict:
	"""Load benchmark results from JSON"""
	# JSON is stored under tests/benchmark/results/data/
	results_file = results_dir / "data" / "benchmark_results.json"
	if not results_file.exists():
		print(f"❌ No results found at {results_file}")
		print("Run benchmark.py first!")
		sys.exit(1)

	with open(results_file) as f:
		return json.load(f)


def analyze_strong_scaling(results: List[Dict], name: str):
	"""Analyze strong scaling results"""
	print(f"\n{'=' * 70}")
	print(f"STRONG SCALING - {name.upper()}")
	print(f"{'=' * 70}")

	t_baseline = results[0]["t_fg"]
	n_fg = results[0]["n_fg"]
	w_work = results[0]["w_work"]

	print(f"\nConfiguration:")
	print(f"  Fixed tasks (N_FG): {n_fg}")
	print(f"  Work per task (W_work): {w_work:.3f}s")
	print(f"  Baseline time T(1): {t_baseline:.3f}s")

	print(
		f"\n{'p':>8} {'T(p)':>10} {'Speedup':>10} {'Efficiency':>12} {'vs Ideal':>12}"
	)
	print(f"{'-' * 8} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 12}")

	for r in results:
		p = r["p"]
		t_p = r["t_fg"]
		speedup = t_baseline / t_p
		efficiency = speedup / p
		ideal_efficiency_gap = abs(1.0 - efficiency)

		print(
			f"{p:8d} {t_p:10.3f} {speedup:10.2f}x {efficiency:11.3f} {ideal_efficiency_gap:11.3f}"
		)

	# Calculate scaling quality
	final = results[-1]
	final_eff = (t_baseline / final["t_fg"]) / final["p"]

	print(f"\nScaling Quality at p={final['p']}:")
	if final_eff > 0.8:
		quality = "EXCELLENT"
	elif final_eff > 0.5:
		quality = "GOOD"
	elif final_eff > 0.3:
		quality = "ACCEPTABLE"
	else:
		quality = "POOR"

	print(f"  Final efficiency: {final_eff:.3f}")
	print(f"  Quality: {quality}")

	# Amdahl's law estimate
	if len(results) > 1:
		# Estimate serial fraction from p=2 data
		p2 = results[1]["p"]
		e2 = (t_baseline / results[1]["t_fg"]) / p2
		# E(p) ≈ 1/(s + (1-s)/p) → s ≈ 1 - 1/(p*E - E + 1)
		serial_fraction = max(0, 1 - 1 / (p2 * e2 - e2 + 1))
		max_speedup = 1 / serial_fraction if serial_fraction > 0 else float("inf")

		print(f"\nAmdahl's Law Analysis:")
		print(f"  Estimated serial fraction: {serial_fraction:.1%}")
		if max_speedup < 1000:
			print(f"  Theoretical max speedup: {max_speedup:.1f}x")
		else:
			print(f"  Theoretical max speedup: >1000x (negligible serial work)")


def analyze_weak_scaling(results: List[Dict], name: str):
	"""Analyze weak scaling results"""
	print(f"\n{'=' * 70}")
	print(f"WEAK SCALING - {name.upper()}")
	print(f"{'=' * 70}")

	t_baseline = results[0]["t_fg"]
	w_work = results[0]["w_work"]
	k = results[0]["n_fg"] // results[0]["p"]  # tasks per worker

	print(f"\nConfiguration:")
	print(f"  Tasks per worker (k): {k}")
	print(f"  Work per task (W_work): {w_work:.3f}s")
	print(f"  Baseline time T(1,{k}): {t_baseline:.3f}s")

	print(f"\n{'p':>8} {'N_FG':>10} {'T(p,N)':>10} {'E_weak':>12} {'vs Ideal':>12}")
	print(f"{'-' * 8} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 12}")

	for r in results:
		p = r["p"]
		n_fg = r["n_fg"]
		t_p = r["t_fg"]
		e_weak = t_baseline / t_p
		ideal_gap = abs(1.0 - e_weak)

		print(f"{p:8d} {n_fg:10d} {t_p:10.3f} {e_weak:11.3f} {ideal_gap:11.3f}")

	# Analyze overhead growth
	final = results[-1]
	final_eff = t_baseline / final["t_fg"]
	overhead_growth = final["t_fg"] / t_baseline

	print(f"\nOverhead Growth at p={final['p']}:")
	print(f"  T({final['p']}) / T(1) = {overhead_growth:.2f}x")
	print(f"  Efficiency: {final_eff:.3f}")

	if final_eff > 0.9:
		quality = "EXCELLENT (overhead < 10%)"
	elif final_eff > 0.7:
		quality = "GOOD (overhead < 30%)"
	elif final_eff > 0.5:
		quality = "ACCEPTABLE (overhead < 50%)"
	else:
		quality = "POOR (overhead > 50%)"

	print(f"  Quality: {quality}")

	# Estimate overhead growth rate
	if len(results) > 1:
		# Fit T(p) ≈ T(1) * (1 + α*p^β)
		# Simple approximation: check if linear or worse
		import math

		p_ratio = results[-1]["p"] / results[0]["p"]
		t_ratio = results[-1]["t_fg"] / results[0]["t_fg"]

		if t_ratio < 1.1:
			growth_type = "Nearly constant (excellent!)"
		elif t_ratio < p_ratio**0.5:
			growth_type = "Sub-linear (good)"
		elif t_ratio < p_ratio:
			growth_type = "Linear (acceptable)"
		else:
			growth_type = "Super-linear (concerning)"

		print(f"\nOverhead Growth Pattern:")
		print(f"  {growth_type}")


def compare_noop_vs_work(strong_noop: List[Dict], strong_work: List[Dict]):
	"""Compare noop vs work to quantify relative overhead"""
	print(f"\n{'=' * 70}")
	print(f"OVERHEAD ANALYSIS: Noop vs Work")
	print(f"{'=' * 70}")

	print(
		f"\n{'p':>8} {'T_noop':>10} {'T_work':>10} {'Overhead%':>12} {'Interpretation':>20}"
	)
	print(f"{'-' * 8} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 20}")

	for noop, work in zip(strong_noop, strong_work):
		p = noop["p"]
		t_noop = noop["t_fg"]
		t_work = work["t_fg"]
		overhead_pct = (t_noop / t_work) * 100

		if overhead_pct > 50:
			interp = "Overhead dominates"
		elif overhead_pct > 20:
			interp = "Significant overhead"
		elif overhead_pct > 5:
			interp = "Moderate overhead"
		else:
			interp = "Work dominates"

		print(
			f"{p:8d} {t_noop:10.3f} {t_work:10.3f} {overhead_pct:11.1f}% {interp:>20}"
		)

	print(f"\nKey Insight:")
	print(f"  If overhead% is high (>20%), FlowGentic wrapper cost is significant.")
	print(
		f"  If overhead% is low (<5%), actual work dominates and scaling will be better."
	)


def generate_summary_report(results: Dict):
	"""Generate executive summary"""
	print(f"\n{'#' * 70}")
	print(f"{'FLOWGENTIC BENCHMARK - EXECUTIVE SUMMARY':^70}")
	print(f"{'#' * 70}")

	# Strong scaling summaries
	for exp_type in ["strong_noop", "strong_work"]:
		if exp_type in results:
			data = results[exp_type]
			max_p = data[-1]["p"]
			t_1 = data[0]["t_fg"]
			t_max = data[-1]["t_fg"]
			speedup = t_1 / t_max
			efficiency = speedup / max_p

			print(f"\n{exp_type.replace('_', ' ').title()}:")
			print(f"  Max parallelism tested: p={max_p}")
			print(f"  Speedup achieved: {speedup:.2f}x (ideal: {max_p}x)")
			print(f"  Final efficiency: {efficiency:.3f} (ideal: 1.000)")

	# Weak scaling summaries
	for exp_type in ["weak_noop", "weak_work"]:
		if exp_type in results:
			data = results[exp_type]
			max_p = data[-1]["p"]
			t_1 = data[0]["t_fg"]
			t_max = data[-1]["t_fg"]
			efficiency = t_1 / t_max

			print(f"\n{exp_type.replace('_', ' ').title()}:")
			print(f"  Max parallelism tested: p={max_p}")
			print(f"  Time growth: {t_max / t_1:.2f}x (ideal: 1.00x)")
			print(f"  Final efficiency: {efficiency:.3f} (ideal: 1.000)")

	print(f"\n{'=' * 70}")
	print(f"RECOMMENDATION:")
	print(f"{'=' * 70}")

	# Determine overall scaling quality
	if "strong_work" in results:
		strong_eff = (
			results["strong_work"][0]["t_fg"] / results["strong_work"][-1]["t_fg"]
		) / results["strong_work"][-1]["p"]

		if strong_eff > 0.7:
			print("✅ FlowGentic shows GOOD strong scaling for production workloads.")
			print("   Overhead is manageable relative to actual work.")
		elif strong_eff > 0.4:
			print("⚠️  FlowGentic shows ACCEPTABLE strong scaling.")
			print("   Consider optimizing wrapper overhead for better performance.")
		else:
			print("❌ FlowGentic shows POOR strong scaling.")
			print("   Significant bottlenecks in coordination/serialization.")
			print("   Profiling recommended to identify hotspots.")

	print(f"\nNext Steps:")
	print(f"  1. Review efficiency plots in results/ directory")
	print(f"  2. If overhead is high, profile with cProfile or py-spy")
	print(f"  3. Scale up to p=1024+ on HPC cluster")
	print(f"  4. Test with realistic workloads (LLM inference, simulation)")


def main():
	results_dir = Path("tests/benchmark/results")
	results = load_results(results_dir)

	# Analyze each experiment
	if "strong_noop" in results:
		analyze_strong_scaling(results["strong_noop"], "noop")

	if "strong_work" in results:
		analyze_strong_scaling(results["strong_work"], "work")

	if "weak_noop" in results:
		analyze_weak_scaling(results["weak_noop"], "noop")

	if "weak_work" in results:
		analyze_weak_scaling(results["weak_work"], "work")

	# Comparative analysis
	if "strong_noop" in results and "strong_work" in results:
		compare_noop_vs_work(results["strong_noop"], results["strong_work"])

	# Executive summary
	generate_summary_report(results)

	print(f"\n{'=' * 70}")
	print(f"Analysis complete! Check plots in {results_dir}")
	print(f"{'=' * 70}\n")


if __name__ == "__main__":
	main()
