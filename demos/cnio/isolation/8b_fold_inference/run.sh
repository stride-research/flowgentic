#!/usr/bin/env bash
# Step 8b: fold_inference, in isolation (real ColabFold, needs a GPU). See SUCCESS.md.
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a

srun -A stride -p gpu --gres=gpu:rtx6000ada:1 -c 4 --mem=16G -t 00:20:00 python3 main.py
