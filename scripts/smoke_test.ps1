# Provenance-bound frozen EXE smoke test.

param(
    [string]$ArtifactDir = "",
    [Parameter(Mandatory = $true)][string]$ExpectedBuildId,
    [Parameter(Mandatory = $true)][string]$ExpectedExeSha256,
    [string]$EvidenceRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ArtifactDir)) {
    $ArtifactDir = Join-Path $Root "dist\RetirementPet"
}
if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $Root "evidence"
}

$failures = [System.Collections.Generic.List[string]]::new()
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$IdentityPrefix = if ($ExpectedBuildId -match "^[0-9a-fA-F]{64}$") {
    $ExpectedBuildId.Substring(0, 12).ToLowerInvariant()
} else {
    "invalid-build"
}
$RunDir = Join-Path $EvidenceRoot ($Stamp + "-" + $IdentityPrefix +
    "-exe-smoke-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
$DataDir = Join-Path $RunDir "data"
$Instance = "smoke-$Stamp-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$ReportWindow = Join-Path $RunDir "window.json"
$RuntimeIdentity = Join-Path $RunDir "runtime-attestation-v2.json"
$LocalImportReport = Join-Path $RunDir "local-import-preflight.json"
$LocalImportTimeoutReport = Join-Path $RunDir "local-import-timeout.json"
$Exe = $null
$BuildInfoPath = $null
$ObservedBuildId = $null
$ObservedExeSha256 = $null
$ObservedRuntimeBuildId = $null
$IdentityProbe = $null
$ImportCanary = $null
$ImportTimeoutCanary = $null
$ObservedImportCanary = $null
$ObservedImportTimeoutCanary = $null
$Quit = $null
$proc1 = $null
$proc2 = $null
$HarnessCompleted = $false
$harnessErrors = [System.Collections.Generic.List[string]]::new()
$cleanupActions = [System.Collections.Generic.List[string]]::new()
$Result = "INVALID"

function Assert-Check($Condition, [string]$Message) {
    if ($Condition) {
        Write-Host "  [ok] $Message"
    } else {
        Write-Host "  [FAIL] $Message"
        $script:failures.Add($Message)
    }
}

function Quote-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
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

function Start-ArtifactProcess([string[]]$Arguments = @()) {
    $Info = [System.Diagnostics.ProcessStartInfo]::new()
    $Info.FileName = $Exe
    $Info.WorkingDirectory = $DataDir
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.Arguments = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join " ")
    $Info.EnvironmentVariables["RETIREMENT_PET_DATA_DIR"] = $DataDir
    $Info.EnvironmentVariables["RETIREMENT_PET_INSTANCE_NAME"] = $Instance
    $Info.EnvironmentVariables.Remove("QT_QPA_PLATFORM")
    return [System.Diagnostics.Process]::Start($Info)
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

try {
    Write-Host "==> Artifact identity"
    if ($ExpectedBuildId -notmatch "^[0-9a-fA-F]{64}$") {
        throw "expected build id must be 64 hexadecimal characters"
    }
    if ($ExpectedExeSha256 -notmatch "^[0-9a-fA-F]{64}$") {
        throw "expected EXE hash must be 64 hexadecimal characters"
    }
    $ArtifactDir = (Resolve-Path -LiteralPath $ArtifactDir).Path
    $Exe = Join-Path $ArtifactDir "RetirementPet.exe"
    $BuildInfoPath = Join-Path $ArtifactDir "_internal\build-info.json"
    Assert-Check (Test-Path -LiteralPath $Exe -PathType Leaf) "executable exists"
    Assert-Check (Test-Path -LiteralPath $BuildInfoPath -PathType Leaf) "build-info.json exists"
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf) -or
            -not (Test-Path -LiteralPath $BuildInfoPath -PathType Leaf)) {
        throw "artifact prerequisites are missing"
    }
    $BuildInfo = Get-Content -Raw -Encoding UTF8 -LiteralPath $BuildInfoPath | ConvertFrom-Json
    $ObservedBuildId = $BuildInfo.build_id
    $ObservedExeSha256 = Get-Sha256Hex $Exe
    Assert-Check ($ObservedBuildId -eq $ExpectedBuildId) "build id matches expected receipt"
    Assert-Check ($ObservedExeSha256 -eq $ExpectedExeSha256.ToLowerInvariant()) "EXE hash matches expected receipt"

    $IdentityProbe = Start-ArtifactProcess @("--build-attestation-v2-out", $RuntimeIdentity)
    $IdentityExited = $IdentityProbe.WaitForExit(30000)
    Assert-Check $IdentityExited "runtime identity probe exited"
    if (-not $IdentityExited) { $IdentityProbe.Kill(); $IdentityProbe.WaitForExit() }
    Assert-Check ($IdentityExited -and $IdentityProbe.ExitCode -eq 0) "runtime identity probe exit code 0"
    Assert-Check (Test-Path -LiteralPath $RuntimeIdentity -PathType Leaf) "runtime identity report exists"
    if (Test-Path -LiteralPath $RuntimeIdentity -PathType Leaf) {
        $Observed = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimeIdentity | ConvertFrom-Json
        $ObservedRuntimeBuildId = $Observed.compiled_identity.build_id
        Assert-Check ($Observed.protocol -eq 2) "runtime attestation protocol is v2"
        Assert-Check ($Observed.external_match -eq $true) "compiled and external identities match"
        Assert-Check ($Observed.compiled_identity.build_id -eq $ExpectedBuildId) "compiled runtime build id matches"
    }

    Write-Host "==> Required resources"
    Assert-Check (Test-Path -LiteralPath (Join-Path $ArtifactDir "_internal\assets\manifest.json")) "asset manifest bundled"
    Assert-Check (Test-Path -LiteralPath (Join-Path $ArtifactDir "_internal\config\defaults.json")) "defaults bundled"
    Assert-Check (Test-Path -LiteralPath (Join-Path $ArtifactDir "_internal\assets\icons\retirement_pet.png")) "icon bundled"
    Assert-Check (Test-Path -LiteralPath (Join-Path $ArtifactDir "_internal\assets\petpack\retirement-cat-official.petpack")) "official PetPack bundled"
    Assert-Check (Test-Path -LiteralPath (Join-Path $ArtifactDir "_internal\assets\petpack\retirement-cat-official-1.0.1.petpack")) "official PetPack v1.0.1 bundled"
    $ExamplePack = Join-Path $ArtifactDir "_internal\assets\petpack\examples\realistic-retirement-cat-0.1.1.petpack"
    Assert-Check (Test-Path -LiteralPath $ExamplePack -PathType Leaf) "semi-realistic preview PetPack bundled"
    if (-not (Test-Path -LiteralPath $ExamplePack -PathType Leaf)) {
        throw "local-import canary PetPack is missing"
    }
    $ExpectedExampleArchiveSha256 = "ce80e7dd73726cdbb252f21d65538014fe10790dce7a44816ebb80bc26c2a964"
    $ExpectedExampleContentDigest = "6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c"
    Assert-Check ((Get-Sha256Hex $ExamplePack) -eq $ExpectedExampleArchiveSha256) "semi-realistic preview PetPack hash matches release source"

    Write-Host "==> Frozen local-import success canary"
    $ImportCanary = Start-ArtifactProcess @(
        "--local-import-preflight-out", $LocalImportReport,
        "--local-import-pack", $ExamplePack)
    $ImportExited = $ImportCanary.WaitForExit(90000)
    Assert-Check $ImportExited "local-import success canary exited"
    if (-not $ImportExited) { $ImportCanary.Kill(); $ImportCanary.WaitForExit() }
    Assert-Check ($ImportExited -and $ImportCanary.ExitCode -eq 0) "local-import success canary exit code 0"
    Assert-Check (Test-Path -LiteralPath $LocalImportReport -PathType Leaf) "local-import success report exists"
    if (Test-Path -LiteralPath $LocalImportReport -PathType Leaf) {
        $ObservedImportCanary = Get-Content -Raw -Encoding UTF8 `
            -LiteralPath $LocalImportReport | ConvertFrom-Json
        Assert-Check ($ObservedImportCanary.schema -eq 1) "local-import report schema is v1"
        Assert-Check ($ObservedImportCanary.operation -eq "local_import_preflight") "local-import operation is exact"
        Assert-Check ($ObservedImportCanary.ipc_protocol -eq 1) "local-import IPC protocol is v1"
        Assert-Check ($ObservedImportCanary.result -eq "PASS") "local-import preflight passed"
        Assert-Check ($ObservedImportCanary.archive_sha256 -eq $ExpectedExampleArchiveSha256) "local-import archive hash is bound"
        Assert-Check ($ObservedImportCanary.content_digest -eq $ExpectedExampleContentDigest) "local-import content digest is bound"
        $ObservedCharacterIds = @($ObservedImportCanary.character_ids)
        Assert-Check ($ObservedCharacterIds.Count -eq 1 -and `
            $ObservedCharacterIds[0] -eq "realistic-cat") "local-import character identity is exact"
        Assert-Check ($ObservedImportCanary.first_frames_verified -eq $true) "local-import QPixmap first frame verified"
        Assert-Check ($ObservedImportCanary.child_exit_code -eq 0) "local-import child exited normally"
        Assert-Check ($ObservedImportCanary.child_reaped -eq $true) "local-import child was reaped"
    }
    Assert-Check (-not (Test-Path -LiteralPath (Join-Path $DataDir "state.json"))) "local-import canary did not start the desktop application"
    Assert-Check (-not (Test-Path -LiteralPath (Join-Path $DataDir "library"))) "local-import canary did not mutate the character library"

    Write-Host "==> Frozen local-import timeout canary"
    $ImportTimeoutCanary = Start-ArtifactProcess @(
        "--local-import-preflight-out", $LocalImportTimeoutReport,
        "--local-import-pack", $ExamplePack,
        "--local-import-preflight-timeout-ms", "1")
    $ImportTimeoutExited = $ImportTimeoutCanary.WaitForExit(30000)
    Assert-Check $ImportTimeoutExited "local-import timeout canary exited"
    if (-not $ImportTimeoutExited) { $ImportTimeoutCanary.Kill(); $ImportTimeoutCanary.WaitForExit() }
    Assert-Check ($ImportTimeoutExited -and $ImportTimeoutCanary.ExitCode -eq 1) "local-import timeout canary used controlled exit code 1"
    Assert-Check (Test-Path -LiteralPath $LocalImportTimeoutReport -PathType Leaf) "local-import timeout report exists"
    if (Test-Path -LiteralPath $LocalImportTimeoutReport -PathType Leaf) {
        $ObservedImportTimeoutCanary = Get-Content -Raw -Encoding UTF8 `
            -LiteralPath $LocalImportTimeoutReport | ConvertFrom-Json
        Assert-Check ($ObservedImportTimeoutCanary.schema -eq 1) "local-import timeout report schema is v1"
        Assert-Check ($ObservedImportTimeoutCanary.operation -eq "local_import_preflight") "local-import timeout operation is exact"
        Assert-Check ($ObservedImportTimeoutCanary.ipc_protocol -eq 1) "local-import timeout IPC protocol is v1"
        Assert-Check ($ObservedImportTimeoutCanary.result -eq "TIMEOUT") "local-import timeout was observed"
        Assert-Check ($ObservedImportTimeoutCanary.child_reaped -eq $true) "timed-out local-import child was reaped"
        Assert-Check ($null -ne $ObservedImportTimeoutCanary.child_exit_code -and `
            $ObservedImportTimeoutCanary.child_exit_code -ne 0) "timed-out local-import child has a non-zero exit code"
    }
    $CanaryDataEntries = @(Get-ChildItem -LiteralPath $DataDir -Force)
    Assert-Check ($CanaryDataEntries.Count -eq 0) "local-import canaries left the application data directory empty"

    Write-Host "==> Primary launch from unrelated working directory"
    $proc1 = Start-ArtifactProcess @("--test-ipc-quit", "--report-window", $ReportWindow)
    $WindowDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not $proc1.HasExited -and
            -not (Test-Path -LiteralPath $ReportWindow -PathType Leaf) -and
            [DateTime]::UtcNow -lt $WindowDeadline) {
        Start-Sleep -Milliseconds 200
    }
    Assert-Check (-not $proc1.HasExited) "first instance remains alive"
    Assert-Check (Test-Path -LiteralPath $ReportWindow -PathType Leaf) "pet reported its native HWND"

    Write-Host "==> Tracked second launch"
    $proc2 = Start-ArtifactProcess
    $SecondExited = $proc2.WaitForExit(15000)
    Assert-Check $SecondExited "second launch exited after forwarding"
    if (-not $SecondExited) { $proc2.Kill(); $proc2.WaitForExit() }
    Assert-Check ($SecondExited -and $proc2.ExitCode -eq 0) "second launch exit code 0"
    Assert-Check (-not $proc1.HasExited) "primary remains alive after forwarding"

    Write-Host "==> Normal exit through IPC"
    $Quit = Start-ArtifactProcess @("--ipc-send", "quit")
    $QuitExited = $Quit.WaitForExit(30000)
    Assert-Check $QuitExited "IPC client exited"
    if (-not $QuitExited) { $Quit.Kill(); $Quit.WaitForExit() }
    Assert-Check ($QuitExited -and $Quit.ExitCode -eq 0) "IPC quit was accepted"

    $PrimaryExited = $proc1.WaitForExit(20000)
    Assert-Check $PrimaryExited "primary exited by itself within 20 seconds"
    if ($PrimaryExited) {
        Assert-Check ($proc1.ExitCode -eq 0) "primary exit code 0"
        Assert-Check (Test-Path -LiteralPath (Join-Path $DataDir "state.json")) "shutdown persisted state.json"
    }
    $ErrorLines = @()
    $LogDir = Join-Path $DataDir "logs"
    if (Test-Path -LiteralPath $LogDir -PathType Container) {
        $ErrorLines = @(Get-ChildItem -LiteralPath $LogDir -File -Filter "*.log" |
            ForEach-Object { Get-Content -LiteralPath $_.FullName } |
            Where-Object { $_ -match "ERROR|Traceback|Unhandled exception" })
    }
    Assert-Check ($ErrorLines.Count -eq 0) "run logs contain no ERROR/Traceback"
    $HarnessCompleted = $true
} catch {
    $harnessErrors.Add("smoke harness exception: $($_.Exception.Message)")
} finally {
    foreach ($Process in @(
            $Quit, $IdentityProbe, $ImportTimeoutCanary, $ImportCanary,
            $proc2, $proc1)) {
        try {
            if ($null -ne $Process -and -not $Process.HasExited) {
                $CleanupMessage = "force terminated pid $($Process.Id)"
                $cleanupActions.Add($CleanupMessage)
                if ($harnessErrors.Count -eq 0) {
                    $failures.Add("failure cleanup required $CleanupMessage")
                }
                $Process.Kill()
                $Process.WaitForExit()
            }
        } catch {
            $harnessErrors.Add(
                "smoke cleanup exception: $($_.Exception.Message)")
        }
    }
    $EvidenceComplete = $HarnessCompleted -and $harnessErrors.Count -eq 0
    $Result = if (-not $EvidenceComplete) {
        "INVALID"
    } elseif ($failures.Count -eq 0) {
        "PASS"
    } else {
        "FAIL"
    }
    $Report = [ordered]@{
        schema = 4
        result = $Result
        evidence_complete = $EvidenceComplete
        harness_completed = $HarnessCompleted
        harness_errors = @($harnessErrors)
        cleanup_actions = @($cleanupActions)
        generated_at = (Get-Date).ToString("o")
        expected_build_id = $ExpectedBuildId
        observed_build_id = $ObservedBuildId
        expected_exe_sha256 = $ExpectedExeSha256.ToLowerInvariant()
        observed_exe_sha256 = $ObservedExeSha256
        observed_runtime_build_id = $ObservedRuntimeBuildId
        attestation_protocol = 2
        local_import_canary = $ObservedImportCanary
        local_import_timeout_canary = $ObservedImportTimeoutCanary
        artifact_dir = $ArtifactDir
        instance = $Instance
        failures = @($failures)
    }
    $ReportJson = $Report | ConvertTo-Json -Depth 6
    Write-Utf8NoBom (Join-Path $RunDir "result.json") $ReportJson
}

if ($Result -eq "INVALID") {
    Write-Host "==> SMOKE INVALID (evidence: $RunDir)"
    $harnessErrors | ForEach-Object { Write-Host "  - $_" }
    exit 2
}
if ($Result -eq "FAIL") {
    Write-Host "==> SMOKE FAIL (evidence: $RunDir)"
    $failures | ForEach-Object { Write-Host "  - $_" }
    exit 1
}
Write-Host "==> SMOKE PASS (evidence: $RunDir)"
exit 0
