param(
    [string]$FromVersion = "1.7.4"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$AppDir = Join-Path $ProjectRoot "dist\SmallEnterpriseAccounting"
$ReleaseDir = Join-Path $ProjectRoot "release\patches"
$InstallerStageRoot = $null

$config = Get-Content -Raw -Encoding UTF8 (Join-Path $ProjectRoot "config.json") | ConvertFrom-Json
$ToVersion = [string]$config.app.version
if ($FromVersion -eq $ToVersion) {
    throw "Patch source and target versions must be different"
}

$AppExecutables = @(Get-ChildItem -LiteralPath $AppDir -Filter "*.exe" -File)
if ($AppExecutables.Count -ne 1) {
    throw "Expected exactly one packaged application executable in $AppDir"
}
$AppExe = $AppExecutables[0].FullName
$PackagedConfig = Join-Path $AppDir "_internal\config.json"
if (-not (Test-Path -LiteralPath $AppExe) -or -not (Test-Path -LiteralPath $PackagedConfig)) {
    throw "Packaged application is missing. Build the full Windows release first."
}
$PackagedVersion = [string]((Get-Content -Raw -Encoding UTF8 $PackagedConfig | ConvertFrom-Json).app.version)
if ($PackagedVersion -ne $ToVersion) {
    throw "Packaged application version $PackagedVersion does not match project version $ToVersion"
}

$smokePath = Join-Path $ProjectRoot "build\packaged-smoke.json"
$cyclePath = Join-Path $ProjectRoot "build\packaged-full-cycle.json"
if (-not (Test-Path -LiteralPath $smokePath) -or -not (Test-Path -LiteralPath $cyclePath)) {
    throw "Packaged acceptance outputs are missing"
}
$smoke = Get-Content -Raw -Encoding UTF8 $smokePath | ConvertFrom-Json
$cycle = Get-Content -Raw -Encoding UTF8 $cyclePath | ConvertFrom-Json
if (-not $smoke.ok -or $smoke.version -ne $ToVersion -or $smoke.journal_mode -ne "wal") {
    throw "Packaged smoke acceptance is not valid for $ToVersion"
}
if (-not $cycle.ok -or $cycle.version -ne $ToVersion -or $cycle.covered_account_count -ne 66 -or $cycle.months_processed -ne 12) {
    throw "Packaged full-cycle acceptance is not valid for $ToVersion"
}

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

New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$InstallerStageRoot = Join-Path $tempRoot (
    "SmallEnterpriseAccounting-Update-{0}-{1}" -f $PID, [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $InstallerStageRoot -Force | Out-Null

try {
    $env:ACCOUNTINGDEMO_PATCH_FROM_VERSION = $FromVersion
    $env:ACCOUNTINGDEMO_PATCH_TO_VERSION = $ToVersion
    $env:ACCOUNTINGDEMO_PROJECT_ROOT = $ProjectRoot
    $env:ACCOUNTINGDEMO_PATCH_APP_DIR = $AppDir
    $env:ACCOUNTINGDEMO_PATCH_RELEASE_DIR = $InstallerStageRoot
    & $iscc (Join-Path $ProjectRoot "installer\feature_update.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup update patch build failed with exit code $LASTEXITCODE"
    }

    $builtInstaller = Get-ChildItem -LiteralPath $InstallerStageRoot -Filter "*.exe" -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $builtInstaller) {
        throw "Inno Setup did not produce the update patch"
    }

    $targetInstaller = Join-Path $ReleaseDir $builtInstaller.Name
    Copy-Item -LiteralPath $builtInstaller.FullName -Destination $targetInstaller -Force
    $hash = (Get-FileHash -LiteralPath $targetInstaller -Algorithm SHA256).Hash
    $hashFile = "$targetInstaller.sha256.txt"
    [IO.File]::WriteAllText(
        $hashFile,
        "$hash  $($builtInstaller.Name)$([Environment]::NewLine)",
        [Text.UTF8Encoding]::new($false)
    )

    $readmePath = Join-Path $ReleaseDir "README-$FromVersion-to-$ToVersion.txt"
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "installer\FEATURE_UPDATE_README.txt") -Destination $readmePath -Force
    $zipPath = Join-Path $ReleaseDir (([IO.Path]::GetFileNameWithoutExtension($targetInstaller)) + ".zip")
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Compress-Archive -LiteralPath $targetInstaller, $hashFile, $readmePath -DestinationPath $zipPath -CompressionLevel Optimal

    Write-Output "PATCH_INSTALLER=$targetInstaller"
    Write-Output "PATCH_SHA256=$hash"
    Write-Output "PATCH_ZIP=$zipPath"
}
finally {
    if ($InstallerStageRoot -and (Test-Path -LiteralPath $InstallerStageRoot)) {
        $resolvedStage = [IO.Path]::GetFullPath($InstallerStageRoot)
        $tempPrefix = $tempRoot.TrimEnd('\') + '\'
        if ($resolvedStage.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item Env:ACCOUNTINGDEMO_PATCH_FROM_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_PATCH_TO_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_PROJECT_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_PATCH_APP_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:ACCOUNTINGDEMO_PATCH_RELEASE_DIR -ErrorAction SilentlyContinue
}
