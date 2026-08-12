param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [ValidateSet("All", "MediaCrawler", "Firecrawl")]
    [string]$Component = "All"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd("\")

function Invoke-Git {
    param([string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git 命令执行失败：git $($Arguments -join ' ')"
    }
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-RepositoryDiffSha256 {
    param([string]$Repository)
    $temporaryDiff = Join-Path ([IO.Path]::GetTempPath()) ("mangrove-external-" + [guid]::NewGuid().ToString("N") + ".patch")
    try {
        & git -C $Repository diff --no-ext-diff --binary --output=$temporaryDiff
        if ($LASTEXITCODE -ne 0) {
            throw "无法读取外部组件差异：$Repository"
        }
        return Get-FileSha256 -Path $temporaryDiff
    }
    finally {
        if (Test-Path -LiteralPath $temporaryDiff) {
            Remove-Item -LiteralPath $temporaryDiff -Force
        }
    }
}

function Install-ExternalComponent {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Revision,
        [string]$RelativeTarget,
        [string]$RelativePatch,
        [string]$PreparedRevision = ""
    )

    $target = [IO.Path]::GetFullPath((Join-Path $root $RelativeTarget))
    $patch = [IO.Path]::GetFullPath((Join-Path $root $RelativePatch))
    if (
        -not $target.StartsWith($root + "\", [StringComparison]::OrdinalIgnoreCase) -or
        -not $patch.StartsWith($root + "\", [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "$Name 路径越界，停止安装。"
    }
    if (-not (Test-Path -LiteralPath $patch -PathType Leaf)) {
        throw "$Name 补丁不存在：$patch"
    }

    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath (Join-Path $target ".git") -PathType Container)) {
            throw "$Name 目标已存在但不是 Git 仓库：$target。请人工备份或移走后重试。"
        }
        $origin = (& git -C $target remote get-url origin).Trim()
        if ($LASTEXITCODE -ne 0 -or $origin -ne $Url) {
            throw "$Name 上游地址与冻结声明不一致，拒绝继续：$origin"
        }

        $currentRevision = (& git -C $target rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or ($currentRevision -ne $Revision -and $currentRevision -ne $PreparedRevision)) {
            throw "$Name 当前提交不是冻结基线，拒绝覆盖：$currentRevision"
        }

        if ($currentRevision -eq $PreparedRevision) {
            $status = @(& git -C $target status --porcelain)
            if ($LASTEXITCODE -ne 0 -or $status.Count -gt 0) {
                throw "$Name 的兼容定制提交之外仍有本机改动，拒绝覆盖：$target"
            }
            Write-Host "[已就绪] $Name：兼容定制提交 $PreparedRevision"
            return
        }

        $actualDiffHash = Get-RepositoryDiffSha256 -Repository $target
        $expectedDiffHash = Get-FileSha256 -Path $patch
        # SHA-256(空文件) 是固定值；直接使用常量以兼容 Windows PowerShell 5.1/.NET Framework。
        $emptyDiffHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        if ($actualDiffHash -eq $expectedDiffHash) {
            Write-Host "[已就绪] $Name：$Revision + Mangrove 补丁"
            return
        }
        if ($actualDiffHash -ne $emptyDiffHash) {
            throw "$Name 包含非冻结补丁改动，拒绝覆盖：$target"
        }
    }
    else {
        $parent = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent | Out-Null
        }
        Invoke-Git @("clone", "--no-checkout", $Url, $target)
    }

    Invoke-Git @("-C", $target, "fetch", "--force", "origin", $Revision)
    Invoke-Git @("-C", $target, "checkout", "--detach", $Revision)
    Invoke-Git @("-C", $target, "apply", "--check", $patch)
    Invoke-Git @("-C", $target, "apply", $patch)

    $actual = (& git -C $target rev-parse HEAD).Trim()
    if ($actual -ne $Revision) {
        throw "$Name 基线不一致：期望 $Revision，实际 $actual"
    }
    if ((Get-RepositoryDiffSha256 -Repository $target) -ne (Get-FileSha256 -Path $patch)) {
        throw "$Name 补丁应用后的内容哈希不一致。"
    }
    Write-Host "[准备完成] $Name：$Revision + Mangrove 补丁"
}

if ($Component -in @("All", "MediaCrawler")) {
    Install-ExternalComponent `
        -Name "MediaCrawler" `
        -Url "https://github.com/NanmiCoder/MediaCrawler" `
        -Revision "c9a111be73586bdf6fc44536f088e4db6ed86d64" `
        -PreparedRevision "07a11337a5e3cfc5544e155803e67bb79b33a1bd" `
        -RelativeTarget "external\MediaCrawler\repo" `
        -RelativePatch "external\patches\mediacrawler-mangrove.patch"
}

if ($Component -in @("All", "Firecrawl")) {
    Install-ExternalComponent `
        -Name "Firecrawl" `
        -Url "https://github.com/firecrawl/firecrawl.git" `
        -Revision "8d679cbcb68ad8456f26166d69fb17d03c7068fe" `
        -PreparedRevision "17c16aa8ab8b47dc202f96627f7b4f1cd87c3ea1" `
        -RelativeTarget "external\firecrawl" `
        -RelativePatch "external\patches\firecrawl-mangrove.patch"
}
