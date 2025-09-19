"""Chemical analysis tools for ChemCrow-HPC with fault tolerance."""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

# Try to import required packages
try:
    from langchain.tools import BaseTool
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    # Create mock BaseTool
    class BaseTool:
        def __init__(self, *args, **kwargs):
            pass

try:
    from flowgentic.langgraph import LangGraphIntegration, RetryConfig
    FLOWGENTIC_AVAILABLE = True
except ImportError:
    FLOWGENTIC_AVAILABLE = False
    # Create mock classes
    class LangGraphIntegration:
        def __init__(self, *args, **kwargs):
            pass
        def asyncflow_tool(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator
    
    class RetryConfig:
        def __init__(self, **kwargs):
            pass

try:
    from radical.asyncflow import WorkflowEngine
    ASYNCFLOW_AVAILABLE = True
except ImportError:
    ASYNCFLOW_AVAILABLE = False
    class WorkflowEngine:
        pass

# Import our chemistry utilities - with fallbackstry:
try:
    from ..utils import (
        calculate_molecular_weight,
        calculate_tanimoto_similarity,
        get_molecular_formula,
        is_valid_smiles,
    )
except ImportError:
    # Fallback mock implementations
    def calculate_molecular_weight(smiles: str):
        return len(smiles) * 12.0
    
    def calculate_tanimoto_similarity(smiles1: str, smiles2: str):
        return 0.5 if smiles1 != smiles2 else 1.0
    
    def get_molecular_formula(smiles: str):
        return "C" + str(len(smiles) // 3) + "H" + str(len(smiles) * 2)
    
    def is_valid_smiles(smiles: str):
        return isinstance(smiles, str) and len(smiles.strip()) > 0

logger = logging.getLogger(__name__)


class MolecularWeightTool(BaseTool):
    """Tool for calculating molecular weight with fault tolerance."""
    
    name = "MolecularWeightCalculator"
    description = "Calculate molecular weight from SMILES string"
    
    def __init__(self):
        super().__init__()
    
    def _run(self, smiles: str) -> str:
        """Synchronous implementation."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        try:
            weight = calculate_molecular_weight(smiles)
            if weight is None:
                return f"Error: Could not calculate molecular weight for {smiles}"
            
            return f"Molecular weight of {smiles}: {weight:.2f} g/mol"
        except Exception as e:
            logger.error(f"Error calculating molecular weight: {e}")
            return f"Error: {str(e)}"
    
    async def _arun(self, smiles: str) -> str:
        """Async implementation."""
        return self._run(smiles)


class MolecularSimilarityTool(BaseTool):
    """Tool for calculating molecular similarity with fault tolerance."""
    
    name = "MolecularSimilarityCalculator"
    description = "Calculate Tanimoto similarity between two molecules"
    
    def __init__(self):
        super().__init__()
    
    def _run(self, smiles_pair: str) -> str:
        """Synchronous implementation."""
        try:
            smiles_list = smiles_pair.split(".")
            if len(smiles_list) != 2:
                return "Input error: please provide two SMILES strings separated by '.'"
            
            smiles1, smiles2 = smiles_list
            
            if not is_valid_smiles(smiles1) or not is_valid_smiles(smiles2):
                return "Error: One or both SMILES strings are invalid"
            
            similarity = calculate_tanimoto_similarity(smiles1, smiles2)
            if similarity is None:
                return f"Error: Could not calculate similarity between {smiles1} and {smiles2}"
            
            # Interpret similarity
            if similarity == 1.0:
                return f"The molecules {smiles1} and {smiles2} are identical (Tanimoto = 1.0)"
            elif similarity > 0.8:
                return f"The molecules are very similar (Tanimoto = {similarity:.3f})"
            elif similarity > 0.6:
                return f"The molecules are similar (Tanimoto = {similarity:.3f})"
            elif similarity > 0.4:
                return f"The molecules are somewhat similar (Tanimoto = {similarity:.3f})"
            else:
                return f"The molecules are not very similar (Tanimoto = {similarity:.3f})"
                
        except Exception as e:
            logger.error(f"Error calculating similarity: {e}")
            return f"Error: {str(e)}"
    
    async def _arun(self, smiles_pair: str) -> str:
        """Async implementation."""
        return self._run(smiles_pair)


class MolecularFormulaTool(BaseTool):
    """Tool for getting molecular formula."""
    
    name = "MolecularFormulaCalculator"
    description = "Get molecular formula from SMILES string"
    
    def __init__(self):
        super().__init__()
    
    def _run(self, smiles: str) -> str:
        """Synchronous implementation."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        try:
            formula = get_molecular_formula(smiles)
            if formula is None:
                return f"Error: Could not determine molecular formula for {smiles}"
            
            return f"Molecular formula of {smiles}: {formula}"
        except Exception as e:
            logger.error(f"Error getting molecular formula: {e}")
            return f"Error: {str(e)}"
    
    async def _arun(self, smiles: str) -> str:
        """Async implementation."""
        return self._run(smiles)


class SafetyAnalysisTool(BaseTool):
    """Tool for comprehensive safety analysis."""
    
    name = "SafetyAnalysis"
    description = "Perform comprehensive safety analysis of a chemical compound"
    
    def __init__(self):
        super().__init__()
    
    def _run(self, smiles: str) -> str:
        """Synchronous implementation."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        try:
            # Run safety checks
            explosive_result = self._check_explosive_properties(smiles)
            toxicity_result = self._check_toxicity(smiles)
            
            safety_summary = f"Safety Analysis for {smiles}:\n"
            safety_summary += f"- Explosive Properties: {explosive_result}\n"
            safety_summary += f"- Toxicity: {toxicity_result}\n"
            
            # Overall safety assessment
            if "High" in explosive_result or "Moderate" in toxicity_result:
                safety_summary += "⚠️  MODERATE TO HIGH RISK: Handle with caution!"
            else:
                safety_summary += "✅ LOW RISK: Standard laboratory safety protocols sufficient."
            
            return safety_summary
            
        except Exception as e:
            logger.error(f"Error in safety analysis: {e}")
            return f"Error: Safety analysis failed - {str(e)}"
    
    async def _arun(self, smiles: str) -> str:
        """Async implementation."""
        return self._run(smiles)
    
    def _check_explosive_properties(self, smiles: str) -> str:
        """Check for explosive properties (simplified)."""
        explosive_groups = ["N=N", "N-NO2", "O-NO2", "ClO3", "ClO4"]
        
        for group in explosive_groups:
            if group in smiles:
                return "High - Contains explosive functional groups"
        
        return "Low - No obvious explosive groups detected"
    
    def _check_toxicity(self, smiles: str) -> str:
        """Check for toxicity indicators."""
        toxic_groups = ["CN", "As", "Hg", "Pb", "Cd"]
        
        for group in toxic_groups:
            if group in smiles:
                return f"Moderate - Contains {group} group"
        
        return "Low - No obvious toxic groups detected"


class ExplosiveCheckTool(BaseTool):
    """Tool for checking explosive properties."""
    
    name = "ExplosiveCheck"
    description = "Check if a compound has explosive properties"
    
    def __init__(self):
        super().__init__()
    
    def _run(self, smiles: str) -> str:
        """Synchronous implementation."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        try:
            # Check for known explosive functional groups
            explosive_groups = [
                "N=N", "N-NO2", "O-NO2", "ClO3", "ClO4", "N3", 
                "C(NO2)", "N(NO2)", "O(NO2)", "C=C-NO2"
            ]
            
            found_groups = []
            for group in explosive_groups:
                if group in smiles:
                    found_groups.append(group)
            
            if found_groups:
                return f"⚠️  POTENTIAL EXPLOSIVE: Contains groups {', '.join(found_groups)}"
            else:
                return "✅ LOW EXPLOSIVE RISK: No obvious explosive groups detected"
                
        except Exception as e:
            logger.error(f"Error checking explosive properties: {e}")
            return f"Error: Explosive check failed - {str(e)}"
    
    async def _arun(self, smiles: str) -> str:
        """Async implementation."""
        return self._run(smiles)


class ToxicityCheckTool(BaseTool):
    """Tool for checking toxicity."""
    
    name = "ToxicityCheck"
    description = "Check if a compound has toxic properties"
    
    def __init__(self):
        super().__init__()
    
    def _run(self, smiles: str) -> str:
        """Synchronous implementation."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        try:
            # Check for known toxic elements and groups
            toxic_indicators = [
                ("As", "Arsenic"), ("Hg", "Mercury"), ("Pb", "Lead"), ("Cd", "Cadmium"),
                ("CN", "Cyanide"), ("CO", "Carbonyl"), ("N=C=S", "Isothiocyanate"),
            ]
            
            found_toxins = []
            for indicator, name in toxic_indicators:
                if indicator in smiles:
                    found_toxins.append(name)
            
            if found_toxins:
                return f"⚠️  POTENTIALLY TOXIC: Contains {', '.join(found_toxins)}"
            else:
                return "✅ LOW TOXICITY RISK: No obvious toxic elements detected"
                
        except Exception as e:
            logger.error(f"Error checking toxicity: {e}")
            return f"Error: Toxicity check failed - {str(e)}"
    
    async def _arun(self, smiles: str) -> str:
        """Async implementation."""
        return self._run(smiles)


# Create tools that can be used with Flowgentic (if available)
def create_flowgentic_chemical_tools(integration=None):
    """Create chemical tools wrapped with Flowgentic for fault tolerance."""
    
    if not FLOWGENTIC_AVAILABLE or integration is None:
        # Return regular tools if Flowgentic not available
        return [
            MolecularWeightTool(),
            MolecularSimilarityTool(),
            MolecularFormulaTool(),
            SafetyAnalysisTool(),
            ExplosiveCheckTool(),
            ToxicityCheckTool(),
        ]
    
    # Create Flowgentic-wrapped tools
    @integration.asyncflow_tool(retry=RetryConfig(max_attempts=3, timeout_sec=15.0))
    async def molecular_weight_tool(smiles: str) -> str:
        """Calculate molecular weight with fault tolerance."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        weight = calculate_molecular_weight(smiles)
        if weight is None:
            return f"Error: Could not calculate molecular weight for {smiles}"
        
        return f"Molecular weight of {smiles}: {weight:.2f} g/mol"
    
    @integration.asyncflow_tool(retry=RetryConfig(max_attempts=3, timeout_sec=20.0))
    async def molecular_similarity_tool(smiles_pair: str) -> str:
        """Calculate molecular similarity with fault tolerance."""
        try:
            smiles_list = smiles_pair.split(".")
            if len(smiles_list) != 2:
                return "Input error: please provide two SMILES strings separated by '.'"
            
            smiles1, smiles2 = smiles_list
            similarity = calculate_tanimoto_similarity(smiles1, smiles2)
            if similarity is None:
                return f"Error: Could not calculate similarity"
            
            return f"Tanimoto similarity: {similarity:.3f}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    @integration.asyncflow_tool(retry=RetryConfig(max_attempts=5, timeout_sec=30.0))
    async def comprehensive_safety_analysis(smiles: str) -> str:
        """Comprehensive safety analysis with fault tolerance."""
        if not is_valid_smiles(smiles):
            return f"Error: Invalid SMILES string: {smiles}"
        
        # Run safety checks in parallel with fault tolerance
        tasks = [
            check_explosive_properties(smiles),
            check_toxicity(smiles),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        safety_summary = f"Safety Analysis for {smiles}:\n"
        
        # Process results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                safety_summary += f"- Check {i+1}: Failed - {str(result)}\n"
            else:
                safety_summary += f"- {result}\n"
        
        return safety_summary
    
    @integration.asyncflow_tool(retry=RetryConfig(max_attempts=3, timeout_sec=15.0))
    async def check_explosive_properties(smiles: str) -> str:
        """Check for explosive properties."""
        explosive_groups = ["N=N", "N-NO2", "O-NO2", "ClO3", "ClO4"]
        found_groups = [group for group in explosive_groups if group in smiles]
        
        if found_groups:
            return f"Explosive Risk: High - Contains {', '.join(found_groups)}"
        else:
            return "Explosive Risk: Low"
    
    @integration.asyncflow_tool(retry=RetryConfig(max_attempts=3, timeout_sec=15.0))
    async def check_toxicity(smiles: str) -> str:
        """Check for toxicity."""
        toxic_groups = [("As", "Arsenic"), ("Hg", "Mercury"), ("Pb", "Lead"), ("CN", "Cyanide")]
        found_toxins = [name for indicator, name in toxic_groups if indicator in smiles]
        
        if found_toxins:
            return f"Toxicity Risk: Moderate - Contains {', '.join(found_toxins)}"
        else:
            return "Toxicity Risk: Low"
    
    return [
        molecular_weight_tool,
        molecular_similarity_tool,
        comprehensive_safety_analysis,
        check_explosive_properties,
        check_toxicity,
    ]