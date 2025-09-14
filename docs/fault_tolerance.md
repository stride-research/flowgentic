# FAULT TOLERANCE
## LANGRAPH
### TOOL-LEVEL
### How It Works

1.  **Retry on Failure**: When a tool invocation fails with a specific type of error (like a network timeout or connection issue -- note: you can modify which are the retryable exceptions), the system doesn't immediately give up. Instead, it classifies the error as "retryable" and schedules another attempt. This is crucial for handling transient issues that might resolve themselves quickly.

2.  **Exponential Backoff**: To avoid overwhelming a struggling service, the system waits for a progressively longer period between each retry. For example, the first retry might be after 1 second, the second after 2, the third after 4, and so on. This gives the external service time to recover and prevents the agent from contributing to a cascade of failures.

3.  **Jitter**: To prevent multiple agents from all retrying at the exact same moment (a "thundering herd" problem), a small, random amount of time is added to the backoff delay. This "jitter" spreads out the retry attempts, reducing the chance of creating a new bottleneck.

4.  **Timeouts**: Each tool invocation is wrapped in a timeout. If the tool's `future` does not complete within the specified time limit, the attempt is aborted, and a `TimeoutError` is raised. This prevents the agent from waiting indefinitely for a non-responsive tool, allowing the retry logic to kick in or the agent to move on.

5.  **Error Handling**: If all retries are exhausted or the error is not considered retryable, the system either raises the exception (if configured to `raise_on_failure`) or returns a structured error payload. This allows the AI agent to understand that the tool failed and to potentially handle the error gracefully, for example, by trying an alternative tool or informing the user of the failure.