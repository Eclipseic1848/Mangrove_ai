param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd("\")
$targets = [System.Collections.Generic.List[System.IO.DirectoryInfo]]::new()

# 只接受固定的可重建目录，避免把“清理测试结果”扩大为删除用户数据或依赖环境。
foreach ($directory in @(Get-ChildItem -LiteralPath $root -Force -Directory)) {
    if (
        $directory.Name -eq ".artifacts" -or
        $directory.Name -eq ".pytest-tmp" -or
        $directory.Name -like ".pytest-tmp-*" -or
        $directory.Name -eq ".pytest_tmp" -or
        $directory.Name -like ".pytest_tmp_*"
    ) {
        $targets.Add($directory)
    }
}

# 只补充固定的测试/审查缓存；frontend/dist 是 8088 同源入口的运行产物，不能按垃圾删除。
foreach ($relativePath in @(
    ".pytest_cache",
    ".hypothesis",
    ".superpowers\sdd",
    "frontend\test-results"
)) {
    $path = Join-Path $root $relativePath
    if (Test-Path -LiteralPath $path -PathType Container) {
        $targets.Add((Get-Item -LiteralPath $path))
    }
}

# 不扫描虚拟环境或依赖目录，只清理项目源码和测试树中的 Python 字节码缓存。
foreach ($relativePath in @("src", "tests", "scripts")) {
    $path = Join-Path $root $relativePath
    if (Test-Path -LiteralPath $path -PathType Container) {
        foreach ($cache in @(Get-ChildItem -LiteralPath $path -Recurse -Force -Directory -Filter "__pycache__")) {
            $targets.Add($cache)
        }
    }
}
$rootCache = Join-Path $root "__pycache__"
if (Test-Path -LiteralPath $rootCache -PathType Container) {
    $targets.Add((Get-Item -LiteralPath $rootCache))
}

$uniqueTargets = @($targets | Sort-Object FullName -Unique)
foreach ($target in $uniqueTargets) {
    $resolved = $target.FullName.TrimEnd("\")
    if (
        $resolved -eq $root -or
        -not $resolved.StartsWith($root + "\", [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "拒绝清理越界路径：$resolved"
    }
}

foreach ($target in @($uniqueTargets | Sort-Object { $_.FullName.Length } -Descending)) {
    Remove-Item -LiteralPath $target.FullName -Recurse -Force
}

Write-Host "[清理完成] 已删除 $($uniqueTargets.Count) 个可重建测试或构建目录。"
