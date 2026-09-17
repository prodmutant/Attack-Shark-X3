<#
.SYNOPSIS
    Remove the Attack Shark X3 mouse filter driver and undo what installing it
    changed.

.DESCRIPTION
    Reverses tools\install_driver.ps1: unbinds the devnode back to the inbox
    mouse driver, deletes the driver package, and optionally removes the test
    certificate and turns test signing back off.

    Safe to run when only some of the steps were ever applied.

.PARAMETER KeepTestSigning
    Leave the boot configuration alone.

.PARAMETER KeepCertificate
    Leave the test certificate in the machine stores.
#>
[CmdletBinding()]
param(
    [switch]$KeepTestSigning,
    [switch]$KeepCertificate
)

$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot
$cer   = Join-Path $root 'driver\out\asxfilter-test.cer'
$hwids = @('HID\VID_1D57&PID_FA60&MI_01', 'HID\VID_1D57&PID_FA61&MI_01')

function Step($n, $t) { Write-Host ("[{0}] {1}" -f $n, $t) -ForegroundColor Cyan }
function Ok($t)       { Write-Host ('    ' + $t) -ForegroundColor Green }
function Note($t)     { Write-Host ('    ' + $t) -ForegroundColor DarkGray }
function Warn($t)     { Write-Host ('    ' + $t) -ForegroundColor Yellow }

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Elevating...' -ForegroundColor Yellow
    $argline = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($KeepTestSigning)  { $argline += '-KeepTestSigning' }
    if ($KeepCertificate)  { $argline += '-KeepCertificate' }
    Start-Process powershell -Verb RunAs -ArgumentList $argline
    return
}

# -- 1. delete the driver package -----------------------------------------
Step 1 'Removing the driver package'

$published = $null
$cur = $null
foreach ($line in (pnputil /enum-drivers)) {
    if ($line -match '(?i)^\s*Published Name:\s*(oem\d+\.inf)') { $cur = $Matches[1] }
    if ($line -match '(?i)^\s*Original Name:\s*asxfilter\.inf') { $published = $cur }
}

if ($published) {
    Note "published as $published"
    pnputil /delete-driver $published /uninstall /force | ForEach-Object { Note $_.Trim() }
    Ok 'driver package removed; the devnode falls back to the inbox mouse driver'
} else {
    Note 'asxfilter.inf was not in the driver store'
}

# -- 2. make sure no devnode still lists the filter ------------------------
Step 2 'Checking the devnodes'

foreach ($hw in $hwids) {
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
      Where-Object { $_.InstanceId -like "$hw\*" } | ForEach-Object {
        $key = "HKLM:\SYSTEM\CurrentControlSet\Enum\$($_.InstanceId)"
        $uf  = (Get-ItemProperty -Path $key -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
        if ($uf -contains 'asxfilter') {
            Warn "$($_.InstanceId) still lists asxfilter; restarting the device"
            Disable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
            Enable-PnpDevice  -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
        } else {
            Ok "$($_.InstanceId) is clean (status $($_.Status))"
        }
      }
}

$svc = Get-CimInstance Win32_SystemDriver -Filter "Name='asxfilter'" -ErrorAction SilentlyContinue
if ($svc) {
    Warn "service asxfilter still registered ($($svc.State)); it will go on the next reboot"
} else {
    Ok 'service asxfilter is gone'
}

# -- 3. certificate --------------------------------------------------------
Step 3 'Test certificate'

if ($KeepCertificate) {
    Note 'kept, as asked'
} elseif (Test-Path $cer) {
    $thumb = (Get-PfxCertificate -FilePath $cer).Thumbprint
    foreach ($store in 'Root', 'TrustedPublisher') {
        $path = "Cert:\LocalMachine\$store\$thumb"
        if (Test-Path $path) {
            Remove-Item $path -Force
            Ok "removed from LocalMachine\$store"
        } else {
            Note "not present in LocalMachine\$store"
        }
    }
} else {
    Note 'no .cer on disk to match against; nothing removed'
}

# -- 4. test signing -------------------------------------------------------
Step 4 'Test signing'

if ($KeepTestSigning) {
    Note 'left on, as asked'
} else {
    bcdedit /set '{current}' testsigning off | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Ok 'turned off - takes effect on the next reboot (the watermark goes with it)'
    } else {
        Warn 'bcdedit failed; turn it off by hand with: bcdedit /set {current} testsigning off'
    }
}

Write-Host ''
Write-Host 'Done. Reboot to finish.' -ForegroundColor Cyan
