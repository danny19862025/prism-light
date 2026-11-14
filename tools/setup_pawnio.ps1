param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
try {
    if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\PawnIO') { exit 0 }
    if ($CheckOnly) { exit 2 }
    Write-Host 'PawnIO enables motherboard and RAM RGB detection. Approve the Windows prompt and complete the official setup wizard.'
    $cache = Join-Path $PSScriptRoot '..\vendor\pawnio'
    New-Item -ItemType Directory -Force $cache | Out-Null
    $installer = Join-Path $cache 'PawnIO_setup-2.2.0.exe'
    $expected = '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032'
    if (!(Test-Path $installer) -or (Get-FileHash $installer -Algorithm SHA256).Hash -ne $expected) {
        Invoke-WebRequest 'https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe' -OutFile $installer -UseBasicParsing
    }
    if ((Get-FileHash $installer -Algorithm SHA256).Hash -ne $expected) { throw 'PawnIO checksum mismatch.' }
    $signature = Get-AuthenticodeSignature $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=namazso\.eu(?:,|$)') { throw 'PawnIO publisher signature could not be verified.' }
    $process = Start-Process -FilePath $installer -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw "PawnIO setup exited with code $($process.ExitCode)." }
    if (!(Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\PawnIO')) { throw 'PawnIO setup was cancelled or the driver was not installed.' }
    if ($process.ExitCode -eq 3010) { Write-Host 'Restart Windows before scanning devices.' }
    exit 0
} catch {
    Write-Error "Hardware setup failed: $_" -ErrorAction Continue
    exit 1
}
