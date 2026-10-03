#!/usr/bin/env bash
# Step 4: design_interface (ProteinMPNN), in isolation. See SUCCESS.md.
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a

srun -A stride -p cpu -c 4 --mem=8G -t 00:15:00 python3 main.py
