#!/usr/bin/env bash
# The reference run: HER CLI entry point, unmodified, nothing of ours in the
# execution path. See SUCCESS.md.

set -euo pipefail
cd "$(dirname "$0")"

CONFIG="$(pwd)/config.json"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified/bin/activate

# Provenance: she does not copy the config into out_dir, and run_cage_flow()
# starts by mkdir -p'ing it, so the collector has no way to know afterwards
# which config produced a run. Copy it in ourselves, named distinctly from any
# config*.json of hers so _load_config_or_exit's listing stays clean.
OUT_DIR="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['out_dir'])" "$CONFIG")"
mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

# Must run from her repo root: CageFlow.py resolves configs/ and the fold
# helper (colabfold_msa.py) relative to its own __file__, and _load_config_or_exit
# lists configs/config*.json from there on a bad path.
cd ~/software/CageFlow

# -p gpu + rtx6000ada because fold_oligomers=true. Pinned to that GRES
# deliberately: a bare --gres=gpu:1 can land on `julia`, whose Blackwell card
# the installed fold stack cannot use (see context/ie-cluster-scitech.md).
START=$(date +%s)
srun -A stride -p gpu --gres=gpu:rtx6000ada:1 -c 8 --mem=32G -t 01:00:00 \
  python3 CageFlow.py "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
