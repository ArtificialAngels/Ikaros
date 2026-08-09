Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class IconProbe {
    [StructLayout(LayoutKind.Sequential)]
    public struct ICONINFO {
        public bool fIcon;
        public uint xHotspot;
        public uint yHotspot;
        public IntPtr hbmMask;
        public IntPtr hbmColor;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct BITMAP {
        public int bmType;
        public int bmWidth;
        public int bmHeight;
        public int bmWidthBytes;
        public ushort bmPlanes;
        public ushort bmBitsPixel;
        public IntPtr bmBits;
    }
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr LoadImage(IntPtr hinst, string lpszName, uint uType, int cx, int cy, uint fuLoad);
    [DllImport("user32.dll")]
    public static extern bool GetIconInfo(IntPtr hIcon, out ICONINFO piconinfo);
    [DllImport("gdi32.dll")]
    public static extern int GetObject(IntPtr hgdiobj, int cbBuffer, out BITMAP lpvObject);
    [DllImport("user32.dll")]
    public static extern bool DestroyIcon(IntPtr hIcon);
    [DllImport("gdi32.dll")]
    public static extern bool DeleteObject(IntPtr hObject);
}
"@

$const = @{ IMAGE_ICON = 1; LR_LOADFROMFILE = 0x10 }

function Probe-Ico($path) {
    Write-Host "=== $path ==="
    foreach ($req in @(0, 256, 512, 1024)) {
        $h = [IconProbe]::LoadImage([IntPtr]::Zero, $path, $const.IMAGE_ICON, $req, $req, $const.LR_LOADFROMFILE)
        if ($h -eq [IntPtr]::Zero) {
            Write-Host ("  request {0,4}: LoadImage FAILED err={1}" -f $req, [System.Runtime.InteropServices.Marshal]::GetLastWin32Error())
            continue
        }
        $info = New-Object IconProbe+ICONINFO
        [void][IconProbe]::GetIconInfo($h, [ref]$info)
        $bm = New-Object IconProbe+BITMAP
        [void][IconProbe]::GetObject($info.hbmColor, [System.Runtime.InteropServices.Marshal]::SizeOf($bm), [ref]$bm)
        Write-Host ("  request {0,4}: got {1,4}x{2,4} bitmap (bpp={3})" -f $req, $bm.bmWidth, $bm.bmHeight, $bm.bmBitsPixel)
        [void][IconProbe]::DeleteObject($info.hbmColor)
        [void][IconProbe]::DeleteObject($info.hbmMask)
        [void][IconProbe]::DestroyIcon($h)
    }
}

Probe-Ico 'E:\Ikaros\probe_bmp512.ico'
Probe-Ico 'E:\Ikaros\probe_png512.ico'
Probe-Ico 'E:\Ikaros\probe_bmp1024.ico'
Probe-Ico 'E:\Ikaros\probe_mix_bmp.ico'
Probe-Ico 'E:\Ikaros\probe_mix_png.ico'
