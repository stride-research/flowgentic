"""Turn a run's history into a figure of the three science metrics.

This lives in the demo, not in the library. Which measurements matter, what
units they carry, which direction counts as better and where the cutoff sits
are facts about protein design, not about logging -- `flowgentic` should not
know what a ddG is.

**Always a distribution, never one bar per candidate.** A real campaign is far
larger than the smoke-test configuration -- CNIO described roughly five poses
each yielding ten sequences from a single shape, across many shapes, and
Matteo's estimate of the input count was "potentially a lot" -- so per-candidate
marks would be unreadable on any run that matters. Keeping one form means every
run produces the same picture and two runs can be compared directly, rather
than the chart silently changing shape with the population size.

A small run therefore looks sparse, which is honest: a handful of lonely bins
is an accurate picture of having almost no data.

Axis bounds and bin count are fixed constants for the same reason. The same
value always lands in the same bin, so nothing is silently rescaled between
runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

#: Emphasis encoding: one accent hue against a de-emphasis gray. The
#: distinction is "this cleared the bar", not "these are different things",
#: so a categorical scheme would be the wrong tool.
SURFACE, INK, INK_2, MUTED, ACCENT = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#2a78d6"

#: Fixed bin count. Held constant, like the axis bounds, so the same value
#: always lands in the same bin and two runs can be read side by side.
BINS = 24


@dataclass(frozen=True)
class Panel:
    """One measurement to chart, and how to read it."""

    field: str
    label: str
    unit: str
    lo: float
    hi: float
    better: str
    gate: float | None = None

    def passes(self, value: float | None) -> bool:
        """Whether a value clears this panel's gate."""
        if self.gate is None or value is None:
            return self.gate is None
        return value <= self.gate if self.better == "lower" else value >= self.gate


PANELS = [
    Panel("ddg", "ddG", "kcal/mol", -16.0, 0.0, "lower", gate=-8.0),
    Panel("plddt", "pLDDT", "confidence", 0.0, 1.0, "higher"),
    Panel("rmsd", "RMSD", "Å", 0.0, 6.0, "lower"),
]


def load(history: Path, step: str = "score_with_rosetta") -> list[dict]:
    """Every candidate that reached `step`, with its recorded measurements."""
    rows = []
    for line in Path(history).read_text().splitlines():
        event = json.loads(line)
        if event["type"] == "candidate_advanced" and event["step"] == step:
            rows.append({"candidate_id": event["candidate_id"], **event["payload"]})
    return rows


def _style(ax, panel: Panel, ylabel: str | None = None) -> None:
    """Shared chrome: recessive grid and axes, direction stated explicitly."""
    arrow = "↓" if panel.better == "lower" else "↑"
    ax.set_facecolor(SURFACE)
    ax.set_title(f"{panel.label}  ({panel.unit})", fontsize=11, color=INK, pad=14, loc="left")
    ax.text(0, 1.015, f"{arrow} {panel.better} is better", transform=ax.transAxes,
            fontsize=9, color=INK_2)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9, color=INK_2)
    ax.tick_params(labelsize=8, colors=INK_2, length=0)
    ax.grid(axis="y", color=MUTED, alpha=0.22, linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.spines["bottom"].set_linewidth(0.8)


def _gate_line(ax, panel: Panel, n: int, vertical: bool) -> None:
    """Draw the cutoff. Dashed deliberately: a threshold, not a gridline."""
    if panel.gate is None:
        return
    draw = ax.axvline if vertical else ax.axhline
    draw(panel.gate, color=INK_2, linestyle="--", linewidth=1, zorder=4)
    if vertical:
        # Anchored at the baseline, not the top, where the summary text lives.
        ax.text(panel.gate, ax.get_ylim()[1] * 0.02, f" cutoff {panel.gate}",
                fontsize=8, color=INK_2, va="bottom", ha="left")
    else:
        ax.text(n - 0.4, panel.gate, f" cutoff {panel.gate}",
                fontsize=8, color=INK_2, va="bottom", ha="right")


def _distributions(axes, rows: list[dict], primary: Panel) -> None:
    """A stacked histogram per metric. Used once identity stops being legible.

    Stacked by gate outcome rather than drawn as one population, because the
    question a reader brings to a large run is not "what did candidate 400
    score" but "how much of the population cleared the bar, and by how far".
    """
    passed = [r for r in rows if primary.passes(r.get(primary.field))]
    failed = [r for r in rows if not primary.passes(r.get(primary.field))]

    for ax, panel in zip(axes, PANELS):
        ax.hist(
            [[r.get(panel.field, 0.0) for r in failed],
             [r.get(panel.field, 0.0) for r in passed]],
            bins=BINS, range=(panel.lo, panel.hi), stacked=True,
            color=[MUTED, ACCENT], zorder=3,
        )
        ax.set_xlim(panel.lo, panel.hi)
        # Headroom so the summary line never lands on top of a tall bin.
        ax.set_ylim(0, ax.get_ylim()[1] * 1.22)
        # A count axis takes whole numbers. On a small run matplotlib would
        # otherwise offer "0.5 candidates".
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
        _style(ax, panel, ylabel="candidates")
        _gate_line(ax, panel, len(rows), vertical=True)

        # The summary a bar chart gave away for free, restated in text.
        values = sorted(r.get(panel.field, 0.0) for r in rows)
        best = values[0] if panel.better == "lower" else values[-1]
        median = values[len(values) // 2]
        ax.text(0.99, 0.96, f"best {best:.2f}   median {median:.2f}",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=8.5, color=INK_2)


def write(history: Path, out_dir: Path, filename: str = "metrics.png") -> Path:
    """Read a run's history and write its figure.

    Raises:
        ValueError: If nothing was scored. An empty chart is worse than none.
    """
    rows = load(history)
    if not rows:
        raise ValueError("no scored candidates in history -- nothing to plot")

    primary = PANELS[0]
    rows = sorted(rows, key=lambda r: r.get(primary.field, 0.0))

    fig, axes = plt.subplots(1, len(PANELS), figsize=(4.3 * len(PANELS), 4.6),
                             facecolor=SURFACE)
    _distributions(axes, rows, primary)

    n_passed = sum(primary.passes(r.get(primary.field)) for r in rows)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (ACCENT, MUTED)]
    fig.legend(handles,
               [f"passed {primary.label} cutoff ({n_passed})",
                f"rejected ({len(rows) - n_passed})"],
               loc="lower center", ncol=2, frameon=False, fontsize=9,
               labelcolor=INK_2, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle(
        f"CageFlow naive pipeline — {len(rows)} candidates scored",
        fontsize=13, color=INK, x=0.078, ha="left", y=0.99,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / filename
    fig.savefig(target, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return target
