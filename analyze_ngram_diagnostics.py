#!/usr/bin/env python3
"""Derive held-out KenLM match-length and training-corpus continuation diagnostics."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


UNK = "__UNK_TOKEN__"
BOS = "<s>"
MATCH_MODELS = ("5gram", "6gram", "5gram_pruned")
EXAMPLE_MIN_FREQUENCY = 100
_DLL_DIRECTORY_HANDLES = []


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_kenlm(binding_dir: Path):
    sys.path.insert(0, str(binding_dir.resolve()))
    if sys.platform == "win32":
        python_home = Path(sys.executable).resolve().parent
        dll_dir = python_home / "Library" / "bin"
        if dll_dir.is_dir():
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_dir)))
    import kenlm

    return kenlm


def read_tokens(handle, label: str) -> list[str]:
    line = handle.readline()
    if not line:
        raise RuntimeError(f"unexpected end of {label}")
    tokens = line.rstrip("\r\n").split()
    if not tokens:
        raise RuntimeError(f"blank record found in {label}")
    return tokens


def collect_training_counts(results_dir: Path, metadata: dict[str, object]) -> tuple[Counter[str], dict[str, set[str]], dict[str, object]]:
    data_dir = results_dir / "data"
    cleaned_path = data_dir / "人民日报1998_清洗后词面.txt"
    article_index_path = data_dir / "corpus_articles.csv"
    train_path = data_dir / "train_for_lmplz.txt"
    test_path = data_dir / "test_for_scoring.txt"
    vocabulary_path = data_dir / "train_vocabulary.txt"
    for path in (cleaned_path, article_index_path, train_path, test_path, vocabulary_path):
        if not path.is_file():
            raise FileNotFoundError(f"required experiment input is missing: {path}")

    vocabulary = set(vocabulary_path.read_text(encoding="utf-8", errors="strict").splitlines())
    frequency: Counter[str] = Counter()
    left_contexts: dict[str, set[str]] = defaultdict(set)
    split_records = Counter()
    split_tokens = Counter()
    split_articles = Counter()

    with article_index_path.open("r", encoding="utf-8-sig", newline="") as index_handle:
        article_rows = list(csv.DictReader(index_handle))
    if not article_rows:
        raise RuntimeError("article split index is empty")
    if any(row.get("split") not in ("train", "test") for row in article_rows):
        raise RuntimeError("article split index contains an unknown split")

    with cleaned_path.open("r", encoding="utf-8", errors="strict", newline="") as clean_handle, \
            train_path.open("r", encoding="utf-8", errors="strict", newline="") as train_handle, \
            test_path.open("r", encoding="utf-8", errors="strict", newline="") as test_handle:
        for article_index, article in enumerate(article_rows):
            split = str(article["split"])
            split_articles[split] += 1
            record_count = int(article["record_count"])
            for _ in range(record_count):
                tokens = read_tokens(clean_handle, "cleaned corpus")
                split_records[split] += 1
                split_tokens[split] += len(tokens)
                mapped = [token if token in vocabulary else UNK for token in tokens]
                target_handle = train_handle if split == "train" else test_handle
                prepared = target_handle.readline()
                if not prepared:
                    raise RuntimeError(f"prepared {split} text ended before the cleaned article index")
                if prepared.rstrip("\r\n").split() != mapped:
                    raise RuntimeError(f"prepared {split} text differs from the cleaned corpus and saved vocabulary")

                if split == "train":
                    previous = BOS
                    for token in tokens:
                        frequency[token] += 1
                        left_contexts[token].add(previous)
                        previous = token

            if article_index + 1 < len(article_rows):
                separator = clean_handle.readline()
                if not separator or separator.strip():
                    raise RuntimeError("cleaned corpus is missing an article separator between indexed article blocks")

        if clean_handle.readline():
            raise RuntimeError("cleaned corpus contains records beyond the article split index")
        if train_handle.readline():
            raise RuntimeError("prepared training text contains records beyond the article split index")
        if test_handle.readline():
            raise RuntimeError("prepared test text contains records beyond the article split index")

    expected = {
        "train": (int(metadata["training_records"]), int(metadata["training_tokens"])),
        "test": (int(metadata["test_records"]), int(metadata["test_tokens"])),
    }
    for split, (records, tokens) in expected.items():
        if (split_records[split], split_tokens[split]) != (records, tokens):
            raise RuntimeError(
                f"{split} split size disagrees with experiment metadata: "
                f"records/tokens={(split_records[split], split_tokens[split])}, expected={(records, tokens)}"
            )

    return frequency, left_contexts, {
        "record_counts": dict(split_records),
        "token_counts": dict(split_tokens),
        "article_counts": dict(split_articles),
        "raw_training_token_types": len(frequency),
        "training_surface_tokens": sum(frequency.values()),
        "distinct_token_left_context_pairs_including_record_bos": sum(len(contexts) for contexts in left_contexts.values()),
        "candidate_rule": "examples use whitespace tokens consisting only of Unicode alphabetic characters; punctuation and tokens containing digits are excluded from the sample, but all tokens remain in frequency and left-context totals",
        "left_context_rule": "count distinct immediately preceding tokens within each training record; count <s> once as the preceding context for a record-initial token; never cross record boundaries",
        "example_minimum_frequency": EXAMPLE_MIN_FREQUENCY,
    }


def choose_examples(frequency: Counter[str], left_contexts: dict[str, set[str]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    candidates = [
        (token, count, len(left_contexts[token]))
        for token, count in frequency.items()
        if token != UNK and token.isalpha() and count >= EXAMPLE_MIN_FREQUENCY
    ]
    by_concentration = sorted(
        candidates,
        key=lambda item: (-(item[1] / item[2]), -item[1], item[0]),
    )
    concentrated = by_concentration[:5]
    used = {item[0] for item in concentrated}
    by_context_diversity = sorted(
        (item for item in candidates if item[0] not in used),
        key=lambda item: (-item[2], -item[1], item[0]),
    )
    broad = by_context_diversity[:5]

    example_rows: list[dict[str, object]] = []
    selected_context_rows: list[dict[str, object]] = []
    for group, items in (("高频/每左邻", concentrated), ("左邻类型较多", broad)):
        for token, count, context_count in items:
            example_rows.append({
                "selection_group": group,
                "word": token,
                "training_frequency": count,
                "distinct_left_contexts": context_count,
                "frequency_per_left_context": round(count / context_count, 4),
            })
            for left in sorted(left_contexts[token]):
                selected_context_rows.append({
                    "selection_group": group,
                    "word": token,
                    "training_frequency": count,
                    "distinct_left_contexts": context_count,
                    "left_context": left,
                })
    return example_rows, selected_context_rows


def score_match_lengths(kenlm, results_dir: Path, metadata: dict[str, object], test_sentences: list[list[str]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    result_rows = {str(row["model"]): row for row in metadata["results"]}
    match_rows: list[dict[str, object]] = []
    model_summaries: list[dict[str, object]] = []
    expected_oov = int(metadata["test_oov_token_count_mapped_to_unk"])
    observed_oov = sum(token == UNK for sentence in test_sentences for token in sentence)
    if observed_oov != expected_oov:
        raise RuntimeError(f"test <UNK> count {observed_oov} differs from recorded OOV count {expected_oov}")

    for model_id in MATCH_MODELS:
        if model_id not in result_rows:
            raise RuntimeError(f"required diagnostic model is missing from metadata: {model_id}")
        row = result_rows[model_id]
        model_path = results_dir / "models" / f"{model_id}.arpa"
        if not model_path.is_file():
            raise FileNotFoundError(f"required ARPA model is missing: {model_path}")
        actual_hash = sha256(model_path)
        if actual_hash != row["arpa_sha256"]:
            raise RuntimeError(f"ARPA SHA-256 mismatch for {model_id}")

        model = kenlm.Model(str(model_path))
        if int(model.order) != int(row["order"]):
            raise RuntimeError(f"KenLM order disagrees with metadata for {model_id}")
        max_order = int(model.order)
        lexical_counts = Counter()
        eos_counts = Counter()
        log10_sum = 0.0
        event_count = 0
        oov_count = 0
        for sentence in test_sentences:
            scores = model.full_scores(" ".join(sentence), bos=True, eos=True)
            sentence_event_count = 0
            for index, (log10_probability, matched_length, oov) in enumerate(scores):
                matched_length = int(matched_length)
                if not 1 <= matched_length <= max_order:
                    raise RuntimeError(f"KenLM returned an invalid matched order {matched_length} for {model_id}")
                is_eos = index == len(sentence)
                if index > len(sentence):
                    raise RuntimeError(f"KenLM returned too many scores for a sentence in {model_id}")
                (eos_counts if is_eos else lexical_counts)[matched_length] += 1
                log10_sum += float(log10_probability)
                event_count += 1
                sentence_event_count += 1
                if oov and not is_eos:
                    oov_count += 1
            if sentence_event_count != len(sentence) + 1:
                raise RuntimeError(f"KenLM full_scores did not return one EOS event for every test record ({model_id})")

        expected_events = int(row["test_prediction_events_including_eos"])
        if event_count != expected_events:
            raise RuntimeError(f"full_scores event count differs from recorded scorer for {model_id}: {event_count} != {expected_events}")
        ppl = 10 ** (-log10_sum / event_count)
        baseline_ppl = float(row["test_perplexity"])
        if abs(ppl - baseline_ppl) > 1e-4:
            raise RuntimeError(f"full_scores PPL differs from saved experiment score for {model_id}: {ppl} vs {baseline_ppl}")

        model_distribution = []
        for match_length in range(1, max_order + 1):
            lexical = lexical_counts[match_length]
            eos = eos_counts[match_length]
            total = lexical + eos
            match_rows.append({
                "model": model_id,
                "match_length": match_length,
                "lexical_token_events": lexical,
                "eos_events": eos,
                "all_prediction_events": total,
                "percent_of_all_events": round(100 * total / event_count, 6),
                "percent_of_lexical_events": round(100 * lexical / (event_count - len(test_sentences)), 6),
                "percent_of_eos_events": round(100 * eos / len(test_sentences), 6),
            })
            model_distribution.append({
                "match_length_including_target": match_length,
                "lexical_token_events": lexical,
                "eos_events": eos,
                "all_prediction_events": total,
                "percent_of_all_events": round(100 * total / event_count, 6),
                "percent_of_lexical_events": round(100 * lexical / (event_count - len(test_sentences)), 6),
                "percent_of_eos_events": round(100 * eos / len(test_sentences), 6),
            })
        model_summaries.append({
            "model": model_id,
            "maximum_order": max_order,
            "test_records": len(test_sentences),
            "lexical_token_events": event_count - len(test_sentences),
            "eos_events": len(test_sentences),
            "all_prediction_events_including_eos": event_count,
            "test_oov_tokens_mapped_to_unk": observed_oov,
            "kenlm_runtime_oov_flags": oov_count,
            "saved_perplexity": baseline_ppl,
            "full_scores_recomputed_perplexity": round(ppl, 8),
            "full_scores_absolute_ppl_difference": round(abs(ppl - baseline_ppl), 10),
            "arpa_sha256": actual_hash,
            "match_length_distribution": model_distribution,
        })
        del model

    return match_rows, model_summaries


def make_plot(path: Path, summaries: list[dict[str, object]]) -> None:
    colors = ["#9ECAE1", "#6BAED6", "#4292C6", "#2171B5", "#084594", "#54278F"]
    fig, ax = plt.subplots(figsize=(9.0, 5.3))
    fig.subplots_adjust(left=0.10, right=0.98, top=0.88, bottom=0.27)
    model_names = ["5-gram", "6-gram", "Pruned 5-gram"]
    positions = list(range(len(summaries)))
    bottoms = [0.0] * len(summaries)
    max_order = max(int(summary["maximum_order"]) for summary in summaries)
    for match_length in range(1, max_order + 1):
        heights = []
        for summary in summaries:
            distribution = summary["match_length_distribution"]
            entry = next((item for item in distribution if item["match_length_including_target"] == match_length), None)
            heights.append(float(entry["percent_of_all_events"]) if entry else 0.0)
        ax.bar(positions, heights, bottom=bottoms, width=0.64, color=colors[match_length - 1], label=f"{match_length}-gram match")
        bottoms = [bottom + height for bottom, height in zip(bottoms, heights)]
    ax.set_xticks(positions, model_names)
    ax.set_ylabel("Held-out prediction events (%)")
    ax.set_ylim(0, 100)
    ax.set_title("Longest matched n-gram length on the fixed test set")
    ax.legend(title="KenLM matched order", ncols=3, loc="upper center", bbox_to_anchor=(0.5, -0.13), frameon=False)
    ax.grid(axis="y", alpha=0.2)
    ax.set_axisbelow(True)
    fig.text(0.5, 0.025, "Each returned length includes the predicted token; EOS events are included in the denominator.",
             ha="center", va="bottom", fontsize=9)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results_1-6"))
    parser.add_argument("--kenlm-python-dir", type=Path, default=Path("tools/kenlm-build/lib"))
    args = parser.parse_args()
    results_dir = args.results_dir
    metadata_path = results_dir / "experiment_metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"completed experiment metadata is required: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    test_path = results_dir / "data" / "test_for_scoring.txt"
    test_sentences = [line.split() for line in test_path.read_text(encoding="utf-8", errors="strict").splitlines() if line.strip()]
    if len(test_sentences) != int(metadata["test_records"]):
        raise RuntimeError("fixed test set record count differs from experiment metadata")
    if sum(map(len, test_sentences)) != int(metadata["test_tokens"]):
        raise RuntimeError("fixed test set token count differs from experiment metadata")

    frequency, left_contexts, training_summary = collect_training_counts(results_dir, metadata)
    examples, context_rows = choose_examples(frequency, left_contexts)
    kenlm = load_kenlm(args.kenlm_python_dir)
    match_rows, model_summaries = score_match_lengths(kenlm, results_dir, metadata, test_sentences)

    diagnostics_dir = results_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        diagnostics_dir / "longest_match_counts.csv",
        ["model", "match_length", "lexical_token_events", "eos_events", "all_prediction_events", "percent_of_all_events", "percent_of_lexical_events", "percent_of_eos_events"],
        match_rows,
    )
    write_csv(
        diagnostics_dir / "training_token_frequency_left_contexts.csv",
        ["token", "training_frequency", "distinct_left_contexts", "frequency_per_left_context"],
        [
            {
                "token": token,
                "training_frequency": count,
                "distinct_left_contexts": len(left_contexts[token]),
                "frequency_per_left_context": round(count / len(left_contexts[token]), 6),
            }
            for token, count in sorted(frequency.items(), key=lambda item: item[0])
        ],
    )
    write_csv(
        diagnostics_dir / "word_frequency_left_contexts.csv",
        ["selection_group", "word", "training_frequency", "distinct_left_contexts", "frequency_per_left_context"],
        examples,
    )
    write_csv(
        diagnostics_dir / "selected_word_left_contexts.csv",
        ["selection_group", "word", "training_frequency", "distinct_left_contexts", "left_context"],
        context_rows,
    )
    summary = {
        "source": {
            "experiment_metadata_sha256": sha256(metadata_path),
            "test_file_sha256": sha256(test_path),
            "cleaned_training_corpus_sha256": sha256(results_dir / "data" / "人民日报1998_清洗后词面.txt"),
            "article_split_index_sha256": sha256(results_dir / "data" / "corpus_articles.csv"),
        },
        "match_length_definition": "KenLM Model.full_scores(..., bos=True, eos=True) returned matched n-gram length; this length counts the predicted token and is not a measure of the model's entire effective dependency. EOS is a predicted event; BOS is context only.",
        "test_event_denominator": {
            "records": len(test_sentences),
            "lexical_token_events": sum(map(len, test_sentences)),
            "eos_events": len(test_sentences),
            "all_prediction_events_including_eos": sum(map(len, test_sentences)) + len(test_sentences),
            "oov_surface_tokens_mapped_to_unk": sum(token == UNK for sentence in test_sentences for token in sentence),
            "note": "One event is scored for every mapped test token and one EOS per non-empty record; there is no BOS prediction event.",
        },
        "training_frequency_and_left_contexts": training_summary,
        "selected_word_examples": examples,
        "models": model_summaries,
    }
    summary_path = diagnostics_dir / "diagnostics_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plot_path = results_dir / "longest_match_distribution.png"
    make_plot(plot_path, model_summaries)

    print(f"[diagnostics-ok] test_records={len(test_sentences)} lexical_events={sum(map(len, test_sentences))} eos_events={len(test_sentences)}")
    for model in model_summaries:
        distribution = ", ".join(
            f"{item['match_length_including_target']}g={item['all_prediction_events']} ({item['percent_of_all_events']:.3f}%)"
            for item in model["match_length_distribution"]
        )
        print(f"[matches] {model['model']} ppl={model['full_scores_recomputed_perplexity']:.8f} {distribution}")
    for example in examples:
        print(f"[word] {example['selection_group']} {example['word']} freq={example['training_frequency']} left={example['distinct_left_contexts']}")
    print(f"[write] {summary_path}")
    print(f"[write] {plot_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(2)
