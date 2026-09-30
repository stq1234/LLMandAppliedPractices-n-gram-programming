# 《人民日报》词级 n-gram 实验

本仓库包含《人民日报》1998 年 1—4 月语料的清洗、文章级训练/测试划分、KenLM n-gram 建模、困惑度评测、指定前缀续写及结果绘图代码。

## 内容

- `run_rmrb_experiment.py`：解析 PFR 记录、清理标注、按文章切分数据，并训练和评测 2–6-gram 与剪枝 5-gram。
- `run_rmrb_experiment_1to4.ps1`：Windows PowerShell 复跑入口，固定使用 1998 年 1–4 月、2–6 阶模型及剪枝 5-gram。
- `plot_ngram_results.py`：根据结果 CSV 和实验元数据生成困惑度折线图、资源柱状图和 token 分布饼图。
- `作业3实验报告.md`：完整实验报告。
- `results_1-4/`：本次实验的 CSV、元数据、训练日志和三张报告图。
- `data/SOURCE.md`：语料来源、文件校验值和复跑说明。

## 数据与运行依赖

出于避免重新分发原始新闻语料的考虑，原始语料归档不随仓库发布。语料来源为 [PeopleDaily1998](https://github.com/chenhui-bupt/PeopleDaily1998)。如已获准使用该数据，请将归档放在 `data/人民日报1998_PFR.zip`。

训练入口为 Windows PowerShell 脚本，并需要本机可用的 KenLM `lmplz.exe` 与 Python `kenlm` 绑定。脚本当前从以下本地路径读取它们：

- `tools/kenlm-build/bin/lmplz.exe`
- `tools/kenlm-build/lib`

这些平台相关的构建产物未提交。可以参考 `tools/BUILD_NOTES.md` 了解本次本地构建记录。安装 Matplotlib 后，可在仓库根目录运行 `python .\plot_ngram_results.py` 重新生成三张图。

## 复跑

在 Windows PowerShell 中，将语料归档及本机 KenLM 构建放到上述位置，然后从仓库根目录运行：

```powershell
.\run_rmrb_experiment_1to4.ps1
```

脚本会写出 `results_1-4/` 下的模型和中间语料文件。大型 ARPA 模型和中间数据不包含在 Git 提交中；实验汇总、元数据、日志和图表已保留在仓库内。

## 主要结果

在固定文章级留出集上，测试 PPL 从 5-gram 的 220.005 降至 6-gram 的 219.624（约 0.17%），而 ARPA 文件大小增加约 46%。剪枝 5-gram 将文件缩小约 69%，但测试 PPL 上升至 226.027。详细设置、结果和适用边界见实验报告。
