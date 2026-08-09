<#
.SYNOPSIS
Waits for, then terminates, processes still running from a Cargo target directory.

.DESCRIPTION
Windows cannot replace a binary whose image is mapped by a live process. Contract
tests leave two kinds of short-lived fastctx processes behind on purpose: control
centers riding out their idle window and detached background-job supervisors
finishing their commands. Back-to-back cargo invocations relink the same
target binary within seconds, so without a drain the next test group fails
with os error 5. Runs between test groups on Windows; a no-op elsewhere and
when nothing holds the directory.
#>
param(
    [string]$TargetDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) "target"),
    [int]$GraceSeconds = 45
)

$ErrorActionPreference = "Stop"
if (-not $IsWindows) {
    exit 0
}
$root = [System.IO.Path]::GetFullPath($TargetDirectory)

function Get-TargetProcesses {
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try {
            $path = $_.MainModule.FileName
            $path -and $path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
        } catch {
            # Access to another user's or a protected process's modules is denied;
            # those can never be our test spawns.
            $false
        }
    }
}

$holders = @(Get-TargetProcesses)
if ($holders.Count -eq 0) {
    exit 0
}
Write-Host "Draining $($holders.Count) process(es) still running from $root"
$deadline = (Get-Date).AddSeconds($GraceSeconds)
while ((Get-Date) -lt $deadline) {
    if (@(Get-TargetProcesses).Count -eq 0) {
        Write-Host "Drained without termination."
        exit 0
    }
    Start-Sleep -Milliseconds 250
}
foreach ($process in @(Get-TargetProcesses)) {
    $path = try { $process.MainModule.FileName } catch { "<unknown>" }
    Write-Host "Terminating $($process.Id) ($path) after the $GraceSeconds-second grace period."
    try { Stop-Process -Id $process.Id -Force -ErrorAction Stop } catch {}
}
exit 0
