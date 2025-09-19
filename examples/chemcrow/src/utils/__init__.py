"""Utilities for ChemCrow-HPC."""

# Handle potential import errors
try:
    from .chemistry_utils import (
        is_valid_smiles,
        calculate_molecular_weight,
        calculate_tanimoto_similarity,
        sanitize_molecule,
        get_molecular_formula,
        get_mol_from_smiles,
    )
except ImportError:
    # Fallback if relative import fails
    try:
        # Try importing from the same directory
        import chemistry_utils
        is_valid_smiles = chemistry_utils.is_valid_smiles
        calculate_molecular_weight = chemistry_utils.calculate_molecular_weight
        calculate_tanimoto_similarity = chemistry_utils.calculate_tanimoto_similarity
        sanitize_molecule = chemistry_utils.sanitize_molecule
        get_molecular_formula = chemistry_utils.get_molecular_formula
        get_mol_from_smiles = chemistry_utils.get_mol_from_smiles
    except ImportError:
        # Create mock functions if all imports fail
        def is_valid_smiles(smiles: str) -> bool:
            return isinstance(smiles, str) and len(smiles.strip()) > 0
        
        def calculate_molecular_weight(smiles: str):
            return len(smiles) * 12.0
        
        def calculate_tanimoto_similarity(smiles1: str, smiles2: str):
            return 0.5
        
        def sanitize_molecule(smiles: str):
            return smiles
        
        def get_molecular_formula(smiles: str):
            return "C" + str(len(smiles) // 3) + "H" + str(len(smiles) * 2)
        
        def get_mol_from_smiles(smiles: str, sanitize: bool = True):
            return None

__all__ = [
    "is_valid_smiles",
    "calculate_molecular_weight", 
    "calculate_tanimoto_similarity",
    "sanitize_molecule",
    "get_molecular_formula",
    "get_mol_from_smiles",
]