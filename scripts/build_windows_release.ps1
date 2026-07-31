param(
    [switch]$SkipInstaller,
    [switch]$SkipPyInstaller,
    [string]$EmbeddedPythonVersion = "3.12.10",
    [ValidateRange(1, 5)]
    [int]$InstallerRetries = 3
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildDir = Join-Path $ProjectRoot "build"
$DistDir = Join-Path $ProjectRoot "dist"
$ReleaseDir = Join-Path $ProjectRoot "release"
$BuildCacheDir = Join-Path $env:LOCALAPPDATA "SmallEnterpriseAccountingBuildCache"
$EmbeddedPythonSha256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
$InstallerStageRoot = $null

function Remove-ProjectDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $prefix = $ProjectRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside project: $full"
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Prepare-EmbeddedPython([string]$Version) {
    $archiveName = "python-$Version-embed-amd64.zip"
    $archivePath = Join-Path $BuildCacheDir $archiveName
    $runtimeRoot = Join-Path $BuildDir "python-embed-$Version"
    New-Item -ItemType Directory -Path $BuildCacheDir -Force | Out-Null

    if (-not (Test-Path -LiteralPath $archivePath)) {
        $downloadPath = "$archivePath.download"
        Remove-Item -LiteralPath $downloadPath -Force -ErrorAction SilentlyContinue
        $uri = "https://www.python.org/ftp/python/$Version/$archiveName"
        Write-Output "Downloading official CPython embedded runtime: $uri"
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $downloadPath
        Move-Item -LiteralPath $downloadPath -Destination $archivePath -Force
    }

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $EmbeddedPythonSha256) {
        throw "Embedded Python archive checksum mismatch: $archivePath"
    }

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeRoot -Force
    $python = Join-Path $runtimeRoot "python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Embedded Python runtime is incomplete: $runtimeRoot"
    }
    & $python -I -c "import argparse, concurrent.futures, ctypes, json, pathlib, subprocess"
    if ($LASTEXITCODE -ne 0) {
        throw "Embedded Python runtime self-check failed"
    }
    return $runtimeRoot
}

$config = Get-Content -Raw -Encoding UTF8 (Join-Path $ProjectRoot "config.json") | ConvertFrom-Json
$AppVersion = [string]$config.app.version
$env:ACCOUNTINGDEMO_PROJECT_ROOT = $ProjectRoot
$env:APP_VERSION = $AppVersion

if (-not $SkipPyInstaller) {
    Remove-ProjectDirectory $BuildDir
    Remove-ProjectDirectory $DistDir
    $Python312Root = Prepare-EmbeddedPython $EmbeddedPythonVersion
    $env:ACCOUNTINGDEMO_PYTHON312_ROOT = $Python312Root
}
if (-not (Test-Path -LiteralPath $ReleaseDir)) {
    New-Item -ItemType Directory -Path $ReleaseDir | Out-Null
}

Push-Location $ProjectRoot
try {
    if (-not $SkipPyInstaller) {
        python -m PyInstaller --noconfirm --clean small_enterprise.spec
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller failed with exit code $LASTEXITCODE"
        }
    }

    $AppDir = Join-Path $DistDir "SmallEnterpriseAccounting"
    $AppExecutables = @(Get-ChildItem -LiteralPath $AppDir -Filter "*.exe" -File)
    if ($AppExecutables.Count -ne 1) {
        throw "Expected exactly one packaged application executable in $AppDir"
    }
    $AppExe = $AppExecutables[0].FullName

    $SmokeRoot = Join-Path $BuildDir "packaged-smoke-data"
    $SmokeOutput = Join-Path $BuildDir "packaged-smoke.json"
    New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
    $env:ACCOUNTINGDEMO_DATA_ROOT = $SmokeRoot
    $env:ACCOUNTINGDEMO_SMOKE_OUTPUT = $SmokeOutput
    $process = Start-Process -FilePath $AppExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $SmokeOutput)) {
        throw "Packaged application smoke test failed with exit code $($process.ExitCode)"
    }
    $smoke = Get-Content -Raw -Encoding UTF8 $SmokeOutput | ConvertFrom-Json
    if (-not $smoke.ok -or $smoke.journal_mode -ne "wal") {
        throw "Packaged application resource or SQLite verification failed"
    }

    $FullCycleRoot = Join-Path $BuildDir "packaged-full-cycle-data"
    $FullCycleOutput = Join-Path $BuildDir "packaged-full-cycle.json"
    New-Item -ItemType Directory -Path $FullCycleRoot -Force | Out-Null
    $env:ACCOUNTINGDEMO_FULL_CYCLE_ROOT = $FullCycleRoot
    $env:ACCOUNTINGDEMO_FULL_CYCLE_OUTPUT = $FullCycleOutput
    $fullCycleProcess = Start-Process -FilePath $AppExe -ArgumentList "--full-cycle-test" -Wait -PassThru -WindowStyle Hidden
    if ($fullCycleProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $FullCycleOutput)) {
        throw "Packaged full-cycle acceptance failed with exit code $($fullCycleProcess.ExitCode)"
    }
    $fullCycle = Get-Content -Raw -Encoding UTF8 $FullCycleOutput | ConvertFrom-Json
    if (-not $fullCycle.ok -or $fullCycle.covered_account_count -ne 66 -or $fullCycle.months_processed -ne 12) {
        throw "Packaged full-cycle acceptance did not cover 12 months and all 66 accounts"
    }

    if (-not $SkipInstaller) {
        $isccCandidates = @(
            (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno\ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        $iscc = $isccCandidates | Select-Object -First 1
        if (-not $iscc) {
            throw "Inno Setup 6 was not found. Install JRSoftware.InnoSetup or rerun with -SkipInstaller."
        }
        $env:ACCOUNTINGDEMO_DIST_DIR = $AppDir

        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $InstallerStageRoot = Join-Path $tempRoot (
            "SmallEnterpriseAccounting-Inno-{0}-{1}" -f $PID, [guid]::NewGuid().ToString("N")
        )
        $resolvedStage = [IO.Path]::GetFullPath($InstallerStageRoot)
        $tempPrefix = $tempRoot.TrimEnd('\') + '\'
        if (-not $resolvedStage.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Installer staging path escaped the system temporary directory: $resolvedStage"
        }
        New-Item -ItemType Directory -Path $InstallerStageRoot -Force | Out-Null

        $builtInstaller = $null
        $installerExitCode = -1
        for ($attempt = 1; $attempt -le $InstallerRetries; $attempt++) {
            $attemptDir = Join-Path $InstallerStageRoot "attempt-$attempt"
            New-Item -ItemType Directory -Path $attemptDir -Force | Out-Null
            $env:ACCOUNTINGDEMO_RELEASE_DIR = $attemptDir
            Write-Output "Building installer (attempt $attempt/$InstallerRetries) in $attemptDir"
            & $iscc (Join-Path $ProjectRoot "installer\small_enterprise.iss")
            $installerExitCode = $LASTEXITCODE
            if ($installerExitCode -eq 0) {
                $builtInstaller = Get-ChildItem -LiteralPath $attemptDir -Filter "*Setup-$AppVersion.exe" -File |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($builtInstaller) {
                    break
                }
                $installerExitCode = -2
            }

            if ($attempt -lt $InstallerRetries) {
                Write-Warning "Inno Setup attempt $attempt failed with exit code $installerExitCode; retrying with a fresh output path."
                Start-Sleep -Seconds (2 * $attempt)
            }
        }
        if (-not $builtInstaller) {
            throw "Inno Setup failed after $InstallerRetries attempts; last exit code $installerExitCode"
        }

        $targetInstaller = Join-Path $ReleaseDir $builtInstaller.Name
        $partialInstaller = "$targetInstaller.partial"
        Remove-Item -LiteralPath $partialInstaller -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $builtInstaller.FullName -Destination $partialInstaller -Force
        $stream = [IO.File]::OpenRead($partialInstaller)
        try {
            if ($stream.ReadByte() -ne 0x4D -or $stream.ReadByte() -ne 0x5A) {
                throw "Generated installer does not have a valid Windows PE header."
            }
        }
        finally {
            $stream.Dispose()
        }
        Move-Item -LiteralPath $partialInstaller -Destination $targetInstaller -Force
        $Installer = Get-Item -LiteralPath $targetInstaller
    }

    Write-Output "APP_DIR=$AppDir"
    Write-Output "SMOKE_OUTPUT=$SmokeOutput"
    Write-Output "FULL_CYCLE_OUTPUT=$FullCycleOutput"
    if (-not $SkipInstaller) {
        Write-Output "INSTALLER=$($Installer.FullName)"
    }
}
finally {
    Pop-Location
    if ($InstallerStageRoot -and (Test-Path -LiteralPath $InstallerStageRoot)) {
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $resolvedStage = [IO.Path]::GetFullPath($InstallerStageRoot)
        $tempPrefix = $tempRoot.TrimEnd('\') + '\'
        if ($resolvedStage.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item Env:ACCOUNTINGDEMO_DATA_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_SMOKE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_FULL_CYCLE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_FULL_CYCLE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_DIST_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_RELEASE_DIR -ErrorAction SilentlyContinue
}
