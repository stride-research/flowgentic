"""Discord notifications: frequent plain-text pings, plus two richer run-lifecycle events.

Four things a driver can call:
  notify(text)       -- one plain-text line, for frequent/low-stakes pings (stage
                         boundaries).
  run_started(...)    -- one or more embeds carrying run identity and every config
                         field, split across cards if Discord's 25-field-per-embed
                         limit is exceeded.
  run_finished(...)   -- one embed with a funnel summary and a POINTER to where the full
                         results live -- never the result files themselves. Those grow
                         with run size; this embed must not, so a run that takes days
                         produces exactly the same shape of message as one that takes
                         minutes, just bigger numbers inside it.
  run_failed(...)     -- one embed when the pipeline raises partway through. Call it
                         from a try/except in the driver, right before re-raising --
                         without this, a crash was previously invisible: run_finished()
                         only fires on a successful return, so nothing ever posted.

All four are no-ops when DISCORD_WEBHOOK_URL is unset, and none of them can raise: a
notification failure must never be able to kill a run.

Deliberately not called from anywhere in metrics.py or from inside a task body: a driver
calls these at its own checkpoints, not once per task -- thousands of workers each posting
would hit Discord's webhook rate limit (~30/min) within seconds. The event count scales
with pipeline STAGE COUNT (one start, one per stage, one end), never with wall time or
task count, which is what keeps a multi-day stage-2/3 run and today's three-minute one
producing the same fixed handful of messages.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

# Without this, Discord's Cloudflare front end returns 403 "error code: 1010" -- it
# blocks urllib's default "Python-urllib/x.y" User-Agent outright, independent of the
# URL or payload being valid (confirmed: the identical request succeeds the instant any
# normal UA is set).
_USER_AGENT = "Mozilla/5.0 (compatible; flowgentic-notifier/1.0)"

_COLOR_START = 0x5865F2  # Discord blurple
_COLOR_DONE = 0x57F287  # Discord green
_COLOR_FAILED = 0xED4245  # Discord red
_MAX_FIELDS_PER_EMBED = 25  # Discord's own hard limit; exceeding it is a 400, not a truncation.


def notify(text: str) -> None:
    """Post one plain-text message, if a webhook is configured.

    Args:
        text: Message body. Truncated to Discord's ~2000 character limit.
    """
    _post({"content": text[:1900]})


def run_started(*, job_id: str, node: str, config: dict[str, Any]) -> None:
    """Post the run-start embed(s): run identity plus every config field.

    Every key in `config` gets a field -- deliberately not a hand-picked or
    denylisted subset. A named-key filter (skip `cageflow_dir`, skip `out_dir`, ...)
    goes stale the moment someone adds a new path-shaped key to config.json; this
    dumps whatever is actually there, so the embed can never drift out of sync with
    the config schema. The one exception is a `_`-prefixed key (e.g. `_README`),
    which is a documented comment-field convention, not a parameter.

    Args:
        job_id: Slurm job id, or any run identifier.
        node: Hostname the run is executing on.
        config: The run's config dict.
    """
    fields = [
        {"name": "Job", "value": f"`{job_id}`", "inline": True},
        {"name": "Node", "value": f"`{node}`", "inline": True},
        {"name": "Scaffold", "value": f"`{Path(config['scaffold_pdb']).name}`", "inline": True},
    ]
    for key, value in config.items():
        if key.startswith("_"):
            continue
        fields.append({"name": key, "value": _safe_field_value(value), "inline": True})

    # Discord rejects an embed with more than 25 fields outright -- a real config
    # already has more keys than that, so this splits into multiple stacked cards
    # rather than silently dropping fields past #25.
    embeds = []
    for i in range(0, len(fields), _MAX_FIELDS_PER_EMBED):
        embed: dict[str, Any] = {"color": _COLOR_START, "fields": fields[i:i + _MAX_FIELDS_PER_EMBED]}
        if i == 0:
            embed["title"] = "\U0001F680 CageFlow run started"
        embeds.append(embed)

    _post({"embeds": embeds})


def run_finished(*, job_id: str, wall_s: float, funnel: dict[str, int], out_dir: str | Path) -> None:
    """Post the run-end embed: a funnel summary and a pointer to the full results --
    never the result files themselves (see module docstring).

    Args:
        job_id: Same identifier run_started() was given.
        wall_s: Total wall-clock seconds for the run.
        funnel: Ordered {label: count} pairs, rendered as "N label -> N label -> ...".
        out_dir: Where the full results (compute_stats.json, qualifying_sequences.csv,
            history.jsonl, ...) actually live.
    """
    funnel_line = " → ".join(f"{v} {k}" for k, v in funnel.items())
    mins, secs = divmod(int(wall_s), 60)
    embed = {
        "title": "✅ CageFlow run finished",
        "description": funnel_line,
        "color": _COLOR_DONE,
        "fields": [
            {"name": "Wall time", "value": f"`{mins:02d}:{secs:02d}`", "inline": True},
            {"name": "Job", "value": f"`{job_id}`", "inline": True},
            {"name": "Full metrics", "value": f"`{out_dir}`", "inline": False},
        ],
    }
    _post({"embeds": [embed]})


def run_failed(*, job_id: str, error: str, out_dir: str | Path) -> None:
    """Post the run-failure embed: what broke and where the logs are.

    Call this from a try/except around the pipeline call, right before re-raising --
    it notifies, it does not replace the crash. Slurm still sees the real nonzero
    exit, sbatch.err still gets the full traceback; this is the thing that makes a
    crash page someone instead of silently disappearing from squeue.

    Args:
        job_id: Same identifier run_started() was given.
        error: Short description of what failed, e.g. f"{type(exc).__name__}: {exc}".
        out_dir: Where the partial results and cageflow.log live, for debugging.
    """
    embed = {
        "title": "❌ CageFlow run failed",
        "color": _COLOR_FAILED,
        "fields": [
            {"name": "Job", "value": f"`{job_id}`", "inline": True},
            {"name": "Error", "value": f"```{error[:1000]}```", "inline": False},
            {"name": "Logs", "value": f"`{out_dir}`", "inline": False},
        ],
    }
    _post({"embeds": [embed]})


def _safe_field_value(value: Any) -> str:
    """Render one config value defensively -- never raises, never breaks the embed.

    A future config value of a shape this module was never updated for (a nested
    object, something unprintable) becomes "N/A" instead of crashing run_started()
    or sending a malformed request.
    """
    if value is None:
        return "N/A"
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 -- any failure here must degrade, not propagate
        return "N/A"
    if not text:
        return "N/A"
    return f"`{text}`"[:1024]


def _post(payload: dict[str, Any]) -> None:
    """Send one JSON payload. Never raises."""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        return
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except Exception as exc:  # noqa: BLE001 -- notification failure must never propagate
        print(f"[discord] post failed, continuing: {exc}", flush=True)
