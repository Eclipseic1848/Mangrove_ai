param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("deepagents", "opencode", "pi")]
    [string]$Candidate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Resolve-Path (Join-Path $Root "..\..")
$Python = Join-Path $Repo ".venv-agentic-bakeoff\Scripts\python.exe"
$Runs = Join-Path $Root "runs"
New-Item -ItemType Directory -Path $Runs -Force | Out-Null

$Before = @(
    Get-ChildItem -LiteralPath $Runs -Directory |
        Where-Object { $_.Name -like "*-$Candidate-p0-09-cancellation-*" } |
        ForEach-Object { $_.FullName }
)
$Stdout = Join-Path $Runs "cancel-$Candidate.stdout.log"
$Stderr = Join-Path $Runs "cancel-$Candidate.stderr.log"
$env:PYTHONIOENCODING = "utf-8"
$Driver = Start-Process -FilePath $Python `
    -ArgumentList @("tui.py", "--candidate", $Candidate, "--case-id", "p0-09-cancellation") `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -WindowStyle Hidden `
    -PassThru

$RunDir = $null
$Ready = $false
$Deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $Deadline) {
    $RunDir = Get-ChildItem -LiteralPath $Runs -Directory |
        Where-Object {
            $_.Name -like "*-$Candidate-p0-09-cancellation-*" -and
            $_.FullName -notin $Before
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($RunDir) {
        $ToolLog = Join-Path $RunDir.FullName "tool_calls.jsonl"
        $IndexReady = (Test-Path -LiteralPath $ToolLog) -and
            ((Get-Content -LiteralPath $ToolLog -Encoding utf8 -Raw) -match "workbook:index")
        $SlowTool = Get-CimInstance Win32_Process |
            Where-Object {
                $_.CommandLine -like "*tool_host.py*" -and
                $_.CommandLine -like "*p0-09-cancellation*" -and
                $_.CommandLine -like "*$($RunDir.Name)*"
            } |
            Select-Object -First 1
        if ($IndexReady -and $SlowTool) {
            $Ready = $true
            break
        }
    }
    Start-Sleep -Milliseconds 250
}

if (-not $RunDir -or -not $Ready) {
    taskkill /PID $Driver.Id /T /F | Out-Null
    throw "Slow tool was not reached within 90 seconds."
}

# 取消请求由统一 Supervisor 读取；模型和具体框架无权忽略。
Set-Content -LiteralPath (Join-Path $RunDir.FullName "cancel.request") `
    -Value "stage1-cancel-probe" `
    -Encoding utf8

$Driver | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue
if (Get-Process -Id $Driver.Id -ErrorAction SilentlyContinue) {
    taskkill /PID $Driver.Id /T /F | Out-Null
    throw "Adapter process tree did not exit within 20 seconds."
}

$StatePath = Join-Path $RunDir.FullName "unified-state.json"
if (-not (Test-Path -LiteralPath $StatePath)) {
    throw "Unified state was not written after cancellation."
}
$State = Get-Content -LiteralPath $StatePath -Encoding utf8 -Raw | ConvertFrom-Json
$Residual = @(
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like "*$($RunDir.Name)*" }
)
$CandidatePath = Join-Path $RunDir.FullName "candidate.json"
$Passed = $State.status -eq "cancelled" -and
    -not (Test-Path -LiteralPath $CandidatePath) -and
    $Residual.Count -eq 0

[ordered]@{
    candidate = $Candidate
    passed = $Passed
    status = $State.status
    candidate_created = Test-Path -LiteralPath $CandidatePath
    residual_processes = $Residual.Count
    run_dir = $RunDir.FullName
} | ConvertTo-Json -Compress

if (-not $Passed) {
    exit 1
}
