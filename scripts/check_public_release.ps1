param(
    [switch]$RunTests,
    [int]$MaxFileSizeMB = 20
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$errors = [Collections.Generic.List[string]]::new()

function Add-CheckError([string]$Message) {
    $errors.Add($Message)
}

function Join-UnicodeChars([int[]]$CodePoints) {
    return -join @($CodePoints | ForEach-Object { [char]$_ })
}

Push-Location $ProjectRoot
try {
    $requiredFiles = @(
        "LICENSE",
        "LICENSE_SUMMARY_EN.md",
        "NOTICE",
        "PATENTS.md",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        ".gitignore"
    )
    foreach ($required in $requiredFiles) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            Add-CheckError "Missing required public-release file: $required"
        }
    }

    $candidateFiles = @(
        & git -c core.quotepath=false ls-files --cached --others --exclude-standard
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    if ($LASTEXITCODE -ne 0) {
        throw "git ls-files failed."
    }

    $privatePrefixes = @(
        "build/", "build-tools/", "data/", "dist/", "models/", "out/",
        "release/", "runtime/", "security/", "tmp/", "__pycache__/"
    )
    foreach ($file in $candidateFiles) {
        $normalized = $file.Replace("\", "/")
        foreach ($prefix in $privatePrefixes) {
            if ($normalized.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                Add-CheckError "Private or generated directory is publishable: $file"
                break
            }
        }
    }

    $sensitiveNamePatterns = @(
        "(^|/|\\)credentials\.json$",
        "(^|/|\\)operation_log\.json$",
        "(^|/|\\)\.env($|\.)",
        "\.(pem|pfx|p12|key)$",
        "\.(db|sqlite)(-wal|-shm)?$"
    )
    foreach ($file in $candidateFiles) {
        foreach ($pattern in $sensitiveNamePatterns) {
            if ($file -match $pattern) {
                Add-CheckError "Potential secret or runtime file: $file"
                break
            }
        }

        $normalized = $file.Replace("\", "/")
        if ($normalized.EndsWith(".pdf", [StringComparison]::OrdinalIgnoreCase) -and
            -not $normalized.StartsWith("tests/fixtures/", [StringComparison]::OrdinalIgnoreCase)) {
            Add-CheckError "Unexpected PDF outside synthetic test fixtures: $file"
        }

        $item = Get-Item -LiteralPath $file
        if ($item.Length -gt ($MaxFileSizeMB * 1MB)) {
            Add-CheckError "File is larger than ${MaxFileSizeMB}MB: $file"
        }
    }

    $textExtensions = @(
        ".py", ".json", ".md", ".txt", ".ps1", ".bat", ".spec", ".iss",
        ".yml", ".yaml", ".toml", ".ini", ".cfg"
    )
    $smallEnterpriseChinese = Join-UnicodeChars @(0x5C0F, 0x4F01, 0x4E1A, 0x4F1A, 0x8BA1)
    $localUserChinese = Join-UnicodeChars @(0x738B, 0x79C9, 0x94A6)
    $legacyBrandChinese = Join-UnicodeChars @(0x5317, 0x4EAC, 0x56FD, 0x5BB6, 0x4F1A, 0x8BA1, 0x5B66, 0x9662)
    $literalChecks = @(
        ("E:" + "\AccountingDemo-" + $smallEnterpriseChinese),
        ("E:" + "\DCA" + "-VM-Test"),
        ("C:" + "\Users\" + $localUserChinese),
        ("1861" + "3327850"),
        $legacyBrandChinese,
        ("U" + "Key")
    )

    foreach ($file in $candidateFiles) {
        $extension = [IO.Path]::GetExtension($file).ToLowerInvariant()
        $isExtensionlessText = [IO.Path]::GetFileName($file) -in @(
            "LICENSE", "NOTICE", ".gitignore", ".gitattributes"
        )
        if ($extension -notin $textExtensions -and -not $isExtensionlessText) {
            continue
        }

        $content = Get-Content -LiteralPath $file -Raw -Encoding UTF8
        foreach ($literal in $literalChecks) {
            if ($content.IndexOf($literal, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Add-CheckError "File contains a private path, legacy brand, or private identifier: $file"
                break
            }
        }
    }

    if ($errors.Count -gt 0) {
        Write-Host "Public-release check failed:" -ForegroundColor Red
        foreach ($message in $errors) {
            Write-Host " - $message" -ForegroundColor Red
        }
        exit 1
    }

    Write-Host "Public-release file check passed ($($candidateFiles.Count) candidate files)." -ForegroundColor Green

    if ($RunTests) {
        & python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Automated tests failed with exit code $LASTEXITCODE."
        }
        Write-Host "Automated tests passed." -ForegroundColor Green
    }
}
finally {
    Pop-Location
}
