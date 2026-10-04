#!/usr/bin/env bash
# Step 7: score_with_rosetta, in isolation (real PyRosetta). See SUCCESS.md.
#
# Different environment from every other step: PyRosetta's own venv, not
# the PyTorch module -- `source` has to happen INSIDE the srun'd shell,
# not before it, or the activation doesn't apply to the job.
set -euo pipefail
cd "$(dirname "$0")"

srun -A stride -p cpu -c 4 --mem=8G -t 00:10:00 \
  bash -c "source ~/venvs/cageflow/bin/activate && python3 main.py"
