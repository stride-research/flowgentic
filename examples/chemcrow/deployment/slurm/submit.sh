#!/bin/bash
#SBATCH --job-name=chemcrow-hpc
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=32
#SBATCH --time=24:00:00
#SBATCH --partition=gpu
#SBATCH --gres=gpu:4
#SBATCH --output=/scratch/logs/chemcrow-%j.out
#SBATCH --error=/scratch/logs/chemcrow-%j.err
#SBATCH --mail-type=ALL
#SBATCH --mail-user=your.email@domain.com

# ChemCrow-HPC SLURM Submission Script
# High-performance chemical analysis on HPC clusters

# Load required modules
module load python/3.11
module load cuda/11.8
module load gcc/11.2.0

# Set up environment variables
export PYTHONPATH="/Users/yamirghofran0/STRIDE/flowgentic/examples/chemcrow/src:$PYTHONPATH"
export CHEMCROW_HPC_CONFIG="/Users/yamirghofran0/STRIDE/flowgentic/examples/chemcrow/config/hpc_config.yaml"
export CHEMCROW_TOOLS_CONFIG="/Users/yamirghofran0/STRIDE/flowgentic/examples/chemcrow/config/tools_config.yaml"

# Optional API keys (set these in your environment or SLURM job)
# export OPENAI_API_KEY="your-openai-api-key"
# export CHEMSPACE_API_KEY="your-chemspace-api-key"
# export SERP_API_KEY="your-serp-api-key"
# export RXN4CHEM_API_KEY="your-rxn4chem-api-key"

# Create necessary directories
mkdir -p /scratch/logs
mkdir -p /scratch/checkpoints
mkdir -p /scratch/results

# Activate virtual environment
source /Users/yamirghofran0/STRIDE/flowgentic/examples/chemcrow/.venv/bin/activate

# Install/verify dependencies
echo "Installing dependencies..."
pip install -r /Users/yamirghofran0/STRIDE/flowgentic/examples/chemcrow/requirements.txt

# Set checkpoint directory
export CHEMCROW_CHECKPOINT_DIR="/scratch/checkpoints/job-$SLURM_JOB_ID"
mkdir -p $CHEMCROW_CHECKPOINT_DIR

# Run the main script
echo "Starting ChemCrow-HPC job $SLURM_JOB_ID"
echo "Nodes: $SLURM_JOB_NUM_NODES"
echo "Tasks: $SLURM_NTASKS"
echo "Working directory: $PWD"

# Execute the workflow
python -m examples.chemcrow.examples.batch_processing \
    --config $CHEMCROW_HPC_CONFIG \
    --tools-config $CHEMCROW_TOOLS_CONFIG \
    --input /scratch/input/compounds.sdf \
    --output /scratch/results/results-$SLURM_JOB_ID.json \
    --checkpoint-dir $CHEMCROW_CHECKPOINT_DIR \
    --batch-size 1000 \
    --max-workers $SLURM_NTASKS \
    --scheduler slurm \
    --partition $SLURM_JOB_PARTITION

# Check exit status
if [ $? -eq 0 ]; then
    echo "ChemCrow-HPC job completed successfully!"
    echo "Results saved to: /scratch/results/results-$SLURM_JOB_ID.json"
else
    echo "ChemCrow-HPC job failed! Check logs: /scratch/logs/chemcrow-$SLURM_JOB_ID.err"
    exit 1
fi

# Cleanup (optional - uncomment if you want to clean up checkpoints)
# rm -rf $CHEMCROW_CHECKPOINT_DIR

echo "Job $SLURM_JOB_ID finished at $(date)"