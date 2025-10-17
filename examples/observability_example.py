"""
Example demonstrating Flowgentice OBSERVE integration.

This example shows how to use the unified observability API for logging,
tracing, and metrics in a Flowgentice application.
"""

import asyncio
import time

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


async def process_data(items: list, logger, tracer):
	"""Simulate data processing with observability."""

	logger.info("Starting data processing", extra={"item_count": len(items)})

	# Create a trace span for this operation
	with tracer.start_span("process_batch") as span:
		span.add_attribute("batch_size", len(items))

		start_time = time.time()

		# Simulate processing
		processed_items = []
		for i, item in enumerate(items):
			# Log individual item processing
			await record_log(
				"item_processed", {"item_id": i, "item_value": item, "batch_size": len(items)}
			)

			# Simulate work
			await asyncio.sleep(0.1)
			processed_items.append(item.upper())

			# Record progress metric
			await record_metric(
				"items_processed",
				1,
				metric_type="counter",
				labels={"batch_id": "batch_001"},
			)

		duration = time.time() - start_time

		# Record operation trace
		await record_trace(
			"process_batch",
			duration=duration,
			attributes={
				"batch_size": len(items),
				"items_processed": len(processed_items),
				"status": "success",
			},
		)

		# Record duration metric
		await record_metric(
			"batch_processing_duration_seconds",
			duration,
			metric_type="histogram",
			labels={"batch_id": "batch_001"},
		)

		span.add_attribute("duration_seconds", duration)
		span.add_attribute("items_processed", len(processed_items))

		logger.info(
			"Data processing completed",
			extra={"items_processed": len(processed_items), "duration": duration},
		)

		return processed_items


async def simulate_error_handling(logger, tracer):
	"""Demonstrate error handling with observability."""

	logger.info("Starting error handling demonstration")

	with tracer.start_span("risky_operation") as span:
		try:
			# Simulate an operation that might fail
			await asyncio.sleep(0.1)

			# Simulate error
			raise ValueError("Simulated error for demonstration")

		except Exception as e:
			# Log the error
			logger.error(
				"Operation failed",
				extra={"error_type": type(e).__name__, "error_message": str(e)},
			)

			# Add error to span
			span.add_attribute("error", True)
			span.add_attribute("error_type", type(e).__name__)
			span.add_attribute("error_message", str(e))

			# Record error metric
			await record_metric(
				"operations_total",
				1,
				metric_type="counter",
				labels={"status": "error", "error_type": type(e).__name__},
			)

			logger.info("Error handled gracefully")


async def main():
	"""Main example function."""

	# Initialize observability (auto-detects environment)
	print("🚀 Initializing Flowgentice observability...")
	config = await initialize_flowgentice_observability(
		"observability_example", output_dir="./example_observability_data"
	)
	print(f"✅ Observability initialized with config: {config}")

	# Create observability components
	logger = get_flowgentice_logger("example_app")
	tracer = get_flowgentice_tracer("example_service")
	metrics = get_flowgentice_metrics("example_namespace")

	logger.info("Starting observability example")

	# Example 1: Process data with full observability
	print("\n📊 Example 1: Data processing with observability")
	items = ["hello", "world", "observability", "rocks"]
	result = await process_data(items, logger, tracer)
	print(f"   Processed {len(result)} items: {result}")

	# Example 2: Error handling
	print("\n⚠️  Example 2: Error handling with observability")
	await simulate_error_handling(logger, tracer)

	# Example 3: Multiple operations with metrics
	print("\n📈 Example 3: Recording various metrics")
	await record_metric("app_startup_count", 1, metric_type="counter")
	await record_metric("memory_usage_mb", 256, metric_type="gauge")
	await record_metric("request_latency_ms", 45.2, metric_type="histogram")

	# Example 4: Concurrent operations
	print("\n🔄 Example 4: Concurrent operations")
	tasks = [
		process_data(["a", "b"], logger, tracer),
		process_data(["c", "d"], logger, tracer),
		process_data(["e", "f"], logger, tracer),
	]
	results = await asyncio.gather(*tasks)
	print(f"   Processed {len(results)} batches concurrently")

	logger.info("Observability example completed")

	# Flush all data before exit
	print("\n💾 Flushing observability data...")
	await flush_all_data()
	print("✅ All observability data flushed successfully")

	print("\n📁 Check ./example_observability_data/ for output files")


if __name__ == "__main__":
	asyncio.run(main())
