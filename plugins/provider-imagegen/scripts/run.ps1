$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$scriptPath = Join-Path $PSScriptRoot 'provider_imagegen.py'

function Test-Python38([string]$Executable, [switch]$UsePyLauncher) {
  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = $Executable
  $info.Arguments = if ($UsePyLauncher) { '-3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"' } else { '-c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"' }
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.RedirectStandardInput = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $info
  try {
    [void]$process.Start()
    $process.StandardInput.Close()
    $process.StandardOutput.ReadToEnd() | Out-Null
    $process.StandardError.ReadToEnd() | Out-Null
    $process.WaitForExit()
    return ($process.ExitCode -eq 0)
  } catch {
    return $false
  } finally {
    if ($null -ne $process) { $process.Dispose() }
  }
}

if ($env:PROVIDER_IMAGEGEN_PYTHON) {
  if (-not [System.IO.Path]::IsPathRooted($env:PROVIDER_IMAGEGEN_PYTHON)) {
    Write-Output '{"ok":false,"stage":"runtime","code":"python_override_not_absolute","retryable":false}'
    exit 1
  }
  if (Test-Python38 $env:PROVIDER_IMAGEGEN_PYTHON) {
    & $env:PROVIDER_IMAGEGEN_PYTHON $scriptPath @args
    exit $LASTEXITCODE
  }
  Write-Output '{"ok":false,"stage":"runtime","code":"python_override_unavailable","retryable":false}'
  exit 1
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $py -and (Test-Python38 $py.Source -UsePyLauncher)) {
  & $py.Source -3 $scriptPath @args
  exit $LASTEXITCODE
}

foreach ($name in @('python', 'python3')) {
  $python = Get-Command $name -ErrorAction SilentlyContinue
  if ($null -ne $python -and (Test-Python38 $python.Source)) {
    & $python.Source $scriptPath @args
    exit $LASTEXITCODE
  }
}

Write-Output '{"ok":false,"stage":"runtime","code":"python_unavailable","retryable":false}'
exit 1
