param(
    [ValidateRange(5, 600)]
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "SilentlyContinue"
$docker = Get-Command "docker.exe" -ErrorAction SilentlyContinue
if ($null -eq $docker) {
    Write-Host "[Docker 检查失败] 未找到 docker.exe，请先安装 Docker Desktop。"
    exit 1
}

function Test-DockerReady {
    & $docker.Source info *> $null
    return $LASTEXITCODE -eq 0
}

if (Test-DockerReady) {
    Write-Host "[Docker 检查通过] Docker Engine 已就绪。"
    exit 0
}

$desktopCandidates = @(
    (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
    (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
)
$desktop = $desktopCandidates | Where-Object {
    Test-Path -LiteralPath $_
} | Select-Object -First 1

if ($null -ne $desktop -and $null -eq (Get-Process -Name "Docker Desktop")) {
    Write-Host "[Docker 启动] Docker Desktop 尚未运行，正在自动启动。"
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
} else {
    Write-Host "[Docker 等待] Docker Desktop 正在初始化。"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$nextProgress = Get-Date
while ((Get-Date) -lt $deadline) {
    if (Test-DockerReady) {
        Write-Host "[Docker 检查通过] Docker Engine 已就绪。"
        exit 0
    }
    if ((Get-Date) -ge $nextProgress) {
        $remaining = [Math]::Max(0, [Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds))
        Write-Host "[Docker 等待] Engine 尚未就绪，最多继续等待 $remaining 秒。"
        $nextProgress = (Get-Date).AddSeconds(10)
    }
    Start-Sleep -Seconds 2
}

Write-Host "[Docker 检查失败] Docker Engine 未在 $TimeoutSeconds 秒内就绪。"
Write-Host "请打开 Docker Desktop 查看错误，修复后重新运行 start_all.bat。"
exit 1
