# HOW TO DO SUPERVISOR
1. Create the state by extending the default SupervisorGraphState which is needed because of ... [fill in here]
2. Create the llm router like this:
        llm_router = agents_manager.execution_wrappers.asyncflow(
            create_llm_router(routing_prompt_template, router_model),
            flow_type=AsyncFlowType.EXECUTION_BLOCK
        )
3. Define agents and tools
4. Add introspectable nodes (optional)
5. Add conditional edges between llm router and supervisor_fan_out (imported from from flowgentic.langGraph.utils.supervisor)
6. Add connections between parallel agents to gather (fan-out)