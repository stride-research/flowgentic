#!/usr/bin/env bash
# Step 2: evaluate_geometry, in isolation. See SUCCESS.md for what this proves.
set -euo pipefail
cd "$(dirname "$0")"

module load SciPy-bundle/2023.11-gfbf-2023b

srun -A stride -p cpu -c 4 --mem=8G -t 00:10:00 python3 main.py
