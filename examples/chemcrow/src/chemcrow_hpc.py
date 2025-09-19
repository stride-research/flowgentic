"""ChemCrow-HPC: High-performance chemical analysis with Flowgentic integration."""

import asyncio
import logging
import os
from typing import Optional, Dict, Any, List, Union
from pathlib import Path

# Try to import required packages
try:
    from langchain.agents import AgentExecutor, create_react_agent
    from langchain.prompts import PromptTemplate
    from langchain_openai import ChatOpenAI
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    # Create mock classes
    class AgentExecutor:
        pass
    class create_react_agent:
        pass
    class PromptTemplate:
        pass
    class ChatOpenAI:
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
    from radical.asyncflow import WorkflowEngine, ConcurrentExecutionBackend
    ASYNCFLOW_AVAILABLE = True
except ImportError:
    ASYNCFLOW_AVAILABLE = False
    # Create mock classes
    class ConcurrentExecutionBackend:
        def __init__(self, *args, **kwargs):
            pass
    
    class WorkflowEngine:
        @classmethod
        async def create(cls, *args, **kwargs):
            return cls()
        def function_task(self, func):
            return func

# Import our tools
try:
    from .tools import (
        MolecularWeightTool,
        MolecularSimilarityTool,
        MolecularFormulaTool,
        SafetyAnalysisTool,
        ExplosiveCheckTool,
        ToxicityCheckTool,
        create_flowgentic_chemical_tools,
    )
except ImportError:
    # Fallback imports
    try:
        from tools import (
            MolecularWeightTool,
            MolecularSimilarityTool,
            MolecularFormulaTool,
            SafetyAnalysisTool,
            ExplosiveCheckTool,
            ToxicityCheckTool,
            create_flowgentic_chemical_tools,
        )
    except ImportError:
        # Create mock tools if imports fail
        def create_flowgentic_chemical_tools(integration=None):
            return []

logger = logging.getLogger(__name__)


class ChemCrowHPC:
    """High-performance ChemCrow implementation with Flowgentic integration."""
    
    def __init__(
        self,
        model: str = "gpt-4",
        temp: float = 0.1,
        openai_api_key: Optional[str] = None,
        hpc_config: Optional[Dict[str, Any]] = None,
        fault_tolerance: bool = True,
        local_mode: bool = False,
    ):
        """Initialize ChemCrow-HPC agent.
        
        Args:
            model: OpenAI model to use
            temp: Temperature for LLM
            openai_api_key: OpenAI API key
            hpc_config: HPC configuration
            fault_tolerance: Enable fault tolerance
            local_mode: Run in local mode (no HPC)
        """
        self.model = model
        self.temp = temp
        self.fault_tolerance = fault_tolerance
        self.local_mode = local_mode
        
        # Set up API key
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("OpenAI API key required")
        
        # Set up HPC configuration
        self.hpc_config = hpc_config or self._load_default_hpc_config()
        
        # Initialize Flowgentic components
        self.flow = None
        self.integration = None
        self.tools = []
        
        # Initialize LLM and agent
        self.llm = None
        self.agent = None
        
        if not local_mode:
            self._setup_flowgentic()
        
        self._setup_llm()
        self._setup_tools()
    
    def _load_default_hpc_config(self) -> Dict[str, Any]:
        """Load default HPC configuration."""
        return {
            "scheduler": "local",
            "max_workers": 4,
            "timeout": 30,
            "retry_attempts": 3,
            "checkpoint_interval": 300,
        }
    
    def _setup_flowgentic(self):
        """Setup Flowgentic for HPC execution."""
        if not FLOWGENTIC_AVAILABLE or not ASYNCFLOW_AVAILABLE:
            logger.warning("Flowgentic or AsyncFlow not available - running in local mode")
            self.local_mode = True
            return
        
        try:
            # Create AsyncFlow backend
            backend = ConcurrentExecutionBackend(self.hpc_config)
            self.flow = WorkflowEngine(backend=backend)
            
            # Create LangGraph integration
            self.integration = LangGraphIntegration(
                self.flow,
                default_retry=RetryConfig(
                    max_attempts=self.hpc_config.get("retry_attempts", 3),
                    timeout_sec=self.hpc_config.get("timeout", 30),
                ) if self.fault_tolerance else None
            )
            
            logger.info("Flowgentic setup completed")
        except Exception as e:
            logger.error(f"Failed to setup Flowgentic: {e}")
            self.local_mode = True
    
    def _setup_llm(self):
        """Setup OpenAI LLM."""
        if not LANGCHAIN_AVAILABLE:
            logger.warning("LangChain not available - LLM functionality limited")
            return
        
        try:
            self.llm = ChatOpenAI(
                model=self.model,
                temperature=self.temp,
                openai_api_key=self.openai_api_key,
            )
            logger.info("LLM setup completed")
        except Exception as e:
            logger.error(f"Failed to setup LLM: {e}")
            self.llm = None
    
    def _setup_tools(self):
        """Setup chemical tools."""
        if self.local_mode:
            # Use regular LangChain tools
            self.tools = [
                MolecularWeightTool(),
                MolecularSimilarityTool(),
                MolecularFormulaTool(),
                SafetyAnalysisTool(),
                ExplosiveCheckTool(),
                ToxicityCheckTool(),
            ]
        else:
            # Use Flowgentic-wrapped tools
            if self.integration:
                self.tools = create_flowgentic_chemical_tools(self.integration)
            else:
                logger.warning("No integration available - falling back to local tools")
                self.tools = [
                    MolecularWeightTool(),
                    MolecularSimilarityTool(),
                    MolecularFormulaTool(),
                    SafetyAnalysisTool(),
                    ExplosiveCheckTool(),
                    ToxicityCheckTool(),
                ]
        
        logger.info(f"Setup {len(self.tools)} chemical tools")
    
    async def __aenter__(self):
        """Async context manager entry."""
        if not self.local_mode and self.flow:
            await self.flow.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if not self.local_mode and self.flow:
            await self.flow.__aexit__(exc_type, exc_val, exc_tb)
    
    async def run(self, prompt: str) -> str:
        """Run chemical analysis with the given prompt.
        
        Args:
            prompt: Natural language prompt describing the chemical task
            
        Returns:
            Analysis results
        """
        if not self.llm:
            return "Error: LLM not available. Please check your OpenAI API key."
        
        try:
            # Simple implementation - extract chemical entities and analyze
            # In a full implementation, this would use sophisticated NLP
            
            logger.info(f"Processing prompt: {prompt}")
            
            # Look for SMILES strings in the prompt
            import re
            smiles_pattern = r'[A-Za-z0-9@+\-\[\]\(\)\\=#]+'
            potential_smiles = re.findall(smiles_pattern, prompt)
            
            # Filter valid SMILES
            valid_smiles = [s for s in potential_smiles if is_valid_smiles(s)]
            
            if not valid_smiles:
                # Try to extract compound names and convert to SMILES
                # This would normally use chemical name-to-SMILES conversion
                return self._handle_text_only_prompt(prompt)
            
            # Analyze the compounds
            results = []
            for smiles in valid_smiles[:3]:  # Limit to 3 compounds for performance
                result = await self._analyze_compound(smiles, prompt)
                results.append(result)
            
            return "\n\n".join(results)
            
        except Exception as e:
            logger.error(f"Error processing prompt: {e}")
            return f"Error: Failed to process prompt - {str(e)}"
    
    async def _analyze_compound(self, smiles: str, prompt: str) -> str:
        """Analyze a single compound based on the prompt."""
        try:
            # Determine what analysis to perform based on prompt keywords
            prompt_lower = prompt.lower()
            
            if "weight" in prompt_lower or "mass" in prompt_lower:
                return await self._analyze_molecular_weight(smiles)
            elif "similarity" in prompt_lower or "compare" in prompt_lower:
                return await self._analyze_similarity(smiles, prompt)
            elif "safety" in prompt_lower or "hazard" in prompt_lower:
                return await self._analyze_safety(smiles)
            elif "formula" in prompt_lower:
                return await self._analyze_formula(smiles)
            else:
                # Default comprehensive analysis
                return await self._comprehensive_analysis(smiles)
                
        except Exception as e:
            logger.error(f"Error analyzing compound {smiles}: {e}")
            return f"Error analyzing {smiles}: {str(e)}"
    
    async def _analyze_molecular_weight(self, smiles: str) -> str:
        """Analyze molecular weight."""
        for tool in self.tools:
            if hasattr(tool, 'name') and 'weight' in tool.name.lower():
                if asyncio.iscoroutinefunction(tool._run):
                    return await tool._run(smiles)
                else:
                    return tool._run(smiles)
        
        return f"Could not analyze molecular weight for {smiles}"
    
    async def _analyze_similarity(self, smiles: str, prompt: str) -> str:
        """Analyze molecular similarity."""
        # This would need to handle pairs of compounds
        return f"Similarity analysis for {smiles}: Requires two compounds for comparison"
    
    async def _analyze_safety(self, smiles: str) -> str:
        """Analyze safety."""
        for tool in self.tools:
            if hasattr(tool, 'name') and ('safety' in tool.name.lower() or 'toxicity' in tool.name.lower()):
                if asyncio.iscoroutinefunction(tool._run):
                    return await tool._run(smiles)
                else:
                    return tool._run(smiles)
        
        return f"Could not analyze safety for {smiles}"
    
    async def _analyze_formula(self, smiles: str) -> str:
        """Analyze molecular formula."""
        for tool in self.tools:
            if hasattr(tool, 'name') and 'formula' in tool.name.lower():
                if asyncio.iscoroutinefunction(tool._run):
                    return await tool._run(smiles)
                else:
                    return tool._run(smiles)
        
        return f"Could not determine formula for {smiles}"
    
    async def _comprehensive_analysis(self, smiles: str) -> str:
        """Perform comprehensive analysis."""
        results = []
        
        # Run multiple analyses
        weight_result = await self._analyze_molecular_weight(smiles)
        formula_result = await self._analyze_formula(smiles)
        safety_result = await self._analyze_safety(smiles)
        
        results.extend([weight_result, formula_result, safety_result])
        
        return f"Comprehensive analysis for {smiles}:\n" + "\n".join(results)
    
    def _handle_text_only_prompt(self, prompt: str) -> str:
        """Handle prompts without SMILES strings."""
        # This would normally use chemical name resolution
        return f"I need SMILES strings or chemical names to analyze. Please provide them in your prompt."
    
    async def analyze_compound_safety(self, smiles: str) -> Dict[str, Any]:
        """Analyze safety of a compound with parallel checks."""
        if not self.local_mode and self.integration:
            # Use Flowgentic for parallel execution
            tasks = []
            for tool in self.tools:
                if hasattr(tool, 'name') and ('safety' in tool.name.lower() or 'toxicity' in tool.name.lower()):
                    if asyncio.iscoroutinefunction(tool):
                        tasks.append(tool(smiles))
                    else:
                        tasks.append(asyncio.to_thread(tool, smiles))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            safety_data = {
                "smiles": smiles,
                "explosive": results[0] if not isinstance(results[0], Exception) else "Check failed",
                "toxicity": results[1] if len(results) > 1 and not isinstance(results[1], Exception) else "Check failed",
            }
            
            return safety_data
        else:
            # Local sequential execution
            return {
                "smiles": smiles,
                "explosive": await self._analyze_safety(smiles),
                "toxicity": "Sequential mode - combined safety analysis",
            }
    
    async def process_large_library(self, library_path: str, batch_size: int = 100) -> List[Dict[str, Any]]:
        """Process a large chemical library with HPC optimization."""
        logger.info(f"Processing library: {library_path}")
        
        # This would normally read chemical libraries (SDF, SMILES files, etc.)
        # For now, return mock data
        compounds = ["CCO", "CC(=O)O", "CC(C)O"]  # Mock compounds
        
        if self.local_mode:
            # Sequential processing
            results = []
            for compound in compounds:
                result = await self.analyze_compound_safety(compound)
                results.append(result)
            return results
        else:
            # Parallel processing with Flowgentic
            tasks = [self.analyze_compound_safety(compound) for compound in compounds]
            results = await asyncio.gather(*tasks)
            return list(results)


# Convenience functions
async def basic_chemical_query(prompt: str, **kwargs) -> str:
    """Basic chemical query with automatic setup."""
    async with ChemCrowHPC(**kwargs) as chemcrow:
        return await chemcrow.run(prompt)


async def parallel_compound_analysis(compounds: List[str], **kwargs) -> List[Dict[str, Any]]:
    """Analyze multiple compounds in parallel."""
    async with ChemCrowHPC(**kwargs) as chemcrow:
        tasks = [chemcrow.analyze_compound_safety(compound) for compound in compounds]
        return await asyncio.gather(*tasks)


# Example usage
if __name__ == "__main__":
    async def example():
        # Basic query
        result = await basic_chemical_query("What is the molecular weight of ethanol?")
        print(result)
        
        # Parallel analysis
        compounds = ["CCO", "CC(=O)O", "CC(C)O"]
        results = await parallel_compound_analysis(compounds)
        for compound, result in zip(compounds, results):
            print(f"{compound}: {result}")
    
    asyncio.run(example())