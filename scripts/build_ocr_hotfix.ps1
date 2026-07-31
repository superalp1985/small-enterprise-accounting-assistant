param(
    [string]$HotfixTag = "HF1",
    [switch]$SkipPyInstaller,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildRoot = Join-Path $ProjectRoot "build\ocr-hotfix"
$WorkDir = Join-Path $BuildRoot "work"
$DistRoot = Join-Path $BuildRoot "dist"
$HotfixAppDir = Join-Path $DistRoot "SmallEnterpriseAccountingHotfix"
$ReleaseDir = Join-Path $ProjectRoot "release\patches"
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

function Get-BaseInstallDir {
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{5D76D84B-EACF-4A77-A9EB-60B72CF9FC47}_is1"
    if (Test-Path -LiteralPath $key) {
        $location = [string](Get-ItemPropertyValue -LiteralPath $key -Name InstallLocation)
        if ($location) {
            return $location.TrimEnd('\')
        }
    }
    return (Join-Path $env:LOCALAPPDATA "Programs\SmallEnterpriseAccounting")
}

$config = Get-Content -Raw -Encoding UTF8 (Join-Path $ProjectRoot "config.json") | ConvertFrom-Json
$BaseVersion = [string]$config.app.version
if ($BaseVersion -ne "1.7.1") {
    throw "This OCR hotfix is pinned to base version 1.7.1; project config reports $BaseVersion"
}
if ($HotfixTag -notmatch '^HF[1-9][0-9]*$') {
    throw "HotfixTag must use the form HF1, HF2, and so on."
}

if (-not $SkipPyInstaller) {
    Remove-ProjectDirectory $BuildRoot
}
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

Push-Location $ProjectRoot
try {
    if (-not $SkipPyInstaller) {
        python -m PyInstaller --noconfirm --clean `
            --workpath $WorkDir `
            --distpath $DistRoot `
            small_enterprise_hotfix.spec
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller hotfix build failed with exit code $LASTEXITCODE"
        }
    }

    $HotfixExecutables = @(Get-ChildItem -LiteralPath $HotfixAppDir -Filter "*.exe" -File)
    if ($HotfixExecutables.Count -ne 1) {
        throw "Expected exactly one hotfix executable in $HotfixAppDir"
    }
    $HotfixExe = $HotfixExecutables[0].FullName

    # Run the rebuilt launcher beside the installed 1.7.1 runtime without
    # replacing the installed application during build verification.
    $BaseInstallDir = Get-BaseInstallDir
    $BaseConfig = Join-Path $BaseInstallDir "_internal\config.json"
    if (-not (Test-Path -LiteralPath $BaseConfig)) {
        throw "Installed 1.7.1 runtime was not found: $BaseConfig"
    }
    $InstalledVersion = [string]((Get-Content -Raw -Encoding UTF8 $BaseConfig | ConvertFrom-Json).app.version)
    if ($InstalledVersion -ne $BaseVersion) {
        throw "Installed runtime version $InstalledVersion does not match hotfix base $BaseVersion"
    }

    $TestExe = Join-Path $BaseInstallDir (
        "SmallEnterpriseAccounting-{0}-packaged-test.exe" -f $HotfixTag
    )
    $SmokeRoot = Join-Path $BuildRoot "packaged-smoke-data"
    $SmokeOutput = Join-Path $BuildRoot "packaged-smoke.json"
    $FullCycleRoot = Join-Path $BuildRoot "packaged-full-cycle-data"
    $FullCycleOutput = Join-Path $BuildRoot "packaged-full-cycle.json"
    New-Item -ItemType Directory -Path $SmokeRoot, $FullCycleRoot -Force | Out-Null

    Copy-Item -LiteralPath $HotfixExe -Destination $TestExe -Force
    try {
        $env:ACCOUNTINGDEMO_DATA_ROOT = $SmokeRoot
        $env:ACCOUNTINGDEMO_SMOKE_OUTPUT = $SmokeOutput
        $smokeProcess = Start-Process -FilePath $TestExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
        if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $SmokeOutput)) {
            throw "Packaged hotfix smoke test failed with exit code $($smokeProcess.ExitCode)"
        }
        $smoke = Get-Content -Raw -Encoding UTF8 $SmokeOutput | ConvertFrom-Json
        if (-not $smoke.ok -or $smoke.version -ne $BaseVersion -or $smoke.journal_mode -ne "wal") {
            throw "Packaged hotfix smoke test did not pass resource, version, and WAL checks"
        }

        $env:ACCOUNTINGDEMO_FULL_CYCLE_ROOT = $FullCycleRoot
        $env:ACCOUNTINGDEMO_FULL_CYCLE_OUTPUT = $FullCycleOutput
        $cycleProcess = Start-Process -FilePath $TestExe -ArgumentList "--full-cycle-test" -Wait -PassThru -WindowStyle Hidden
        if ($cycleProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $FullCycleOutput)) {
            throw "Packaged hotfix full-cycle test failed with exit code $($cycleProcess.ExitCode)"
        }
        $cycle = Get-Content -Raw -Encoding UTF8 $FullCycleOutput | ConvertFrom-Json
        if (-not $cycle.ok -or $cycle.covered_account_count -ne 66 -or $cycle.months_processed -ne 12) {
            throw "Packaged hotfix full-cycle test did not cover 12 months and all 66 accounts"
        }
    }
    finally {
        Remove-Item -LiteralPath $TestExe -Force -ErrorAction SilentlyContinue
        Remove-Item Env:ACCOUNTINGDEMO_DATA_ROOT -ErrorAction SilentlyContinue
        Remove-Item Env:ACCOUNTINGDEMO_SMOKE_OUTPUT -ErrorAction SilentlyContinue
        Remove-Item Env:ACCOUNTINGDEMO_FULL_CYCLE_ROOT -ErrorAction SilentlyContinue
        Remove-Item Env:ACCOUNTINGDEMO_FULL_CYCLE_OUTPUT -ErrorAction SilentlyContinue
    }

    if (-not $SkipInstaller) {
        $isccCandidates = @(
            (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
            "$env:LOCALAPPDATA\Programs\Inno\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        $iscc = $isccCandidates | Select-Object -First 1
        if (-not $iscc) {
            throw "Inno Setup 6 was not found"
        }

        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $InstallerStageRoot = Join-Path $tempRoot (
            "SmallEnterpriseAccounting-OCR-{0}-{1}" -f $PID, [guid]::NewGuid().ToString("N")
        )
        New-Item -ItemType Directory -Path $InstallerStageRoot -Force | Out-Null

        $env:ACCOUNTINGDEMO_BASE_VERSION = $BaseVersion
        $env:ACCOUNTINGDEMO_HOTFIX_TAG = $HotfixTag
        $env:ACCOUNTINGDEMO_PROJECT_ROOT = $ProjectRoot
        $env:ACCOUNTINGDEMO_HOTFIX_EXE = $HotfixExe
        $env:ACCOUNTINGDEMO_HOTFIX_RELEASE_DIR = $InstallerStageRoot
        & $iscc (Join-Path $ProjectRoot "installer\ocr_hotfix.iss")
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup hotfix build failed with exit code $LASTEXITCODE"
        }

        $builtInstaller = Get-ChildItem -LiteralPath $InstallerStageRoot -Filter "*.exe" -File |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $builtInstaller) {
            throw "Inno Setup did not produce the OCR hotfix installer"
        }
        $targetInstaller = Join-Path $ReleaseDir $builtInstaller.Name
        Copy-Item -LiteralPath $builtInstaller.FullName -Destination $targetInstaller -Force

        $hash = (Get-FileHash -LiteralPath $targetInstaller -Algorithm SHA256).Hash
        $hashFile = Join-Path $ReleaseDir ("{0}.sha256.txt" -f $builtInstaller.Name)
        $checksumLine = "$hash  $($builtInstaller.Name)" + [Environment]::NewLine
        [IO.File]::WriteAllText($hashFile, $checksumLine, [Text.UTF8Encoding]::new($false))

        $readmeName = "README-$BaseVersion-$HotfixTag.txt"
        $readmePath = Join-Path $ReleaseDir $readmeName
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "installer\OCR_HOTFIX_README.txt") `
            -Destination $readmePath -Force

        $zipName = ([IO.Path]::GetFileNameWithoutExtension($targetInstaller)) + ".zip"
        $zipPath = Join-Path $ReleaseDir $zipName
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
        Compress-Archive -LiteralPath $targetInstaller, $hashFile, $readmePath -DestinationPath $zipPath -CompressionLevel Optimal

        Write-Output "HOTFIX_INSTALLER=$targetInstaller"
        Write-Output "HOTFIX_SHA256=$hash"
        Write-Output "HOTFIX_ZIP=$zipPath"
    }

    Write-Output "HOTFIX_EXE=$HotfixExe"
    Write-Output "SMOKE_OUTPUT=$SmokeOutput"
    Write-Output "FULL_CYCLE_OUTPUT=$FullCycleOutput"
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
    Remove-Item Env:ACCOUNTINGDEMO_BASE_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_HOTFIX_TAG -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_PROJECT_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_HOTFIX_EXE -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_HOTFIX_RELEASE_DIR -ErrorAction SilentlyContinue
}
