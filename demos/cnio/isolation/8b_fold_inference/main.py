"""Step 8b: fold_inference.

See SUCCESS.md in this folder for what this proves.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import CAGEFLOW_DIR, check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# Same PYTHONPATH leak as step 8a's main.py -- see the comment there.
# _fold_one_sequence below also spawns colabfold's own self-contained
# python/binary, which would otherwise inherit our module-loaded
# PYTHONPATH and crash on an ABI-mismatched numpy.
os.environ.pop("PYTHONPATH", None)

DOCK44 = CAGEFLOW_DIR / "run_1c5e_noflip" / "dock_44"

config = load_config(__file__)

# seq must be ONE chain's sequence, not the ":"-joined assembly --
# fold_sequence() calls write_multimer_fasta(seq, n_chains, ...), which
# repeats `seq` n_chains times itself. Same contract as step 8a's
# fetch_msa(); see that file's comment for the full derivation.
fasta_text = (DOCK44 / "fold_seq_20" / "input.fasta").read_text()
copies = fasta_text.splitlines()[1].split(":")
seq = copies[0]
n_chains = len(copies)

# Reuse the real, precomputed alignment from step 8a's fixture rather than
# fetching again -- this step tests INFERENCE, 8a already tested fetching.
a3m_path = str(DOCK44 / "fold_seq_20" / "msa.a3m")

# Same stand-in pattern as step 7's docked_oligomer: the real pipeline
# sources native_pdb_text as docked_oligomer["copies"][0] (CageFlow.py's
# fold_oligomer(), where it builds _fold_one_sequence's arg_list) -- i.e.
# just the scaffold's own PDB text, one copy, nothing assembly-specific.
native_pdb_text = (CAGEFLOW_DIR / "1c5e_trimer.pdb").read_text()

out_dir = str(Path(__file__).resolve().parent / "_scratch")

result = CageFlow._fold_one_sequence(seq, n_chains, native_pdb_text, out_dir, config, a3m_path=a3m_path)

check("fold reported ok", result["ok"], str(result.get("error")))
check("a real PDB was written", result.get("pdb_path") and Path(result["pdb_path"]).exists(),
      str(result.get("pdb_path")))
check("mean_plddt is a real number", isinstance(result.get("mean_plddt"), float),
      str(result.get("mean_plddt")))
check("rmsd against the docked pose is a real number or a named failure reason",
      isinstance(result.get("rmsd"), float) or result.get("error"),
      f"rmsd={result.get('rmsd')}, error={result.get('error')}")

write_artifact(__file__, {
    "pdb_path": result.get("pdb_path"),
    "mean_plddt": result.get("mean_plddt"),
    "ptm": result.get("ptm"),
    "iptm": result.get("iptm"),
    "rmsd": result.get("rmsd"),
    "error": result.get("error"),
})

print(f"\nfold: mean_plddt={result.get('mean_plddt')} "
      f"iptm={result.get('iptm')} rmsd={result.get('rmsd')}")
