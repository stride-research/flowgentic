from enum import Enum
from pydantic import BaseModel
from typing import Any, Dict, List


class TaskState(BaseModel):
	task_id: int


class WorkloadType(str, Enum):
	FIXED_AGENTS_VARY_TOOLS = "fixed_agents_vary_tools"
	FIXED_TOOLS_VARY_AGENTS = "fixed_tools_vary_agents"


class BenchmarkConfig(BaseModel):
	"""Configuration for benchmark runs"""

	# 1) Defined in config.yml and not modifed
	run_name: str
	run_description: str

	workload_id: str

	n_of_agents: int
	n_of_tool_calls: int
	n_of_backend_slots: int

	# 2) Edited by the benchmarking program
	workload_type: WorkloadType = WorkloadType.FIXED_AGENTS_VARY_TOOLS
	tool_execution_duration_time: int


class BenchmarkResult(BenchmarkConfig):
	makespan: float
	total_flowgentic_overhead: float
	total_execution_time: float


class WorkloadResult(BaseModel):
	result: Dict[str, Any]
	flowgentic_overhead: float
	execution_time: float
