#!/usr/bin/env bash
#SBATCH -A biostructure
#SBATCH -p biostructure-fortran
#SBATCH --qos=biostructure-priority-qos
#SBATCH --gres=gpu:rtx6000bb:1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 01:00:00
#SBATCH -J cf_original
#SBATCH -o /home/javidsegura/software/isolation/1_sync/original/sbatch.out
#SBATCH -e /home/javidsegura/software/isolation/1_sync/original/sbatch.err

set -euo pipefail

REF_DIR="/home/javidsegura/software/isolation/1_sync/original"
CONFIG="$REF_DIR/config.json"

# Self-contained venv: no `module load`, so PYTHONPATH is never set. See
# context/current-implementation.md "UPDATE 2026-10-20" for why that matters.
source ~/.venvs/asyncflow/bin/activate

OUT_DIR="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['out_dir'])" "$CONFIG")"
mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

START=$(date +%s)
python3 "$REF_DIR/run_original.py" "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
