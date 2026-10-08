#!/usr/bin/env bash
#SBATCH -A stride
#SBATCH -p gpu
#SBATCH --gres=gpu:rtx6000ada:1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 01:00:00
#SBATCH -J cf_reference
#SBATCH -o /home/javidsegura/software/isolation/1_sync/reference/sbatch.out
#SBATCH -e /home/javidsegura/software/isolation/1_sync/reference/sbatch.err
#
# sbatch version of run.sh: fully detached from any SSH session, survives a
# dropped connection or a local wrapper timing out. See run.sh for why each
# piece here is the way it is -- this is the identical body, just submitted
# as a batch job instead of held open via an interactive srun.
set -euo pipefail
# NOT "cd $(dirname "$0")" -- sbatch copies the submitted script into a
# SLURM spool dir (/var/spool/slurmd/job<id>/) and runs it FROM THERE, so
# $0/pwd resolve to the spool dir, not this file's real location. Confirmed
# the hard way: the CPU sibling of this script (job 22808) crashed instantly
# with a FileNotFoundError for config_cpu.json because of exactly this.
# Hardcoded absolute path instead.
REF_DIR="/home/javidsegura/software/isolation/1_sync/reference"
CONFIG="$REF_DIR/config.json"

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
