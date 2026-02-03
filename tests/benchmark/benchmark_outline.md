# Outline
This document explain on how we generate the data and the corresponding plots cited in the paper for this thursday. Some references are also made to other peripheral work to carry out for the same deadline. 

Code principle: have a benchmark folder within test that can read a config file for the paremets of the workload that is being benchmarked. We have different modular experiments each responsible for their data generation and data plotting. Please use the existing examples as references of your work should be structured.

DoD: The definition of done of the plots is not only to be able to run locally but also to have them executed in HPC cluster for thursday as well\

Code set-up:
- please make sure your work is branched out: "hotfix/benchmarking_redesign"
- write ur experiments tests/benchmark/run_experiments.py and test/benchmark/experiments. 
- please try to avoid chaning any code outside the test/benchmark folder
- make ur code modular and not overengineered. 
- remember to use the user-defined input provided in config.yml! If u feel u need to expand the parameters defiend based on the requirements of ur experiment feel free to do so
- unless explicitly note differnetly in ur experiment (e.g., experiment 3), please use the workload that uses langgraph + asyncflow (our standard) as per defined here: tests/benchmark/data_generation/workload/langgraph.p
- plots should be generated in results/{experiment_config_name}/plots/{experiment_name}/...

# Primitives:
- Measuring overhead, througnput of flowgentic its done accross multiple experiments. If we work in silos (not sharing the progress in this overlapping areas) we would be following a suboptimal strategy. We should actively communicate via our channel about the progress in this cross-experiment shared areas for us to recyle that and integrate it into our tasks 

# Glossary
- Dummy workload: the most basic representation of an agent (no complex tool calling, minimal # of nodes/edges in teh graph) (current implementation in: tests/benchmark/data_generation/workload/langgraph.py is dummy )

# Experiments 
## EXPERIMENT 1: Coordination Overhead and Scalability 
Major details about these 3 figures in page 7 of the paper 
### Task 1: figure 1a plot
#### Responsible 
xxx 
#### Deadline
Midnight feb 4
### Task : figure 1b plot
#### Responsible 
xxx 
#### Deadline
Midnight feb 4


## EXPERIMENT 2: Scaling with synthetic adaptive workload
### Task 1: getting the strong and weak scaling plots 
#### Iterative process:
1. Strong scaling for dummy workload report makespan, then weak scaling
2. Report coordination (i.e., flowgentic) overhead
3. Report throughput

#### Responsible 
Javi 
#### Deadline
Midnight feb 4

## EXPERIMENT 3: 
### Task 1: getting plots varying backend execution engine 
#### Iterative process:
1. Report coordination (i.e., flowgentic) overhead with varying execution engines (e.g., Parsl vs Asyncflow)
2. Report throughput with varying execution engines (e.g., Parsl vs Asyncflow)

#### Responsible 
xxx
#### Deadline
Midnight feb 4

## EXPERIMENT 4:
Yousef's work. Details excluded from here

# Peripheral Work
## Framework Support Expansion
```
I just added support for microsoft autogen and parsl. Yall can now check out examples: https://github.com/stride-research/flowgentic/pull/89
Examples are: (asyncflow + langgraph), (asyncflow + autogen), (parsl + langgraph), (parsl + autogen). You can see how FG's footprint is minimal. Alter the number of tools + backend slots and assess the makespan printed at the end accordingly. Small note: footprint for autogen is slgihtly larger cause there are some extra lines for creaeting a DumyLLMProvider. Langgraph was easier)
I think it would be beneiftial if @Diana Cordovez @Diego Oliveros continue this expansion toward other frameworks. Yall can organize as u prefer. I suggest expanding to these multi-agent orchestraion framewokrs first (due to relevance): LlamaIndex, CrewAI, Academy-Agents. For backend engines: Ray (see: https://github.com/ray-project/ray) 
```
## Update documentation based on new API
### Responsible: 
xxx
## Add test suite for the existing API
### Responsible: 
xxx
## Add more complex workload with DummyLLMProvider
### Responsible: 
xxx

# Next Sprint
- Add queue for increased throughput
- Test all experiemtns with different backend engines 
- Move away from dummy workload (e.g., dynamic execution feedback)
- Allow to define worklaod as number of agents with number of tools fixed (currently we only support the opposite)
- Report ensembles
- Figure 1c