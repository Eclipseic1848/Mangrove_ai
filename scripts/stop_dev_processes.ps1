param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [switch]$IncludeLaunchers,
    [int[]]$Ports = @(5173, 8088)
)

$ErrorActionPreference = "SilentlyContinue"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd("\").ToLowerInvariant()
$processes = @(Get-CimInstance Win32_Process)
$byId = @{}
foreach ($process in $processes) {
    $byId[[int]$process.ProcessId] = $process
}

function Get-VerifiedParent {
    param([object]$Process)

    $parent = $byId[[int]$Process.ParentProcessId]
    if ($null -eq $parent) {
        return $null
    }
    # Windows 会复用已退出进程的 PID。只有创建时间不晚于子进程，才能视为真实父进程；
    # 否则沿陈旧 ParentProcessId 追溯会把无关测试进程树误接到正在运行的平台服务。
    if ([datetime]$parent.CreationDate -gt [datetime]$Process.CreationDate) {
        return $null
    }
    return $parent
}

# 绝对路径双击 start_all.bat 时，当前清理进程的祖先命令行也包含项目根和启动器名。
# 这些 PID 属于本次启动链，不能按“旧启动器的后代”回收，否则脚本会在启动中途杀死自己。
$currentStartupChainIds = [System.Collections.Generic.HashSet[int]]::new()
$currentProcess = $byId[[int]$PID]
for ($depth = 0; $depth -lt 8 -and $null -ne $currentProcess; $depth++) {
    [void]$currentStartupChainIds.Add([int]$currentProcess.ProcessId)
    $currentProcess = Get-VerifiedParent -Process $currentProcess
}

function Test-IsMangroveProcess {
    param([object]$Process)

    $cursor = $Process
    for ($depth = 0; $depth -lt 8 -and $null -ne $cursor; $depth++) {
        $normalized = ([string]$cursor.CommandLine).ToLowerInvariant()
        if (
            $normalized.Contains("--mangrove-service-root") -and
            $normalized.Contains($root)
        ) {
            return $true
        }
        if (
            $normalized.Contains($root + "\frontend") -and
            $normalized.Contains("vite")
        ) {
            return $true
        }
        if (
            $normalized.Contains($root) -and
            $normalized.Contains("start_all.bat")
        ) {
            if ($currentStartupChainIds.Contains([int]$cursor.ProcessId)) {
                return $false
            }
            if ($depth -gt 0 -or $IncludeLaunchers) {
                return $true
            }
        }
        $cursor = Get-VerifiedParent -Process $cursor
    }
    return $false
}

$seedIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($listener in @(Get-NetTCPConnection -State Listen | Where-Object {
    $_.LocalPort -in $Ports
})) {
    $listenerProcess = $byId[[int]$listener.OwningProcess]
    if (Test-IsMangroveProcess -Process $listenerProcess) {
        [void]$seedIds.Add([int]$listener.OwningProcess)
    }
}

foreach ($process in $processes) {
    if (Test-IsMangroveProcess -Process $process) {
        [void]$seedIds.Add([int]$process.ProcessId)
    }
}

$rootIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($seedId in $seedIds) {
    $cursor = $byId[$seedId]
    if ($null -eq $cursor) {
        continue
    }
    $candidate = $cursor
    for ($depth = 0; $depth -lt 8; $depth++) {
        $parent = Get-VerifiedParent -Process $candidate
        if ($null -eq $parent) {
            break
        }
        $parentCommand = ([string]$parent.CommandLine).ToLowerInvariant()
        if ($parentCommand.Contains("start_all.bat")) {
            break
        }
        if ($parent.Name -notin "cmd.exe", "node.exe", "python.exe") {
            break
        }
        $candidate = $parent
    }
    [void]$rootIds.Add([int]$candidate.ProcessId)
}

foreach ($processId in $rootIds) {
    & taskkill.exe /PID $processId /T /F *> $null
}

Start-Sleep -Milliseconds 300
$remaining = @(Get-NetTCPConnection -State Listen | Where-Object {
    $_.LocalPort -in $Ports
})
if ($remaining.Count -gt 0) {
    Write-Host "[停止警告] 端口 5173 或 8088 仍被占用，请检查是否有非本项目进程。"
    exit 1
}

Write-Host "[停止完成] 本项目后端和前端进程树已清理。"
exit 0
