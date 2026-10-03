#!/usr/bin/env bash
# Step 3: select_top_docks, in isolation. See SUCCESS.md for what this proves.
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a

srun -A stride -p cpu -c 4 --mem=8G -t 00:10:00 python3 main.py
