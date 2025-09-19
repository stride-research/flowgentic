#!/usr/bin/env python3
"""
UV-based setup script for ChemCrow-HPC

This script sets up ChemCrow-HPC using uv package manager with proper
dependency management and environment configuration.
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(cmd, check=True):
    """Run a shell command."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        sys.exit(1)
    return result

def setup_uv_environment():
    """Set up the uv environment for ChemCrow-HPC."""
    print("🧪 Setting up ChemCrow-HPC with uv...")
    
    # Check if uv is available
    try:
        run_command("uv --version", check=True)
    except:
        print("❌ uv not found. Installing uv...")
        # Install uv
        if sys.platform == "darwin" or sys.platform == "linux":
            run_command("curl -LsSf https://astral.sh/uv/install.sh | sh", check=True)
            # Add to PATH
            uv_path = Path.home() / ".cargo" / "bin"
            os.environ["PATH"] = f"{uv_path}:{os.environ.get('PATH', '')}"
        else:
            print("❌ Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/")
            sys.exit(1)
    
    # Create virtual environment
    print("📦 Creating virtual environment...")
    run_command("uv venv", check=True)
    
    # Install dependencies from pyproject.toml
    print("📚 Installing dependencies...")
    run_command("uv pip install -e .", check=True)
    
    # Install additional ChemCrow dependencies
    chemcrow_deps = [
        "rdkit-pypi>=2023.9.1",
        "pandas>=2.0.0",
        "numpy>=1.24.0",
        "scipy>=1.11.0",
        "scikit-learn>=1.3.0",
        "aiohttp>=3.8.0",
        "httpx>=0.24.0",
        "requests>=2.31.0",
        "pyyaml>=6.0",
        "loguru>=0.7.0",
    ]
    
    for dep in chemcrow_deps:
        print(f"Installing {dep}...")
        run_command(f"uv pip install {dep}", check=True)
    
    print("✅ Environment setup complete!")

def create_chemcrow_scripts():
    """Create convenient scripts for running ChemCrow-HPC."""
    
    # Create run script
    run_script = """#!/bin/bash
# ChemCrow-HPC Run Script
# Usage: ./run_chemcrow.sh [command]

# Activate virtual environment
source .venv/bin/activate

# Set Python path
export PYTHONPATH="examples/chemcrow/src:$PYTHONPATH"

# Run the command
if [ $# -eq 0 ]; then
    echo "Running ChemCrow-HPC demo..."
    python examples/chemcrow/examples/demo_simple.py
else
    echo "Running: $@"
    python "$@"
fi
"""
    
    with open("run_chemcrow.sh", "w") as f:
        f.write(run_script)
    
    # Make executable
    os.chmod("run_chemcrow.sh", 0o755)
    
    # Create SLURM submission helper
    slurm_helper = r"""#!/bin/bash
# ChemCrow-HPC SLURM Helper
# Usage: ./submit_slurm.sh [options]

# Default values
JOB_NAME="chemcrow-hpc"
NODES=2
TIME="04:00:00"
PARTITION="compute"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            JOB_NAME="$2"
            shift 2
            ;;
        --nodes)
            NODES="$2"
            shift 2
            ;;
        --time)
            TIME="$2"
            shift 2
            ;;
        --partition)
            PARTITION="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--name NAME] [--nodes NODES] [--time TIME] [--partition PARTITION]"
            exit 1
            ;;
    esac
done

# Create SLURM script
cat > chemcrow_job.sbatch << 'EOF'
#!/bin/bash
#SBATCH --job-name=$JOB_NAME
#SBATCH --nodes=$NODES
#SBATCH --ntasks-per-node=32
#SBATCH --time=$TIME
#SBATCH --partition=$PARTITION
#SBATCH --output=/scratch/logs/chemcrow-%j.out
#SBATCH --error=/scratch/logs/chemcrow-%j.err

# Load modules
module load python/3.11
module load cuda/11.8

# Activate virtual environment
source .venv/bin/activate

# Set environment variables
export PYTHONPATH="examples/chemcrow/src:$PYTHONPATH"
export CHEMCROW_HPC_CONFIG="examples/chemcrow/config/hpc_config.yaml"

# Create directories
mkdir -p /scratch/logs
mkdir -p /scratch/checkpoints
mkdir -p /scratch/results

# Run ChemCrow-HPC
echo "Starting ChemCrow-HPC job $SLURM_JOB_ID"
python examples/chemcrow/examples/demo_simple.py

echo "Job completed!"
EOF

echo "Created SLURM script: chemcrow_job.sbatch"
echo "To submit: sbatch chemcrow_job.sbatch"
"""
    
    with open("submit_slurm.sh", "w") as f:
        f.write(slurm_helper)
    
    os.chmod("submit_slurm.sh", 0o755)
    
    print("✅ Created convenience scripts:")
    print("  - run_chemcrow.sh: Run ChemCrow-HPC locally")
    print("  - submit_slurm.sh: Helper for SLURM submission")

def create_environment_template():
    """Create environment template file."""
    
    env_template = """# ChemCrow-HPC Environment Variables
# Copy this to .env and fill in your API keys

# OpenAI API Key (required for LLM functionality)
OPENAI_API_KEY=your-openai-api-key-here

# Optional API Keys for enhanced functionality
CHEMSPACE_API_KEY=your-chemspace-api-key
SERP_API_KEY=your-serp-api-key  
RXN4CHEM_API_KEY=your-rxn4chem-api-key
SEMANTIC_SCHOLAR_API_KEY=your-semantic-scholar-api-key

# HPC Configuration
CHEMCROW_HPC_CONFIG=examples/chemcrow/config/hpc_config.yaml
CHEMCROW_TOOLS_CONFIG=examples/chemcrow/config/tools_config.yaml
"""
    
    with open(".env.template", "w") as f:
        f.write(env_template)
    
    print("✅ Created .env.template for API key configuration")

def main():
    """Main setup function."""
    print("🚀 ChemCrow-HPC UV Setup Script")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not (Path.cwd() / "pyproject.toml").exists():
        print("❌ Error: pyproject.toml not found. Please run this from the flowgentic root directory.")
        sys.exit(1)
    
    # Setup uv environment
    setup_uv_environment()
    
    # Create scripts
    create_chemcrow_scripts()
    
    # Create environment template
    create_environment_template()
    
    print("\n" + "=" * 50)
    print("🎉 ChemCrow-HPC setup complete!")
    print("\nNext steps:")
    print("1. Copy .env.template to .env and fill in your API keys")
    print("2. Run: ./run_chemcrow.sh")
    print("3. For HPC: ./submit_slurm.sh --nodes 4 --time 24:00:00")
    print("\nTo activate the environment manually:")
    print("source .venv/bin/activate")
    print("export PYTHONPATH=examples/chemcrow/src:\$PYTHONPATH")

if __name__ == "__main__":
    main()