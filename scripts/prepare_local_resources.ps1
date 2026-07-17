param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeSource,

    [Parameter(Mandatory = $true)]
    [string]$ModelSource
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeSourcePath = (Resolve-Path -LiteralPath $RuntimeSource).Path
$modelSourcePath = (Resolve-Path -LiteralPath $ModelSource).Path
$runtimeTarget = Join-Path $projectRoot "runtime"
$modelTarget = Join-Path $projectRoot "models"

New-Item -ItemType Directory -Path $runtimeTarget -Force | Out-Null
New-Item -ItemType Directory -Path $modelTarget -Force | Out-Null

Write-Host "Copying runtime resources..."
Copy-Item -Path (Join-Path $runtimeSourcePath "*") `
    -Destination $runtimeTarget -Recurse -Force

Write-Host "Copying model resources..."
Copy-Item -Path (Join-Path $modelSourcePath "*") `
    -Destination $modelTarget -Recurse -Force

Write-Host "Local resources are ready under: $projectRoot"
