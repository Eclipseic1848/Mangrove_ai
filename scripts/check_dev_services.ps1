param(
    [ValidateRange(5, 600)]
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "SilentlyContinue"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$backendReady = $false
$platformReady = $false
$lanBound = $false

while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8088/api/health" -TimeoutSec 2
        $backendReady = $health.ok -eq $true
    } catch {
        $backendReady = $false
    }

    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8088/" -UseBasicParsing -TimeoutSec 2
        $contentType = [string]$response.Headers["Content-Type"]
        $platformReady = (
            $response.StatusCode -eq 200 -and
            $contentType.StartsWith("text/html")
        )
    } catch {
        $platformReady = $false
    }

    $listeners = Get-NetTCPConnection -LocalPort 8088 -State Listen
    $lanBound = @($listeners | Where-Object {
        $_.LocalAddress -eq "0.0.0.0" -or $_.LocalAddress -eq "::"
    }).Count -gt 0

    if ($backendReady -and $platformReady -and $lanBound) {
        Write-Host "[健康检查通过] 8088 API、同源前端和局域网监听均已就绪。"
        exit 0
    }
    Start-Sleep -Seconds 1
}

Write-Host "[健康检查失败] backend=$backendReady platform=$platformReady lan_binding=$lanBound"
if (-not $backendReady) {
    Write-Host "  - 后端未通过 http://127.0.0.1:8088/api/health"
}
if (-not $platformReady) {
    Write-Host "  - 8088 同源前端未通过 http://127.0.0.1:8088/"
}
if (-not $lanBound) {
    Write-Host "  - 平台没有监听 0.0.0.0:8088 或 [::]:8088，局域网无法访问"
}
exit 1
