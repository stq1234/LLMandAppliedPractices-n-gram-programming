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
$pythonScript = Join-Path $PSScriptRoot 'run_rmrb_experiment.py'
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
        '--input', "$drive\data\人民日报1998_PFR.zip",
        '--output-dir', "$drive\results_1-4",
        '--lmplz', "$drive\tools\kenlm-build\bin\lmplz.exe",
        '--kenlm-python-dir', "$drive\tools\kenlm-build\lib",
        '--dll-dir', $dllDir,
        '--temp-prefix', "$drive/tools/tmp",
        '--encoding', 'utf-8-sig',
        '--months', '1,2,3,4',
        '--orders', '2,3,4,5,6',
        '--pruned-fivegram',
        '--source-url', 'https://github.com/chenhui-bupt/PeopleDaily1998',
        '--source-note', 'Repository describes the PKU PeopleDaily1998 PFR corpus as January-April 1998; only those four named monthly files are used.',
        '--memory', '2G'
    )
    & $python -X utf8 $pythonScript @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    & subst.exe $drive /D | Out-Null
}

exit $exitCode
