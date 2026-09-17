param(
  [string]$Action = "shot",
  [int]$X = 0, [int]$Y = 0, [int]$X2 = 0, [int]$Y2 = 0,
  [int]$Item = 1, [int]$ItemH = 22, [int]$Pad = 2, [int]$MenuIndex = 0,
  [string]$Out = "captures/ui.png"
)
$ErrorActionPreference = 'SilentlyContinue'
Add-Type @"
using System;using System.Runtime.InteropServices;using System.Text;
public struct R{public int L,T,Rt,B;}
public class WU{
 public delegate bool EnumProc(IntPtr h,IntPtr l);
 [DllImport("user32.dll")]public static extern bool EnumWindows(EnumProc e,IntPtr l);
 [DllImport("user32.dll")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll")]public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out R r);
 [DllImport("user32.dll")]public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f);
 [DllImport("user32.dll")]public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int c);
 [DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int cx,int cy,uint f);
}
"@

$proc = Get-Process X3 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { "ERR: X3 not running"; exit 1 }
$targetPid = $proc.Id

function Get-ProcWindows {
  $script:wins = @()
  $cb = [WU+EnumProc]{
    param($h, $l)
    $wpid = 0
    [WU]::GetWindowThreadProcessId($h, [ref]$wpid) | Out-Null
    if ($wpid -eq $targetPid -and [WU]::IsWindowVisible($h)) {
      $r = New-Object R
      [WU]::GetWindowRect($h, [ref]$r) | Out-Null
      $script:wins += [pscustomobject]@{
        H = $h; L = $r.L; T = $r.T; W = $r.Rt - $r.L; Ht = $r.B - $r.T
      }
    }
    return $true
  }
  [WU]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
  return $script:wins
}

$all = Get-ProcWindows
$main = $all | Where-Object { $_.W -gt 400 } |
        Sort-Object { $_.W * $_.Ht } -Descending | Select-Object -First 1
if (-not $main -and $proc.MainWindowHandle -ne 0) {
  [WU]::ShowWindow($proc.MainWindowHandle, 5) | Out-Null
  Start-Sleep -Milliseconds 700
  $all = Get-ProcWindows
  $main = $all | Where-Object { $_.W -gt 400 } |
          Sort-Object { $_.W * $_.Ht } -Descending | Select-Object -First 1
}
if (-not $main) { "ERR: no window"; exit 1 }

function Send-Click($hwnd, $cx, $cy) {
  $lp = [IntPtr](($cy -shl 16) -bor ($cx -band 0xFFFF))
  [WU]::PostMessage($hwnd, 0x200, [IntPtr]0, $lp) | Out-Null   # WM_MOUSEMOVE
  Start-Sleep -Milliseconds 90
  [WU]::PostMessage($hwnd, 0x201, [IntPtr]1, $lp) | Out-Null   # WM_LBUTTONDOWN
  Start-Sleep -Milliseconds 70
  [WU]::PostMessage($hwnd, 0x202, [IntPtr]0, $lp) | Out-Null   # WM_LBUTTONUP
  Start-Sleep -Milliseconds 350
}

function Save-Window($hwnd, $w, $h, $path) {
  Add-Type -AssemblyName System.Drawing
  $bmp = New-Object System.Drawing.Bitmap($w, $h)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc(); [WU]::PrintWindow($hwnd, $hdc, 2) | Out-Null; $g.ReleaseHdc($hdc)
  $g.Dispose()
  $full = if ([System.IO.Path]::IsPathRooted($path)) { $path } else { Join-Path (Get-Location) $path }
  $bmp.Save($full, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
  return $full
}

switch ($Action) {
  "shot"  { "shot -> " + (Save-Window $main.H $main.W $main.Ht $Out) }
  "info"  {
    "hwnd=$($main.H) at $($main.L),$($main.T) size=$($main.W)x$($main.Ht)"
    foreach ($w in $all) { "   win $($w.H) $($w.L),$($w.T) $($w.W)x$($w.Ht)" }
  }
  "move"  {
    [WU]::SetWindowPos($main.H, [IntPtr]::Zero, $X, $Y, 0, 0, (0x0001 -bor 0x0004 -bor 0x0010)) | Out-Null
    Start-Sleep -Milliseconds 300
    $r2 = New-Object R; [WU]::GetWindowRect($main.H, [ref]$r2) | Out-Null
    "moved to $($r2.L),$($r2.T)"
  }
  "click" { Send-Click $main.H $X $Y; "clicked $X,$Y" }
  "drag"  {
    $lp1 = [IntPtr](($Y -shl 16) -bor ($X -band 0xFFFF))
    $lp2 = [IntPtr](($Y2 -shl 16) -bor ($X2 -band 0xFFFF))
    [WU]::PostMessage($main.H, 0x200, [IntPtr]0, $lp1) | Out-Null
    Start-Sleep -Milliseconds 80
    [WU]::PostMessage($main.H, 0x201, [IntPtr]1, $lp1) | Out-Null
    Start-Sleep -Milliseconds 80
    for ($i = 1; $i -le 14; $i++) {
      $ix = [int]($X + ($X2 - $X) * $i / 14)
      $iy = [int]($Y + ($Y2 - $Y) * $i / 14)
      $lp = [IntPtr](($iy -shl 16) -bor ($ix -band 0xFFFF))
      [WU]::PostMessage($main.H, 0x200, [IntPtr]1, $lp) | Out-Null
      Start-Sleep -Milliseconds 35
    }
    Start-Sleep -Milliseconds 120
    [WU]::PostMessage($main.H, 0x202, [IntPtr]0, $lp2) | Out-Null
    Start-Sleep -Milliseconds 350
    "dragged $X,$Y -> $X2,$Y2"
  }
  "menuinfo" {
    $menus = @($all | Where-Object { $_.H -ne $main.H -and $_.W -gt 20 -and $_.Ht -gt 20 } |
              Sort-Object Ht -Descending)
    if (-not $menus) { "no popup window"; exit 1 }
    for ($i = 0; $i -lt $menus.Count; $i++) {
      $m = $menus[$i]
      "menu[$i] $($m.H) at $($m.L),$($m.T) $($m.W)x$($m.Ht) items~$([int](($m.Ht - 2*$Pad)/$ItemH))"
    }
  }
  "menuitem" {
    # DuiLib menus are real top-level windows; clicks must go to the popup itself.
    # MenuIndex 0 is the tallest popup (the parent menu); 1 is its open submenu.
    $menus = @($all | Where-Object { $_.H -ne $main.H -and $_.W -lt 400 -and $_.Ht -gt 20 } |
              Sort-Object Ht -Descending)
    $menu = $menus[$MenuIndex]
    if (-not $menu) { "ERR: no popup menu at index $MenuIndex"; exit 1 }
    $cx = [int]($menu.W / 2)
    $cy = [int]($Pad + ($Item - 1) * $ItemH + $ItemH / 2)
    Send-Click $menu.H $cx $cy
    "menu $($menu.H) ($($menu.W)x$($menu.Ht)) item $Item -> client $cx,$cy"
  }
  "dialoginfo" {
    $dl = @($all | Where-Object { $_.H -ne $main.H } | Sort-Object Ht -Descending)
    if (-not $dl) { "no popup window"; exit 1 }
    for ($i = 0; $i -lt $dl.Count; $i++) {
      "dlg[$i] $($dl[$i].H) at $($dl[$i].L),$($dl[$i].T) $($dl[$i].W)x$($dl[$i].Ht)"
    }
  }
  "dialogshot" {
    $dl = @($all | Where-Object { $_.H -ne $main.H -and $_.W -gt 80 -and $_.Ht -gt 60 } |
           Sort-Object Ht -Descending)
    $d = $dl[$MenuIndex]
    if (-not $d) { "ERR: no dialog at index $MenuIndex"; exit 1 }
    "dialogshot ($($d.W)x$($d.Ht)) -> " + (Save-Window $d.H $d.W $d.Ht $Out)
  }
  "dialogclick" {
    $dl = @($all | Where-Object { $_.H -ne $main.H -and $_.W -gt 80 -and $_.Ht -gt 60 } |
           Sort-Object Ht -Descending)
    $d = $dl[$MenuIndex]
    if (-not $d) { "ERR: no dialog at index $MenuIndex"; exit 1 }
    Send-Click $d.H $X $Y
    "dialog $($d.H) click $X,$Y"
  }
  "menushot" {
    $menus = @($all | Where-Object { $_.H -ne $main.H -and $_.W -lt 400 -and $_.Ht -gt 20 } |
              Sort-Object Ht -Descending)
    $menu = $menus[$MenuIndex]
    if (-not $menu) { "ERR: no popup menu at index $MenuIndex"; exit 1 }
    "menushot ($($menu.W)x$($menu.Ht)) -> " + (Save-Window $menu.H $menu.W $menu.Ht $Out)
  }
}
