#!/bin/bash
#SBATCH --job-name=flowgentic-dragon-first-debug
#SBATCH --account=bebo-delta-cpu
#SBATCH --partition=cpu
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16g
#SBATCH --time=00:15:00
#SBATCH --output=tests/benchmark/logs/debug_%j.out
#SBATCH --error=tests/benchmark/logs/debug_%j.err

mkdir -p tests/benchmark/logs

echo "=== Starting on $(hostname) at $(date) ==="

# ── Activate env ──────────────────────────────────────
source .venv/bin/activate
echo "Python: $(python --version)"

# ── Smallest possible config ──────────────────────────
cat > tests/benchmark/config.yml << EOF
run_name: "dragon-debug-$(date +%Y%m%d_%H%M%S)"
run_description: "Minimal Dragon validation: 4 agents x 1 tool, noop"
environment:
  n_of_agents: 4
  n_of_tool_calls_per_agent: 1
  n_of_backend_slots: 1
  tool_execution_duration_time: 0
workload_id: "langgraph_asyncflow"
EOF

echo "=== Config written ==="
cat tests/benchmark/config.yml

echo "=== Launching with Dragon ==="
dragon tests/benchmark/data_generation/run_experiments.py

echo "=== Done at $(date) ==="