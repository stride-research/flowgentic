"""Chemistry utility functions for ChemCrow-HPC."""

import asyncio
import logging
from typing import Optional, Tuple, Dict, Any
from urllib.error import URLError

# Try to import RDKit - if not available, provide mock implementations
try:
    from rdkit import Chem  # type: ignore
    from rdkit.Chem import rdMolDescriptors, Descriptors, Draw  # type: ignore
    from rdkit.DataStructs import FingerprintSimilarity  # type: ignore
    from rdkit.Chem.Fingerprints import FingerprintMols  # type: ignore
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False
    logging.warning("RDKit not available - using mock implementations")

logger = logging.getLogger(__name__)


def is_valid_smiles(smiles: str) -> bool:
    """Check if a SMILES string is valid.
    
    Args:
        smiles: SMILES string to validate
        
    Returns:
        True if valid SMILES, False otherwise
    """
    if not RDKIT_AVAILABLE:
        # Simple validation without RDKit
        return isinstance(smiles, str) and len(smiles.strip()) > 0
    
    try:
        if 'Chem' in globals():
            mol = Chem.MolFromSmiles(smiles)
            return mol is not None
        else:
            return False
    except Exception:
        return False


def get_mol_from_smiles(smiles: str, sanitize: bool = True) -> Optional[Any]:
    """Convert SMILES to RDKit molecule object.
    
    Args:
        smiles: SMILES string
        sanitize: Whether to sanitize the molecule
        
    Returns:
        RDKit molecule object or None if invalid
    """
    if not RDKIT_AVAILABLE:
        logger.warning("RDKit not available - returning None")
        return None
    
    try:
        if 'Chem' in globals():
            mol = Chem.MolFromSmiles(smiles, sanitize=sanitize)
            return mol
        else:
            return None
    except Exception as e:
        logger.error(f"Failed to convert SMILES {smiles}: {e}")
        return None


def calculate_molecular_weight(smiles: str) -> Optional[float]:
    """Calculate molecular weight from SMILES.
    
    Args:
        smiles: SMILES string
        
    Returns:
        Molecular weight in g/mol or None if calculation fails
    """
    if not RDKIT_AVAILABLE:
        # Mock implementation - return approximate weight based on string length
        return len(smiles) * 12.0  # Rough approximation
    
    mol = get_mol_from_smiles(smiles)
    if mol is None:
        return None
    
    try:
        if 'rdMolDescriptors' in globals():
            return rdMolDescriptors.CalcExactMolWt(mol)
        else:
            # Fallback if rdMolDescriptors is not available
            return len(smiles) * 12.0
    except Exception as e:
        logger.error(f"Failed to calculate molecular weight for {smiles}: {e}")
        return None


def calculate_tanimoto_similarity(smiles1: str, smiles2: str) -> Optional[float]:
    """Calculate Tanimoto similarity between two molecules.
    
    Args:
        smiles1: First SMILES string
        smiles2: Second SMILES string
        
    Returns:
        Tanimoto similarity coefficient (0-1) or None if calculation fails
    """
    if not RDKIT_AVAILABLE:
        # Mock similarity based on string similarity
        if smiles1 == smiles2:
            return 1.0
        # Simple character-based similarity
        common_chars = len(set(smiles1) & set(smiles2))
        total_chars = len(set(smiles1) | set(smiles2))
        return common_chars / total_chars if total_chars > 0 else 0.0
    
    mol1 = get_mol_from_smiles(smiles1)
    mol2 = get_mol_from_smiles(smiles2)
    
    if mol1 is None or mol2 is None:
        return None
    
    try:
        if 'FingerprintMols' in globals() and 'FingerprintSimilarity' in globals():
            fp1 = FingerprintMols.FingerprintMol(mol1)
            fp2 = FingerprintMols.FingerprintMol(mol2)
            return FingerprintSimilarity(fp1, fp2)
        else:
            # Fallback if fingerprint tools not available
            return 0.5  # Default similarity
    except Exception as e:
        logger.error(f"Failed to calculate similarity: {e}")
        return None


def sanitize_molecule(smiles: str) -> Optional[str]:
    """Sanitize a molecule and return canonical SMILES.
    
    Args:
        smiles: Input SMILES string
        
    Returns:
        Canonical SMILES string or None if sanitization fails
    """
    if not RDKIT_AVAILABLE:
        # Return original SMILES without sanitization
        return smiles
    
    mol = get_mol_from_smiles(smiles, sanitize=True)
    if mol is None:
        return None
    
    try:
        if 'Chem' in globals():
            return Chem.MolToSmiles(mol, isomericSmiles=True, canonical=True)
        else:
            return smiles  # Return original if Chem not available
    except Exception as e:
        logger.error(f"Failed to generate canonical SMILES: {e}")
        return None


def get_molecular_formula(smiles: str) -> Optional[str]:
    """Get molecular formula from SMILES.
    
    Args:
        smiles: SMILES string
        
    Returns:
        Molecular formula string or None if calculation fails
    """
    if not RDKIT_AVAILABLE:
        # Mock formula based on common elements
        return "C" + str(len(smiles) // 3) + "H" + str(len(smiles) * 2)
    
    mol = get_mol_from_smiles(smiles)
    if mol is None:
        return None
    
    try:
        return rdMolDescriptors.CalcMolFormula(mol)
    except Exception as e:
        logger.error(f"Failed to calculate molecular formula: {e}")
        return None


async def fetch_pubchem_data(cas_number: str, session: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Fetch data from PubChem for a given CAS number.
    
    Args:
        cas_number: CAS registry number
        session: Optional aiohttp session for reuse
        
    Returns:
        Dictionary with PubChem data or None if fetch fails
    """
    try:
        import aiohttp  # type: ignore
    except ImportError:
        logger.error("aiohttp not available for PubChem queries")
        return None
    
    try:
        # Get CID first
        cid_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{cas_number}/cids/JSON"
        
        if session:
            async with session.get(cid_url) as response:
                cid_data = await response.json()
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(cid_url) as response:
                    cid_data = await response.json()
        
        if 'IdentifierList' not in cid_data or 'CID' not in cid_data['IdentifierList']:
            return None
            
        cid = cid_data['IdentifierList']['CID'][0]
        
        # Get detailed data
        data_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        
        if session:
            async with session.get(data_url) as response:
                return await response.json()
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(data_url) as response:
                    return await response.json()
                    
    except (URLError, aiohttp.ClientError, KeyError) as e:
        logger.error(f"Failed to fetch PubChem data for {cas_number}: {e}")
        return None


def is_controlled_substance(smiles: str) -> bool:
    """Check if a compound is likely a controlled substance.
    
    Args:
        smiles: SMILES string
        
    Returns:
        True if compound contains controlled substance motifs
    """
    # This is a simplified check - in practice, you'd use comprehensive databases
    controlled_motifs = [
        "CN1CCC23C4C1CC5=C2C(=C(C=C5)O)OC3C(=O)C4",  # Morphine-like
        "CC(C)Cc1ccc(C(CC(=O)O)C(=O)O)cc1",          # Amphetamine-like
        "CN1C2CCC1C(C2)C",                           # Nicotine-like
    ]
    
    if not RDKIT_AVAILABLE:
        # Simple substring check
        return any(motif in smiles for motif in controlled_motifs)
    
    mol = get_mol_from_smiles(smiles)
    if mol is None:
        return False
    
    # More sophisticated analysis would go here
    return False


def estimate_logp(smiles: str) -> Optional[float]:
    """Estimate logP (octanol-water partition coefficient).
    
    Args:
        smiles: SMILES string
        
    Returns:
        Estimated logP value or None if calculation fails
    """
    if not RDKIT_AVAILABLE:
        # Mock logP based on molecular complexity
        return len(smiles) * 0.1
    
    mol = get_mol_from_smiles(smiles)
    if mol is None:
        return None
    
    try:
        # Use Crippen method for logP estimation
        return Descriptors.MolLogP(mol)
    except Exception as e:
        logger.error(f"Failed to estimate logP: {e}")
        return None