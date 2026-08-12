$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Resolve-Path (Join-Path $Root "..\..")
$Venv = Join-Path $Repo ".venv-agentic-bakeoff"

if (-not (Test-Path -LiteralPath (Join-Path $Venv "Scripts\python.exe"))) {
    py -3.13 -m venv $Venv
}

& (Join-Path $Venv "Scripts\python.exe") -m pip install `
    --disable-pip-version-check `
    --retries 5 `
    --timeout 120 `
    "deepagents==0.6.12" `
    "langchain-openai==1.4.1"

Push-Location $Root
try {
    npm.cmd install `
        --ignore-scripts `
        --omit=optional `
        --fetch-retries=5 `
        --fetch-retry-mintimeout=20000 `
        --fetch-retry-maxtimeout=120000 `
        --fetch-timeout=600000
}
finally {
    Pop-Location
}

Write-Host "阶段 1 原型依赖已安装。"
