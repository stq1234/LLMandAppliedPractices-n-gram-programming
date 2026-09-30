#!/usr/bin/env python3
"""Clean the 1998 People's Daily corpus and train/evaluate KenLM models."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import zipfile
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Iterable


TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
RECORD_RE = re.compile(r"^(\d{8})-(\d+)-(\d+)-(\d+)/([A-Za-z][A-Za-z0-9_-]*)$")
CHUNK_RE = re.compile(r"\]([A-Za-z][A-Za-z0-9_-]*)$")
POS_RE = re.compile(r"^(.*)/([A-Za-z][A-Za-z0-9_-]*)$")
MONTH_FILE_RE = re.compile(r"(?:^|/)1998(\d{2})\.txt$", re.IGNORECASE)
UNK = "__UNK_TOKEN__"
PREFIX = "在阳光明媚的五月，我们学校胜利召开了……"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_months(value: str) -> list[int]:
    if not value.strip():
        return []
    months = sorted({int(part.strip()) for part in value.split(",") if part.strip()})
    if any(month < 1 or month > 12 for month in months):
        raise ValueError("--months 只能包含 1 到 12 的月份")
    return months


def read_source(path: Path, encoding: str, months: list[int]) -> tuple[list[tuple[str, str]], dict[str, object]]:
    archive_digest = sha256(path)
    source_files: list[tuple[str, str]] = []
    member_metadata: list[dict[str, object]] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            candidates = []
            for name in archive.namelist():
                match = MONTH_FILE_RE.search(name)
                if match and (not months or int(match.group(1)) in months):
                    candidates.append((int(match.group(1)), name))
            candidates.sort()
            if not candidates:
                raise ValueError(f"ZIP 中没有找到指定月份的 1998 年语料文件: {months}")
            for month, name in candidates:
                raw = archive.read(name)
                source_files.append((name, raw.decode(encoding, errors="strict")))
                member_metadata.append({
                    "name": name,
                    "month": month,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                })
    else:
        raw = path.read_bytes()
        source_files.append((path.name, raw.decode(encoding, errors="strict")))
        member_metadata.append({
            "name": path.name,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return source_files, {
        "archive_bytes": path.stat().st_size,
        "archive_sha256": archive_digest,
        "encoding": encoding,
        "selected_months": months,
        "source_files": member_metadata,
    }


def clean_corpus(
    path: Path,
    cleaned_path: Path,
    encoding: str,
    months: list[int],
    expected_bytes: int | None,
    expected_sha256: str,
) -> tuple[list[list[str]], list[str], dict[str, object]]:
    source_files, source_meta = read_source(path, encoding, months)
    if expected_bytes is not None and int(source_meta["archive_bytes"]) != expected_bytes:
        raise ValueError(f"语料大小校验失败: expected={expected_bytes}, actual={source_meta['archive_bytes']}")
    if expected_sha256 and str(source_meta["archive_sha256"]).lower() != expected_sha256.lower():
        raise ValueError("语料 SHA-256 校验失败")
    sentences: list[list[str]] = []
    article_ids: list[str] = []
    stats: dict[str, object] = {
        **source_meta,
        "physical_lines": 0,
        "blank_lines": 0,
        "records": 0,
        "articles": 0,
        "removed_record_ids": 0,
        "surface_tokens": 0,
        "removed_pos_tags": 0,
        "removed_chunk_labels": 0,
        "removed_bracket_markers": 0,
        "removed_extra_annotation_markers": 0,
        "removed_control_characters": 0,
        "unique_dates": 0,
        "date_start": "",
        "date_end": "",
        "untagged_pieces": 0,
        "untagged_examples": [],
    }
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    seen_articles: set[str] = set()
    seen_dates: set[str] = set()
    prior_article: str | None = None
    with cleaned_path.open("w", encoding="utf-8", newline="\n") as output:
        for source_name, text in source_files:
            for line_no, raw_line in enumerate(text.splitlines(), 1):
                stats["physical_lines"] = int(stats["physical_lines"]) + 1
                stripped = raw_line.strip()
                if not stripped:
                    stats["blank_lines"] = int(stats["blank_lines"]) + 1
                    continue
                pieces = stripped.split()
                match = RECORD_RE.fullmatch(pieces[0]) if pieces else None
                if not match:
                    raise ValueError(f"{source_name}:{line_no} 没有匹配人民日报记录编号: {stripped[:120]}")
                date, edition, article, _segment, _record_tag = match.groups()
                seen_dates.add(date)
                article_id = f"{date}-{edition}-{article}"
                seen_articles.add(article_id)
                stats["records"] = int(stats["records"]) + 1
                stats["removed_record_ids"] = int(stats["removed_record_ids"]) + 1
                sentence: list[str] = []
                for piece in pieces[1:]:
                    token_text = piece
                    chunk = CHUNK_RE.search(token_text)
                    if chunk:
                        token_text = token_text[: chunk.start()]
                        stats["removed_chunk_labels"] = int(stats["removed_chunk_labels"]) + 1
                        stats["removed_bracket_markers"] = int(stats["removed_bracket_markers"]) + 1
                    if token_text.endswith("/%"):
                        token_text = token_text[:-2]
                        stats["removed_extra_annotation_markers"] = int(stats["removed_extra_annotation_markers"]) + 1
                    pos_match = POS_RE.fullmatch(token_text)
                    if pos_match and TAG_RE.fullmatch(pos_match.group(2)):
                        token_text = pos_match.group(1)
                        stats["removed_pos_tags"] = int(stats["removed_pos_tags"]) + 1
                    else:
                        stats["untagged_pieces"] = int(stats["untagged_pieces"]) + 1
                        examples = stats["untagged_examples"]
                        if len(examples) < 20:
                            examples.append({"source": source_name, "line": line_no, "piece": piece})
                    controls = sum(unicodedata.category(char) == "Cc" for char in token_text)
                    if controls:
                        stats["removed_control_characters"] = int(stats["removed_control_characters"]) + controls
                        token_text = "".join(char for char in token_text if unicodedata.category(char) != "Cc")
                    markers = token_text.count("[") + token_text.count("]")
                    if markers:
                        stats["removed_bracket_markers"] = int(stats["removed_bracket_markers"]) + markers
                        token_text = token_text.replace("[", "").replace("]", "")
                    if token_text:
                        sentence.append(token_text)
                if not sentence:
                    raise ValueError(f"{source_name}:{line_no} 的编号之后没有词面内容")
                sentences.append(sentence)
                article_ids.append(article_id)
                stats["surface_tokens"] = int(stats["surface_tokens"]) + len(sentence)
                if prior_article is not None and prior_article != article_id:
                    output.write("\n")
                output.write(" ".join(sentence) + "\n")
                prior_article = article_id
    stats["articles"] = len(seen_articles)
    stats["unique_dates"] = len(seen_dates)
    stats["date_start"] = min(seen_dates) if seen_dates else ""
    stats["date_end"] = max(seen_dates) if seen_dates else ""
    if int(stats["untagged_pieces"]):
        raise ValueError(f"有 {stats['untagged_pieces']} 个词片段没有识别出词性标签，请检查 untagged_examples")
    return sentences, article_ids, stats


def split_by_article(
    sentences: list[list[str]], article_ids: list[str], ratio: float, seed: int
) -> tuple[list[int], list[int], dict[str, object]]:
    groups: OrderedDict[str, list[int]] = OrderedDict()
    for index, article_id in enumerate(article_ids):
        groups.setdefault(article_id, []).append(index)
    keys = list(groups)
    random.Random(seed).shuffle(keys)
    target = round(len(sentences) * ratio)
    train_ids: set[str] = set()
    train_records = 0
    for article_id in keys:
        if train_records >= target or len(train_ids) >= len(keys) - 1:
            break
        train_ids.add(article_id)
        train_records += len(groups[article_id])
    train_indices = [i for i, article_id in enumerate(article_ids) if article_id in train_ids]
    test_indices = [i for i, article_id in enumerate(article_ids) if article_id not in train_ids]
    if not train_indices or not test_indices:
        raise ValueError("文章级训练/测试切分失败")
    metadata = {
        "seed": seed,
        "requested_train_ratio": ratio,
        "train_records": len(train_indices),
        "test_records": len(test_indices),
        "train_articles": len(train_ids),
        "test_articles": len(keys) - len(train_ids),
        "train_article_ids": sorted(train_ids),
        "test_article_ids": sorted(set(keys) - train_ids),
    }
    return train_indices, test_indices, metadata


def write_modeling_text(
    path: Path,
    sentences: Iterable[list[str]],
    keep_words: set[str] | None = None,
) -> tuple[int, int]:
    token_count = 0
    mapped_count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for sentence in sentences:
            values = [w if keep_words is None or w in keep_words else UNK for w in sentence]
            mapped_count += sum(w == UNK for w in values)
            token_count += len(values)
            output.write(" ".join(values) + "\n")
    return token_count, mapped_count


def segment_prefix(prefix: str, vocabulary: set[str]) -> list[str]:
    candidates = sorted(vocabulary, key=lambda token: (-len(token), token))
    result: list[str] = []
    position = 0
    while position < len(prefix):
        token = next((word for word in candidates if prefix.startswith(word, position)), None)
        if token is None:
            result.append(UNK)
            position += 1
        else:
            result.append(token)
            position += len(token)
    return result


def generate(
    model,
    kenlm,
    candidates: list[str],
    prefix_tokens: list[str],
    max_tokens: int,
    min_tokens: int,
    temperature: float,
    top_k: int,
    seed: int,
) -> list[str]:
    rng = random.Random(seed)
    current = kenlm.State()
    scratch = kenlm.State()
    model.BeginSentenceWrite(current)
    for token in prefix_tokens:
        model.BaseScore(current, token, scratch)
        current, scratch = scratch, current
    generated: list[str] = []
    for _ in range(max_tokens):
        scores = [
            (token, model.BaseScore(current, token, scratch))
            for token in candidates
            if token != "</s>" or len(generated) >= min_tokens
        ]
        scores.sort(key=lambda item: (-item[1], item[0]))
        if top_k > 0:
            scores = scores[:top_k]
        if temperature <= 0 or top_k == 1:
            chosen = scores[0][0]
        else:
            best = scores[0][1]
            weights = [math.exp((score - best) * math.log(10.0) / temperature) for _, score in scores]
            chosen = rng.choices([token for token, _ in scores], weights=weights, k=1)[0]
        if chosen == "</s>":
            break
        generated.append(chosen)
        model.BaseScore(current, chosen, scratch)
        current, scratch = scratch, current
    return generated


def score_perplexity(model, sentences: list[list[str]]) -> tuple[float, int]:
    log10_sum = 0.0
    predicted = 0
    for sentence in sentences:
        if sentence:
            text = " ".join(sentence)
            log10_sum += float(model.score(text, bos=True, eos=True))
            predicted += len(sentence) + 1
    return 10 ** (-log10_sum / predicted), predicted


def read_arpa_counts(path: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line in handle:
            match = re.fullmatch(r"ngram (\d+)=(\d+)\s*", line)
            if match:
                counts[int(match.group(1))] = int(match.group(2))
            elif counts and line.startswith("\\1-grams:"):
                break
    return counts


def run(args: argparse.Namespace) -> int:
    source = Path(args.input)
    output = Path(args.output_dir)
    data_dir = output / "data"
    model_dir = output / "models"
    log_dir = output / "logs"
    model_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    months = parse_months(args.months)
    print("[source] reading and verifying the original corpus", flush=True)
    cleaned, article_ids, corpus_stats = clean_corpus(
        source,
        data_dir / "人民日报1998_清洗后词面.txt",
        args.encoding,
        months,
        args.expected_bytes,
        args.expected_sha256,
    )
    print(
        f"[clean] records={corpus_stats['records']} articles={corpus_stats['articles']} "
        f"tokens={corpus_stats['surface_tokens']} removed_pos_tags={corpus_stats['removed_pos_tags']} "
        f"removed_chunk_labels={corpus_stats['removed_chunk_labels']} "
        f"removed_brackets={corpus_stats['removed_bracket_markers']} "
        f"removed_extra_markers={corpus_stats['removed_extra_annotation_markers']} "
        f"removed_controls={corpus_stats['removed_control_characters']}",
        flush=True,
    )
    train_indices, test_indices, split_meta = split_by_article(cleaned, article_ids, args.train_ratio, args.split_seed)
    train_raw = [cleaned[i] for i in train_indices]
    test_raw = [cleaned[i] for i in test_indices]
    train_frequency = Counter(token for sentence in train_raw for token in sentence)
    keep_words = {token for token, count in train_frequency.items() if count >= args.min_count}
    keep_words.add(UNK)
    train_path = data_dir / "train_for_lmplz.txt"
    test_path = data_dir / "test_for_scoring.txt"
    train_token_count, rare_train_tokens = write_modeling_text(train_path, train_raw, keep_words)
    test_token_count, test_oov_tokens = write_modeling_text(test_path, test_raw, keep_words)
    print(
        f"[split] train={len(train_raw)} records/{split_meta['train_articles']} articles "
        f"test={len(test_raw)} records/{split_meta['test_articles']} articles "
        f"train_tokens={train_token_count} test_tokens={test_token_count} "
        f"train_rare_mapped={rare_train_tokens} test_oov_mapped={test_oov_tokens}",
        flush=True,
    )

    dll_dir = Path(args.dll_dir) if args.dll_dir else None
    if dll_dir and dll_dir.exists():
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    sys.path.insert(0, str(Path(args.kenlm_python_dir)))
    try:
        import kenlm
    except ImportError as exc:
        raise RuntimeError(f"无法载入 KenLM Python 绑定: {args.kenlm_python_dir}") from exc

    exe = Path(args.lmplz)
    candidate_words = sorted(keep_words - {UNK})
    candidate_words.append("</s>")
    prefix_tokens = segment_prefix(args.prefix, keep_words)
    results: list[dict[str, object]] = []
    model_specs = [(order, None) for order in args.orders]
    if args.pruned_fivegram:
        model_specs.append((5, [0, 0, 0, 1, 1]))

    for index, (order, pruning) in enumerate(model_specs, 1):
        model_id = f"{order}gram" + ("_pruned" if pruning else "")
        arpa_path = model_dir / f"{model_id}.arpa"
        log_path = log_dir / f"{model_id}.log"
        temp_prefix = Path(args.temp_prefix) / model_id
        temp_prefix.parent.mkdir(parents=True, exist_ok=True)
        command = [str(exe), "-o", str(order), "-S", args.memory, "-T", str(temp_prefix)]
        if pruning:
            command += ["--prune", *map(str, pruning)]
        print(f"[train-start {index}/{len(model_specs)}] {' '.join(command)}", flush=True)
        started = time.perf_counter()
        with train_path.open("rb") as input_handle, arpa_path.open("wb") as model_handle, log_path.open("wb") as log_handle:
            result = subprocess.run(command, stdin=input_handle, stdout=model_handle, stderr=log_handle)
        fallback = False
        elapsed = time.perf_counter() - started
        if result.returncode != 0:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            if "BadDiscountException" in log_text and not pruning:
                fallback = True
                command += ["--discount_fallback", "0.5", "1", "1.5"]
                print(f"[train-retry {model_id}] using fallback discounts 0.5, 1, 1.5", flush=True)
                started = time.perf_counter()
                with train_path.open("rb") as input_handle, arpa_path.open("wb") as model_handle, log_path.open("wb") as log_handle:
                    result = subprocess.run(command, stdin=input_handle, stdout=model_handle, stderr=log_handle)
                elapsed += time.perf_counter() - started
        if result.returncode != 0 or arpa_path.stat().st_size == 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"KenLM {model_id} training failed (exit {result.returncode}):\n{tail}")
        print(
            f"[train-done {index}/{len(model_specs)}] {model_id} seconds={elapsed:.3f} "
            f"arpa_bytes={arpa_path.stat().st_size} fallback={fallback}",
            flush=True,
        )

        model = kenlm.Model(str(arpa_path))
        mapped_test = [[token if token in keep_words else UNK for token in sentence] for sentence in test_raw]
        ppl, predicted = score_perplexity(model, mapped_test)
        greedy = "".join(generate(model, kenlm, candidate_words, prefix_tokens, args.max_tokens, args.min_tokens, 0.0, 1, args.generation_seed + order))
        sampled = "".join(generate(model, kenlm, candidate_words, prefix_tokens, args.max_tokens, args.min_tokens, args.temperature, args.top_k, args.generation_seed + order * 101))
        row = {
            "model": model_id,
            "order": order,
            "pruning_thresholds": "" if not pruning else ",".join(map(str, pruning)),
            "discount_fallback": fallback,
            "training_seconds": round(elapsed, 6),
            "arpa_bytes": arpa_path.stat().st_size,
            "arpa_ngram_counts": ", ".join(f"{n}g={count}" for n, count in read_arpa_counts(arpa_path).items()),
            "test_perplexity": round(ppl, 8),
            "test_prediction_events_including_eos": predicted,
            "test_oov_mapped_tokens": test_oov_tokens,
            "prefix": args.prefix,
            "prefix_tokens": " / ".join(prefix_tokens),
            "greedy_continuation": greedy,
            "minimum_generated_tokens": args.min_tokens,
            "unk_token_suppressed_during_generation": True,
            "sampling_temperature": args.temperature,
            "sampling_top_k": args.top_k,
            "sampled_continuation": sampled,
        }
        results.append(row)
        print(f"[eval] {model_id} test_ppl={ppl:.5f} greedy={greedy} sampled={sampled}", flush=True)
        del model

    csv_path = output / "experiment_results.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    metadata = {
        "source": {
            "input_path": str(source),
            "repository_url": args.source_url,
            "source_note": args.source_note,
            "archive_bytes": corpus_stats["archive_bytes"],
            "archive_sha256": corpus_stats["archive_sha256"],
            "selected_months": months,
            "files": corpus_stats["source_files"],
        },
        "corpus_stats": corpus_stats,
        "split": split_meta,
        "training_records": len(train_raw),
        "test_records": len(test_raw),
        "training_tokens": train_token_count,
        "test_tokens": test_token_count,
        "training_rare_token_count_mapped_to_unk": rare_train_tokens,
        "test_oov_token_count_mapped_to_unk": test_oov_tokens,
        "test_oov_rate": test_oov_tokens / test_token_count if test_token_count else 0,
        "min_train_frequency": args.min_count,
        "rare_train_tokens_mapped_to": UNK,
        "test_oov_tokens_mapped_to": UNK,
        "kept_vocabulary_size": len(keep_words),
        "kenlm_tool": str(exe),
        "kenlm_python_binding": str(Path(args.kenlm_python_dir)),
        "estimator": "KenLM lmplz, modified Kneser-Ney",
        "memory_limit": args.memory,
        "orders": args.orders,
        "pruned_fivegram": args.pruned_fivegram,
        "prefix": args.prefix,
        "generation": {"greedy_top_k": 1, "temperature": args.temperature, "top_k": args.top_k, "minimum_tokens": args.min_tokens, "max_tokens": args.max_tokens, "unk_token_suppressed": True, "seed": args.generation_seed},
        "results": results,
    }
    (output / "experiment_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[write] {csv_path}", flush=True)
    print(f"[write] {output / 'experiment_metadata.json'}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean the People’s Daily corpus and run KenLM n-gram experiments")
    parser.add_argument("--input", default="data/人民日报1998_PFR.zip")
    parser.add_argument("--output-dir", default="results_1-4")
    parser.add_argument("--lmplz", default="tools/kenlm-build/bin/lmplz.exe")
    parser.add_argument("--kenlm-python-dir", default="tools/kenlm-build/lib")
    parser.add_argument("--dll-dir", default="")
    parser.add_argument("--temp-prefix", default="tools/tmp")
    parser.add_argument("--orders", type=lambda v: [int(x) for x in v.split(",")], default=[2, 3, 4, 5, 6])
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--months", default="1,2,3,4", help="comma-separated month numbers to read from a 1998 corpus ZIP")
    parser.add_argument("--expected-bytes", type=int)
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--source-note", default="")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--split-seed", type=int, default=20260929)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--memory", default="2G")
    parser.add_argument("--prefix", default=PREFIX)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=24)
    parser.add_argument("--min-tokens", type=int, default=6)
    parser.add_argument("--generation-seed", type=int, default=314159)
    parser.add_argument("--pruned-fivegram", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(2)
