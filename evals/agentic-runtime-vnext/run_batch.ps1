param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("deepagents", "opencode", "pi")]
    [string]$Candidate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Resolve-Path (Join-Path $Root "..\..")
$Python = Join-Path $Repo ".venv-agentic-bakeoff\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

& $Python (Join-Path $Root "batch_run.py") --candidate $Candidate --repeats 3
