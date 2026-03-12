#!/bin/bash
#SBATCH --job-name=flowgentic-N8192-p2048
#SBATCH --account=bebo-delta-cpu
#SBATCH --partition=cpu
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=128
#SBATCH --time=04:00:00
#SBATCH --output=tests/benchmark/logs/N8192_p2048_%j.out
#SBATCH --error=tests/benchmark/logs/N8192_p2048_%j.err

set -euo pipefail

cd "$SLURM_SUBMIT_DIR" # explicity set dir to submission dir
mkdir -p tests/benchmark/logs

echo "=== Starting on $(hostname) at $(date) ==="
echo "=== Working directory: $(pwd) ==="
echo "=== SLURM_JOB_ID=$SLURM_JOB_ID  SLURM_NNODES=$SLURM_NNODES ==="

# ── Activate venv (already installed on shared fs) ────
source .venv/bin/activate
echo "Python: $(python --version)"
echo "Dragon: $(which dragon)"

# ── Load .env (Discord webhook etc.) ──────────────────
set -a
source tests/benchmark/.env
set +a
echo "DISCORD_WEBHOOK is ${DISCORD_WEBHOOK:+set}"

# ── Smallest possible config ──────────────────────────
cat > tests/benchmark/config.yml << EOF
run_name: "delta-N8192-k64-p2048-$(date +%Y%m%d_%H%M%S)"
run_description: "Full run: 128 agents x 64 tools = 8192 total, p=2048"
environment:
  n_of_agents: 128
  n_of_tool_calls_per_agent: 64
  n_of_backend_slots: 11
  tool_execution_duration_time: 3
workload_id: "langgraph_asyncflow"
EOF

echo "=== Config written ==="
cat tests/benchmark/config.yml

echo "=== Launching with Dragon ==="
dragon tests/benchmark/data_generation/run_experiments.py

echo "=== Done at $(date) ==="
