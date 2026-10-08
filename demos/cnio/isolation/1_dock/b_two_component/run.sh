#!/usr/bin/env bash
# Step 1b: dock_two_component, in isolation. See SUCCESS.md for what this proves.
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified/bin/activate

srun -A stride -p cpu -c 4 --mem=8G -t 00:10:00 python3 main.py
