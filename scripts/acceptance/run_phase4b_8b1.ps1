[CmdletBinding()]
param(
    [ValidateSet("preflight", "full")]
    [string]$Mode = "full",
    [string]$RunId = "g5-$(Get-Date -Format 'yyyyMMdd-HHmmss')",
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ModelBaseUrl,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ModelName,
    [ValidateRange(1024, 65532)]
    [int]$Port = 18088,
    [ValidateRange(1, 7200)]
    [int]$ModelTimeoutSeconds = 1800,
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runner = Join-Path $projectRoot "scripts\acceptance\run_phase4b_8b1.py"

$arguments = @(
    $runner,
    "--project-root", $projectRoot,
    "--run-id", $RunId,
    "--mode", $Mode,
    "--model-base-url", $ModelBaseUrl,
    "--model-name", $ModelName,
    "--port", [string]$Port,
    "--model-timeout-seconds", [string]$ModelTimeoutSeconds
)

$env:PYTHONUTF8 = "1"
& $PythonExecutable @arguments
exit $LASTEXITCODE
