# Build a provenance-bound RetirementPet onedir release from git archive HEAD.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build.ps1

param(
    [ValidateSet("none", "before-prior-move", "after-prior-move",
                 "after-candidate-publish")]
    [string]$FaultInjectionStage = "none"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$ReleaseRoot = Join-Path $Root ".release"
$ToolchainAttestation = Join-Path $ReleaseRoot "toolchain-attestation.json"

function Assert-PlainDirectory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label directory is missing"
    }
    $Item = Get-Item -Force -LiteralPath $Path
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "$Label directory must not be a reparse point"
    }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Get-Sha256Hex([string]$Path) {
    $Stream = [System.IO.File]::OpenRead($Path)
    try {
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString(
                $Hasher.ComputeHash($Stream))).Replace("-", "").ToLowerInvariant()
        } finally {
            $Hasher.Dispose()
        }
    } finally {
        $Stream.Dispose()
    }
}

Assert-PlainDirectory $Root "repository root"
if (-not (Test-Path -LiteralPath $ReleaseRoot)) {
    New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
}
Assert-PlainDirectory $ReleaseRoot "release root"

if (-not (Test-Path -LiteralPath $ToolchainAttestation -PathType Leaf)) {
    Write-Error (
        "private toolchain attestation is missing; run " +
        ".venv\Scripts\python.exe -I -B scripts\release_identity.py " +
        "attest-toolchain")
}
$AttestationItem = Get-Item -Force -LiteralPath $ToolchainAttestation
if ($AttestationItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Write-Error "private toolchain attestation must not be a reparse point"
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Error "release venv is missing; create .venv and install requirements-dev.txt"
}

Set-Location -LiteralPath $Root

# Ignore ambient Python/Qt/PyInstaller injection.  -I is also used for each
# Python invocation; exact runtime bytes are checked against the private
# .release attestation, which is itself bound to the public build lock.
foreach ($Name in @(
        "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE",
        "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE",
        "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "PYSIDE_DESIGNER_PLUGINS",
        "PYSIDE_DISABLE_INTERNAL_QT_CONF", "RETIREMENT_PET_BUILD_INFO",
        "RETIREMENT_PET_BUILD_MODULE_DIR")) {
    Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
}

# Point byte-code lookup at a new empty directory before the first Python
# process.  ``-B`` prevents writes; the trusted environment equivalents cover
# test-spawned Python descendants that do not inherit our command-line flags.
# The unique prefix also prevents stale ignored ``__pycache__`` reads.
$PycacheRoot = Join-Path ([IO.Path]::GetTempPath()) `
    ("retirement-pet-build-pycache-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $PycacheRoot | Out-Null
$env:PYTHONPYCACHEPREFIX = $PycacheRoot
$env:PYTHONDONTWRITEBYTECODE = "1"
try {
$PythonIsolationArgs = @(
    "-X", "pycache_prefix=$PycacheRoot", "-I", "-B"
)
$ObservedPycacheRoot = (& $VenvPython @PythonIsolationArgs -c `
    "import sys; print(sys.pycache_prefix or '')").Trim()
if ($LASTEXITCODE -ne 0 -or $ObservedPycacheRoot -ne $PycacheRoot) {
    Write-Error "isolated byte-code lookup was not activated"
}

Write-Host "==> Freezing clean source identity"
& $VenvPython @PythonIsolationArgs scripts\release_identity.py assert-clean
if ($LASTEXITCODE -ne 0) { Write-Error "source identity is not clean" }
& $VenvPython @PythonIsolationArgs -m pip check
if ($LASTEXITCODE -ne 0) { Write-Error "installed package dependencies are broken" }
& $VenvPython @PythonIsolationArgs scripts\release_identity.py `
    verify-toolchain --attestation $ToolchainAttestation
if ($LASTEXITCODE -ne 0) {
    Write-Error "exact release toolchain does not match its private attestation"
}

$Commit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $Commit -notmatch "^[0-9a-f]{40}$") {
    Write-Error "unable to resolve source commit"
}
$RunId = $Commit.Substring(0, 12) + "-" + (Get-Date -Format "yyyyMMdd-HHmmss") + `
    "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8)
$RunRoot = Join-Path $ReleaseRoot $RunId
$StageRoot = Join-Path $RunRoot "source"
$Archive = Join-Path $RunRoot "source.tar"
$ArtifactRoot = Join-Path $RunRoot "artifact"
$CandidateDist = Join-Path $ArtifactRoot "RetirementPet"
$BuildInfo = Join-Path $RunRoot "build-info.json"
$BuildModule = Join-Path $RunRoot "retirement_pet_build_info.py"
$EvidenceRoot = Join-Path $RunRoot "evidence"
$RunToolchainAttestation = Join-Path $EvidenceRoot "toolchain-attestation.json"
$WorkRoot = Join-Path $RunRoot "pyinstaller-work"

function Invoke-PublishFault([string]$Stage) {
    if ($FaultInjectionStage -ne $Stage) {
        return
    }
    $FaultReport = [ordered]@{
        schema = 1
        intentional = $true
        stage = $Stage
        commit = $Commit
        generated_at = (Get-Date).ToString("o")
    } | ConvertTo-Json
    Write-Utf8NoBom `
        (Join-Path $RunRoot "publish-fault-injection.json") $FaultReport
    throw "intentional publish fault injected at $Stage"
}

New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null
Copy-Item -LiteralPath $ToolchainAttestation `
    -Destination $RunToolchainAttestation
if ((Get-Sha256Hex $ToolchainAttestation) -ne
        (Get-Sha256Hex $RunToolchainAttestation)) {
    Write-Error "run-local toolchain attestation copy changed"
}
& git -c core.autocrlf=false archive --format=tar "--output=$Archive" $Commit
if ($LASTEXITCODE -ne 0) { Write-Error "git archive failed" }
# GNU tar (Git for Windows /usr/bin/tar) misreads "E:\..." as a remote
# host spec; prefer the Windows system tar when present.
$TarExe = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path -LiteralPath $TarExe -PathType Leaf)) {
    $TarExe = "tar"
}
& $TarExe -xf $Archive -C $StageRoot
if ($LASTEXITCODE -ne 0) { Write-Error "git archive extraction failed" }

$StageReparse = @(Get-ChildItem -LiteralPath $StageRoot -Recurse -Force |
    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
if ($StageReparse.Count -ne 0) {
    Write-Error "git archive staging contains a reparse point"
}

# Bootstrap trust from the already-clean working-tree verifier.  No program
# extracted by tar is executed until every relevant staged byte is proven to
# equal its committed Git blob.
& $VenvPython @PythonIsolationArgs scripts\release_identity.py `
    verify-snapshot --source-root $StageRoot --git-root $Root --commit $Commit
if ($LASTEXITCODE -ne 0) { Write-Error "git archive differs from committed source" }

Write-Host "==> Verifying exact toolchain and generating compiled identity"
& $VenvPython @PythonIsolationArgs scripts\release_identity.py generate `
    --source-root $StageRoot --git-root $Root --output $BuildInfo `
    --module-output $BuildModule --attestation $RunToolchainAttestation
if ($LASTEXITCODE -ne 0) { Write-Error "build identity generation failed" }
$Expected = Get-Content -Raw -Encoding UTF8 -LiteralPath $BuildInfo | ConvertFrom-Json
if ($Expected.commit -ne $Commit) { Write-Error "build identity commit drift" }

Write-Host "==> Running regular test tier against archived source"
$PreviousQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = "offscreen"
$TestTemp = Join-Path $env:TEMP ("retirement-pet-build-tests-" + [Guid]::NewGuid().ToString("N"))
try {
    Push-Location -LiteralPath $StageRoot
    try {
        & $VenvPython @PythonIsolationArgs -m pytest `
            -p no:cacheprovider tests --basetemp $TestTemp
        if ($LASTEXITCODE -ne 0) { Write-Error "tests failed; aborting build" }
    } finally {
        Pop-Location
    }
} finally {
    if ($null -eq $PreviousQtPlatform) {
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $PreviousQtPlatform
    }
    if (Test-Path -LiteralPath $TestTemp) {
        $ResolvedTestTemp = (Resolve-Path -LiteralPath $TestTemp).Path
        $ResolvedSystemTemp = [IO.Path]::GetFullPath(
            [IO.Path]::GetTempPath()).TrimEnd(
                [IO.Path]::DirectorySeparatorChar,
                [IO.Path]::AltDirectorySeparatorChar)
        if (-not $ResolvedTestTemp.StartsWith(
                $ResolvedSystemTemp + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase) -or
                -not (Split-Path -Leaf $ResolvedTestTemp).StartsWith(
                    "retirement-pet-build-tests-",
                    [StringComparison]::Ordinal)) {
            Write-Error "refusing to clean an unexpected test directory"
        }
        Remove-Item -LiteralPath $ResolvedTestTemp -Recurse -Force
    }
}

& $VenvPython @PythonIsolationArgs scripts\release_identity.py assert-clean
if ($LASTEXITCODE -ne 0) { Write-Error "source changed while tests ran" }
& $VenvPython @PythonIsolationArgs scripts\release_identity.py `
    verify-snapshot --source-root $StageRoot --git-root $Root --commit $Commit
if ($LASTEXITCODE -ne 0) { Write-Error "archived source changed while tests ran" }
& $VenvPython @PythonIsolationArgs scripts\release_identity.py `
    verify-toolchain --attestation $RunToolchainAttestation `
    --expected-attestation-sha256 $Expected.toolchain.attestation_sha256
if ($LASTEXITCODE -ne 0) { Write-Error "toolchain changed while tests ran" }

Write-Host "==> Building isolated onedir package (UPX disabled)"
$env:RETIREMENT_PET_BUILD_INFO = $BuildInfo
$env:RETIREMENT_PET_BUILD_MODULE_DIR = $RunRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $RunRoot "pyinstaller-config"
& $VenvPython @PythonIsolationArgs -m PyInstaller --noconfirm --clean `
    --distpath $ArtifactRoot --workpath $WorkRoot `
    (Join-Path $StageRoot "RetirementPet.spec")
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller build failed" }

$Exe = Join-Path $CandidateDist "RetirementPet.exe"
if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
    Write-Error "expected executable is missing"
}
$ArtifactReparse = @(Get-ChildItem -LiteralPath $CandidateDist -Recurse -Force |
    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
if ($ArtifactReparse.Count -ne 0) {
    Write-Error "built artifact contains a reparse point"
}

# Prune Qt DLLs for excluded bindings inside the unique candidate only.
$ResolvedCandidate = (Resolve-Path -LiteralPath $CandidateDist).Path
$PySide6Dir = Join-Path $ResolvedCandidate "_internal\PySide6"
$Prune = @(
    "Qt6Qml.dll", "Qt6QmlMeta.dll", "Qt6QmlModels.dll",
    "Qt6QmlWorkerScript.dll", "Qt6Quick*.dll", "Qt6Pdf*.dll",
    "Qt6VirtualKeyboard.dll", "Qt6Charts*.dll", "Qt6DataVisualization*.dll",
    "Qt6WebEngine*.dll", "Qt6WebChannel.dll", "Qt6WebSockets.dll",
    "Qt6Bluetooth.dll", "Qt6Nfc.dll", "Qt6Positioning.dll",
    "Qt6Location.dll", "Qt6Sensors.dll", "Qt6SerialPort.dll",
    "Qt6TextToSpeech.dll", "Qt6PrintSupport.dll", "Qt6Sql.dll",
    "Qt6Test.dll", "Qt6Xml.dll", "Qt6Designer.dll", "Qt6Help.dll",
    "Qt6UiTools.dll", "Qt6MultimediaWidgets.dll", "Qt6StateMachine.dll"
)
foreach ($Pattern in $Prune) {
    Remove-Item -Path (Join-Path $PySide6Dir $Pattern) -Force -ErrorAction SilentlyContinue
}
$QmlDir = Join-Path $PySide6Dir "qml"
if (Test-Path -LiteralPath $QmlDir) {
    $ResolvedQml = (Resolve-Path -LiteralPath $QmlDir).Path
    if (-not $ResolvedQml.StartsWith(
            $ResolvedCandidate + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "refusing to prune outside the candidate artifact"
    }
    Remove-Item -LiteralPath $ResolvedQml -Recurse -Force
}

$EmbeddedBuildInfo = Join-Path $ResolvedCandidate "_internal\build-info.json"
if (-not (Test-Path -LiteralPath $EmbeddedBuildInfo -PathType Leaf) -or
        (Get-Sha256Hex $BuildInfo) -ne
        (Get-Sha256Hex $EmbeddedBuildInfo)) {
    Write-Error "external embedded build identity is missing or changed"
}

Write-Host "==> Validating runtime identity, inventory, and hard budgets"
& $VenvPython @PythonIsolationArgs scripts\dist_manifest.py `
    --dist $ResolvedCandidate --expected-commit $Expected.commit `
    --expected-build-id $Expected.build_id --evidence-root $EvidenceRoot `
    --git-root $Root
if ($LASTEXITCODE -ne 0) { Write-Error "distribution validation failed" }

$CandidateReceiptPaths = @(Get-ChildItem -LiteralPath $EvidenceRoot `
    -Recurse -File -Filter "release-receipt.json")
if ($CandidateReceiptPaths.Count -ne 1) {
    Write-Error "candidate validation did not produce exactly one receipt"
}
$CandidateReceipt = Get-Content -Raw -Encoding UTF8 `
    -LiteralPath $CandidateReceiptPaths[0].FullName | ConvertFrom-Json
if ($CandidateReceipt.result -ne "PASS") {
    Write-Error "candidate release receipt is not PASS"
}

Write-Host "==> Running candidate first-launch smoke before publication"
$PowerShellExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellExe -PathType Leaf)) {
    Write-Error "PowerShell executable is unavailable for frozen smoke"
}
& $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $Root "scripts\smoke_test.ps1") `
    -ArtifactDir $ResolvedCandidate `
    -ExpectedBuildId $Expected.build_id `
    -ExpectedExeSha256 $CandidateReceipt.exe_sha256 `
    -EvidenceRoot (Join-Path $EvidenceRoot "candidate-smoke")
if ($LASTEXITCODE -ne 0) { Write-Error "candidate first-launch smoke failed" }

Write-Host "==> Verifying candidate native window/startup contract"
& $VenvPython @PythonIsolationArgs scripts\verify_windows.py `
    --target exe --artifact-dir $ResolvedCandidate `
    --expected-build-id $Expected.build_id `
    --expected-exe-sha256 $CandidateReceipt.exe_sha256 `
    --evidence-root (Join-Path $EvidenceRoot "candidate-window")
if ($LASTEXITCODE -ne 0) { Write-Error "candidate native window gate failed" }

# No mutation is allowed between validation and publication.  These are the
# final pre-publish gates; post-publish validation below binds public bytes to
# the candidate receipt and rolls back on any mismatch.
& $VenvPython @PythonIsolationArgs scripts\release_identity.py assert-clean
if ($LASTEXITCODE -ne 0) { Write-Error "source changed before publication" }
& $VenvPython @PythonIsolationArgs scripts\release_identity.py `
    verify-snapshot --source-root $StageRoot --git-root $Root --commit $Commit
if ($LASTEXITCODE -ne 0) { Write-Error "archived source changed before publication" }
& $VenvPython @PythonIsolationArgs scripts\release_identity.py `
    verify-toolchain --attestation $RunToolchainAttestation `
    --expected-attestation-sha256 $Expected.toolchain.attestation_sha256
if ($LASTEXITCODE -ne 0) { Write-Error "toolchain changed during packaging" }

# Publish only after the unique candidate passes.  Preserve the prior dist as
# a recoverable run-local backup instead of deleting it in place.
$DistParent = Join-Path $Root "dist"
$Published = Join-Path $DistParent "RetirementPet"
New-Item -ItemType Directory -Force -Path $DistParent | Out-Null
Assert-PlainDirectory $DistParent "dist root"
$ResolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$ResolvedDistParent = (Resolve-Path -LiteralPath $DistParent).Path
if (-not $ResolvedDistParent.StartsWith(
        $ResolvedRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    Write-Error "refusing to publish outside the repository root"
}
$Backup = Join-Path $RunRoot "previous-RetirementPet"
$FailedPublish = Join-Path $RunRoot "failed-published-RetirementPet"
$HadPriorArtifact = Test-Path -LiteralPath $Published
$PriorMoved = $false
$CandidateMoveStarted = $false
$CandidatePublished = $false
try {
    Invoke-PublishFault "before-prior-move"
    if ($HadPriorArtifact) {
        $PublishedItem = Get-Item -Force -LiteralPath $Published
        if ($PublishedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Error "refusing to replace a reparse-point artifact"
        }
        $ResolvedPublished = (Resolve-Path -LiteralPath $Published).Path
        if (-not $ResolvedPublished.StartsWith(
                $ResolvedDistParent + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) {
            Write-Error "refusing to replace an artifact outside dist"
        }
        Move-Item -LiteralPath $ResolvedPublished -Destination $Backup
        $PriorMoved = $true
    }
    Invoke-PublishFault "after-prior-move"
    $CandidateMoveStarted = $true
    Move-Item -LiteralPath $ResolvedCandidate -Destination $Published
    $CandidatePublished = $true
    Invoke-PublishFault "after-candidate-publish"

    $PostPublishEvidence = Join-Path $EvidenceRoot "post-publish"
    & $VenvPython @PythonIsolationArgs scripts\dist_manifest.py `
        --dist $Published --expected-commit $Expected.commit `
        --expected-build-id $Expected.build_id `
        --expected-artifact-id $CandidateReceipt.artifact_id `
        --expected-exe-sha256 $CandidateReceipt.exe_sha256 `
        --expected-file-count $CandidateReceipt.file_count `
        --expected-total-bytes $CandidateReceipt.total_bytes `
        --evidence-root $PostPublishEvidence --git-root $Root
    if ($LASTEXITCODE -ne 0) {
        throw "published artifact validation failed"
    }
    $PublishedReceiptPaths = @(Get-ChildItem `
        -LiteralPath $PostPublishEvidence -Recurse -File `
        -Filter "release-receipt.json")
    if ($PublishedReceiptPaths.Count -ne 1) {
        throw "published validation did not produce exactly one receipt"
    }
    $PublishedReceipt = Get-Content -Raw -Encoding UTF8 `
        -LiteralPath $PublishedReceiptPaths[0].FullName | ConvertFrom-Json
    foreach ($Field in @(
            "schema", "result", "recipe_id", "artifact_id", "exe_sha256",
            "commit", "git_tree", "file_count", "total_bytes",
            "attestation_protocol")) {
        if ($CandidateReceipt.$Field -ne $PublishedReceipt.$Field) {
            throw "published receipt differs at $Field"
        }
    }
    & $VenvPython @PythonIsolationArgs scripts\release_identity.py assert-clean
    if ($LASTEXITCODE -ne 0) {
        throw "source changed during publication"
    }
} catch {
    if (($CandidatePublished -or $CandidateMoveStarted) -and
            (Test-Path -LiteralPath $Published)) {
        $FailedItem = Get-Item -Force -LiteralPath $Published
        if ($FailedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "publication failed and the public artifact is a reparse point"
        }
        $ResolvedFailedArtifact = (Resolve-Path -LiteralPath $Published).Path
        if (-not $ResolvedFailedArtifact.StartsWith(
                $ResolvedDistParent + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) {
            throw "publication failed and the public artifact path is unsafe"
        }
        Move-Item -LiteralPath $ResolvedFailedArtifact `
            -Destination $FailedPublish
    }
    if ($PriorMoved -and (Test-Path -LiteralPath $Backup)) {
        Move-Item -LiteralPath $Backup -Destination $Published
    }
    throw
}

Write-Host "==> Build OK"
Write-Host "    artifact: $Published"
Write-Host "    run:      $RunRoot"
Write-Host "    receipt:  $($PublishedReceiptPaths[0].FullName)"
Write-Host "    build_id: $($Expected.build_id)"
Write-Host "    commit:   $($Expected.commit)"
} finally {
    Remove-Item Env:PYTHONPYCACHEPREFIX -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
    $TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $ResolvedPycache = [IO.Path]::GetFullPath($PycacheRoot)
    $ExpectedPrefix = $TempRoot.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar
    $SafeLeaf = (Split-Path -Leaf $ResolvedPycache) -like `
        "retirement-pet-build-pycache-*"
    if ($SafeLeaf -and $ResolvedPycache.StartsWith(
            $ExpectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $ResolvedPycache) {
            try {
                Remove-Item -LiteralPath $ResolvedPycache -Recurse -Force
            } catch {
                Write-Warning "could not clean the isolated byte-code path"
            }
        }
    } else {
        Write-Warning "refusing to clean an unexpected byte-code path"
    }
}
