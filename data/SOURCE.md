# 人民日报 1998 年 1—6 月教师带音语料

## 输入归档

- 归档文件：`data/teacher_1998_jan_jun.rar`
- 文件大小：12,093,296 字节
- SHA-256：`30c4435bcce7d082c2175720a4549114f80656761df95d3c8b20f0ee48e5d7ab`
- 格式：单卷 RAR4，无加密。Python 通过 `libarchive-c` 读取；成员完整列表、字节数与 SHA-256 写入 `results_1-6/experiment_metadata.json`。
- 原始文件由课程教师提供；保留原归档字节，未将提取文件改写后冒充源文件。
- 编码：GB18030，无 UTF-8 BOM。逐物理记录严格解码。`errors=replace` 不用于训练流程。
- 表面格式：记录 ID `YYYYMMDD-版次-文章-段落`，后接空白分隔的已分词正文，部分词项内嵌 `{pinyin+声调}` 读音。无 `/POS` 标注。清理时保留原分词、标点，移除读音标注和 `〓` 版式符号。

## 六个月归档成员

| 月份 | 成员 | 字节数 | SHA-256 |
|---|---|---:|---|
| 1 | `1998-上半年-带音/1998-01-2003版-带音.txt` | 5,320,005 | `37b85555df2f72642ff9815ac01f51d4f50024d80ca0e2e8a6be6f93b1038639` |
| 2 | `1998-上半年-带音/1998-02-2003版-带音.txt` | 5,441,098 | `a050351664f459aed2247bc45cd46c25da2ce893e11c44a9dea06a54529f19d8` |
| 3 | `1998-上半年-带音/1998-03-2003版-带音.txt` | 5,951,910 | `2f9d8ecfd92eec1b8a7009bd146da5faad218a34ae17fe460944bd772b2e97db` |
| 4 | `1998-上半年-带音/1998-04-2003版-带音.txt` | 6,107,764 | `43462d53f2f7603168745af3200a9e6fe883ddcdf18618280358499e0ffc3fca` |
| 5 | `1998-上半年-带音/1998-05-2003版-带音.txt` | 5,920,187 | `04ae37614a55c2418bcb5c7697c4980afa75a74734e930f06bee6e83022d0aea` |
| 6 | `1998-上半年-带音/1998-06-2003版-带音.txt` | 5,946,378 | `f18f8f3d6e886644baf8306e1f08a31e3a8d390709782f913ae1ea12f3e97c5f2` |

## Strict-decode and conflicting-ID exclusions

Five records contain one illegal byte (`AD`) under strict GB18030 decoding. Their member-relative byte offsets, line starts, IDs, and decoder reasons are stored in `corpus_stats.decode_errors` within the experiment metadata. No replacement character is inserted. The entire article for each affected ID is omitted:

| Article ID | Record ID(s) | Member-relative offending byte offset(s) |
|---|---|---|
| `19980323-09-005` | `19980323-09-005-012` | 4,252,061 |
| `19980430-03-005` | `19980430-03-005-007` | 5,945,346 |
| `19980507-04-007` | `19980507-04-007-005`, `...-011`, `...-013` | 1,025,114; 1,025,580; 1,025,759 |

January record ID `19980127-09-001-032` appears twice with different body text. Its two records are at member-relative line-start offsets 4,965,857 and 4,966,114. The full article `19980127-09-001` is excluded; both variants and source positions are recorded in `corpus_stats.conflicting_record_ids`.

The audited archive contains no undecodable line without a record ID and no nonempty line without a recognized ID. These categories are still counted and listed separately by the parser so a changed source cannot silently enter the corpus as a record.

## Previous source and scope

An earlier n-gram run used a PFR corpus with different record IDs, tokenization, annotation conventions, and punctuation. The current run reads only the teacher archive listed above. Because the input and text representation differ, the two perplexity values are not a controlled before/after comparison.

The experiment repository is <https://github.com/stq1234/LLMandAppliedPractices-n-gram-programming>.
