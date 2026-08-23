$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$scriptPath = Join-Path $PSScriptRoot 'provider_chat_completions.py'

function Test-Python38([string]$Executable, [switch]$UsePyLauncher) {
  $arguments = if ($UsePyLauncher) {
    '-3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"'
  } else {
    '-c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"'
  }

  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = $Executable
  $info.Arguments = $arguments
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
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return ($process.ExitCode -eq 0)
  } catch {
    return $false
  } finally {
    if ($null -ne $process) {
      $process.Dispose()
    }
  }
}

$python = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $python) {
  if (Test-Python38 $python.Source -UsePyLauncher) {
    & $python.Source -3 $scriptPath @args
    exit $LASTEXITCODE
  }
}
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $python) {
  if (Test-Python38 $python.Source) {
    & $python.Source $scriptPath @args
    exit $LASTEXITCODE
  }
}
Write-Output '{"ok":false,"stage":"runtime","code":"python_unavailable","retryable":false}'
exit 1
