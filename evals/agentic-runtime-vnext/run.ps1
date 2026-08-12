$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Resolve-Path (Join-Path $Root "..\..")
$Python = Join-Path $Repo ".venv-agentic-bakeoff\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "请先运行 .\evals\agentic-runtime-vnext\setup.ps1"
}

$env:PYTHONIOENCODING = "utf-8"
& $Python (Join-Path $Root "tui.py")
