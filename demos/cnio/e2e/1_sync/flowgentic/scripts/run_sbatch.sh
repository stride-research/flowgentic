#!/usr/bin/env bash
# Track B launch script. Sibling of ../original/run_sbatch.sh (Track A) -- same partition, same
# config content (modulo out_dir), so the two runs are the actual comparison per DESIGN.md.
#SBATCH -A biostructure
#SBATCH -p biostructure-fortran
#SBATCH --qos=biostructure-priority-qos
#SBATCH --gres=gpu:rtx6000bb:1
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=8
#SBATCH -c 8
#SBATCH -t 00:30:00
#SBATCH -J cf_flowgentic
#SBATCH -o /home/javidsegura/software/isolation/1_sync/flowgentic/sbatch.out
#SBATCH -e /home/javidsegura/software/isolation/1_sync/flowgentic/sbatch.err

set -euo pipefail

FG_DIR="/home/javidsegura/software/isolation/1_sync/flowgentic"
CONFIG="$FG_DIR/config/config.json"

# Self-contained venv: no `module load`, so PYTHONPATH is never set. See
# context/current-implementation.md "UPDATE 2026-10-20" for why that matters.
source ~/.venvs/asyncflow/bin/activate
export PYTHONUNBUFFERED=1

# .env lives 3 parents up locally but the cluster deployment collapses that path, so it's
# pointed at explicitly rather than guessed. Missing file = no-op (Discord notifs just stay off).
export FLOWGENTIC_ENV_FILE="/home/javidsegura/software/isolation/.env"

# Dragon children must `import flowgentic` to deserialize their tasks (cloudpickle ships by
# reference), so it has to be on site-packages, not just sitting next to this script.
# --no-deps: flowgentic is stdlib-only; don't let pip's resolver touch anything else pinned.
FLOWGENTIC_PKG="/home/javidsegura/software/flowgentic"
python3 -m pip install --no-deps --quiet -e "$FLOWGENTIC_PKG" python-dotenv

OUT_DIR="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['out_dir'])" "$CONFIG")"
mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

# -s (single-node services only), not -w slurm -t tcp: the latter stands up Dragon's full
# multi-node overlay even for a one-node job, and that overlay has hung here before. Nothing
# in this pipeline is multi-node. No --mem: Dragon's /dev/shm pools are ~5GB and the node has
# 756GB to itself; capping it just gives the pools a ceiling to hit for no benefit.

START=$(date +%s)
dragon -s "$FG_DIR/src/main.py" "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
