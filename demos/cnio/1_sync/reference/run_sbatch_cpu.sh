#!/usr/bin/env bash
#SBATCH -A stride
#SBATCH -p cpu
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:30:00
#SBATCH -J cf_reference_cpu
#SBATCH -o /home/javidsegura/software/isolation/1_sync/reference/sbatch_cpu.out
#SBATCH -e /home/javidsegura/software/isolation/1_sync/reference/sbatch_cpu.err
#
# CPU-only reference run: fold_oligomers=false, so this never needs a GPU and
# is never queued behind biostructure-priority on haskell. Gets real dock,
# geometry, select, design, score, and -- critically -- real Rosetta timing,
# while the full GPU run (run_sbatch.sh, job 22806) stays queued separately.
set -euo pipefail
# NOT "cd $(dirname "$0")" -- sbatch copies the submitted script into a
# SLURM spool dir (/var/spool/slurmd/job<id>/) and runs it FROM THERE, so
# $0/pwd resolve to the spool dir, not this file's real location. Confirmed
# the hard way: job 22808 crashed instantly with a FileNotFoundError for
# config_cpu.json because of exactly this. Hardcoded absolute path instead.
REF_DIR="/home/javidsegura/software/isolation/1_sync/reference"
CONFIG="$REF_DIR/config_cpu.json"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified/bin/activate

OUT_DIR="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['out_dir'])" "$CONFIG")"
mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

cd ~/software/CageFlow

START=$(date +%s)
python3 CageFlow.py "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
