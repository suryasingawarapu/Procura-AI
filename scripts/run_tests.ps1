# Runs the test suite with a UTF-8 console on Windows.
#
#   .\scripts\run_tests.ps1                 # everything
#   .\scripts\run_tests.ps1 tests\unit     # a subset
#
# The suite never writes to the database; see tests\README.md.

$ErrorActionPreference = "Stop"

$env:PYTHONIOENCODING = "utf-8"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
}
catch {
    Write-Verbose "Could not change the console output encoding: $_"
}

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Virtual environment not found at $python"
    exit 1
}

& $python -m pytest @args
exit $LASTEXITCODE
