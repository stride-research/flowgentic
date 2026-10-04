"""Step 8a: fold_msa.

See SUCCESS.md in this folder for what this proves.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import CAGEFLOW_DIR, check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# CageFlow imports pandas transitively (via history_table.py), which needs
# `module load PyTorch/...` (see run.sh) -- that module sets PYTHONPATH to
# EESSI's own python3.11 site-packages. _fetch_one_msa below spawns
# colabfold's OWN self-contained python3.10, which inherits os.environ
# wholesale (fold.py's subprocess.run has no env= override). Without this,
# that subprocess picks up EESSI's numpy (built for 3.11) instead of its
# own bundled one -- an ABI mismatch, not a missing package. Safe to drop
# here: the import above already happened, so Python's module cache no
# longer needs PYTHONPATH to find pandas/numpy for our own process.
os.environ.pop("PYTHONPATH", None)

DOCK44 = CAGEFLOW_DIR / "run_1c5e_noflip" / "dock_44"

config = load_config(__file__)

# The real designed sequence for this dock, from Laura's own committed
# run. input.fasta is ":"-joined (one entry per physical chain), but
# fetch_msa()/colabfold_msa.py want ONE chain's sequence plus n_chains as
# the cardinality -- NOT the colon-joined assembly. Confirmed via
# CageFlow.fold_oligomer(), which builds this exact call with
# `seq = _oligomer_chain_seq(...)` (mpnn_io.py: "one chain's sequence...
# fold_oligomer() folds ONE oligomer, so it needs one chain's sequence
# repeated n_chains times, not the whole assembly") and
# colabfold_msa.py's own docstring ("ONE unique sequence with cardinality
# n_chains"). Passing the joined string silently asks ColabFold to align
# a 287-residue fake chain instead of the real 95-residue one.
fasta_text = (DOCK44 / "fold_seq_20" / "input.fasta").read_text()
copies = fasta_text.splitlines()[1].split(":")
seq = copies[0]
n_chains = len(copies)

result = CageFlow._fetch_one_msa(seq, n_chains, str(Path(__file__).resolve().parent / "_scratch"), config)

check("MSA fetch reported ok", result["ok"], str(result.get("error")))

a3m_path = Path(result["a3m_path"])
header = a3m_path.read_text().splitlines()[0]
expected_header = f"#{len(copies[0])}\t{n_chains}"

check("the .a3m file exists", a3m_path.exists(), str(a3m_path))
check(f"the .a3m header matches the real criterion ({expected_header!r})",
      header == expected_header, f"got {header!r}")

write_artifact(__file__, {
    "a3m_path": str(a3m_path),
    "header": header,
    "n_chains": n_chains,
    "chain_length": len(copies[0]),
})

print(f"\nMSA fetched: {a3m_path}, header={header!r}")
