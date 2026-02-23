#!/bin/bash
#SBATCH --job-name=dragon-hello
#SBATCH --account=bebo-delta-gpu
#SBATCH --partition=gpuA100x4
#SBATCH --nodes=2
#SBATCH --gpus-per-node=1
#SBATCH --exclusive
#SBATCH --time=00:05:00
#SBATCH --output=tests/benchmark/logs/dragon_hello_%j.out
#SBATCH --error=tests/benchmark/logs/dragon_hello_%j.err

mkdir -p tests/benchmark/logs
source .venv/bin/activate
dragon-config add --tcp-runtime

dragon debug_file.py