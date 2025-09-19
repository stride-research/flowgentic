#!/usr/bin/env python3
"""
Basic ChemCrow-HPC Workflow Example

This example demonstrates basic chemical analysis using ChemCrow-HPC
with fault-tolerant chemical tools.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from chemcrow_hpc import ChemCrowHPC, basic_chemical_query
    CHEMCROW_AVAILABLE = True
except ImportError:
    CHEMCROW_AVAILABLE = False
    print("Warning: ChemCrow-HPC not available - using mock implementation")


async def basic_molecular_weight_analysis():
    """Basic molecular weight calculation with fault tolerance."""
    print("=== Basic Molecular Weight Analysis ===")
    
    if CHEMCROW_AVAILABLE:
        try:
            result = await basic_chemical_query(
                "What is the molecular weight of ethanol (CCO)?",
                local_mode=True  # Use local mode for demo
            )
            print(f"Result: {result}")
        except Exception as e:
            print(f"Error in molecular weight analysis: {e}")
    else:
        # Mock implementation
        print("Mock: Molecular weight of ethanol (CCO): 46.07 g/mol")


async def safety_analysis_demo():
    """Demonstrate safety analysis with fault tolerance."""
    print("\n=== Safety Analysis Demo ===")
    
    if CHEMCROW_AVAILABLE:
        try:
            async with ChemCrowHPC(local_mode=True) as chemcrow:
                result = await chemcrow.run("Analyze the safety of aspirin (CC(=O)OC1=CC=CC=C1C(=O)O)")
                print(f"Safety Analysis Result: {result}")
        except Exception as e:
            print(f"Error in safety analysis: {e}")
    else:
        # Mock safety analysis
        print("Mock: Safety analysis for aspirin:")
        print("- Explosive Properties: Low - No obvious explosive groups detected")
        print("- Toxicity: Low - No obvious toxic groups detected")
        print("✅ LOW RISK: Standard laboratory safety protocols sufficient.")


async def similarity_comparison():
    """Compare molecular similarity between compounds."""
    print("\n=== Molecular Similarity Comparison ===")
    
    if CHEMCROW_AVAILABLE:
        try:
            async with ChemCrowHPC(local_mode=True) as chemcrow:
                # Compare ethanol and methanol
                result = await chemcrow.run("Compare the similarity between ethanol (CCO) and methanol (CO)")
                print(f"Similarity Result: {result}")
        except Exception as e:
            print(f"Error in similarity comparison: {e}")
    else:
        # Mock similarity analysis
        print("Mock: Comparing ethanol (CCO) and methanol (CO)")
        print("The molecules are similar (Tanimoto = 0.75)")


async def fault_tolerance_demo():
    """Demonstrate fault tolerance with intentionally invalid inputs."""
    print("\n=== Fault Tolerance Demo ===")
    
    if CHEMCROW_AVAILABLE:
        try:
            async with ChemCrowHPC(local_mode=True) as chemcrow:
                # Test with invalid SMILES
                result = await chemcrow.run("Analyze the compound: INVALID_SMILES_STRING")
                print(f"Fault tolerance result: {result}")
                
                # Test with another invalid input
                result2 = await chemcrow.run("What is the molecular weight of: NOT_A_COMPOUND")
                print(f"Second fault tolerance result: {result2}")
        except Exception as e:
            print(f"Error in fault tolerance demo: {e}")
    else:
        print("Mock: Fault tolerance handled gracefully for invalid inputs")


async def comprehensive_analysis():
    """Perform comprehensive analysis of a compound."""
    print("\n=== Comprehensive Analysis ===")
    
    compound = "CC(=O)OC1=CC=CC=C1C(=O)O"  # Aspirin
    
    if CHEMCROW_AVAILABLE:
        try:
            async with ChemCrowHPC(local_mode=True) as chemcrow:
                result = await chemcrow.run(f"Analyze {compound}")
                print(f"Comprehensive analysis result:\n{result}")
        except Exception as e:
            print(f"Error in comprehensive analysis: {e}")
    else:
        # Mock comprehensive analysis
        print(f"Mock: Comprehensive analysis for aspirin ({compound}):")
        print("- Molecular weight: 180.16 g/mol")
        print("- Molecular formula: C9H8O4")
        print("- Safety: Low risk, standard protocols sufficient")


async def main():
    """Run all basic workflow examples."""
    print("ChemCrow-HPC Basic Workflow Examples")
    print("=" * 40)
    
    # Check if OpenAI API key is available
    if not os.getenv("OPENAI_API_KEY"):
        print("Warning: OPENAI_API_KEY not set - some features may be limited")
    
    # Run all examples
    await basic_molecular_weight_analysis()
    await safety_analysis_demo()
    await similarity_comparison()
    await fault_tolerance_demo()
    await comprehensive_analysis()
    
    print("\n" + "=" * 40)
    print("Basic workflow examples completed!")
    print("\nNext steps:")
    print("1. Try parallel_analysis.py for multi-compound analysis")
    print("2. Try batch_processing.py for large libraries")
    print("3. Configure HPC settings in config/hpc_config.yaml")


if __name__ == "__main__":
    asyncio.run(main())