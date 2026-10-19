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
$previousPath = $env:PATH
$exitCode = 1

& subst.exe $drive $root
if ($LASTEXITCODE -ne 0) {
    throw "Could not map the assignment folder to $drive."
}

try {
    if (Test-Path -LiteralPath $dllDir) {
        $env:PATH = "$dllDir;$env:PATH"
    }
    & $python -X utf8 "$drive\analyze_ngram_diagnostics.py" `
        --results-dir "$drive\results_1-6" `
        --kenlm-python-dir "$drive\tools\kenlm-build\lib"
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "KenLM diagnostics failed with exit code $exitCode."
    }
}
finally {
    $env:PATH = $previousPath
    & subst.exe $drive /D | Out-Null
}

exit $exitCode
