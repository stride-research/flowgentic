from re import sub
import subprocess

# Design Patterns
subprocess.run(
	args=["make", "examples-sequential-research"],
	check=True,  # Raises CalledProcessError if return code is non-zero
)
subprocess.run(
	args=["make", "examples-supervisor-toy"],
	check=True,  # Raises CalledProcessError if return code is non-zero
)

# Memory
# Services
# MCP
