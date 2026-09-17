<#
  Stops Windows idle-suspending the Attack Shark receiver.

  Windows is allowed to power down the dongle's USB interfaces, and USB
  selective suspend is on even under the High performance plan. On a 2.4 GHz
  mouse that shows up as a stutter or a lag spike on the first movement or
  click after a short pause.

  Run from an ELEVATED PowerShell:
      powershell -ExecutionPolicy Bypass -File tools\fix_usb_lag.ps1
  Undo with:
      powershell -ExecutionPolicy Bypass -File tools\fix_usb_lag.ps1 -Undo
#>
param([switch]$Undo)

$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This needs an elevated PowerShell (Run as administrator)." -ForegroundColor Yellow
    exit 1
}

$VID = 'VID_1D57'          # Attack Shark X3 receiver / wired
$want = if ($Undo) { 1 } else { 0 }
$label = if ($Undo) { 'restoring Windows defaults' } else { 'disabling idle suspend' }
Write-Host "$label for $VID`n"

# ---- 1. USB selective suspend, both AC and DC -----------------------------
$sub = '2a737441-1930-4402-8d77-b2bebba308a3'   # USB settings
$set = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'   # USB selective suspend
powercfg /setacvalueindex SCHEME_CURRENT $sub $set $want | Out-Null
powercfg /setdcvalueindex SCHEME_CURRENT $sub $set $want | Out-Null
powercfg /setactive SCHEME_CURRENT | Out-Null
Write-Host ("  USB selective suspend -> {0}" -f $(if ($want) { 'enabled' } else { 'DISABLED' }))

# ---- 2. per-device power management on the receiver's interfaces ----------
$n = 0
Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable |
    Where-Object { $_.InstanceName -like "*$VID*" } | ForEach-Object {
        try {
            $_.Enable = [bool]$want
            Set-CimInstance -InputObject $_
            $n++
        } catch {
            Write-Host "  ! could not set $($_.InstanceName)" -ForegroundColor DarkYellow
        }
    }
Write-Host ("  'turn off this device to save power' -> {0} on {1} interface(s)" -f `
    $(if ($want) { 'allowed' } else { 'BLOCKED' }), $n)

# ---- 3. the registry flags the USB stack actually reads -------------------
$touched = 0
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB' |
    Where-Object { $_.PSChildName -like "$VID*" } | ForEach-Object {
        Get-ChildItem $_.PSPath | ForEach-Object {
            $p = Join-Path $_.PSPath 'Device Parameters'
            if (Test-Path $p) {
                Set-ItemProperty $p -Name 'EnhancedPowerManagementEnabled' -Value $want -Type DWord
                Set-ItemProperty $p -Name 'AllowIdleIrpInD3' -Value $want -Type DWord
                Set-ItemProperty $p -Name 'DeviceSelectiveSuspended' -Value $want -Type DWord
                $touched++
            }
        }
    }
Write-Host "  device power flags updated on $touched node(s)"

Write-Host "`nUnplug and replug the receiver (or reboot) for this to take effect." -ForegroundColor Green
