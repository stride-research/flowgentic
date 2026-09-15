"""
Note: you have a full example breakdown commented at the end of this document

CageFlow, adaptive: a sketch of what we would build.

Pseudocode that happens to be Python. Nothing here runs, nothing is tuned,
no number means anything. The point is the shape.

CageFlow today runs every stage through executors.py, whose whole contract
is one line:

    map(fn, arg_list) -> list of results, in submission order

It is honest and it works. But it can only say "run these, tell me when
they're all done", and that forecloses five decisions:

    (1) DECLARE     this task needs a GPU, that one needs a CPU
    (2) REACT       act on a result when it lands, not when the slowest
                    task in its stage lands
    (3) CHOOSE      when a scarce slot frees, pick who gets it next
    (4) STOP        cancel work whose result can no longer change anything
    (5) RETHINK     change the rule mid-run, when the model we were
                    selecting by stops being right

Markers (1)..(5) point at where each one lives

Our architecture:
    Architecture

╔══════════════════════════════════════════════════════════════════════╗
║  AGENT                                        cadence: ~every N rounds ║
║                                                                        ║
║  observes   calibration drift · budget per pool · diversity · spend    ║
║  acts       retune_selection · retrain · distrust · reshape_search     ║
║             replicate · abandon · add_goal / revoke_goal · stop        ║
║                                                                        ║
║  changes the RULES, never ranks a candidate                           ║
╚═══════════════════════════════════╤══════════════════════════════════╝
                                    │ bar, goals, trust, spread
                                    ▼
╔══════════════════════════════════════════════════════════════════════╗
║  ALLOCATOR                                   cadence: every freed slot ║
║                                                                        ║
║      c = best_by(value_per_unit, ready)     ← argmax, pure arithmetic  ║
║               ╱                    ╲                                   ║
║              ▼                      ▼                                  ║
║   ┌────────────────────┐   ┌──────────────────────┐                   ║
║   │ TRAJECTORY MODEL   │   │ BUDGET  (a vector)   │                   ║
║   │ predict → value, σ │   │  gpu ▓▓▓▓░░░░  40 h  │                   ║
║   │ calibration()      │   │  cpu ▓▓░░░░░░ 900 h  │                   ║
║   │ update(actual)     │   │  scarcest() → gpu    │                   ║
║   └─────────▲──────────┘   └──────────────────────┘                   ║
╚═════════════╪══════════════════════╤═══════════════════════════════════╝
              │ free labels          │ submit(resource=gpu) · cancel()
              │                      ▼
              │        ┌──────────────────────────────────────┐
              │        │  ASYNCFLOW / RADICAL-PILOT           │  exists
              │        │  places tasks, returns as they land  │  already
              │        └──────────────────┬───────────────────┘
              │                           ▼
              │        ┌──────────────────────────────────────┐
              └────────┤  THE SCIENCE                         │  CNIO's,
        every expensive│  dock → geom → design → score → fold │  unchanged
        result is one  └──────────────────────────────────────┘
        labelled example

Notes from Laura meeting:
    - Go and make this generalizable
    - Interface design can be run in either cpu or gpu (potentially if gpu is too slow just try the cpu -- checking the queue for instance)
    - 


"""


# ══════════════════════════════════════════════════════════════════════════
# THE PIPELINE
#
# CageFlow's seven stages, as its own docstring lists them. Cost, hardware
# and noise differ per stage, and that is the entire problem: there is no
# single "unit of compute" to budget in.
# ══════════════════════════════════════════════════════════════════════════
STAGES = {
    #  name              hardware  rough cost   noise           status
    "dock":             ("cpu",   "ms",        "none",         "real"),
    "geometry":         ("cpu",   "ms",        "none",         "real"),
    "design":           ("gpu",   "seconds",   "sampled @T",   "real"),   # ProteinMPNN
    "score":            ("cpu",   "20-40 s",   "packer, unseeded", "real"),  # Rosetta
    "fold":             ("gpu",   "minutes",   "model",        "NOT IMPLEMENTED"), # ESMFold
    "rmsd":             ("cpu",   "ms",        "none",         "NOT IMPLEMENTED"),
    "fold_cage":        ("gpu",   "many min",  "model",        "NOT IMPLEMENTED"), # Alphafold
}



# ══════════════════════════════════════════════════════════════════════════
# THE THREE THINGS WE ADD
# ══════════════════════════════════════════════════════════════════════════

class Budget:
    """A vector, not a number.

    GPU-hours and CPU-hours are separate pools and you cannot spend one on
    the other. An allocator tracking a single scalar will cheerfully plan
    work it has no hardware left to run. 
    """
    def affords(self, stage): ...
    def charge(self, stage): ...
    def scarcest(self): ...          # which pool is the binding constraint right now


class TrajectoryModel:
    """Predicts an expensive outcome from cheap evidence, with error bars.

    This is what makes the whole thing more than a scheduler. Given partial
    evidence about a candidate (geometry passed, design score is 0.8, no
    fold yet) it answers: what will the expensive stages say, and how sure
    am I?

    The uncertainty is the important half. It is what lets us tell apart
    "this candidate is bad" from "we don't know yet, and finding out is
    cheap", which is the difference between pruning and gambling.

    We assume it could be the case that a score in the cheap stage mays stop predicting 
    the score in the expensive ones, meaning the model is uncertain for that input 
    regionp´

    """
    def predict(self, candidate) -> "value, uncertainty": ...

    def value_per_unit(self, candidate, budget):
        """Expected gain divided by cost in the SCARCEST pool.

        Not cost in general. A candidate that needs the resource we are
        about to run out of is expensive even if it is cheap in wall-clock
        (i.e., small absolute wall clock, but high relative percentage of the budget store).
        """

    def calibration(self) -> float:
        """How far predictions have drifted from outcomes, lately.

        Every expensive stage we pay for hands us one (predicted, actual)
        pair for free. When these stop agreeing, every decision downstream
        of this model is being made on bad information.
        """

    def update(self, candidate, actual): ...


class Agent:
    """
    Core definition:
        ´The agent sets the terms the allocator works under: where to sample, what to trust,
        what counts as good enough, and when to stop. It never ranks candidates and never picks 
        which one runs next — that stays arithmetic.´

    The rare decision. Not a scheduler, and not one LLM call.

    It runs perhaps every twentieth round, reads the run's state, and picks
    from a fixed, typed set of actions. Each action is auditable, reversible
    and logged with its justification. That is a bounded action space, which
    is what makes this defensible rather than "we asked a model."

    Deliberately NOT in this list: which candidate runs next. That is
    arithmetic (TrajectoryModel.value_per_unit) and must stay arithmetic --
    reproducible, cheap, and inspectable by a reviewer.

    Actions are revertible. For instance, if callibration falls after retraining we 
    should revert such action

    """

    # --- what it can look at ---
    def observe(self):
        """calibration drift, budget remaining per pool, diversity of
        surviving candidates, per-stage noise, what's already been spent"""

    # --- what it can do ---

    def retune_selection(self, bar):
        """Change how picky we are.

        Was: buy a GPU run for anything above 0.5. Twenty bought, most came
        back mediocre. Now: 0.7. Eight bought, better ones.
        Goes the other way too: budget about to expire unspent, lower it.
        """

    def retrain(self):
        """Refit the trajectory model on recent rounds.

        We switched scaffold at round 20. What the model learned on the old
        one no longer holds. Forget the old examples.
        """

    def distrust(self, stage):
        """Stop feeding one signal to the model.

        The docking `score` turned out to be near-noise for ranking.
        Drop it, keep contact count.
        """

    def reshape_search(self, spread):
        """Change where the next candidates come from.

        Three rounds, no winners  -> look further out.
        Found a good region       -> look closer in.
        """

    def replicate(self, candidate, n):
        """Buy certainty instead of novelty.

        Top two candidates differ by 0.3 kcal/mol. Rosetta's own noise is
        about 0.3 kcal/mol. So we do not actually know which is better.
        Run each three more times instead of trying a new candidate.
        """

    def abandon(self, scaffold):
        """Kill a branch, take its budget back.

        Scaffold B is fifteen rounds in and below A's median. Stop it.
        Give the leftover GPU-hours to A.
        """

    def add_goal(self, goal):
        """Add a condition that did not exist at launch.

        Winners have all converged to nearly the same sequence.
        Add "keep diversity above X", and the allocator starts preferring
        candidates unlike the current winners.
        """

    def revoke_goal(self, goal):
        """Drop a target that turned out to be unreachable.

        We asked for better than -12. This scaffold tops out near -9.
        Drop it, or the run never ends.
        """

    def stop(self):
        """Stop spending, hand back what is left. Campaing ends.

        Ten rounds, no improvement. Nothing in CageFlow can do this today:
        optimize_loop runs n_rounds and exits regardless of what it found.
        """

# `replicate` is worth pausing on. Rosetta's packer is stochastic and
# CageFlow does not seed it, so two runs of the same design give different
# ddG. When two candidates are within that noise band, the right move is
# sometimes to spend more on measuring rather than more on exploring. No
# stage-based pipeline can express "buy more certainty about this one".


# ══════════════════════════════════════════════════════════════════════════
# THE LOOP
#
# Two loops, actually, at different speeds: orchestration outside, science inside.
# ══════════════════════════════════════════════════════════════════════════
def adaptive(budget, model, agent):

    while not converged and budget.remaining():

        # ── OUTER: propose. CageFlow's loop resamples docks from a
        # distribution reshaped by last round's winners. Adaptivity is not
        # only filtering a fixed list, it is choosing where to look next.
        candidates = propose(from_=winners_so_far, spread=agent.spread) # After 1 sweep of a grid over the space
                                                                        # u later sample from gaussian whose center
                                                                        # is pouleld toward the prior winners. Agent can define the sprea

        pending = {dock(c): "dock" for c in candidates}

        while pending:

            # (3) CHOOSE: a slot just freed. Spend it on the best candidate
            # known RIGHT NOW, ranked by expected value per unit of the
            # scarcest resource. Not the first to arrive; not a set fixed
            # before any evidence existed.
            while free_slot(budget.scarcest()) and budget.affords("design"):
                c = best_by(model.value_per_unit, ready)
                if model.predict(c).value < bar: # bar is thresholfd for score on gpu usage
                    break                     # nothing left is worth the hardware
                pending[design(c)] = "design"
                budget.charge("design")

            # (4) STOP: nothing further is affordable, so more cheap evidence
            # cannot change any decision. Cancel it instead of completing it.
            if not budget.affords("design"): # dock evaluates wether design is appropiate 
                cancel(f for f, stage in pending.items() if stage == "dock")

            # (2) REACT: handle whatever lands next
            for result, stage in first_completed(pending):

                if stage in ("dock", "geometry"):
                    ready.append(result)

                elif stage == "design":
                    model.update(result.candidate, actual=result)   # free label
                    if budget.affords("score"):
                        pending[score(result)] = "score"            # start NOW
                        budget.charge("score")

                elif stage == "score":
                    model.update(result.candidate, actual=result)
                    record(result)

        # ── INNER: rethink. (5) We paid for expensive stages this round, so
        # we now know whether the model we were selecting by was right. If it
        # has drifted, continuing to spend on its advice is burning budget.
        if model.calibration() < threshold: 
            agent.act(agent.observe())      # picks from the action set above


"""
## Full example:

    Two different campaign config
    running, 200 GPU-hours and 1000 CPU-hours, goal ddG < -12.

    Round: 1–8
    What's observed: Normal. Allocator ranks by value_per_unit, spends GPU on the
    top.
    Agent acts: —
    ────────────────────────────────────────
    Round: 9
    What's observed: Model predicted ~0.75 for its picks; the Rosetta scores coming
    back sit at the population median. Calibration is dropping.
    Agent acts: distrust("design") — the ProteinMPNN score has decoupled from ddG,
    stop weighting it heavily. retrain() — refit on recent rounds.
    ────────────────────────────────────────
    Round: 12
    What's observed: Post-retrain the model is honest but less confident. A bar of
    0.5 now lets too much through.
    Agent acts: retune_selection(0.7)
    ────────────────────────────────────────
    Round: 15
    What's observed: Campaing B has eaten 60 GPU-h, best is −7.2. campaign A's
    median is −8.5. B's last five rounds flat.
    Agent acts: abandon("B") — ~40 GPU-h returns to the pool for A. => IS THIS A STRONG ASSUMPTION?
    ────────────────────────────────────────
    Round: 18
    What's observed: A's last four rounds of winners share 17 of 20 interface
    positions. Mode collapse.
    Agent acts: add_goal(diversity > 0.4) and reshape_search(spread=wide) — stop
    circling the same pocket.
    ────────────────────────────────────────
    Round: 24
    What's observed: Top two candidates: −11.8 and −11.5. Rosetta's own run-to-run
    spread is ~0.3. We cannot actually tell them apart.
    Agent acts: replicate(top1, n=3), replicate(top2, n=3)
    ────────────────────────────────────────
    Round: 27
    What's observed: Best confirmed at −11.6. The −12 target looks unreachable on
    this scaffold.
    Agent acts: revoke_goal(ddG < -12), add_goal(best achievable in remaining
    budget)
    ────────────────────────────────────────
    Round: 30
    What's observed: Ten rounds no improvement, honest check agrees with the cheap
    score, 15 GPU-h left.
    Agent acts: stop()

## Small ntoes:
        - we make an estimate of the cost per stage, then reconcile the difference when the job finished
        - agent.abandon() assumes we can claim back part of the budget that wasnt used. if this is not the case,
        we would change our strategy around this method
"""