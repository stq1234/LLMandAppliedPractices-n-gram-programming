#!/usr/bin/env python3
"""Check corpus cleaning, article isolation, vocabulary derivation, and model scores."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import psutil


PHONETIC_RE = re.compile(r"\{[^{}]*\}")
UNK = "__UNK_TOKEN__"


def read_sentences(path: Path) -> list[list[str]]:
    return [line.split() for line in path.read_text(encoding="utf-8", errors="strict").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def score_perplexity(model, sentences: list[list[str]]) -> tuple[float, int]:
    log10_sum = 0.0
    events = 0
    for sentence in sentences:
        if sentence:
            log10_sum += float(model.score(" ".join(sentence), bos=True, eos=True))
            events += len(sentence) + 1
    return 10 ** (-log10_sum / events), events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results_1-6"))
    parser.add_argument("--kenlm-python-dir", type=Path, default=Path("tools/kenlm-build/lib"))
    parser.add_argument("--rescore-models", action="store_true", help="load each ARPA and recompute held-out perplexity")
    parser.add_argument("--max-cpus", type=int, default=4)
    args = parser.parse_args()
    if args.max_cpus < 1:
        raise ValueError("--max-cpus must be at least 1")

    process = psutil.Process()
    process.cpu_affinity(process.cpu_affinity()[:args.max_cpus])
    root = args.results_dir
    metadata_path = root / "experiment_metadata.json"
    if not metadata_path.is_file():
        metadata_path = root / "corpus_preflight.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stats = metadata["corpus_stats"]
    split = metadata["split"]
    data_dir = root / "data"

    cleaned_path = data_dir / "人民日报1998_清洗后词面.txt"
    cleaned_text = cleaned_path.read_text(encoding="utf-8", errors="strict")
    if "\ufffd" in cleaned_text or "〓" in cleaned_text or PHONETIC_RE.search(cleaned_text):
        raise RuntimeError("cleaned corpus contains a replacement character, layout marker, or phonetic annotation")

    article_index_path = data_dir / "corpus_articles.csv"
    with article_index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        article_rows = list(csv.DictReader(handle))
    train_ids = set(split["train_article_ids"])
    test_ids = set(split["test_article_ids"])
    index_train_ids = {row["article_id"] for row in article_rows if row["split"] == "train"}
    index_test_ids = {row["article_id"] for row in article_rows if row["split"] == "test"}
    if train_ids & test_ids:
        raise RuntimeError("train and test article ID lists overlap")
    if train_ids != index_train_ids or test_ids != index_test_ids:
        raise RuntimeError("corpus article index does not match metadata split IDs")

    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in cleaned_text.splitlines():
        if line.strip():
            current.append(line.split())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    if len(blocks) != len(article_rows):
        raise RuntimeError(f"cleaned corpus has {len(blocks)} article blocks but index has {len(article_rows)}")
    article_signatures: set[tuple[tuple[str, ...], ...]] = set()
    train_raw: list[list[str]] = []
    test_raw: list[list[str]] = []
    for block, row in zip(blocks, article_rows):
        if len(block) != int(row["record_count"]):
            raise RuntimeError(f"record count mismatch for article {row['article_id']}")
        signature = tuple(tuple(sentence) for sentence in block)
        if signature in article_signatures:
            raise RuntimeError(f"exact duplicate article remains: {row['article_id']}")
        article_signatures.add(signature)
        (train_raw if row["split"] == "train" else test_raw).extend(block)

    if len(train_raw) != metadata["training_records"] or len(test_raw) != metadata["test_records"]:
        raise RuntimeError("article index record counts do not match experiment metadata")
    if len(blocks) != stats["articles"] or len(train_raw) + len(test_raw) != stats["records"]:
        raise RuntimeError("cleaned corpus totals do not match cleaning statistics")

    vocabulary_path = data_dir / "train_vocabulary.txt"
    vocabulary = set(vocabulary_path.read_text(encoding="utf-8", errors="strict").splitlines())
    train_frequency = Counter(token for sentence in train_raw for token in sentence)
    expected_vocabulary = {token for token, count in train_frequency.items() if count >= metadata["min_train_frequency"]}
    expected_vocabulary.add(UNK)
    if vocabulary != expected_vocabulary:
        raise RuntimeError("saved vocabulary is not derived solely from training text at the configured threshold")

    expected_train = [[token if token in vocabulary else UNK for token in sentence] for sentence in train_raw]
    expected_test = [[token if token in vocabulary else UNK for token in sentence] for sentence in test_raw]
    train_path = data_dir / "train_for_lmplz.txt"
    test_path = data_dir / "test_for_scoring.txt"
    if read_sentences(train_path) != expected_train:
        raise RuntimeError("lmplz training text does not match the cleaned training split and saved vocabulary")
    if read_sentences(test_path) != expected_test:
        raise RuntimeError("scoring text does not match the cleaned test split and saved vocabulary")
    if any("\ufffd" in token or "〓" in token or PHONETIC_RE.search(token) for sentence in expected_train + expected_test for token in sentence):
        raise RuntimeError("prepared train/test data contains a replacement character, layout marker, or pronunciation annotation")
    test_oov = sum(token not in vocabulary for sentence in test_raw for token in sentence)
    if test_oov != metadata["test_oov_token_count_mapped_to_unk"]:
        raise RuntimeError("test OOV count does not match saved metadata")
    expected_excluded = set(stats["invalid_decode_article_ids"]) | set(stats["conflicting_article_ids"])
    if expected_excluded != set(stats["excluded_article_ids"]):
        raise RuntimeError("excluded article list does not equal the union of decode-error and conflict articles")
    if any(error["byte_offset"] < error["line_byte_offset"] for error in stats["decode_errors"]):
        raise RuntimeError("a decode-error byte offset precedes its source line")
    if stats["no_id_lines_ignored"] != len(stats["no_id_line_examples"]):
        if stats["no_id_lines_ignored"] > len(stats["no_id_line_examples"]):
            print(f"[audit] {stats['no_id_lines_ignored']} unindexed lines; first {len(stats['no_id_line_examples'])} examples retained")

    check = {
        "source_archive_sha256": metadata["source"]["archive_sha256"],
        "cleaned_records": len(train_raw) + len(test_raw),
        "cleaned_articles": len(blocks),
        "train_articles": len(train_ids),
        "test_articles": len(test_ids),
        "train_article_test_article_overlap": 0,
        "training_only_vocabulary_size_including_unk": len(vocabulary),
        "test_oov_tokens_mapped_to_unk": test_oov,
        "decode_error_rows": len(stats["decode_errors"]),
        "invalid_decode_articles_excluded": len(stats["invalid_decode_article_ids"]),
        "conflicting_articles_excluded": len(stats["conflicting_article_ids"]),
        "no_id_lines_ignored": stats["no_id_lines_ignored"],
        "rescore_model_results": [],
    }

    if args.rescore_models:
        sys.path.insert(0, str(args.kenlm_python_dir.resolve()))
        import kenlm

        result_rows = {row["model"]: row for row in metadata["results"]}
        for model_id, result_row in result_rows.items():
            model_path = root / "models" / f"{model_id}.arpa"
            if model_path.stat().st_size != int(result_row["arpa_bytes"]):
                raise RuntimeError(f"ARPA size mismatch for {model_id}")
            if result_row.get("arpa_sha256") and sha256(model_path) != result_row["arpa_sha256"]:
                raise RuntimeError(f"ARPA SHA-256 mismatch for {model_id}")
            model = kenlm.Model(str(model_path))
            ppl, events = score_perplexity(model, expected_test)
            del model
            if events != int(result_row["test_prediction_events_including_eos"]):
                raise RuntimeError(f"held-out prediction-event count mismatch for {model_id}")
            difference = abs(ppl - float(result_row["test_perplexity"]))
            if difference > 1e-6:
                raise RuntimeError(f"re-scored PPL differs for {model_id}: {ppl} vs {result_row['test_perplexity']}")
            check["rescore_model_results"].append({
                "model": model_id,
                "ppl": round(ppl, 8),
                "prediction_events_including_eos": events,
                "arpa_bytes": model_path.stat().st_size,
                "arpa_sha256": result_row.get("arpa_sha256", ""),
            })

    validation_path = root / "validation.json"
    validation_path.write_text(json.dumps(check, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[validation-ok] records={check['cleaned_records']} articles={check['cleaned_articles']} "
          f"train_articles={check['train_articles']} test_articles={check['test_articles']} "
          f"vocabulary={check['training_only_vocabulary_size_including_unk']} test_oov={test_oov}")
    if args.rescore_models:
        for row in check["rescore_model_results"]:
            print(f"[rescore-ok] {row['model']} ppl={row['ppl']:.8f} sha256={row['arpa_sha256']}")
    print(f"[write] {validation_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
