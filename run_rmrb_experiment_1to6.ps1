param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$driveLetter = @('K', 'R', 'T', 'V', 'W', 'X', 'Y', 'Z') |
    Where-Object { -not (Get-PSDrive -Name $_ -ErrorAction SilentlyContinue) } |
    Select-Object -First 1
if (-not $driveLetter) {
    throw 'No free drive letter is available for the temporary KenLM path mapping.'
}

$drive = "${driveLetter}:"
$python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$pythonHome = Split-Path -Parent $python
$dllDir = Join-Path $pythonHome 'Library\bin'
$pythonScript = "$drive\run_rmrb_experiment.py"
$plotScript = "$drive\plot_ngram_results.py"
$validatorScript = "$drive\validate_ngram_results.py"
$exitCode = 1

& subst.exe $drive $root
if ($LASTEXITCODE -ne 0) {
    throw "Could not map the assignment folder to $drive."
}

try {
    if (Test-Path -LiteralPath $dllDir) {
        $env:PATH = "$dllDir;$env:PATH"
    }
    $arguments = @(
        '--input', "$drive\data\teacher_1998_jan_jun.rar",
        '--output-dir', "$drive\results_1-6",
        '--lmplz', "$drive\tools\kenlm-build\bin\lmplz.exe",
        '--kenlm-python-dir', "$drive\tools\kenlm-build\lib",
        '--dll-dir', $dllDir,
        '--temp-prefix', "$drive/tools/tmp",
        '--encoding', 'gb18030',
        '--months', '1,2,3,4,5,6',
        '--expected-bytes', '12093296',
        '--expected-sha256', '30c4435bcce7d082c2175720a4549114f80656761df95d3c8b20f0ee48e5d7ab',
        '--orders', '2,3,4,5,6',
        '--pruned-fivegram',
        '--source-note', 'Course-provided People Daily corpus for January through June 1998 with inline pronunciation annotations; archive member hashes are recorded in experiment metadata.',
        '--memory', '2G',
        '--max-cpus', '4'
    )
    if ($PreflightOnly) {
        $arguments += '--preflight-only'
    }
    & $python -X utf8 $pythonScript @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Corpus preparation or KenLM training failed with exit code $exitCode."
    }
    if (-not $PreflightOnly) {
        & $python -X utf8 $plotScript `
            --results "$drive\results_1-6\experiment_results.csv" `
            --metadata "$drive\results_1-6\experiment_metadata.json" `
            --output-dir "$drive\results_1-6"
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Plot generation failed with exit code $exitCode."
        }
        & $python -X utf8 $validatorScript `
            --results-dir "$drive\results_1-6" `
            --kenlm-python-dir "$drive\tools\kenlm-build\lib" `
            --rescore-models `
            --max-cpus 4
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Corpus/model validation failed with exit code $exitCode."
        }
    }
}
finally {
    & subst.exe $drive /D | Out-Null
}

exit $exitCode
