#!/usr/bin/env python3
"""Stand-in for ColabFold, invoked exactly as the real one would be.

Deliberately a separate process with a command-line interface, because that is
how CageFlow already calls ColabFold: its own source notes that it must run as
a subprocess in ColabFold's conda environment and "never imported", since the
environments conflict irreconcilably. The same is true of ProteinMPNN and
PyRosetta.

Replacing this with the real thing means changing the command string in
main.py. Nothing about the pipeline's structure changes.

Usage:
    fold_stub.py --sequence SEQ1 --msa msa::abc123
"""

import argparse
import hashlib
import json
import random
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--msa", required=True)
    parser.add_argument("--seconds", type=float, default=0.15)
    args = parser.parse_args()

    # Seeded on the sequence so the same input always folds to the same
    # numbers, which is what makes the cross-dock cache observable.
    seed = int(hashlib.md5(args.sequence.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    time.sleep(args.seconds)

    print(json.dumps({
        "sequence": args.sequence,
        "msa": args.msa,
        "plddt": round(rng.uniform(0.5, 0.95), 3),
        "iptm": round(rng.uniform(0.4, 0.90), 3),
        "rmsd": round(rng.uniform(0.5, 4.0), 3),
    }))


if __name__ == "__main__":
    main()
