<#
.SYNOPSIS
  Builds the Surge Windows installer: UI -> PyInstaller bundle -> self-test -> Inno Setup.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 0.2.0 -Python C:\Python312\python.exe

.NOTES
  Needs Python 3.12, Node 20.19+ or 22.12+ and Inno Setup 6 (ISCC.exe). ffmpeg and yt-dlp are downloaded and
  checksum-verified into build\cache on first use.
  Output: build\installer\Surge-Setup-<version>.exe and SHA256SUMS.txt
#>
[CmdletBinding()]
param(
    [string]$Version,        # default: __version__ in backend\surge\__init__.py (a leading "v" is dropped)
    [string]$Python,         # a Python 3.12 interpreter; default: py -3.12, then python on PATH
    [string]$FfmpegDir,      # folder holding ffmpeg.exe + ffprobe.exe; default: the pinned Gyan build is downloaded
    [switch]$SkipSelfTest,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Pinned ffmpeg: Gyan.dev "full" static build (GPLv3; includes the NVENC/QSV/AMF encoders Surge probes for).
$FfmpegVersion = "9.0"
$FfmpegSha256 = "F42F0C4B04EAE3AC918707FF66E3E0FF0CEE527BFA6D322624D4BC1160D5055E"  # of the release zip

$Root = Split-Path $PSScriptRoot -Parent
$Build = Join-Path $Root "build"
$Cache = Join-Path $Build "cache"
$Dist = Join-Path $Build "dist"
$AppDir = Join-Path $Dist "Surge"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed (exit code $LASTEXITCODE)" }
}

function Get-File([string]$Url, [string]$Dest) {
    if (Test-Path $Dest) { return }
    New-Item -ItemType Directory -Force (Split-Path $Dest) | Out-Null
    Run curl.exe @("-L", "--fail", "--silent", "--show-error", "-o", "$Dest.part", $Url)
    Move-Item "$Dest.part" $Dest
}

function Test-Python312([string]$Exe) {
    try { return ((& $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null) -eq "3.12") } catch { return $false }
}

function Find-Python312 {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $p = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        $ErrorActionPreference = $prev
        if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p }
    }
    $candidates += @(Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($c in $candidates) { if (Test-Python312 $c) { return $c } }
    throw "Python 3.12 not found. Install it, or pass -Python <path to a 3.12 python.exe>."
}

# ---- version
if (-not $Version -or $Version -notmatch '^v?\d+\.\d+\.\d+') {
    $Version = [regex]::Match((Get-Content "$Root\backend\surge\__init__.py" -Raw), '__version__\s*=\s*"([^"]+)"').Groups[1].Value
}
$Version = $Version.TrimStart("v")
$VersionNumeric = [regex]::Match($Version, '^\d+\.\d+\.\d+').Value + ".0"   # the installer's numeric file version
Write-Host "Building Surge $Version"

# ---- Python environment
Step "Python environment"
if (-not $Python) { $Python = Find-Python312 } elseif (-not (Test-Python312 $Python)) { throw "-Python must be a 3.12 interpreter: $Python" }
$Venv = Join-Path $Build "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { Run $Python @("-m", "venv", $Venv) }
Run $VenvPy @("-m", "pip", "install", "--disable-pip-version-check", "--no-input",
              "-r", "$Root\backend\requirements.txt", "-r", "$PSScriptRoot\requirements-build.txt")

# ---- UI
Step "Building the UI"
Push-Location "$Root\ui"
try {
    if (-not (Test-Path node_modules)) { Run npm.cmd @("ci", "--no-audit", "--no-fund") }
    Run npm.cmd @("run", "build")
} finally { Pop-Location }

# ---- ffmpeg, ffprobe, yt-dlp
Step "ffmpeg, ffprobe and yt-dlp"
$Bin = Join-Path $Cache "bin"
New-Item -ItemType Directory -Force $Bin | Out-Null
if ($FfmpegDir) {
    Copy-Item (Join-Path $FfmpegDir "ffmpeg.exe"), (Join-Path $FfmpegDir "ffprobe.exe") $Bin -Force
    $lic = Join-Path (Split-Path $FfmpegDir -Parent) "LICENSE"
    if (Test-Path $lic) { Copy-Item $lic (Join-Path $Bin "ffmpeg-LICENSE.txt") -Force }
} else {
    $zip = Join-Path $Cache "ffmpeg-$FfmpegVersion-full_build.zip"
    Get-File "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVersion/ffmpeg-$FfmpegVersion-full_build.zip" $zip
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($got -ne $FfmpegSha256) { Remove-Item $zip -Force; throw "ffmpeg zip checksum mismatch (got $got)" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -match '/bin/(ffmpeg|ffprobe)\.exe$') { $name = $entry.Name }
            elseif ($entry.FullName -match '^[^/]+/LICENSE$') { $name = "ffmpeg-LICENSE.txt" }
            else { continue }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $Bin $name), $true)
        }
    } finally { $archive.Dispose() }
}
$sums = Join-Path $Cache "yt-dlp.SHA2-256SUMS"
Remove-Item $sums -ErrorAction SilentlyContinue
Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS" $sums
$want = ((Get-Content $sums | Where-Object { $_ -match '\s\*?yt-dlp\.exe$' } | Select-Object -First 1) -split '\s+')[0]
if (-not $want) { throw "yt-dlp.exe is not listed in SHA2-256SUMS" }
$ytdlp = Join-Path $Bin "yt-dlp.exe"
if (-not (Test-Path $ytdlp) -or (Get-FileHash $ytdlp -Algorithm SHA256).Hash -ne $want.ToUpper()) {
    Remove-Item $ytdlp -ErrorAction SilentlyContinue
    Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" $ytdlp
    if ((Get-FileHash $ytdlp -Algorithm SHA256).Hash -ne $want.ToUpper()) { throw "yt-dlp.exe checksum mismatch" }
}
foreach ($f in "ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe") { if (-not (Test-Path (Join-Path $Bin $f))) { throw "$f missing from $Bin" } }

# ---- freeze
Step "Freezing with PyInstaller"
if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
Run $VenvPy @("-m", "PyInstaller", "$PSScriptRoot\surge.spec", "--noconfirm", "--clean",
              "--distpath", $Dist, "--workpath", (Join-Path $Build "work"))
Copy-Item (Join-Path $Bin "ffmpeg.exe"), (Join-Path $Bin "ffprobe.exe"), $ytdlp $AppDir
New-Item -ItemType Directory -Force (Join-Path $AppDir "licenses") | Out-Null
if (Test-Path (Join-Path $Bin "ffmpeg-LICENSE.txt")) { Copy-Item (Join-Path $Bin "ffmpeg-LICENSE.txt") (Join-Path $AppDir "licenses") }
Copy-Item "$Root\LICENSE", "$Root\THIRD_PARTY_NOTICES.md" $AppDir

# ---- prove the frozen app works
if (-not $SkipSelfTest) {
    Step "Self-test of the frozen app"
    $tmp = Join-Path $Build "selftest-data"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $env:SURGE_DATA = $tmp
    try {
        $p = Start-Process (Join-Path $AppDir "Surge.exe") -ArgumentList "--selftest" -Wait -PassThru
        if (Test-Path "$tmp\selftest.txt") { Get-Content "$tmp\selftest.txt" | Write-Host }
        if ($p.ExitCode -ne 0) { throw "Self-test failed (exit code $($p.ExitCode)); see $tmp\surge.log" }

        if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
            Write-Warning "Port 8765 is busy (is Surge running?). Skipping the engine start check."
        } else {
            $srv = Start-Process (Join-Path $AppDir "Surge.exe") -ArgumentList "--server" -PassThru
            try {
                $ok = $false
                for ($i = 0; $i -lt 90 -and -not $ok; $i++) {
                    Start-Sleep -Seconds 1
                    if ($srv.HasExited) { throw "Engine exited early (code $($srv.ExitCode)); see $tmp\surge.log" }
                    try { $ok = (Invoke-WebRequest "http://127.0.0.1:8765/" -UseBasicParsing -TimeoutSec 3).Content -match 'id="root"' } catch { }
                }
                if (-not $ok) { throw "Engine did not serve the UI on 127.0.0.1:8765 within 90 s; see $tmp\surge.log" }
                Write-Host "Engine started and served the UI on http://127.0.0.1:8765"
            } finally { if (-not $srv.HasExited) { Stop-Process -Id $srv.Id -Force } }
        }
    } finally { Remove-Item Env:SURGE_DATA -ErrorAction SilentlyContinue }
}

# ---- installer
if (-not $SkipInstaller) {
    Step "Inno Setup"
    $iscc = @(
        (Get-Command iscc -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php" }
    Run $iscc @("/Q", "/DAppVersion=$Version", "/DAppVersionNumeric=$VersionNumeric", "$PSScriptRoot\surge.iss")
    $setup = Get-Item (Join-Path $Build "installer\Surge-Setup-$Version.exe")
    $hash = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash.ToLower()
    "$hash *$($setup.Name)" | Set-Content (Join-Path $setup.DirectoryName "SHA256SUMS.txt") -Encoding ascii
    Write-Host ("`nInstaller: {0}  ({1:N0} MB)`nSHA-256:   {2}" -f $setup.FullName, ($setup.Length / 1MB), $hash) -ForegroundColor Green
}
