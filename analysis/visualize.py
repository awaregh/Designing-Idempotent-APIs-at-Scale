"""
Visualisation module for idempotency strategy comparison.

Generates the following plots and saves them as PNG files:
1. duplicate_rate_by_strategy.png   — bar chart
2. p95_latency_by_strategy.png      — bar chart
3. radar_comparison.png             — multi-dimensional radar chart
4. correctness_by_strategy.png      — horizontal bar chart
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for server-side rendering
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf",
]


def _load(results_path: str) -> list[dict[str, Any]]:
    with open(results_path) as fh:
        data = json.load(fh)
    return data.get("metrics", [])


def _strategy_names(metrics: list[dict]) -> list[str]:
    return [m["strategy"] for m in metrics]


def generate_plots(results_path: str, output_dir: str) -> list[str]:
    """
    Generate all comparison plots.

    Args:
        results_path: Path to summary.json produced by run_experiment.py
        output_dir:   Directory where PNG files will be saved

    Returns:
        List of output file paths.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    metrics = _load(results_path)
    if not metrics:
        print("No metrics found in results file.")
        return []

    strategies = _strategy_names(metrics)
    colors = PALETTE[: len(strategies)]
    saved: list[str] = []

    # ── 1. Duplicate Rate ────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    dup_rates = [m.get("duplicate_creation_rate", 0) for m in metrics]
    bars = ax.bar(strategies, dup_rates, color=colors)
    ax.set_title("Duplicate Payment Creation Rate by Strategy", fontsize=14)
    ax.set_ylabel("Duplicate Rate (lower = better)")
    ax.set_ylim(0, max(max(dup_rates) * 1.2, 0.1))
    ax.set_xticklabels(strategies, rotation=30, ha="right")
    for bar, val in zip(bars, dup_rates):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.002,
                f"{val:.3f}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    p = str(out / "duplicate_rate_by_strategy.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # ── 2. P95 Latency ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    p95_vals = [m.get("p95_ms", 0) for m in metrics]
    bars = ax.bar(strategies, p95_vals, color=colors)
    ax.set_title("P95 Latency by Strategy (ms)", fontsize=14)
    ax.set_ylabel("P95 Latency (ms) — lower = faster")
    ax.set_xticklabels(strategies, rotation=30, ha="right")
    for bar, val in zip(bars, p95_vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                f"{val:.1f}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    p = str(out / "p95_latency_by_strategy.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # ── 3. Correctness ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    correctness = [m.get("correctness_score", 0) for m in metrics]
    h_bars = ax.barh(strategies, correctness, color=colors)
    ax.set_title("Correctness Score by Strategy", fontsize=14)
    ax.set_xlabel("Correctness Score (higher = better)")
    ax.set_xlim(0, 1.1)
    for bar, val in zip(h_bars, correctness):
        ax.text(val + 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:.3f}", va="center", fontsize=9)
    plt.tight_layout()
    p = str(out / "correctness_by_strategy.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # ── 4. Radar Chart ────────────────────────────────────────────────────────
    categories = ["Correctness", "Speed\n(inv P95)", "No Duplicates", "Low Conflict"]
    n_cat = len(categories)
    angles = [i * 2 * math.pi / n_cat for i in range(n_cat)] + [0]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    max_p95 = max(p95_vals) or 1

    for i, m in enumerate(metrics):
        correctness_val = m.get("correctness_score", 0)
        speed_val = 1 - (m.get("p95_ms", 0) / max_p95)
        no_dup_val = 1 - m.get("duplicate_creation_rate", 0)
        no_conflict_val = 1 - m.get("conflict_rate", 0)
        values = [correctness_val, speed_val, no_dup_val, no_conflict_val]
        values_plot = values + [values[0]]

        ax.plot(angles, values_plot, "o-", linewidth=2, color=colors[i], label=m["strategy"])
        ax.fill(angles, values_plot, alpha=0.1, color=colors[i])

    ax.set_thetagrids(
        [a * 180 / math.pi for a in angles[:-1]], categories
    )
    ax.set_ylim(0, 1)
    ax.set_title("Multi-Dimensional Strategy Comparison", fontsize=14, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15))
    plt.tight_layout()
    p = str(out / "radar_comparison.png")
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    saved.append(p)

    print(f"Generated {len(saved)} plots in {output_dir}")
    return saved


if __name__ == "__main__":
    import sys

    rp = sys.argv[1] if len(sys.argv) > 1 else "results/summary.json"
    od = sys.argv[2] if len(sys.argv) > 2 else "results"
    generate_plots(rp, od)
