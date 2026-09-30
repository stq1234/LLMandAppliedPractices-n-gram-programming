# KenLM 本地构建记录

- 来源包：`kenlm-ex-0.2.1.tar.gz`，PyPI SHA-256 `6657c2fd8b3650e0c234d1309272041ae4fd712d7fe27b60e3c72f57d5cebb55`。这是基于 KenLM 的 CMake 发行包，随附 LGPL-2.1-or-later 许可文本。
- 构建环境：Windows x64、MSVC 19.44、CMake 3.14、Python 3.12.7。
- 构建目标：`lmplz.exe` 和 `kenlm.pyd`；C++训练器由 KenLM 构建器生成 Modified Kneser–Ney ARPA 模型，Python 绑定负责困惑度和状态式续写。
- 本地适配：系统 CMake 为 3.14，因此仅在本地源码副本将 CMake 最低版本从 3.28 调低；为 MSVC 增加 `/DNOMINMAX`；移除 `lmplz_main.cc` 重复注册的默认 `-h/--help` 标记；给 Python 模块导入库增加 `_python` 后缀，避免与静态 `kenlm.lib` 撞名。
- 构建使用 Release 配置。当前中文项目路径会让 KenLM 的 Windows CRT 无法重开 ARPA 文件，复跑脚本因此临时用空闲盘符将该目录映射为 ASCII 路径，脚本退出时会解除映射。
- `build_binary.exe` 因源码使用 Windows 下不存在的 `unistd.h` 未构建；本实验直接用 KenLM Python 绑定加载 ARPA 文件，不依赖二进制 `.klm` 文件。

KenLM 上游说明：<https://github.com/kpu/kenlm>。发行包信息与平台说明：<https://pypi.org/project/kenlm-ex/0.2.1/>。
