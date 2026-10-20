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
    param([string]$Repository, [string[]]$ExcludedPaths = @())
    $temporaryDiff = Join-Path ([IO.Path]::GetTempPath()) ("mangrove-external-" + [guid]::NewGuid().ToString("N") + ".patch")
    try {
        $arguments = @("-C", $Repository, "diff", "--no-ext-diff", "--binary", "--output=$temporaryDiff")
        if ($ExcludedPaths.Count) {
            $arguments += @("--", ".") + @($ExcludedPaths | ForEach-Object { ":(exclude)$_" })
        }
        & git @arguments
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

function Get-MediaCrawlerCookieOverlay {
    param([string]$Repository)
    $patch = Join-Path $root "external\patches\mediacrawler-cookie-refresh.patch"
    $manifestPath = Join-Path $root "external\patches\mediacrawler-cookie-refresh.manifest.json"
    foreach ($path in @($patch, $manifestPath, (Join-Path $root "external"), (Join-Path $root "external\patches"),
        (Join-Path $root "external\MediaCrawler"), $Repository, (Join-Path $Repository "media_platform"), (Join-Path $Repository "media_platform\xhs"))) {
        if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "小红书会话补丁路径含链接，拒绝继续。"
        }
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $paths = @("media_platform/xhs/client.py", "media_platform/xhs/session_cookies.py")
    if ($manifest.schema -ne "mangrove-xhs-cookie-refresh-v1" -or $manifest.line_endings -ne "preserve" -or
        $manifest.patch_sha256 -cnotmatch '^[0-9a-f]{64}$' -or @($manifest.files).Count -ne 2 -or
        (@($manifest.files | ForEach-Object { $_.path }) -join "`n") -cne ($paths -join "`n") -or
        (Get-FileSha256 -Path $patch) -cne $manifest.patch_sha256) {
        throw "小红书会话补丁清单或摘要不匹配。"
    }
    $patchPaths = @(& git -C $Repository apply --numstat $patch | ForEach-Object { ($_ -split "`t")[-1] })
    if ($LASTEXITCODE -ne 0 -or ($patchPaths -join "`n") -cne ($paths -join "`n")) {
        throw "小红书会话补丁超出两个冻结文件。"
    }
    $before = $true
    $after = $true
    $crlf = $false
    for ($index = 0; $index -lt 2; $index++) {
        $entry = $manifest.files[$index]
        foreach ($phase in @("before", "after")) {
            $digest = $entry.($phase + "_sha256")
            $raw = $entry.($phase + "_raw_sha256")
            if ($index -eq 1 -and $phase -eq "before") {
                if ($null -ne $digest -or $null -ne $raw) { throw "小红书新增文件的前件必须为空。" }
            } elseif ($digest -cnotmatch '^[0-9a-f]{64}$' -or @($raw).Count -ne 2 -or
                @($raw | Where-Object { $_ -cnotmatch '^[0-9a-f]{64}$' }).Count -or $digest -cnotin $raw) {
                throw "小红书会话文件摘要未冻结。"
            }
        }
        $path = Join-Path $Repository $entry.path
        try { $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop }
        catch [Management.Automation.ItemNotFoundException] { $item = $null }
        if ($null -eq $item) {
            $before = $before -and $index -eq 1
            $after = $false
            continue
        }
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "小红书会话文件类型不明确。"
        }
        $text = (New-Object Text.UTF8Encoding($false, $true)).GetString([IO.File]::ReadAllBytes($path))
        $hasCrlf = $text.Contains("`r`n")
        $lf = $text.Replace("`r`n", "`n")
        if ($lf.Contains("`r") -or ($hasCrlf -and $text.Replace("`r`n", "").Contains("`n"))) {
            throw "小红书会话文件含未知混合换行。"
        }
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try { $digest = ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($lf)))).Replace("-", "").ToLowerInvariant() }
        finally { $algorithm.Dispose() }
        $rawDigest = Get-FileSha256 -Path $path
        $before = $before -and $digest -ceq $entry.before_sha256 -and $rawDigest -cin $entry.before_raw_sha256
        $after = $after -and $digest -ceq $entry.after_sha256 -and $rawDigest -cin $entry.after_raw_sha256
        if ($index -eq 0) { $crlf = $hasCrlf }
    }
    if (-not $before -and -not $after) { throw "小红书会话补丁不是完整前件或后件，拒绝覆盖。" }
    return @{ Patch = $patch; Paths = $paths; Installed = $after; Crlf = $crlf }
}

function Install-MediaCrawlerCookieOverlay {
    param([string]$Repository)
    $overlay = Get-MediaCrawlerCookieOverlay -Repository $Repository
    if ($overlay.Installed) { return }
    # 仅按原客户端行尾应用冻结补丁，不暂存文件或改写整文件换行。
    $autocrlf = if ($overlay.Crlf) { "true" } else { "false" }
    $arguments = @("-c", "core.autocrlf=$autocrlf", "-C", $Repository, "apply")
    Invoke-Git ($arguments + @("--check", $overlay.Patch))
    Invoke-Git ($arguments + @($overlay.Patch))
    $installed = Get-MediaCrawlerCookieOverlay -Repository $Repository
    if (-not $installed.Installed -or $installed.Crlf -ne $overlay.Crlf) {
        throw "小红书会话补丁应用后的摘要或原换行不一致。"
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
    $overlayPaths = @()
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

        if ($Name -eq "MediaCrawler") {
            $overlay = Get-MediaCrawlerCookieOverlay -Repository $target
            $overlayPaths = $overlay.Paths
            $status = @(& git --no-optional-locks -C $target status --porcelain --untracked-files=all)
            $allowedOverlay = @((" M " + $overlayPaths[0]), ("?? " + $overlayPaths[1]))
            # 只豁免摘要已核实的工作树覆盖；暂存、未知新文件及其他定制改动不能借此放行。
            $unknown = @($status | Where-Object {
                $_ -cnotin $allowedOverlay -and ($currentRevision -eq $PreparedRevision -or $_ -cnotlike " M *")
            })
            if ($LASTEXITCODE -ne 0 -or $unknown.Count) { throw "MediaCrawler 包含非冻结工作树改动，拒绝覆盖。" }
        }
        if ($currentRevision -eq $PreparedRevision) {
            if ($Name -eq "MediaCrawler") {
                Install-MediaCrawlerCookieOverlay -Repository $target
            } else {
                $status = @(& git -C $target status --porcelain)
                if ($LASTEXITCODE -ne 0 -or $status.Count -gt 0) {
                    throw "$Name 的兼容定制提交之外仍有本机改动，拒绝覆盖：$target"
                }
            }
            Write-Host "[已就绪] $Name：兼容定制提交 $PreparedRevision"
            return
        }

        $actualDiffHash = Get-RepositoryDiffSha256 -Repository $target -ExcludedPaths $overlayPaths
        $expectedDiffHash = Get-FileSha256 -Path $patch
        # SHA-256(空文件) 是固定值；直接使用常量以兼容 Windows PowerShell 5.1/.NET Framework。
        $emptyDiffHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        if ($actualDiffHash -eq $expectedDiffHash) {
            if ($Name -eq "MediaCrawler") { Install-MediaCrawlerCookieOverlay -Repository $target }
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
    if ((Get-RepositoryDiffSha256 -Repository $target -ExcludedPaths $overlayPaths) -ne (Get-FileSha256 -Path $patch)) {
        throw "$Name 补丁应用后的内容哈希不一致。"
    }
    if ($Name -eq "MediaCrawler") { Install-MediaCrawlerCookieOverlay -Repository $target }
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
