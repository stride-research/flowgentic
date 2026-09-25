# v2 pipeline flow

What `main.py` actually executes, one campaign, stage 1 (naive: every dock finishes
before selection, every design finishes before dedup — no async refill yet). Kept in
sync with the code, not aspirational: regenerate this by hand whenever `run()` changes.

```mermaid
flowchart TD
    grid["Build grid: RADII x SPINS\n(4 x 3 = 12 points)"]

    subgraph cpu_pool["CPU pool (2 workers)"]
        dock["dock_oligomer(i, radius, spin)"]
        geometry["evaluate_geometry(pose)"]
        relax["relax(candidate)"]
        rosetta["score_with_rosetta(relaxed)"]
        msa["fetch_msa(sequence)"]
    end

    subgraph gpu_pool["GPU pool (1 worker)"]
        design["design_interface(pose, N_SEQUENCES)"]
        fold["fold_command(sequence, msa_handle)\n(executable_task)"]
    end

    select{"select_top_docks\nclashes==0 and\ncontacts>=MIN_CONTACTS ?"}
    reject_dock(["REJECTED\nclash or too few contacts /\nnot in top N_DOCKS_KEPT"])
    supersede(["parent -> SUPERSEDED\nfanned out at design_interface"])

    dedup{"dedup_sequences\n(dock, sequence) seen\nwithin this pose?"}
    reject_dup(["DEDUPLICATED"])

    fold_cache{{"fold cache, keyed on\nsequence alone --\nshared across poses"}}

    gate{"score_with_rosetta\nddg <= ROSETTA_CUTOFF ?"}
    completed(["COMPLETED"])
    reject_score(["REJECTED\nddG above cutoff"])

    collect["collect: record ddg + fold metrics\n(pLDDT, ipTM, RMSD recorded,\nonly ddg gates)"]
    finish["RUN_FINISHED -> flow.shutdown()\nsummarise() + history.jsonl + reports/metrics.png"]

    grid --> dock --> geometry --> select
    select -- "top N_DOCKS_KEPT" --> supersede --> design
    select -- no --> reject_dock

    design -- "N_SEQUENCES children per pose" --> dedup
    dedup -- "first time" --> branch_split(( ))
    dedup -- "duplicate" --> reject_dup

    branch_split --> relax --> rosetta --> gate
    branch_split -. "sequence only,\nno relax dependency" .-> msa --> fold --> fold_cache

    fold_cache -. "looked up by sequence\nwhen its candidate scores" .-> collect
    gate -- yes --> completed --> collect
    gate -- no --> reject_score --> collect

    collect --> finish
```
