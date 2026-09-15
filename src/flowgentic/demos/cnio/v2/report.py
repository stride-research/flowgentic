"""Turn a run's history into a figure of the three science metrics.

Reads `history.jsonl` and plots ddG, pLDDT and RMSD per surviving candidate.

Three panels rather than one chart, because the metrics have different units
and different directions -- kcal/mol where lower is better, a 0-1 confidence
where higher is better, and angstroms where lower is better. Putting them on
one axis would be meaningless, and putting them on two axes would be worse.

Axis limits are fixed constants rather than fitted to the data, so two runs
produce directly comparable pictures. A bar that reaches higher in one run
than another means the number is genuinely larger, not that the axis moved.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
ACCENT = "#2a78d6"

#: Fixed ranges, so the y-axis never rescales between runs. Each entry is
#: (label, unit, lo, hi, better_direction).
PANELS = [
    ("ddg", "ddG", "kcal/mol", -16.0, 0.0, "lower"),
    ("plddt", "pLDDT", "confidence", 0.0, 1.0, "higher"),
    ("rmsd", "RMSD", "Å", 0.0, 6.0, "lower"),
]


def load(history: Path) -> list[dict]:
    """Every candidate that reached scoring, in the order it was recorded."""
    rows = []
    for line in history.read_text().splitlines():
        event = json.loads(line)
        if event["type"] == "candidate_advanced" and event["step"] == "score_with_rosetta":
            rows.append({"id": event["candidate_id"], **event["payload"]})
    return rows


def plot(rows: list[dict], out: Path) -> Path:
    """Write the three-panel figure and return where it landed."""
    if not rows:
        raise ValueError("no scored candidates in history -- nothing to plot")

    rows = sorted(rows, key=lambda r: r["ddg"])
    labels = [f"{r['sequence']}\ndock {r['dock']}" for r in rows]
    # Emphasis, not categorical: candidates that cleared the gate carry the
    # accent hue, the rest recede to gray. Identity is never colour-alone --
    # every bar is directly labelled and the legend names both states.
    passed = [r["ddg"] <= r["cutoff"] for r in rows]
    colors = [ACCENT if p else MUTED for p in passed]

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), facecolor=SURFACE)
    x = range(len(rows))

    for ax, (key, name, unit, lo, hi, better) in zip(axes, PANELS):
        ax.set_facecolor(SURFACE)
        values = [r[key] for r in rows]
        ax.bar(x, values, color=colors, width=0.62, zorder=3)

        for i, v in enumerate(values):
            # Label outside the bar, on whichever side the bar grows toward,
            # so a value is never hidden under its own mark.
            offset = (hi - lo) * 0.025
            below = v < 0
            ax.text(i, v - offset if below else v + offset, f"{v:.2f}",
                    ha="center", va="top" if below else "bottom",
                    fontsize=9, color=INK, zorder=4)

        arrow = "↓" if better == "lower" else "↑"
        ax.set_title(f"{name}  ({unit})", fontsize=11, color=INK, pad=14, loc="left")
        ax.text(0, 1.015, f"{arrow} {better} is better", transform=ax.transAxes,
                fontsize=9, color=INK_SECONDARY)

        ax.set_ylim(lo, hi)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8, color=INK_SECONDARY)
        ax.tick_params(axis="y", labelsize=8, colors=INK_SECONDARY, length=0)
        ax.tick_params(axis="x", length=0)
        ax.grid(axis="y", color=MUTED, alpha=0.22, linewidth=0.8, zorder=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(MUTED)
        ax.spines["bottom"].set_linewidth(0.8)

    # Only ddG gates anything today; the other two are recorded and unused.
    axes[0].axhline(rows[0]["cutoff"], color=INK_SECONDARY, linestyle="--",
                    linewidth=1, zorder=2)
    axes[0].text(len(rows) - 0.4, rows[0]["cutoff"], f" cutoff {rows[0]['cutoff']}",
                 fontsize=8, color=INK_SECONDARY, va="bottom", ha="right")

    handles = [
        plt.Rectangle((0, 0), 1, 1, color=ACCENT),
        plt.Rectangle((0, 0), 1, 1, color=MUTED),
    ]
    fig.legend(handles, ["passed ddG cutoff", "rejected"], loc="lower center",
               ncol=2, frameon=False, fontsize=9, labelcolor=INK_SECONDARY,
               bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("CageFlow naive pipeline — science metrics per candidate",
                 fontsize=13, color=INK, x=0.078, ha="left", y=0.99)

    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return out


def write(history: Path, out_dir: Path) -> Path:
    """Read a run's history and write its figure."""
    return plot(load(history), out_dir / "science_metrics.png")
