# 人民日报词级 n-gram 实验

本仓库使用课程提供的《人民日报》1998 年 1—6 月带音语料，按文章去重、清洗和切分后，训练 KenLM Modified Kneser–Ney 2–6-gram，并比较一个剪枝 5-gram。当前结果和完整本地报告基于 `results_1-6/` 中的实际运行产物。

## 主要文件

- `run_rmrb_experiment.py`：严格逐行解码、清理带音记录、审计异常、文章级去重和切分、构造训练词表、训练和评测 n-gram 模型。
- `run_rmrb_experiment_1to6.ps1`：Windows PowerShell 入口；支持先运行 `-PreflightOnly` 做统计检查，也可执行完整训练、生成图表和复核指标。
- `validate_ngram_results.py`：检查清洗文本、文章划分、训练词表和切分文件，并可重新加载 ARPA 模型复算 PPL。
- `plot_ngram_results.py`：从 CSV 和元数据生成三张结果图。
- `results_1-6/experiment_results.csv`、`experiment_metadata.json`、`validation.json`：实际结果、完整数据清理/切分审计信息、复核结果。
- `results_1-6/data/`：清洗后词面语料、文章级 split 索引、训练词表及 KenLM/评分输入文本。
- `作业3实验报告.md`：本次实验报告。
- `data/SOURCE.md`：教师语料的成员清单、哈希、解码错误和冲突记录。
- `data/corpus_audit.md`：最终清理语料、排除项、统计与文章级切分审计。
- `tools/BUILD_NOTES.md`、`tools/kenlm-windows.patch`：KenLM Windows 构建记录和本地补丁。

## 数据处理和评测

原始归档 `data/teacher_1998_jan_jun.rar` 使用 GB18030 严格逐记录解码。读音花括号标注和 `〓` 版式标记会移除；原有空白分词和标点保留。对完全相同的段落和文章去重。出现无法解码记录或同一记录编号正文冲突的整篇文章排除，并在 `experiment_metadata.json` 记录来源文件、行号、成员内字节偏移、异常字节或冲突位置。没有记录编号的非空行会单独审计，不纳入训练。

文章去重完成后，按固定随机种子将文章分入训练和测试。词表只从训练文本按最小词频 2 生成；训练稀有词和测试集 OOV 映射到 `__UNK_TOKEN__`。每个模型使用相同的训练文本、词表和文章级测试集。PPL 用 KenLM 对每条测试记录计算并计入 EOS 事件。

## 运行环境

需要 Windows、Python 3.12、PowerShell、KenLM `lmplz.exe` 与 Python 绑定、可加载的 libarchive 动态库，以及 `requirements.txt` 中的 Python 包。若使用 Conda，可通过 `conda install -c conda-forge libarchive` 安装 libarchive DLL，再安装 Python requirements。KenLM 构建步骤和源码 SHA-256 见 `tools/BUILD_NOTES.md`。本地 KenLM 构建目录会被 `.gitignore` 排除；运行前应把可用构建放在 `tools/kenlm-build/`。

创建环境后，从仓库根目录先运行统计和清理预检：

```powershell
python -m pip install -r requirements.txt
.\run_rmrb_experiment_1to6.ps1 -PreflightOnly
```

确认 `results_1-6/corpus_preflight.json` 后执行完整实验：

```powershell
.\run_rmrb_experiment_1to6.ps1
```

脚本将当前进程及 KenLM 子进程限制在最多 4 个 CPU 核心。KenLM 在 Windows 下不能从含中文的工作路径可靠打开 ARPA 文件，因此脚本会临时把仓库目录映射到空闲 ASCII 盘符，并在结束时解除映射。清洗数据、split、词表、日志、元数据和图表会写入 `results_1-6/`。大型 ARPA 文件不纳入版本控制；其字节数和 SHA-256 会保存在元数据及验证文件里。

如只需用已有结果重画图，可在仓库根目录运行：

```powershell
python .\plot_ngram_results.py
```

如需独立重查数据与模型结果：

```powershell
python .\validate_ngram_results.py --results-dir .\results_1-6
```

此命令会核对清洗、切分与训练词表。完整训练入口会在临时 ASCII 盘符下重新加载各 ARPA 文件并复算 PPL；这是因为当前 Windows KenLM 构建无法可靠打开中文目录中的模型路径。

本次实验的代码及相关说明可在该仓库中获取：<https://github.com/stq1234/LLMandAppliedPractices-n-gram-programming>。
