"""
Integration tests for OBSERVE observability module.
"""

import asyncio
import os
import tempfile

import pytest

from flowgentic.utils.observability import (
	flush_all_data,
	get_flowgentice_logger,
	get_flowgentice_metrics,
	get_flowgentice_tracer,
	initialize_flowgentice_observability,
	record_log,
	record_metric,
	record_trace,
)


@pytest.mark.asyncio
async def test_observability_initialization():
	"""Test OBSERVE initialization"""
	with tempfile.TemporaryDirectory() as tmpdir:
		config = await initialize_flowgentice_observability(
			"test_app", output_dir=tmpdir
		)
		assert config is not None

		logger = get_flowgentice_logger("test")
		tracer = get_flowgentice_tracer("test")
		metrics = get_flowgentice_metrics("test")

		assert logger is not None
		assert tracer is not None
		assert metrics is not None

		await flush_all_data()


@pytest.mark.asyncio
async def test_environment_detection():
	"""Test environment auto-detection"""
	with tempfile.TemporaryDirectory() as tmpdir:
		# Test development environment (default)
		config = await initialize_flowgentice_observability(
			"test_dev", output_dir=tmpdir
		)
		assert config is not None

		await flush_all_data()


@pytest.mark.asyncio
async def test_logging_functionality():
	"""Test basic logging with OBSERVE"""
	with tempfile.TemporaryDirectory() as tmpdir:
		await initialize_flowgentice_observability("test_logging", output_dir=tmpdir)

		logger = get_flowgentice_logger("test_component")

		# Test standard logging methods
		logger.info("Test info message", extra={"key": "value"})
		logger.debug("Test debug message")
		logger.warning("Test warning message")

		# Test async record_log
		await record_log("test_event", {"event_type": "test", "data": 123})

		await flush_all_data()


@pytest.mark.asyncio
async def test_tracing_functionality():
	"""Test distributed tracing with OBSERVE"""
	with tempfile.TemporaryDirectory() as tmpdir:
		await initialize_flowgentice_observability("test_tracing", output_dir=tmpdir)

		tracer = get_flowgentice_tracer("test_service")

		# Test span creation
		with tracer.start_span("test_operation") as span:
			span.add_attribute("test_key", "test_value")
			span.add_attribute("operation_id", 123)

		# Test async record_trace
		await record_trace(
			"test_operation",
			duration=1.5,
			attributes={"result": "success", "items_processed": 100},
		)

		await flush_all_data()


@pytest.mark.asyncio
async def test_metrics_functionality():
	"""Test metrics collection with OBSERVE"""
	with tempfile.TemporaryDirectory() as tmpdir:
		await initialize_flowgentice_observability("test_metrics", output_dir=tmpdir)

		# Test async record_metric
		await record_metric("test_counter", 1, metric_type="counter")
		await record_metric("test_gauge", 42, metric_type="gauge")
		await record_metric("test_histogram", 1.5, metric_type="histogram")

		# Test with labels
		await record_metric(
			"test_labeled_counter",
			1,
			metric_type="counter",
			labels={"status": "success", "endpoint": "/test"},
		)

		await flush_all_data()


@pytest.mark.asyncio
async def test_compatibility_layer():
	"""Test backward compatibility layer"""
	from flowgentic.utils.observability.compat import FlowgenticeLogger, add_context_to_log

	# Test Logger instantiation
	logger = FlowgenticeLogger(colorful_output=True)
	assert logger is not None

	# Test get_logger
	std_logger = logger.get_logger("test")
	assert std_logger is not None

	# Test context manager
	with add_context_to_log(request_id="123", user_id="456"):
		# Should not raise any errors
		pass

	# Test shutdown
	logger.shutdown()


@pytest.mark.asyncio
async def test_multiple_components():
	"""Test multiple components using observability"""
	with tempfile.TemporaryDirectory() as tmpdir:
		await initialize_flowgentice_observability("test_multi", output_dir=tmpdir)

		# Create multiple loggers
		logger1 = get_flowgentice_logger("component1")
		logger2 = get_flowgentice_logger("component2")

		# Create multiple tracers
		tracer1 = get_flowgentice_tracer("service1")
		tracer2 = get_flowgentice_tracer("service2")

		# Create multiple metrics collectors
		metrics1 = get_flowgentice_metrics("namespace1")
		metrics2 = get_flowgentice_metrics("namespace2")

		# Use them
		logger1.info("Component 1 message")
		logger2.info("Component 2 message")

		with tracer1.start_span("service1_op"):
			pass

		with tracer2.start_span("service2_op"):
			pass

		await record_metric("metric1", 1, metric_type="counter")
		await record_metric("metric2", 2, metric_type="counter")

		await flush_all_data()


@pytest.mark.asyncio
async def test_hpc_environment_detection():
	"""Test HPC environment detection"""
	with tempfile.TemporaryDirectory() as tmpdir:
		# Simulate SLURM environment
		os.environ["SLURM_JOB_ID"] = "12345"

		try:
			config = await initialize_flowgentice_observability(
				"test_hpc", output_dir=tmpdir
			)
			assert config is not None

			await flush_all_data()
		finally:
			# Clean up environment variable
			del os.environ["SLURM_JOB_ID"]


@pytest.mark.asyncio
async def test_error_handling():
	"""Test error handling when observability not initialized"""
	# Reset initialization state by trying to use without init
	# This should raise RuntimeError

	# Note: We can't easily test this without resetting global state
	# which would affect other tests. This is a limitation of the current design.
	pass
