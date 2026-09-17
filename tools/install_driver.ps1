<#
.SYNOPSIS
    Install the Attack Shark X3 mouse filter driver.

.DESCRIPTION
    Four steps, in this order, because each depends on the last:

      1. Trust the build's test certificate (LocalMachine Root and
         TrustedPublisher). Without this the catalog is worthless.
      2. Turn on test signing. A self-signed kernel driver cannot load on an
         ordinary x64 Windows, and this needs a reboot to take effect.
      3. Stage the driver package into the driver store.
      4. Bind it to the X3's devnode.

    Step 4 needs devcon rather than pnputil alone. The devnode is already
    claimed by the inbox msmouse.inf, which is WHQL signed and therefore
    outranks anything signed locally, so "install the better match" will never
    choose ours. devcon update applies an INF to a devnode by name and skips
    the ranking contest entirely.

    Run it again after a reboot to continue where it left off; every step is
    idempotent.

.PARAMETER Force
    Re-bind the devnode even if the filter already appears installed.

.PARAMETER SkipTestSigning
    Do not touch the boot configuration. For a machine that already has test
    signing on, or where the driver will be signed some other way.
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipTestSigning
)

$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $PSScriptRoot
$out     = Join-Path $root 'driver\out'
$inf     = Join-Path $out 'asxfilter.inf'
$cer     = Join-Path $out 'asxfilter-test.cer'
$hwids   = @('HID\VID_1D57&PID_FA60&MI_01', 'HID\VID_1D57&PID_FA61&MI_01')

function Step($n, $text) { Write-Host ("[{0}] {1}" -f $n, $text) -ForegroundColor Cyan }
function Ok($text)       { Write-Host ("    " + $text) -ForegroundColor Green }
function Note($text)     { Write-Host ("    " + $text) -ForegroundColor DarkGray }
function Warn($text)     { Write-Host ("    " + $text) -ForegroundColor Yellow }

# -- elevation -------------------------------------------------------------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Elevating...' -ForegroundColor Yellow
    $argline = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Force)           { $argline += '-Force' }
    if ($SkipTestSigning) { $argline += '-SkipTestSigning' }
    Start-Process powershell -Verb RunAs -ArgumentList $argline
    return
}

if (-not (Test-Path $inf)) {
    throw "no built driver at $inf - run: python tools\build_driver.py"
}

# -- 1. trust the test certificate -----------------------------------------
Step 1 'Trusting the test signing certificate'

if (-not (Test-Path $cer)) { throw "missing $cer - rebuild without --no-sign" }

$thumb = (Get-PfxCertificate -FilePath $cer).Thumbprint
foreach ($store in 'Root', 'TrustedPublisher') {
    $path = "Cert:\LocalMachine\$store\$thumb"
    if (Test-Path $path) {
        Note "$store already trusts $($thumb.Substring(0,16))..."
    } else {
        Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
        Ok "added to LocalMachine\$store"
    }
}

# -- 2. test signing -------------------------------------------------------
Step 2 'Checking test signing'

$bcd = (bcdedit /enum '{current}' | Out-String)
$testsigning = $bcd -match '(?im)^\s*testsigning\s+Yes\s*$'

if ($testsigning) {
    Ok 'test signing is on'
} elseif ($SkipTestSigning) {
    Warn 'test signing is OFF and -SkipTestSigning was given; the driver will not load'
} else {
    bcdedit /set '{current}' testsigning on | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'bcdedit failed. If Secure Boot is enabled, turn it off in firmware first: test signing cannot be enabled while it is on.'
    }
    Warn 'test signing enabled - REBOOT, then run this script again.'
    Warn 'Windows will show a desktop watermark, and some kernel anti-cheat'
    Warn 'products refuse to run in this mode. tools\uninstall_driver.ps1 reverses it.'
    return
}

# -- 3. stage the package --------------------------------------------------
Step 3 'Adding the driver package to the driver store'

$already = (pnputil /enum-drivers | Out-String) -match '(?im)^\s*Original Name:\s*asxfilter\.inf\s*$'
if ($already -and -not $Force) {
    Note 'asxfilter.inf is already staged (use -Force to re-add)'
} else {
    $r = pnputil /add-driver $inf /install 2>&1 | Out-String
    Write-Host ($r.Trim() -split "`n" | ForEach-Object { '    ' + $_.Trim() }) -Separator "`n"
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 259) {
        throw "pnputil failed ($LASTEXITCODE)"
    }
    Ok 'staged'
}

# -- 4. bind it to the devnode ---------------------------------------------
Step 4 'Binding the filter to the X3 devnode'

$devcon = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\Tools" -Recurse `
            -Filter devcon.exe -ErrorAction SilentlyContinue |
          Where-Object { $_.DirectoryName -match '\\x64$' } |
          Sort-Object FullName -Descending | Select-Object -First 1

if (-not $devcon) {
    Warn 'devcon.exe not found in the WDK; falling back to pnputil only.'
    Warn 'If the filter does not appear below, install the WDK tools and re-run.'
} else {
    foreach ($hw in $hwids) {
        $present = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
                   Where-Object { $_.InstanceId -like "$hw\*" }
        if (-not $present) { Note "$hw is not connected - skipped"; continue }

        $r = & $devcon.FullName update $inf $hw 2>&1 | Out-String
        Note ($r.Trim() -replace "`r?`n", "`n    ")
    }
}

# -- verify ----------------------------------------------------------------
Write-Host ''
Step '*' 'Result'

$bound = $false
foreach ($hw in $hwids) {
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
      Where-Object { $_.InstanceId -like "$hw\*" } | ForEach-Object {
        $key = "HKLM:\SYSTEM\CurrentControlSet\Enum\$($_.InstanceId)"
        $uf  = (Get-ItemProperty -Path $key -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
        if ($uf -contains 'asxfilter') {
            Ok "$($_.InstanceId)"
            Ok "  UpperFilters = $($uf -join ', ')   status $($_.Status)"
            $bound = $true
        } else {
            Warn "$($_.InstanceId): UpperFilters = $(if ($uf) { $uf -join ', ' } else { '(none)' })"
        }
      }
}

$svc = Get-CimInstance Win32_SystemDriver -Filter "Name='asxfilter'" -ErrorAction SilentlyContinue
if ($svc) { Ok "service asxfilter: $($svc.State)" } else { Warn 'service asxfilter is not registered' }

if (Test-Path '\\.\AttackSharkFilter') {
    Ok 'control device \\.\AttackSharkFilter is open for business'
} else {
    Note 'control device not visible from here (it is ACLed to SYSTEM and Administrators);'
    Note 'confirm with:  python tools\verify_injection.py --status   (elevated)'
}

if (-not $bound) {
    Write-Host ''
    Warn 'The filter is not bound yet. The usual causes, in order of likelihood:'
    Warn '  - you have not rebooted since test signing was switched on'
    Warn '  - the mouse or its dongle is not connected'
    Warn '  - Secure Boot is on, which blocks test signing outright'
}
