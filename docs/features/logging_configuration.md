# Logging Configuration Guide

## Overview

Flowgentic supports flexible logging configuration through `config.yml`, allowing you to control:
- **Log level**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Output destination**: stdout, file, or both
- **File rotation**: Automatic log file rotation based on size

## Configuration Structure

```yaml
logger:
  level: DEBUG                    # Logging level
  output: both                    # Output mode: "stdout", "file", or "both"
  file:                          # File configuration (only used when output is "file" or "both")
    path: logs/flowgentic.log    # Path to log file
    max_bytes: 10485760          # Max file size before rotation (10MB)
    backup_count: 5              # Number of backup files to keep
```

## Output Modes

### 1. Console Only (`stdout`)
Logs are written only to the console/terminal.

```yaml
logger:
  level: INFO
  output: stdout
```

**Use case**: Development, debugging, containerized environments where logs are captured from stdout

### 2. File Only (`file`)
Logs are written only to a file with automatic rotation.

```yaml
logger:
  level: INFO
  output: file
  file:
    path: logs/flowgentic.log
    max_bytes: 10485760  # 10MB
    backup_count: 5
```

**Use case**: Production environments, background services, when you need persistent logs

**Log rotation**: When the log file reaches `max_bytes`, it's rotated:
- `flowgentic.log` → `flowgentic.log.1`
- `flowgentic.log.1` → `flowgentic.log.2`
- etc., up to `backup_count` files

### 3. Both Console and File (`both`)
Logs are written to both console and file simultaneously.

```yaml
logger:
  level: DEBUG
  output: both
  file:
    path: logs/flowgentic.log
    max_bytes: 10485760
    backup_count: 5
```

**Use case**: Development with persistent logs, troubleshooting in production

## Log Levels

Control verbosity with the `level` setting:

- **DEBUG**: Detailed diagnostic information (most verbose)
- **INFO**: General informational messages
- **WARNING**: Warning messages about potential issues
- **ERROR**: Error events that might still allow continued execution
- **CRITICAL**: Critical events that may cause system failure (least verbose)

Example:
```yaml
logger:
  level: WARNING  # Only WARNING, ERROR, and CRITICAL logs will be shown
  output: stdout
```

## File Configuration Options

When using `output: file` or `output: both`, you can configure:

| Option | Description | Default | Example |
|--------|-------------|---------|---------|
| `path` | Path to log file (relative to project root) | `logs/flowgentic.log` | `logs/app.log` |
| `max_bytes` | Maximum file size before rotation (bytes) | 10485760 (10MB) | 52428800 (50MB) |
| `backup_count` | Number of rotated files to keep | 5 | 10 |

### Path Examples

```yaml
# Relative path (recommended)
file:
  path: logs/flowgentic.log

# Nested directories
file:
  path: logs/production/app.log

# Absolute path (not recommended)
file:
  path: /var/log/flowgentic/app.log
```

## Output Format

All logs are formatted as JSON for easy parsing and integration with log management systems:

```json
{
  "time": "2025-11-06 10:30:45,123",
  "name": "flowgentic.langGraph.main",
  "level": "INFO",
  "message": "Agent execution started"
}
```

### Console Output
- Colorized JSON (when `colorful_output=True`)
- Easier to read during development

### File Output
- Plain JSON (never colorized)
- Suitable for log aggregation tools (ELK, Splunk, etc.)
- UTF-8 encoded

## Common Configurations

### Development
```yaml
logger:
  level: DEBUG
  output: both
  file:
    path: logs/dev.log
    max_bytes: 5242880  # 5MB
    backup_count: 3
```

### Production
```yaml
logger:
  level: INFO
  output: file
  file:
    path: logs/production.log
    max_bytes: 52428800  # 50MB
    backup_count: 10
```

### CI/CD Pipeline
```yaml
logger:
  level: INFO
  output: stdout  # Logs captured by CI system
```

### Debugging Issues
```yaml
logger:
  level: DEBUG
  output: both  # See logs in real-time AND save to file
  file:
    path: logs/debug.log
    max_bytes: 10485760
    backup_count: 5
```

## Programmatic Usage

The logger is automatically initialized when you import `flowgentic`. To use it in your code:

```python
import logging
import flowgentic  # Initializes logger based on config.yml

# Get a logger for your module
logger = logging.getLogger(__name__)

# Use standard logging methods
logger.debug("Detailed information")
logger.info("General information")
logger.warning("Warning message")
logger.error("Error occurred")
logger.critical("Critical failure")

# Log with structured data
logger.info("User action", extra={
    "user_id": "12345",
    "action": "login",
    "status": "success"
})

# Log exceptions with traceback
try:
    risky_operation()
except Exception as e:
    logger.exception("Operation failed")
```

## Troubleshooting

### Logs not appearing in file
1. Check that `output` is set to `file` or `both`
2. Verify the `file.path` is correct
3. Ensure the application has write permissions to the log directory
4. Check if log level filters out your messages

### File rotation not working
1. Verify `max_bytes` is set to a reasonable value
2. Check that `backup_count` is greater than 0
3. Ensure sufficient disk space

### Console output missing colors
- Colors only appear in stdout mode when `colorful_output=True`
- File logs are never colorized (by design)

## Best Practices

1. **Use appropriate log levels**: Don't log everything as INFO
2. **Configure for environment**: DEBUG for dev, INFO/WARNING for production
3. **Monitor log file size**: Adjust `max_bytes` based on your log volume
4. **Keep enough backups**: Set `backup_count` to retain sufficient history
5. **Use structured logging**: Add `extra` fields for better log analysis
6. **Avoid sensitive data**: Don't log passwords, tokens, or PII
7. **Regular log review**: Check logs periodically for errors and warnings

## Related Files

- `src/flowgentic/utils/logger/logger.py` - Logger implementation
- `src/flowgentic/__init__.py` - Logger initialization
- `config.yml` - Configuration file
