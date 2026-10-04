"""Step 7: score_with_rosetta (the real PyRosetta worker).

Deliberately NOT using _shared.py here -- see SUCCESS.md for why (that
module imports CageFlow.py at load time, which transitively needs numpy
and pandas; both are broken/missing in the PyRosetta venv this step runs
under, and neither is actually needed for Rosetta scoring itself).

Calls rosetta_scoring.score_sequence() directly -- the exact function
CageFlow._score_one_sequence_rosetta() calls, which is itself a thin
try/except wrapper around this same call. Importing rosetta_scoring
instead of CageFlow skips an unrelated broken dependency chain, not any
real science.

Uses Laura's own committed real run (run_1c5e_noflip/dock_44) as input
instead of chaining dock->design ourselves -- that chain alone costs
~3 minutes of real ProteinMPNN inference per run (see 5_score_designs'
SUCCESS.md), and Rosetta scoring itself is ~134s/sequence on top. Her
fixture is real historical output, not anything we generated.

See SUCCESS.md for what this proves and the format reconciliation this
needed.
"""

import json
import sys
from pathlib import Path

CAGEFLOW_DIR = Path.home() / "software" / "CageFlow"
DOCK44 = CAGEFLOW_DIR / "run_1c5e_noflip" / "dock_44"

sys.path.insert(0, str(CAGEFLOW_DIR))
import rosetta_scoring  # noqa: E402


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        raise AssertionError(f"{label}: {detail}")


# Laura's own real, historical run for this dock -- not anything we
# generated. mpnn_input.json is the actual ProteinMPNN config CageFlow
# wrote for dock 44; docked_assembly.pdb/ref.symm are its real structure
# files.
mpnn_cfg = json.loads((DOCK44 / "mpnn_input.json").read_text())
assembly_pdb = (DOCK44 / "docked_assembly.pdb").read_text()
symm_file = str(DOCK44 / "ref.symm")
chains_to_design = mpnn_cfg["chains_to_design"]

# thread_sequence() does `for resnum in design_positions:` -- a FLAT list
# of residue numbers reused for every chain, NOT the {chain: [...]} dict
# build_mpnn_config wrote to mpnn_input.json (that dict shape is ProteinMPNN's
# own input format, a different consumer). Verified before flattening, not
# assumed: every chain's list in the real file is identical, which is
# exactly what a homooligomeric tie means -- one interface, shared by
# every copy -- so using chain A's list alone loses nothing.
design_positions_by_chain = mpnn_cfg["design_positions"]
assert len(set(tuple(v) for v in design_positions_by_chain.values())) == 1, \
    "design_positions differs by chain -- this dock is not a simple homooligomeric tie"
design_positions = design_positions_by_chain[chains_to_design[0]]

# Laura's own minimal stand-in pattern (tools/relax_qualifying.py's
# _fake_docked_oligomer): _copy0_chains only ever reads
# docked_oligomer["copies"][0] for a chain count, and every copy in a real
# docked assembly is a placement of the same scaffold -- no need to redo
# the docking geometry just to get a number already implicit in the
# scaffold file.
docked_oligomer = {"copies": [(CAGEFLOW_DIR / "1c5e_trimer.pdb").read_text()]}

# The real designed sequence for this dock, reformatted from
# fold_seq_20/input.fasta's ColabFold-style ":"-joined trimer (3 copies,
# what folding tests) to thread_sequence()'s own expected "/"-joined,
# full-assembly-length format (12 copies, matching chains_to_design --
# what Rosetta scores). Same real ProteinMPNN-designed 95-residue
# sequence either way; only the join character and copy count differ
# because the two consumers test different scopes. Verified before
# reformatting, not assumed: all 3 of fold_seq_20's copies are identical,
# confirming this is genuinely a homooligomeric tie.
fasta_text = (DOCK44 / "fold_seq_20" / "input.fasta").read_text()
copies = fasta_text.splitlines()[1].split(":")
assert len(set(copies)) == 1, "fold_seq_20 is not a homooligomeric tie -- reformatting would be wrong"
seq = "/".join([copies[0]] * len(chains_to_design))

result = rosetta_scoring.score_sequence(
    assembly_pdb, seq, chains_to_design, design_positions, docked_oligomer, symm_file,
    chi_min=True, relax=False, seed=20260831,
)

check("score_sequence returned the real metric set",
      isinstance(result, dict) and "dG_separated" in result,
      str(sorted(result))[:200])
check("dG_separated is a real number",
      isinstance(result.get("dG_separated"), float), str(result.get("dG_separated")))
check("shape_complementarity is a real number",
      isinstance(result.get("shape_complementarity"), float),
      str(result.get("shape_complementarity")))

out_path = Path(__file__).resolve().parent / "artifact.json"
out_path.write_text(json.dumps({
    k: v for k, v in result.items() if isinstance(v, (int, float, str, type(None)))
}, indent=2, default=str))

print(f"\ndG_separated = {result['dG_separated']:.3f}  "
      f"shape_complementarity = {result['shape_complementarity']:.3f}")
