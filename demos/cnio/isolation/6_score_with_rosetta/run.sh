#!/usr/bin/env bash
# Step 6: score_with_rosetta, in isolation (real PyRosetta). See SUCCESS.md.
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified/bin/activate

# Chains dock->design->score before Rosetta even starts, then Rosetta
# scores each qualifying sequence at ~134s -- generous wall time.
srun -A stride -p cpu -c 4 --mem=8G -t 00:20:00 python3 main.py
