# KenLM Windows build record

## Source and targets

- Source distribution: `kenlm-ex-0.2.1.tar.gz`, package `kenlm-ex==0.2.1`, PyPI sdist SHA-256 `6657c2fd8b3650e0c234d1309272041ae4fd712d7fe27b60e3c72f57d5cebb55`.
- Build outputs required by the experiment: `tools/kenlm-build/bin/lmplz.exe` and `tools/kenlm-build/lib/kenlm.pyd`.
- The build used Release configuration, x64 MSVC, Python 3.12, CMake 3.14, max n-gram order 6, and the Python binding enabled. `lmplz` estimates Modified Kneser–Ney models; the binding loads ARPA files for scoring and stateful continuation.
- The local working build directory is intentionally ignored. Its CMake cache embeds the original mapped drive and toolchain paths; never copy that cache as a reusable build configuration.

## Windows reproducible build outline

Use an x64 Visual Studio Developer PowerShell with CMake, MSVC, NMake, and Python 3.12 available. `libarchive-c` is needed to read the input RAR; it is unrelated to KenLM. From the repository root, unpack the source archive and apply the checked-in Windows patch:

```powershell
New-Item -ItemType Directory -Path .\tools\kenlm-src -Force | Out-Null
tar.exe -xzf .\tools\kenlm-ex-0.2.1.tar.gz -C .\tools\kenlm-src
Push-Location .\tools\kenlm-src\kenlm_ex-0.2.1
git apply ..\..\kenlm-windows.patch
Pop-Location
```

Configure and build in a fresh output directory:

```powershell
cmake -S .\tools\kenlm-src\kenlm_ex-0.2.1 `
  -B .\tools\kenlm-build `
  -G "NMake Makefiles" `
  -DCMAKE_BUILD_TYPE=Release `
  -DENABLE_PYTHON=ON `
  -DKENLM_MAX_ORDER=6 `
  -DCMAKE_C_FLAGS=/DNOMINMAX `
  -DCMAKE_CXX_FLAGS=/DNOMINMAX
cmake --build .\tools\kenlm-build --config Release
```

Confirm that `lmplz.exe` exists under `tools/kenlm-build/bin/` and that `kenlm.pyd` exists under `tools/kenlm-build/lib/`. The active Python interpreter must match the interpreter selected when the binding is configured. On Windows, the wrapper temporarily maps the repository path to an ASCII drive because this local KenLM build could not reliably reopen ARPA paths containing Chinese characters.

## Patch rationale

`tools/kenlm-windows.patch` contains the source changes that were verified in the local build:

1. Lower CMake's minimum from 3.28 to 3.14 for the installed Windows CMake version.
2. Remove a duplicate `-h/--help` registration from `lmplz`; CLI11 already provides help and otherwise rejects startup.
3. Give the Python module import library the `_python` suffix to avoid colliding with static `kenlm.lib` under MSVC.

`/DNOMINMAX` is supplied at CMake configure time to avoid Windows macro collisions. The standalone binary build of `build_binary.exe` was not used; this experiment loads ARPA models through the Python binding.

## Environment used for the recorded run

Windows x64, MSVC 19.44, Python 3.12.7, CMake 3.14.0, KenLM source package 0.2.1. The run-time Python requirements for corpus parsing, resource limiting, and plotting are pinned in `requirements.txt`.

KenLM upstream: <https://github.com/kpu/kenlm>. Package: <https://pypi.org/project/kenlm-ex/0.2.1/>.
