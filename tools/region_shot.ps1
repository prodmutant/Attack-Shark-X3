param([int]$DX=0, [int]$DY=0, [int]$W=460, [int]$H=470, [string]$Out="")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public struct RR{public int L,T,Rt,B;}
public class GG{[DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out RR r);}
"@ -ErrorAction SilentlyContinue

$p = Get-Process X3 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { "ERR: X3 not running"; exit 1 }
$r = New-Object RR
[GG]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
$x = $r.L + $DX
$y = $r.T + $DY
if (-not $Out) { $Out = "C:\Users\prodmutant\Desktop\AttackSharkRE\captures\shots\region.png" }
try {
  $bmp = New-Object System.Drawing.Bitmap($W, $H)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($W, $H)))
  $g.Dispose()
  $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
  "OK screen($x,$y) ${W}x${H} -> $Out"
} catch {
  "SAVE FAILED: $($_.Exception.Message)"
  exit 1
}
