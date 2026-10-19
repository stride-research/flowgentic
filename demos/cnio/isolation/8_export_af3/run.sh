#!/usr/bin/env bash
# Step 8: export_af3_jobs, in isolation. See SUCCESS.md for what this proves.
source /etc/profile.d/z01_eessi.sh
set -euo pipefail
cd "$(dirname "$0")"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified/bin/activate

srun -A biostructure -p biostructure-priority --qos=biostructure-priority-qos -c 4 --mem=8G -t 00:15:00 python3 main.py
