$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot '.browsers'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv (Join-Path $PSScriptRoot '.venv')
}
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
& $python -m playwright install chromium
& $python (Join-Path $PSScriptRoot 'helper.py')
