#!/usr/bin/env bash
# Track B launch script. Sibling of ../original/run_sbatch.sh (Track A) -- same partition, same
# config content (modulo out_dir), so the two runs are the actual comparison per DESIGN.md.
#SBATCH -A biostructure
#SBATCH -p biostructure-fortran
#SBATCH --qos=biostructure-priority-qos
#SBATCH --gres=gpu:rtx6000bb:1
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=8
#SBATCH -c 8
#SBATCH -t 00:30:00
#SBATCH -J cf_flowgentic
#SBATCH -o /home/javidsegura/software/isolation/1_sync/flowgentic/sbatch.out
#SBATCH -e /home/javidsegura/software/isolation/1_sync/flowgentic/sbatch.err

source /etc/profile.d/z01_eessi.sh
set -euo pipefail

FG_DIR="/home/javidsegura/software/isolation/1_sync/flowgentic"
CONFIG="$FG_DIR/config.json"

module load PyTorch/2.1.2-foss-2023a
source ~/venvs/cageflow_unified_dragon_test/bin/activate
export PYTHONUNBUFFERED=1

OUT_DIR="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['out_dir'])" "$CONFIG")"
mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

# Single-node launcher (-s), NOT the multi-node `-w slurm -t tcp` path.
#
# This job is --nodes=1, but `-w slurm` makes Dragon stand up its full distributed runtime
# anyway: a frontend, a backend, and a TCP overlay mesh that the node negotiates with itself
# over 127.0.0.1-ish addresses. Observed on this cluster: the launcher came up advertising two
# host-ids for one physical node, and the overlay/frontend port pair was assigned
# non-deterministically -- job 24037 got `--ip-addrs 10.205.20.32:6566 10.205.20.32:6565`
# (distinct ports, ran), job 24039 got `10.205.20.32:6566 10.205.20.32:6566` (the SAME port
# twice, wedged with a dragon-tcp process spinning at 99.6% CPU and the application never
# producing a line of output). That is a bring-up race in a network layer this job has no use
# for. Dragon's own docs reserve the TCP agent for multi-node runs; it is also the same
# transport implicated in the V2/V3 DDict hangs on this cluster (rhapsody issue #95).
#
# `-s` skips frontend, backend and overlay entirely: just local services, global services and
# shared-memory channels on one node. Nothing in this pipeline is multi-node.
#
# No --mem either: Dragon's default pools are 4 GB managed + 1 GB infrastructure of /dev/shm
# (dragon/infrastructure/facts.py: DEFAULT_SINGLE_DEF_SEG_SZ, DEFAULT_SINGLE_INF_SEG_SZ), and
# shm counts against the Slurm memory cgroup. The node has 756 GB and we are the only job on
# it; capping at 32 G bought nothing and gave the pools a ceiling to hit.

START=$(date +%s)
dragon -s "$FG_DIR/main.py" "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
