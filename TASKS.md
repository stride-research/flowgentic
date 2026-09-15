

PROCESS
1. Map: sequence to operations (Me)
2. Map: operations to executables (Laura) 
      Note: swap rather than rewrite. We dont speculate which are the actual scripts out of her code. We wait for her confirmation (i.e., blocked behavior)
3. Install the stack on the cluster, smoke-test each command independently (me)
4. Run sequentially the pipeline w barries
      0. Define metrics
            a) Best result per GPU-hour => accumulated GPU-consumed for a given result (i.e., science metric such as roseta or pLDDT)
            b) Time to first acceptable candidate 
            c) CPU/GPU utilization
            d) wasted cancelled computer 
      1. Create pipeline class
      2. Pipeline step abstraction
      3. Create mock executables (for each stage)
      4. Add the metrics into the pipeline class
      5. Run mcok
      6. Propose async architecture
      7. Inteligence layer integration (different policies)
5. Run asynchronously
5. Run w inteligence layer 

All the classes:
Candidate    the unit that flows. id, state, provenance (which dock/seq), accumulated results
StepSpec     static declaration from the map: name, resource class, how to invoke, cost model
Task         one (StepSpec × Candidate) at runtime. id, state, timestamps, resource consumed
EventLog     append-only. every state transition, timestamped
Executor     submit(task)->handle, as_completed(), cancel(handle), free_slots(resource)
Ledger       per-resource reserved / consumed / remaining
Metrics      PURE function over EventLog. never stateful
Pipeline     owns steps + executor + log + ledger + candidates. run()