#!/usr/bin/env bash
# Step 7: fold_oligomer, in isolation (real ColabFold, needs a GPU). See SUCCESS.md.
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified/bin/activate

# Chains the WHOLE pipeline (dock -> ... -> rosetta -> fold) for real, so
# generous wall time: Rosetta scoring alone is ~134s/sequence.
srun -A stride -p gpu --gres=gpu:rtx6000ada:1 -c 4 --mem=16G -t 00:30:00 python3 main.py
