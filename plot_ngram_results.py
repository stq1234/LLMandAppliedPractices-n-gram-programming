#!/usr/bin/env python3
"""Create result charts from the recorded KenLM CSV and experiment metadata."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt


BLUE = "#176B87"
BLUE_LIGHT = "#65A9C2"
ORANGE = "#D97706"
GREEN = "#57A773"
GRAY = "#C9D2D8"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results_1-6/experiment_results.csv"))
    parser.add_argument("--metadata", type=Path, default=Path("results_1-6/experiment_metadata.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("results_1-6"))
    args = parser.parse_args()

    with args.results.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with args.metadata.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)

    full = sorted(
        (row for row in rows if not row["pruning_thresholds"]),
        key=lambda row: int(row["order"]),
    )
    pruned = next((row for row in rows if row["pruning_thresholds"]), None)
    if len(full) < 2:
        raise ValueError(f"Expected at least two unpruned n-gram orders in {args.results}")
    if not pruned:
        raise ValueError(f"Expected a pruned comparison row in {args.results}")

    labels = [f"{int(row['order'])}-gram" for row in full] + ["5-gram pruned"]
    orders = [int(row["order"]) for row in full]
    perplexities = [float(row["test_perplexity"]) for row in full]
    sizes_mib = [int(row["arpa_bytes"]) / (1024 * 1024) for row in full]
    training_seconds = [float(row["training_seconds"]) for row in full]
    prune_ppl = float(pruned["test_perplexity"])
    prune_size_mib = int(pruned["arpa_bytes"]) / (1024 * 1024)
    prune_seconds = float(pruned["training_seconds"])

    plt.rcParams.update({
        "font.size": 10,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Line chart: keep the full-order trend visible and mark the pruned 5-gram separately.
    figure, axis = plt.subplots(figsize=(8.4, 5.0), constrained_layout=True)
    axis.plot(orders, perplexities, marker="o", linewidth=2.6, markersize=7,
              color=BLUE, label="Modified Kneser–Ney")
    for order, value in zip(orders, perplexities):
        offset = (-30, -16) if order == 5 else (0, 9)
        axis.annotate(f"{value:.2f}", (order, value), xytext=offset,
                      textcoords="offset points", ha="center", fontsize=9)
    axis.scatter([5], [prune_ppl], marker="D", s=76, color=ORANGE,
                 edgecolor="white", linewidth=0.8, zorder=4, label="5-gram pruned")
    axis.annotate(f"{prune_ppl:.2f}", (5, prune_ppl), xytext=(12, 5),
                  textcoords="offset points", fontsize=9, color="#8A4B00")
    adjacent_delta = (perplexities[-2] - perplexities[-1]) / perplexities[-2] * 100
    adjacent_change = "lower" if adjacent_delta >= 0 else "higher"
    axis.text(
        0.02, 0.98,
        f"{orders[-2]}→{orders[-1]}-gram: {abs(adjacent_delta):.2f}% {adjacent_change} PPL",
        transform=axis.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        color="#36454F",
    )
    axis.set_title("Held-out test perplexity by n-gram order", pad=12)
    axis.set_xlabel("n-gram order")
    axis.set_ylabel("Perplexity (lower is better)")
    plotted_ppl = perplexities + [prune_ppl]
    axis.set_ylim(bottom=min(plotted_ppl) * 0.92, top=max(plotted_ppl) * 1.12)
    axis.set_xticks(orders)
    axis.grid(axis="y", alpha=0.22)
    axis.legend(frameon=False, loc="upper right")
    figure.savefig(args.output_dir / "perplexity_by_order.png", dpi=220, bbox_inches="tight")
    plt.close(figure)

    # Bar charts: separate axes prevent seconds and MiB from sharing a misleading scale.
    all_seconds = training_seconds + [prune_seconds]
    all_sizes = sizes_mib + [prune_size_mib]
    colors = [BLUE] * len(full) + [ORANGE]
    x_positions = list(range(len(labels)))
    figure, (time_axis, size_axis) = plt.subplots(1, 2, figsize=(12.0, 5.0), constrained_layout=True)
    time_bars = time_axis.bar(x_positions, all_seconds, color=colors, width=0.68)
    time_axis.bar_label(time_bars, fmt="%.2f", padding=3, fontsize=8)
    time_axis.set_title("One-run training time")
    time_axis.set_ylabel("Seconds")
    time_axis.set_xticks(x_positions, labels, rotation=25, ha="right")
    time_axis.grid(axis="y", alpha=0.22)
    size_bars = size_axis.bar(x_positions, all_sizes, color=colors, width=0.68)
    size_axis.bar_label(size_bars, fmt="%.1f", padding=3, fontsize=8)
    size_axis.set_title("ARPA model file size")
    size_axis.set_ylabel("MiB")
    size_axis.set_xticks(x_positions, labels, rotation=25, ha="right")
    size_axis.grid(axis="y", alpha=0.22)
    figure.suptitle("Training cost and model size", fontweight="bold", fontsize=13)
    figure.savefig(args.output_dir / "resource_comparison_bars.png", dpi=220, bbox_inches="tight")
    plt.close(figure)

    # Horizontal stacked bars show the corpus split and held-out coverage.
    training_tokens = int(metadata["training_tokens"])
    test_tokens = int(metadata["test_tokens"])
    test_oov = int(rows[0]["test_oov_mapped_tokens"])
    if test_oov > test_tokens:
        raise ValueError("Test OOV token count exceeds the test token total")
    train_percent = 100 * training_tokens / (training_tokens + test_tokens)
    test_percent = 100 - train_percent
    oov_percent = 100 * test_oov / test_tokens
    covered_percent = 100 - oov_percent
    figure, (split_axis, coverage_axis) = plt.subplots(1, 2, figsize=(11.5, 3.8), constrained_layout=True)
    split_axis.barh(0, train_percent, color=BLUE, height=0.52)
    split_axis.barh(0, test_percent, left=train_percent, color=ORANGE, height=0.52)
    split_axis.text(train_percent / 2, 0, f"Train  {training_tokens:,}\n{train_percent:.1f}%",
                    color="white", ha="center", va="center", fontweight="bold", fontsize=10)
    split_axis.text(train_percent + test_percent / 2, 0, f"Test  {test_tokens:,}\n{test_percent:.1f}%",
                    color="white", ha="center", va="center", fontweight="bold", fontsize=9)
    split_axis.set_title("Article-level train / test split", fontweight="bold")
    split_axis.set_xlabel("Share of surface-token events (%)")
    split_axis.set_yticks([])
    split_axis.set_xlim(0, 100)
    split_axis.set_xticks([0, 25, 50, 75, 100])
    split_axis.grid(axis="x", alpha=0.22)
    split_axis.set_axisbelow(True)

    coverage_axis.barh(0, covered_percent, color=GREEN, height=0.52)
    coverage_axis.barh(0, oov_percent, left=covered_percent, color=GRAY, height=0.52)
    coverage_axis.text(covered_percent / 2, 0, f"In vocabulary  {test_tokens - test_oov:,}\n{covered_percent:.2f}%",
                       color="white", ha="center", va="center", fontweight="bold", fontsize=10)
    coverage_axis.annotate(f"OOV → UNK  {test_oov:,} ({oov_percent:.2f}%)",
                           xy=(covered_percent + oov_percent / 2, 0), xytext=(85, 0.42), ha="center", va="bottom", fontsize=9,
                           arrowprops={"arrowstyle": "-", "color": "#53636B", "lw": 0.9})
    coverage_axis.set_title("Held-out vocabulary coverage", fontweight="bold")
    coverage_axis.set_xlabel("Share of test-token events (%)")
    coverage_axis.set_yticks([])
    coverage_axis.set_xlim(0, 106)
    coverage_axis.set_xticks([0, 25, 50, 75, 100])
    coverage_axis.grid(axis="x", alpha=0.22)
    coverage_axis.set_axisbelow(True)
    figure.suptitle("Corpus split and test vocabulary coverage", fontweight="bold", fontsize=13)
    figure.savefig(args.output_dir / "corpus_token_coverage.png", dpi=220, bbox_inches="tight")
    plt.close(figure)

    for name in ("perplexity_by_order.png", "resource_comparison_bars.png", "corpus_token_coverage.png"):
        output = args.output_dir / name
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"Chart was not written: {output}")
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
