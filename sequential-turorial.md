# SEQUENTIAL

## 1) Set-up
### main.py
1. Specify backend to be used
2. Use langraph integration object
3. Start the introspector
4. Build workflow (see below)

## 2) Tools
### actions.py
1. Create an actions.py folder. Here you will define all the agent tools or function/service tasks . Best practice: a class per node.
2. Decorate with functions with the corresponding wrapper type
3. Return the tools tools in a dictionary with key being name (that will be used by the graph nodes (see below)) and value being the corresponding callabe

### actions_registry.py
1. Inherit the BaseToolRegistry object. Append here all the tools and tasks for each of the nodes
2. Define a register _register_agent_tools function that aggreagte to the agent_tools attribute the tools for the agents. Do the same for function tasks

## 3) Nodes
### nodes.py
1. Define all the nodes with the following pattern: property wrapper around an instance method that returns a callable decorated by the asyncflow wrapper EXECUTION_BLOCK
2. Define a get_all_nodes method that returns a dict with key name of the node and value the corresponding callable

## 4) Workflow Builder
### builder.py
1. Register the tools 
2. Create a StateGraph instance with the proper workflow state
3. Register all nodes (iterate through returned dict ) and do: 1) add the node for introspection (this will allow it to be tracked for a final usage report), 2) the output of 1 is added as a node to the workflow
4. Invoke register nodes to introspection which will is nededed for the report after all the nodes have been added
5. Add (conditional edges) to the graph 
6. Retutrn workflow object

## 5) Final configuration
1. Buid the workflow
2. Add checkpointer (optional)
3. Create initial state
4. Stream the graph
5. Generate report by calling the agent introspector and render the graph