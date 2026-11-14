param([ValidateSet('setup', 'init', 'demo', 'jobs', 'compile', 'test', 'lint', 'loc', 'load', 'recovery', 'check')] [string]$Task = 'check')

$ErrorActionPreference = 'Continue'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force -Path evidence | Out-Null

function Save-Result([string]$Name, [string]$Command, [int]$ExitCode) {
    $line = "$(Get-Date -Format o) | $Name | $Command | exit=$ExitCode"
    Add-Content -LiteralPath evidence\commands.txt -Value $line
}

if ($Task -eq 'setup') {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m pip install -r requirements-dev.txt
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $python)) {
    throw '先运行 .\tasks.ps1 setup'
}

if ($Task -eq 'init') {
    & $python -m app.cli init-db
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m app.cli seed-demo
    exit $LASTEXITCODE
}

if ($Task -eq 'demo') {
    & $python -m app.cli demo
    exit $LASTEXITCODE
}

if ($Task -eq 'jobs') {
    & $python -m app.cli run-jobs
    exit $LASTEXITCODE
}

if ($Task -eq 'compile' -or $Task -eq 'check') {
    & $python -m compileall app scripts 2>&1 | Tee-Object evidence\compile.txt
    Save-Result 'compile' 'python -m compileall app scripts' $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Task -eq 'test' -or $Task -eq 'check') {
    $testProcess = Start-Process -FilePath $python -ArgumentList '-m unittest discover -s tests -v' -Wait -PassThru -WindowStyle Hidden -RedirectStandardError evidence\unittest.txt
    Get-Content evidence\unittest.txt
    Save-Result 'test' 'python -m unittest discover -s tests -v' $testProcess.ExitCode
    if ($testProcess.ExitCode -ne 0) { exit $testProcess.ExitCode }
}

if ($Task -eq 'loc' -or $Task -eq 'check') {
    $locProcess = Start-Process -FilePath $python -ArgumentList 'scripts/count_loc.py' -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput evidence\loc.txt
    Get-Content evidence\loc.txt
    Save-Result 'loc' 'python scripts/count_loc.py' $locProcess.ExitCode
    if ($locProcess.ExitCode -ne 0) { exit $locProcess.ExitCode }
}

if ($Task -eq 'lint' -or $Task -eq 'check') {
    $lintProcess = Start-Process -FilePath $python -ArgumentList '-m pylint app scripts tests --exit-zero' -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput evidence\pylint.txt -RedirectStandardError evidence\pylint-errors.txt
    Get-Content evidence\pylint.txt
    Save-Result 'lint-report' 'python -m pylint app scripts tests --exit-zero' $lintProcess.ExitCode
    if ($lintProcess.ExitCode -ne 0) { exit $lintProcess.ExitCode }
    & $python -m pylint -E app scripts tests
    Save-Result 'lint-errors' 'python -m pylint -E app scripts tests' $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Task -eq 'load') {
    $loadProcess = Start-Process -FilePath $python -ArgumentList '-m scripts.load_test' -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput evidence\load_test.txt -RedirectStandardError evidence\load-errors.txt
    Get-Content evidence\load_test.txt
    Save-Result 'load' 'python -m scripts.load_test' $loadProcess.ExitCode
    exit $loadProcess.ExitCode
}

if ($Task -eq 'recovery') {
    $recoveryProcess = Start-Process -FilePath $python -ArgumentList '-m scripts.recovery_test' -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput evidence\recovery.txt -RedirectStandardError evidence\recovery-errors.txt
    Get-Content evidence\recovery.txt
    Save-Result 'recovery' 'python -m scripts.recovery_test' $recoveryProcess.ExitCode
    exit $recoveryProcess.ExitCode
}
