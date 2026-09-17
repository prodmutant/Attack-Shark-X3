param([string]$Proc = "X3")
Add-Type @"
using System;using System.Runtime.InteropServices;using System.Text;
public struct RC{public int L,T,R,B;}
public class WT{
 public delegate bool EP(IntPtr h,IntPtr l);
 [DllImport("user32.dll")]public static extern bool EnumWindows(EP e,IntPtr l);
 [DllImport("user32.dll")]public static extern bool EnumChildWindows(IntPtr p,EP e,IntPtr l);
 [DllImport("user32.dll")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll")]public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out RC r);
 [DllImport("user32.dll")]public static extern int GetClassName(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")]public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")]public static extern IntPtr GetParent(IntPtr h);
 [DllImport("user32.dll")]public static extern IntPtr GetWindow(IntPtr h,uint c);
}
"@ -ErrorAction SilentlyContinue

$p = Get-Process $Proc -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { "ERR: $Proc not running"; exit 1 }
$tp = $p.Id
$script:rows = @()

function Add-Row($h, $depth) {
  $r = New-Object RC
  [WT]::GetWindowRect($h, [ref]$r) | Out-Null
  $c = New-Object System.Text.StringBuilder 128
  [WT]::GetClassName($h, $c, 128) | Out-Null
  $t = New-Object System.Text.StringBuilder 128
  [WT]::GetWindowText($h, $t, 128) | Out-Null
  $script:rows += [pscustomobject]@{
    Depth = $depth
    H     = [int64]$h
    Vis   = [WT]::IsWindowVisible($h)
    Owner = [int64][WT]::GetWindow($h, 4)   # GW_OWNER
    X = $r.L; Y = $r.T; W = $r.R - $r.L; Ht = $r.B - $r.T
    Cls = $c.ToString(); Txt = $t.ToString()
  }
}

$childCb = [WT+EP]{ param($h, $l) Add-Row $h 1; return $true }

$cb = [WT+EP]{
  param($h, $l)
  $wp = 0
  [WT]::GetWindowThreadProcessId($h, [ref]$wp) | Out-Null
  if ($wp -eq $tp) {
    Add-Row $h 0
    [WT]::EnumChildWindows($h, $childCb, [IntPtr]::Zero) | Out-Null
  }
  return $true
}
[WT]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

$script:rows | Where-Object { $_.W -gt 40 -and $_.Ht -gt 20 } |
  Format-Table Depth, H, Vis, Owner, X, Y, W, Ht, Cls, Txt -AutoSize
