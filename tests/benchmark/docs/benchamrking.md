# FlowGentic Scaling Benchmark

Minimalist benchmark suite for measuring FlowGentic strong and weak scaling per Matteo Turilli's specifications.

## Quick Start

```bash
# Install dependencies
pip install flowgentic radical-asyncflow matplotlib pydantic

# Run benchmark
cd tests/benchmark
python benchmark.py

# Results in tests/benchmark/results/
ls results/
```

## What It Measures

1. **Strong Scaling**: Fixed 1000 tasks, varying workers [1, 2, 4, 8, 16]
   - Noop tasks (W_work=0): Isolates FlowGentic overhead
   - Work tasks (W_work=0.01s): Full stack performance

2. **Weak Scaling**: 100 tasks per worker, scaling both
   - Noop: Reveals coordination overhead growth
   - Work: Shows production-like behavior

## Output

```
results/
├── benchmark_results.json           # Raw data (all runs)
├── strong_noop_makespan.png         # T(p) vs p
├── strong_noop_speedup.png          # S(p) vs p  
├── strong_noop_efficiency.png       # E(p) vs p (KEY METRIC)
├── strong_work_*.png                # Same plots with W_work>0
├── weak_noop_efficiency.png         # E_weak(p) vs p
└── weak_work_efficiency.png         # E_weak(p) with work
```

## Customization

Edit `benchmark.py` main():

```python
# Scale up for HPC
p_values = [1, 1024, 16384, 131072, 1048576]

# More tasks
n_fg_fixed = 10000
k_per_worker = 1000

# Real work (e.g., 100ms compute)
w_work_seconds = 0.1
```

## Key Metrics

- **E_strong(p) = T(1) / (p·T(p))**: Should be ~1.0 for perfect scaling
- **E_weak(p) = T(1) / T(p)**: Should stay constant as both work and p grow
- **Ideal**: Horizontal line at E=1.0 on efficiency plots
- **Reality**: E decays due to coordination overhead (Amdahl's law)

## Interpretation

**Strong Efficiency Plot (most important):**
- Steep drop → high overhead
- Levels off at E>0.5 → acceptable
- E→0 → serial bottleneck

**Weak Efficiency Plot:**
- Flat line → excellent scalability
- Linear decay → overhead grows with p
- Check noop vs work gap → relative overhead

## Technical Details

See `BENCHMARK_REPORT.md` for complete analysis:
- Mathematical foundations
- Implementation details
- Expected results
- Troubleshooting guide

## Requirements Met

✅ Strong scaling: E = T(1)/(p·T(p))  
✅ Weak scaling: E = T(1)/T(p)  
✅ Noop backend (W_work=0)  
✅ Real work (W_work>0)  
✅ Makespan measurement  
✅ 3 plots per strong scaling experiment  
✅ Weak scaling efficiency plots  
✅ Ideal reference lines  
✅ JSON export for reproducibility

## Minimal by Design

- No complex graphs (single task type)
- No LLM calls (pure overhead test)
- No distributed execution (ThreadPoolExecutor only)
- Clean separation of concerns (strong/weak/noop/work)

Extend as needed for your workload!