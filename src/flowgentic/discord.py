"""Optional Discord notifications. One function, fire and forget.

Deliberately not called from anywhere in metrics.py or from inside a task body:
a driver calls this at its own checkpoints (stage boundaries, run start/end),
not once per task -- thousands of workers each posting twice would hit
Discord's webhook rate limit (~30/min) within seconds. This stays a handful of
calls per run regardless of how many tasks that run contains.

Disabled entirely when FLOWGENTIC_DISCORD_WEBHOOK is unset, so calling this on
a run that does not want notifications costs one environment lookup.
"""

from __future__ import annotations

import json
import os
import urllib.request


def notify(text: str) -> None:
    """Post one message to the webhook, if one is configured.

    Args:
        text: Message body. Truncated to Discord's ~2000 character limit.

    Never raises: a notifier must not be able to kill a run.
    """
    webhook = os.environ.get("FLOWGENTIC_DISCORD_WEBHOOK")
    if not webhook:
        return
    request = urllib.request.Request(
        webhook,
        data=json.dumps({"content": text[:1900]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except Exception as exc:  # noqa: BLE001 -- notification failure must never propagate
        print(f"[discord] post failed, continuing: {exc}", flush=True)
