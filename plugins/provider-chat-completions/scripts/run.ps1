$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$scriptPath = Join-Path $PSScriptRoot 'provider_chat_completions.py'
$hasPipelineInput = $MyInvocation.ExpectingInput

function ConvertTo-NativeArgument([string]$Value) {
  if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
    return $Value
  }

  $quoted = New-Object System.Text.StringBuilder
  [void]$quoted.Append('"')
  $backslashes = 0
  foreach ($character in $Value.ToCharArray()) {
    if ($character -eq '\') {
      $backslashes += 1
      continue
    }
    if ($character -eq '"') {
      for ($index = 0; $index -lt (($backslashes * 2) + 1); $index += 1) {
        [void]$quoted.Append('\')
      }
      [void]$quoted.Append('"')
      $backslashes = 0
      continue
    }
    for ($index = 0; $index -lt $backslashes; $index += 1) {
      [void]$quoted.Append('\')
    }
    $backslashes = 0
    [void]$quoted.Append($character)
  }
  for ($index = 0; $index -lt ($backslashes * 2); $index += 1) {
    [void]$quoted.Append('\')
  }
  [void]$quoted.Append('"')
  return $quoted.ToString()
}

function Invoke-PythonWithPipelineInput(
  [string]$Executable,
  [string[]]$PrefixArguments,
  [string[]]$ScriptArguments,
  [string]$InputText
) {
  $argumentValues = @($PrefixArguments) + @($scriptPath) + @($ScriptArguments)
  $nativeArguments = @($argumentValues | ForEach-Object { ConvertTo-NativeArgument ([string]$_) })

  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = $Executable
  $info.Arguments = $nativeArguments -join ' '
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.RedirectStandardInput = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true

  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $info
  try {
    [void]$process.Start()
    $stdoutCopy = $process.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
    $stderrCopy = $process.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
    $utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    $inputBytes = $utf8.GetBytes($InputText)
    $process.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
    $process.StandardInput.Close()
    $process.WaitForExit()
    $stdoutCopy.GetAwaiter().GetResult()
    $stderrCopy.GetAwaiter().GetResult()
    return $process.ExitCode
  } finally {
    if ($null -ne $process) {
      $process.Dispose()
    }
  }
}

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
    if ($hasPipelineInput) {
      $pipelineItems = @($input | ForEach-Object { [string]$_ })
      $pipelineText = [string]::Join([Environment]::NewLine, [string[]]$pipelineItems)
      exit (Invoke-PythonWithPipelineInput `
        -Executable $python.Source `
        -PrefixArguments @('-3') `
        -ScriptArguments $args `
        -InputText $pipelineText)
    }
    & $python.Source -3 $scriptPath @args
    exit $LASTEXITCODE
  }
}
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $python) {
  if (Test-Python38 $python.Source) {
    if ($hasPipelineInput) {
      $pipelineItems = @($input | ForEach-Object { [string]$_ })
      $pipelineText = [string]::Join([Environment]::NewLine, [string[]]$pipelineItems)
      exit (Invoke-PythonWithPipelineInput `
        -Executable $python.Source `
        -PrefixArguments @() `
        -ScriptArguments $args `
        -InputText $pipelineText)
    }
    & $python.Source $scriptPath @args
    exit $LASTEXITCODE
  }
}
Write-Output '{"ok":false,"stage":"runtime","code":"python_unavailable","retryable":false}'
exit 1
