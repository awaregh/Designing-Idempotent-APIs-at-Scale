"""
Results comparison utilities.

Loads collected metrics JSON and produces a Pandas DataFrame with a
per-strategy comparison table, printed via Rich.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console
from rich.table import Table


def load_results(path: str) -> dict[str, Any]:
    """Load metrics JSON produced by run_experiment.py."""
    with open(path) as fh:
        return json.load(fh)


def generate_comparison_table(results: dict[str, Any]) -> pd.DataFrame:
    """
    Build a DataFrame from the results dict.

    Columns: Strategy, Duplicate Rate, Correctness, P50 (ms), P95 (ms),
             P99 (ms), Conflict Rate, Samples
    """
    rows = []
    metrics_list = results.get("metrics", [])

    for m in metrics_list:
        rows.append(
            {
                "Strategy": m.get("strategy", ""),
                "Duplicate Rate": m.get("duplicate_creation_rate", 0),
                "Correctness": m.get("correctness_score", 0),
                "P50 (ms)": m.get("p50_ms", 0),
                "P95 (ms)": m.get("p95_ms", 0),
                "P99 (ms)": m.get("p99_ms", 0),
                "Conflict Rate": m.get("conflict_rate", 0),
                "Samples": m.get("latency_samples", 0),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Correctness", ascending=False).reset_index(drop=True)
    return df


def print_comparison(df: pd.DataFrame) -> None:
    """Print the comparison DataFrame as a Rich table."""
    console = Console()
    table = Table(title="Idempotency Strategy Comparison", show_lines=True)

    for col in df.columns:
        table.add_column(col, justify="right" if col != "Strategy" else "left")

    for _, row in df.iterrows():
        cells = []
        for col in df.columns:
            val = row[col]
            if isinstance(val, float):
                cells.append(f"{val:.4f}" if "Rate" in col or "Score" in col or "Correctness" in col else f"{val:.2f}")
            else:
                cells.append(str(val))
        table.add_row(*cells)

    console.print(table)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "results/summary.json"
    results = load_results(path)
    df = generate_comparison_table(results)
    print_comparison(df)
