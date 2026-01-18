import json

from pythonjsonlogger import jsonlogger


# ANSI color codes
class Colors:
	# Reset
	RESET = "\033[0m"

	# Basic colors
	BLACK = "\033[30m"
	RED = "\033[31m"
	GREEN = "\033[32m"
	YELLOW = "\033[33m"
	BLUE = "\033[34m"
	MAGENTA = "\033[35m"
	CYAN = "\033[36m"
	WHITE = "\033[37m"

	# Bright colors
	BRIGHT_BLACK = "\033[90m"
	BRIGHT_RED = "\033[91m"
	BRIGHT_GREEN = "\033[92m"
	BRIGHT_YELLOW = "\033[93m"
	BRIGHT_BLUE = "\033[94m"
	BRIGHT_MAGENTA = "\033[95m"
	BRIGHT_CYAN = "\033[96m"
	BRIGHT_WHITE = "\033[97m"

	# Background colors
	BG_BLACK = "\033[40m"
	BG_RED = "\033[41m"
	BG_GREEN = "\033[42m"
	BG_YELLOW = "\033[43m"
	BG_BLUE = "\033[44m"
	BG_MAGENTA = "\033[45m"
	BG_CYAN = "\033[46m"
	BG_WHITE = "\033[47m"

	# Styles
	BOLD = "\033[1m"
	DIM = "\033[2m"
	ITALIC = "\033[3m"
	UNDERLINE = "\033[4m"


# Custom colorful formatter
class ColoredJSONFormatter(jsonlogger.JsonFormatter):
	"""A JSON formatter that adds colors to the output"""

	LEVEL_COLORS = {
		"DEBUG": Colors.BRIGHT_BLACK,
		"INFO": Colors.BRIGHT_BLUE,
		"WARNING": Colors.BRIGHT_YELLOW,
		"ERROR": Colors.BRIGHT_RED,
		"CRITICAL": Colors.RED + Colors.BOLD,
	}

	LEVEL_EMOJIS = {
		"DEBUG": "🐞",
		"INFO": "ℹ️",
		"WARNING": "⚠️",
		"ERROR": "❌",
		"CRITICAL": "🔥",
	}

	def format(self, record):
		# Get the base JSON string
		json_str = super().format(record)

		# Parse back to dict for pretty printing
		log_dict = json.loads(json_str)

		# Create a more visual format
		formatted_parts = []

		# Time with dim color
		time_str = log_dict.get("time", "")
		formatted_parts.append(f"{Colors.DIM}[{time_str}]{Colors.RESET}")

		# Level with color, emoji, and padding
		level = log_dict.get("level", "")
		color = self.LEVEL_COLORS.get(level, Colors.WHITE)
		emoji = self.LEVEL_EMOJIS.get(level, "")
		level_colored = f"{color}{emoji} {level:8}{Colors.RESET}"
		formatted_parts.append(f"[{level_colored}]")

		# Logger name with cyan color
		name = log_dict.get("name", "")
		name_colored = f"{Colors.CYAN}{name}{Colors.RESET}"
		formatted_parts.append(f"({name_colored})")

		# Message with bold
		message = log_dict.get("message", "")
		message_colored = f"{Colors.ITALIC}{message}{Colors.RESET}"
		formatted_parts.append(message_colored)

		# Add context fields with different colors
		context_parts = []
		for key, value in log_dict.items():
			if key not in ["time", "level", "name", "message"]:
				colored_key = f"{Colors.BRIGHT_MAGENTA}{key}{Colors.RESET}"
				colored_value = f"{Colors.BRIGHT_GREEN}{value}{Colors.RESET}"
				context_parts.append(f"{colored_key}={colored_value}")

		if context_parts:
			formatted_parts.append(f"[{', '.join(context_parts)}]")

		return " ".join(formatted_parts)
