$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$scriptPath = Join-Path $PSScriptRoot 'provider_chat_completions.py'
$python = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $python) {
  & $python.Source -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' *> $null
  if ($LASTEXITCODE -eq 0) {
    & $python.Source -3 $scriptPath @args
    exit $LASTEXITCODE
  }
}
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $python) {
  & $python.Source -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' *> $null
  if ($LASTEXITCODE -eq 0) {
    & $python.Source $scriptPath @args
    exit $LASTEXITCODE
  }
}
Write-Output '{"ok":false,"stage":"runtime","code":"python_unavailable","retryable":false}'
exit 1
