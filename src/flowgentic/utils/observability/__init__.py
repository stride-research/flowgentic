"""
Flowgentice Observability Module

Provides unified observability (logging, tracing, metrics) using OBSERVE.
Automatically configures for HPC environments and LangGraph workflows.
"""

import os
from typing import Optional

from observe import (
	ObservabilityConfig,
	create_logger,
	create_metrics,
	create_tracer,
	flush_all_data,
	initialize_observability,
	record_log,
	record_metric,
	record_trace,
)

__all__ = [
	"initialize_flowgentice_observability",
	"get_flowgentice_logger",
	"get_flowgentice_tracer",
	"get_flowgentice_metrics",
	"record_log",
	"record_trace",
	"record_metric",
	"flush_all_data",
]

# Global state
_initialized = False
_config: Optional[ObservabilityConfig] = None


async def initialize_flowgentice_observability(
	application_name: str = "flowgentice",
	output_dir: str = "./observability_data",
	preset: Optional[str] = None,  # Auto-detect or override
	**kwargs,
) -> ObservabilityConfig:
	"""
	Initialize OBSERVE for Flowgentice.

	Args:
		application_name: Name of the application
		output_dir: Directory for observability data
		preset: Environment preset (development, hpc, production, testing)
		**kwargs: Additional OBSERVE configuration options

	Returns:
		ObservabilityConfig instance
	"""
	global _initialized, _config

	if _initialized:
		return _config

	# Auto-detect HPC environment if not specified
	if preset is None:
		preset = _detect_environment()

	# Initialize OBSERVE with Flowgentice-specific defaults
	_config = await initialize_observability(
		application_name,
		output_dir=output_dir,
		preset=preset,
		output_mode="workflow_based",  # Organize by workflow/session
		logs_format="jsonl",  # JSONL for streaming
		traces_format="parquet",  # High-performance for traces
		metrics_format="parquet",  # High-performance for metrics
		**kwargs,
	)

	_initialized = True
	return _config


def _detect_environment() -> str:
	"""Detect execution environment (HPC, development, etc.)"""

	# Check for HPC indicators
	if any(key in os.environ for key in ["SLURM_JOB_ID", "PBS_JOBID", "LSB_JOBID"]):
		return "hpc"

	# Check for CI/testing - use debugging preset for tests
	if os.environ.get("CI") or os.environ.get("PYTEST_CURRENT_TEST"):
		return "debugging"

	# Default to development
	return "development"


def get_flowgentice_logger(component: str):
	"""Get a logger for a Flowgentice component"""
	if not _initialized:
		raise RuntimeError(
			"Observability not initialized. Call initialize_flowgentice_observability() first."
		)
	return create_logger(component)


def get_flowgentice_tracer(service: str):
	"""Get a tracer for a Flowgentice service"""
	if not _initialized:
		raise RuntimeError(
			"Observability not initialized. Call initialize_flowgentice_observability() first."
		)
	return create_tracer(service)


def get_flowgentice_metrics(namespace: str):
	"""Get a metrics collector for a Flowgentice namespace"""
	if not _initialized:
		raise RuntimeError(
			"Observability not initialized. Call initialize_flowgentice_observability() first."
		)
	return create_metrics(namespace)
