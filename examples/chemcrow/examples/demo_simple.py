#!/usr/bin/env python3
"""
Simple ChemCrow-HPC Demonstration

This example demonstrates the core concepts of ChemCrow-HPC without
requiring all the complex dependencies.
"""

import asyncio
import logging
from typing import Dict, Any, List

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MockChemicalTool:
    """Mock chemical tool for demonstration."""
    
    def __init__(self, name: str):
        self.name = name
    
    def analyze(self, smiles: str) -> str:
        """Mock analysis."""
        if "invalid" in smiles.lower():
            return f"Error: Invalid SMILES string: {smiles}"
        
        # Mock results based on tool type
        if "weight" in self.name.lower():
            weight = len(smiles) * 12.0  # Mock molecular weight
            return f"Molecular weight of {smiles}: {weight:.2f} g/mol"
        elif "safety" in self.name.lower():
            return f"Safety analysis for {smiles}: Low risk - standard protocols sufficient"
        elif "similarity" in self.name.lower():
            return f"Similarity analysis: Requires two compounds for comparison"
        elif "formula" in self.name.lower():
            return f"Molecular formula of {smiles}: C{len(smiles)//3}H{len(smiles)*2}"
        else:
            return f"Analysis complete for {smiles}"


class SimpleChemCrowHPC:
    """Simplified ChemCrow-HPC for demonstration."""
    
    def __init__(self):
        self.tools = [
            MockChemicalTool("MolecularWeightCalculator"),
            MockChemicalTool("SafetyAnalyzer"),
            MockChemicalTool("SimilarityCalculator"),
            MockChemicalTool("FormulaCalculator"),
        ]
    
    async def run(self, prompt: str) -> str:
        """Process a chemical analysis prompt."""
        logger.info(f"Processing prompt: {prompt}")
        
        # Extract SMILES from prompt (simple extraction)
        import re
        smiles_pattern = r'[A-Za-z0-9@+\-\[\]\(\)\\=#]+'
        potential_smiles = re.findall(smiles_pattern, prompt)
        
        # Filter for valid-looking SMILES (simplified)
        valid_smiles = [s for s in potential_smiles if len(s) > 2 and not s.isalpha()]
        
        if not valid_smiles:
            return "No valid chemical structures found. Please provide SMILES strings."
        
        # Analyze the first compound found
        smiles = valid_smiles[0]
        
        # Determine what analysis to perform
        prompt_lower = prompt.lower()
        
        if "weight" in prompt_lower or "mass" in prompt_lower:
            return self.tools[0].analyze(smiles)
        elif "safety" in prompt_lower or "hazard" in prompt_lower:
            return self.tools[1].analyze(smiles)
        elif "similarity" in prompt_lower or "compare" in prompt_lower:
            return self.tools[2].analyze(smiles)
        elif "formula" in prompt_lower:
            return self.tools[3].analyze(smiles)
        else:
            # Comprehensive analysis
            results = []
            for tool in self.tools:
                results.append(tool.analyze(smiles))
            return "Comprehensive analysis:\n" + "\n".join(results)
    
    async def analyze_compound_safety(self, smiles: str) -> Dict[str, Any]:
        """Analyze compound safety with fault tolerance simulation."""
        # Simulate fault tolerance with retries
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # Simulate potential failure
                if attempt < 2 and len(smiles) < 3:  # Simulate failure on first attempts
                    raise Exception("Simulated API failure")
                
                safety_result = self.tools[1].analyze(smiles)
                
                return {
                    "smiles": smiles,
                    "explosive": "Low risk - no explosive groups detected",
                    "toxicity": "Low risk - no toxic groups detected",
                    "environmental": "Low environmental impact",
                    "attempts": attempt + 1,
                    "status": "success"
                }
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.1 * (2 ** attempt))  # Exponential backoff
        
        # If all attempts failed, return failure result
        return {
            "smiles": smiles,
            "error": "All retry attempts failed",
            "attempts": max_retries,
            "status": "failed"
        }


async def basic_analysis_demo():
    """Demonstrate basic chemical analysis."""
    print("=== Basic Chemical Analysis Demo ===")
    
    chemcrow = SimpleChemCrowHPC()
    
    # Test molecular weight calculation
    result = await chemcrow.run("What is the molecular weight of ethanol CCO?")
    print(f"Query: What is the molecular weight of ethanol CCO?")
    print(f"Result: {result}")
    print()
    
    # Test safety analysis
    result = await chemcrow.run("Analyze the safety of aspirin CC(=O)OC1=CC=CC=C1C(=O)O")
    print(f"Query: Analyze the safety of aspirin")
    print(f"Result: {result}")
    print()
    
    # Test comprehensive analysis
    result = await chemcrow.run("Analyze CC(=O)O")  # Acetic acid
    print(f"Query: Analyze CC(=O)O")
    print(f"Result: {result}")


async def fault_tolerance_demo():
    """Demonstrate fault tolerance with retries."""
    print("=== Fault Tolerance Demo ===")
    
    chemcrow = SimpleChemCrowHPC()
    
    # Test with problematic input that will fail initially
    result = await chemcrow.analyze_compound_safety("CC")  # Short SMILES that will "fail" initially
    print(f"Fault tolerance result: {result}")


async def parallel_analysis_demo():
    """Demonstrate parallel analysis concept."""
    print("=== Parallel Analysis Demo ===")
    
    chemcrow = SimpleChemCrowHPC()
    
    # Simulate parallel analysis of multiple compounds
    compounds = ["CCO", "CC(=O)O", "CC(C)O"]  # ethanol, acetic acid, isopropanol
    
    print(f"Analyzing {len(compounds)} compounds in parallel...")
    
    # Create tasks for parallel execution
    tasks = [chemcrow.analyze_compound_safety(compound) for compound in compounds]
    
    # Execute in parallel (simulated)
    results = await asyncio.gather(*tasks)
    
    print("Parallel analysis results:")
    for compound, result in zip(compounds, results):
        print(f"  {compound}: {result['status']} (attempts: {result.get('attempts', 1)})")


async def hpc_simulation():
    """Simulate HPC-style batch processing."""
    print("=== HPC Batch Processing Simulation ===")
    
    chemcrow = SimpleChemCrowHPC()
    
    # Simulate a large batch of compounds
    batch_size = 10
    compounds = [f"CC{chr(65 + i % 26)}" for i in range(batch_size)]  # Generate mock compounds
    
    print(f"Processing batch of {batch_size} compounds...")
    
    # Process in batches (simulating HPC batch processing)
    batch_results = []
    batch_size = 5
    
    for i in range(0, len(compounds), batch_size):
        batch = compounds[i:i+batch_size]
        print(f"Processing batch {i//batch_size + 1}: {batch}")
        
        # Process batch
        tasks = [chemcrow.analyze_compound_safety(compound) for compound in batch]
        results = await asyncio.gather(*tasks)
        batch_results.extend(results)
        
        # Simulate checkpointing
        print(f"  Checkpoint: processed {len(results)} compounds")
    
    print(f"Batch processing complete! Processed {len(batch_results)} compounds total")
    
    # Summary statistics
    successful = sum(1 for r in batch_results if r['status'] == 'success')
    failed = len(batch_results) - successful
    
    print(f"Summary: {successful} successful, {failed} failed")


async def main():
    """Run all demonstration examples."""
    print("ChemCrow-HPC Simple Demonstration")
    print("=" * 50)
    print("This demo shows the core concepts without requiring complex dependencies.\n")
    
    await basic_analysis_demo()
    await fault_tolerance_demo()
    await parallel_analysis_demo()
    await hpc_simulation()
    
    print("\n" + "=" * 50)
    print("Demo completed! Key features demonstrated:")
    print("✅ Basic chemical analysis (molecular weight, safety, etc.)")
    print("✅ Fault tolerance with retries and exponential backoff")
    print("✅ Parallel processing of multiple compounds")
    print("✅ Batch processing with checkpointing")
    print("✅ HPC-style workflow simulation")
    print("\nTo run the full implementation, install the required dependencies:")
    print("pip install -r requirements.txt")


if __name__ == "__main__":
    asyncio.run(main())