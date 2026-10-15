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
import psutil


TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
RECORD_RE = re.compile(r"^(\d{8})-(\d+)-(\d+)-(\d+)(?:/[A-Za-z][A-Za-z0-9_-]*)?$")
CHUNK_RE = re.compile(r"\]([A-Za-z][A-Za-z0-9_-]*)$")
POS_RE = re.compile(r"^(.*)/([A-Za-z][A-Za-z0-9_-]*)$")
MONTH_FILE_RE = re.compile(r"(?:^|/)1998(\d{2})\.txt$", re.IGNORECASE)
TEACHER_MONTH_RE = re.compile(r"1998[-_](\d{2})[-_]2003版[-_]带音\.txt$", re.IGNORECASE)
PHONETIC_RE = re.compile(r"\{[^{}]*\}")
UNK = "__UNK_TOKEN__"
PREFIX = "在阳光明媚的五月，我们学校胜利召开了……"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def portable_repo_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if "tools" in parts:
        return "/".join(parts[parts.index("tools"):])
    return Path(value).name


def parse_months(value: str) -> list[int]:
    if not value.strip():
        return []
    months = sorted({int(part.strip()) for part in value.split(",") if part.strip()})
    if any(month < 1 or month > 12 for month in months):
        raise ValueError("--months 只能包含 1 到 12 的月份")
    return months


def read_source(path: Path, encoding: str, months: list[int]) -> tuple[list[tuple[str, int, bytes]], dict[str, object]]:
    archive_digest = sha256(path)
    source_files: list[tuple[str, int, bytes]] = []
    member_metadata: list[dict[str, object]] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            candidates = []
            for name in archive.namelist():
                match = MONTH_FILE_RE.search(name) or TEACHER_MONTH_RE.search(name)
                if match and (not months or int(match.group(1)) in months):
                    candidates.append((int(match.group(1)), name))
            candidates.sort()
            if not candidates:
                raise ValueError(f"ZIP 中没有找到指定月份的 1998 年语料文件: {months}")
            for month, name in candidates:
                raw = archive.read(name)
                source_files.append((name, month, raw))
                member_metadata.append({
                    "name": name,
                    "month": month,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                })
    elif path.suffix.lower() == ".rar":
        try:
            import libarchive
        except ImportError as exc:
            raise RuntimeError("读取教师 RAR 语料需要安装 libarchive-c==5.1") from exc
        candidates = []
        with libarchive.file_reader(str(path)) as archive:
            for entry in archive:
                match = MONTH_FILE_RE.search(entry.pathname) or TEACHER_MONTH_RE.search(entry.pathname)
                if match and entry.size and (not months or int(match.group(1)) in months):
                    month = int(match.group(1))
                    raw = b"".join(entry.get_blocks())
                    if len(raw) != entry.size:
                        raise ValueError(f"RAR 成员长度不符: {entry.pathname}")
                    candidates.append((month, entry.pathname, raw))
        candidates.sort(key=lambda item: (item[0], item[1]))
        if not candidates:
            raise ValueError(f"RAR 中没有找到指定月份的 1998 年语料文件: {months}")
        for month, name, raw in candidates:
            source_files.append((name, month, raw))
            member_metadata.append({
                "name": name,
                "month": month,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    else:
        raw = path.read_bytes()
        month_match = MONTH_FILE_RE.search(path.name) or TEACHER_MONTH_RE.search(path.name)
        month = int(month_match.group(1)) if month_match else 0
        source_files.append((path.name, month, raw))
        member_metadata.append({
            "name": path.name,
            "month": month,
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
    article_rows: OrderedDict[str, list[dict[str, object]]] = OrderedDict()
    record_groups: OrderedDict[str, list[dict[str, object]]] = OrderedDict()
    invalid_decode_articles: set[str] = set()
    decode_errors: list[dict[str, object]] = []
    unindexed_decode_errors: list[dict[str, object]] = []
    unindexed_lines: list[dict[str, object]] = []
    seen_dates: set[str] = set()
    stats: dict[str, object] = {
        **source_meta,
        "physical_lines": 0,
        "blank_lines": 0,
        "decoded_record_rows": 0,
        "records": 0,
        "articles_before_exclusion": 0,
        "articles": 0,
        "removed_record_ids": 0,
        "surface_tokens": 0,
        "removed_phonetic_annotations": 0,
        "removed_layout_markers": 0,
        "removed_control_characters": 0,
        "removed_duplicate_record_rows": 0,
        "removed_duplicate_segments": 0,
        "removed_duplicate_articles": 0,
        "removed_empty_records": 0,
        "unique_dates": 0,
        "date_start": "",
        "date_end": "",
        "no_id_lines_ignored": 0,
        "no_id_line_examples": unindexed_lines,
        "decode_errors": decode_errors,
        "unindexed_decode_errors": unindexed_decode_errors,
        "conflicting_record_ids": [],
        "invalid_decode_article_ids": [],
        "conflicting_article_ids": [],
        "excluded_article_ids": [],
    }
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    for source_name, month, raw in source_files:
        byte_offset = 0
        for line_no, raw_line_with_ending in enumerate(raw.splitlines(keepends=True), 1):
            line_offset = byte_offset
            byte_offset += len(raw_line_with_ending)
            stats["physical_lines"] = int(stats["physical_lines"]) + 1
            raw_line = raw_line_with_ending.rstrip(b"\r\n")
            if not raw_line.strip():
                stats["blank_lines"] = int(stats["blank_lines"]) + 1
                continue
            try:
                decoded = raw_line.decode(encoding, errors="strict")
            except UnicodeDecodeError as exc:
                prefix = raw_line.split(None, 1)[0].decode("ascii", errors="ignore")
                match = RECORD_RE.fullmatch(prefix)
                article_id = "-".join(match.groups()[:3]) if match else None
                error = {
                    "source": source_name,
                    "month": month,
                    "line": line_no,
                    "line_byte_offset": line_offset,
                    "byte_offset": line_offset + exc.start,
                    "record_id": prefix if match else None,
                    "article_id": article_id,
                    "reason": exc.reason,
                    "invalid_bytes_hex": raw_line[exc.start:exc.end].hex(" "),
                }
                if article_id:
                    invalid_decode_articles.add(article_id)
                    decode_errors.append(error)
                else:
                    unindexed_decode_errors.append(error)
                continue

            if "\ufffd" in decoded:
                prefix = decoded.split(None, 1)[0]
                match = RECORD_RE.fullmatch(prefix)
                article_id = "-".join(match.groups()[:3]) if match else None
                char_offset = len(decoded[:decoded.index("\ufffd")].encode(encoding, errors="strict"))
                error = {
                    "source": source_name,
                    "month": month,
                    "line": line_no,
                    "line_byte_offset": line_offset,
                    "byte_offset": line_offset + char_offset,
                    "record_id": prefix if match else None,
                    "article_id": article_id,
                    "reason": "literal U+FFFD character",
                    "invalid_bytes_hex": "",
                }
                if article_id:
                    invalid_decode_articles.add(article_id)
                    decode_errors.append(error)
                else:
                    unindexed_decode_errors.append(error)
                continue

            stripped = decoded.strip()
            pieces = stripped.split()
            match = RECORD_RE.fullmatch(pieces[0]) if pieces else None
            if not match:
                stats["no_id_lines_ignored"] = int(stats["no_id_lines_ignored"]) + 1
                if len(unindexed_lines) < 100:
                    unindexed_lines.append({
                        "source": source_name,
                        "month": month,
                        "line": line_no,
                        "byte_offset": line_offset,
                        "text": stripped[:200],
                    })
                continue

            date, edition, article, _segment = match.groups()
            record_id = pieces[0].split("/", 1)[0]
            article_id = f"{date}-{edition}-{article}"
            seen_dates.add(date)
            stats["decoded_record_rows"] = int(stats["decoded_record_rows"]) + 1
            stats["removed_record_ids"] = int(stats["removed_record_ids"]) + 1
            sentence: list[str] = []
            for piece in pieces[1:]:
                annotation_matches = PHONETIC_RE.findall(piece)
                if annotation_matches:
                    stats["removed_phonetic_annotations"] = int(stats["removed_phonetic_annotations"]) + len(annotation_matches)
                    piece = PHONETIC_RE.sub("", piece)
                marker_count = piece.count("〓")
                if marker_count:
                    stats["removed_layout_markers"] = int(stats["removed_layout_markers"]) + marker_count
                    piece = piece.replace("〓", "")
                controls = sum(unicodedata.category(char) == "Cc" for char in piece)
                if controls:
                    stats["removed_control_characters"] = int(stats["removed_control_characters"]) + controls
                    piece = "".join(char for char in piece if unicodedata.category(char) != "Cc")
                if piece:
                    sentence.append(piece)
            if not sentence:
                stats["removed_empty_records"] = int(stats["removed_empty_records"]) + 1
                continue
            row = {
                "record_id": record_id,
                "article_id": article_id,
                "sentence": sentence,
                "source": source_name,
                "line": line_no,
                "byte_offset": line_offset,
            }
            article_rows.setdefault(article_id, []).append(row)
            record_groups.setdefault(record_id, []).append(row)

    conflict_article_ids: set[str] = set()
    for record_id, rows in record_groups.items():
        variants = {tuple(row["sentence"]) for row in rows}
        article_ids_for_record = sorted({str(row["article_id"]) for row in rows})
        if len(variants) > 1 or len(article_ids_for_record) > 1:
            conflict_article_ids.update(article_ids_for_record)
            stats["conflicting_record_ids"].append({
                "record_id": record_id,
                "article_ids": article_ids_for_record,
                "variants": len(variants),
                "locations": [{"source": row["source"], "line": row["line"], "byte_offset": row["byte_offset"]} for row in rows],
            })
        else:
            stats["removed_duplicate_record_rows"] = int(stats["removed_duplicate_record_rows"]) + max(0, len(rows) - 1)

    stats["articles_before_exclusion"] = len(article_rows)
    stats["invalid_decode_article_ids"] = sorted(invalid_decode_articles)
    stats["conflicting_article_ids"] = sorted(conflict_article_ids)
    excluded_articles = invalid_decode_articles | conflict_article_ids
    stats["excluded_article_ids"] = sorted(excluded_articles)
    unique_articles: OrderedDict[str, list[list[str]]] = OrderedDict()
    article_signatures: dict[tuple[tuple[str, ...], ...], str] = {}
    for article_id, rows in article_rows.items():
        if article_id in excluded_articles:
            continue
        seen_records: set[str] = set()
        seen_segments: set[tuple[str, ...]] = set()
        kept: list[list[str]] = []
        for row in rows:
            record_id = str(row["record_id"])
            sentence = list(row["sentence"])
            if record_id in seen_records:
                continue
            seen_records.add(record_id)
            signature = tuple(sentence)
            if signature in seen_segments:
                stats["removed_duplicate_segments"] = int(stats["removed_duplicate_segments"]) + 1
                continue
            seen_segments.add(signature)
            kept.append(sentence)
        if not kept:
            continue
        article_signature = tuple(tuple(sentence) for sentence in kept)
        if article_signature in article_signatures:
            stats["removed_duplicate_articles"] = int(stats["removed_duplicate_articles"]) + 1
            continue
        article_signatures[article_signature] = article_id
        unique_articles[article_id] = kept

    sentences: list[list[str]] = []
    article_ids: list[str] = []
    with cleaned_path.open("w", encoding="utf-8", newline="\n") as output:
        for article_id, article_sentences in unique_articles.items():
            if article_ids:
                output.write("\n")
            for sentence in article_sentences:
                sentences.append(sentence)
                article_ids.append(article_id)
                stats["surface_tokens"] = int(stats["surface_tokens"]) + len(sentence)
                output.write(" ".join(sentence) + "\n")
    stats["records"] = len(sentences)
    stats["articles"] = len(unique_articles)
    stats["excluded_articles"] = len(excluded_articles)
    stats["unique_dates"] = len(seen_dates)
    stats["date_start"] = min(seen_dates) if seen_dates else ""
    stats["date_end"] = max(seen_dates) if seen_dates else ""
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


def restrict_cpu_affinity(max_cpus: int) -> list[int]:
    if max_cpus < 1:
        raise ValueError("--max-cpus 必须至少为 1")
    process = psutil.Process()
    available = process.cpu_affinity()
    selected = available[:max_cpus]
    if not selected:
        raise RuntimeError("无法确定可用 CPU 亲和性，拒绝在未限核的情况下启动训练")
    process.cpu_affinity(selected)
    return process.cpu_affinity()


def run(args: argparse.Namespace) -> int:
    source = Path(args.input)
    output = Path(args.output_dir)
    data_dir = output / "data"
    model_dir = output / "models"
    log_dir = output / "logs"
    model_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    cpu_affinity = restrict_cpu_affinity(args.max_cpus)
    print(f"[cpu] process affinity restricted to {len(cpu_affinity)} CPUs: {cpu_affinity}", flush=True)

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
        f"tokens={corpus_stats['surface_tokens']} "
        f"decode_errors={len(corpus_stats['decode_errors'])} "
        f"excluded_articles={corpus_stats['excluded_articles']} "
        f"conflicting_record_ids={len(corpus_stats['conflicting_record_ids'])} "
        f"phonetics_removed={corpus_stats['removed_phonetic_annotations']} "
        f"layout_markers_removed={corpus_stats['removed_layout_markers']} "
        f"duplicate_segments_removed={corpus_stats['removed_duplicate_segments']} "
        f"duplicate_articles_removed={corpus_stats['removed_duplicate_articles']}",
        flush=True,
    )
    train_indices, test_indices, split_meta = split_by_article(cleaned, article_ids, args.train_ratio, args.split_seed)
    train_raw = [cleaned[i] for i in train_indices]
    test_raw = [cleaned[i] for i in test_indices]
    train_article_ids = set(split_meta["train_article_ids"])
    article_record_counts = Counter(article_ids)
    article_index_path = data_dir / "corpus_articles.csv"
    with article_index_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["article_id", "record_count", "split"])
        for article_id, record_count in article_record_counts.items():
            writer.writerow([article_id, record_count, "train" if article_id in train_article_ids else "test"])
    train_frequency = Counter(token for sentence in train_raw for token in sentence)
    keep_words = {token for token, count in train_frequency.items() if count >= args.min_count}
    keep_words.add(UNK)
    train_path = data_dir / "train_for_lmplz.txt"
    test_path = data_dir / "test_for_scoring.txt"
    vocabulary_path = data_dir / "train_vocabulary.txt"
    vocabulary_path.write_text("\n".join(sorted(keep_words)) + "\n", encoding="utf-8")
    train_token_count, rare_train_tokens = write_modeling_text(train_path, train_raw, keep_words)
    test_token_count, test_oov_tokens = write_modeling_text(test_path, test_raw, keep_words)
    print(
        f"[split] train={len(train_raw)} records/{split_meta['train_articles']} articles "
        f"test={len(test_raw)} records/{split_meta['test_articles']} articles "
        f"train_tokens={train_token_count} test_tokens={test_token_count} "
        f"train_rare_mapped={rare_train_tokens} test_oov_mapped={test_oov_tokens}",
        flush=True,
    )

    source_record = {
        "input_file": source.name,
        "repository_url": args.source_url,
        "source_note": args.source_note,
        "archive_bytes": corpus_stats["archive_bytes"],
        "archive_sha256": corpus_stats["archive_sha256"],
        "selected_months": months,
        "encoding": args.encoding,
        "files": corpus_stats["source_files"],
    }
    preflight = {
        "source": source_record,
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
        "kept_vocabulary_size": len(keep_words),
        "vocabulary_file": vocabulary_path.name,
        "article_index_file": article_index_path.name,
        "model_orders": args.orders,
        "pruned_fivegram": args.pruned_fivegram,
        "max_cpus": args.max_cpus,
        "cpu_affinity": cpu_affinity,
    }
    if args.preflight_only:
        preflight_path = output / "corpus_preflight.json"
        preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[preflight-only] no KenLM training started; wrote {preflight_path}", flush=True)
        return 0

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
            "arpa_sha256": sha256(arpa_path),
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
        "source": source_record,
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
        "kenlm_tool": portable_repo_path(args.lmplz),
        "kenlm_python_binding": portable_repo_path(args.kenlm_python_dir),
        "estimator": "KenLM lmplz, modified Kneser-Ney",
        "memory_limit": args.memory,
        "max_cpus": args.max_cpus,
        "cpu_affinity": cpu_affinity,
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
    parser.add_argument("--input", default="data/teacher_1998_jan_jun.rar")
    parser.add_argument("--output-dir", default="results_1-6")
    parser.add_argument("--lmplz", default="tools/kenlm-build/bin/lmplz.exe")
    parser.add_argument("--kenlm-python-dir", default="tools/kenlm-build/lib")
    parser.add_argument("--dll-dir", default="")
    parser.add_argument("--temp-prefix", default="tools/tmp")
    parser.add_argument("--orders", type=lambda v: [int(x) for x in v.split(",")], default=[2, 3, 4, 5, 6])
    parser.add_argument("--encoding", default="gb18030")
    parser.add_argument("--months", default="1,2,3,4,5,6", help="comma-separated month numbers to read from a 1998 corpus archive")
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
    parser.add_argument("--max-cpus", type=int, default=4, help="restrict this process and child training process affinity")
    parser.add_argument("--preflight-only", action="store_true", help="clean, audit, split, and write token data without training KenLM")
    parser.add_argument("--pruned-fivegram", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(2)
