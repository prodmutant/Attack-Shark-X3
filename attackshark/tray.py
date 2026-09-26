"""System-tray launcher for the driver.

Double-click the exe, it sits in the tray, the interface opens in the browser.
No console window, one instance only. Pure ctypes so the packaged exe stays
small and dependency-free.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import socket
import sys
import threading
import webbrowser

from .server import DEFAULT_PORT, serve

u32 = C.WinDLL("user32", use_last_error=True)
k32 = C.WinDLL("kernel32", use_last_error=True)
shell32 = C.WinDLL("shell32", use_last_error=True)

APP_NAME = "PRODMUTANT X3 Driver"
MUTEX_NAME = "Local\\AttackSharkX3DriverSingleInstance"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AttackSharkX3Driver"

WM_DESTROY, WM_COMMAND, WM_APP = 0x0002, 0x0111, 0x8000
WM_TRAY = WM_APP + 1
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
IDI_APPLICATION = 32512
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0000, 0x0800, 0x0008
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

ID_OPEN, ID_AUTOSTART, ID_QUIT = 1, 2, 3

WNDPROC = C.WINFUNCTYPE(C.c_longlong, W.HWND, C.c_uint, W.WPARAM, W.LPARAM)

# Without a declared prototype ctypes guesses each argument's type from the
# value it is handed, and guesses `int` - so a 64-bit window handle or lparam
# raises OverflowError instead of being passed. The callback then returns
# nothing, the default handler never runs for that message, and because it
# happens inside a ctypes callback Python prints "Exception ignored" and
# carries on, which is why this was noise rather than a crash.
u32.DefWindowProcW.argtypes = [W.HWND, C.c_uint, W.WPARAM, W.LPARAM]
u32.DefWindowProcW.restype = C.c_longlong
u32.CreateWindowExW.restype = W.HWND
u32.DestroyWindow.argtypes = [W.HWND]


class WNDCLASS(C.Structure):
    _fields_ = [("style", C.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


class NOTIFYICONDATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", C.c_uint),
                ("uFlags", C.c_uint), ("uCallbackMessage", C.c_uint),
                ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD),
                ("szInfo", W.WCHAR * 256), ("uVersion", C.c_uint),
                ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD)]


class POINT(C.Structure):
    _fields_ = [("x", C.c_long), ("y", C.c_long)]


def resource_dir():
    """PyInstaller unpacks data next to the frozen module; source runs in place."""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))


def icon_path():
    """The tray and window icon - your own copy first.

    Same rule the web artwork follows in `server._custom`: a local
    `logo.custom.ico` beside `logo.ico` wins when running from source, so the
    face in the tray matches the face in the page. A build always carries
    the shipped icon.
    """
    for rel in ("attackshark/web/logo.custom.ico", "attackshark/web/logo.ico"):
        p = os.path.join(resource_dir(), *rel.split("/"))
        if os.path.isfile(p):
            return p
    return None


def free_port(preferred=DEFAULT_PORT):
    for port in (preferred, preferred + 1, preferred + 2, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


# ------------------------------------------------------------- autostart ---
def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    exe = sys.executable
    cmd = f'"{exe}"' if getattr(sys, "frozen", False) else \
          f'"{exe}" -m attackshark tray'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except OSError:
                pass


class Tray:
    def __init__(self, port):
        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.hwnd = None
        self.nid = None
        self._proc = WNDPROC(self._wndproc)   # keep a strong reference

    # ------------------------------------------------------------ window ---
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            low = lparam & 0xFFFF
            if low in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                webbrowser.open(self.url)
            elif low == WM_RBUTTONUP:
                self._menu()
            return 0
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == ID_OPEN:
                webbrowser.open(self.url)
            elif cmd == ID_AUTOSTART:
                set_autostart(not autostart_enabled())
            elif cmd == ID_QUIT:
                u32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, C.byref(self.nid))
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        menu = u32.CreatePopupMenu()
        u32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Open interface")
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        flags = MF_STRING | (MF_CHECKED if autostart_enabled() else 0)
        u32.AppendMenuW(menu, flags, ID_AUTOSTART, "Start with Windows")
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit")
        pt = POINT()
        u32.GetCursorPos(C.byref(pt))
        u32.SetForegroundWindow(self.hwnd)      # else the menu will not dismiss
        cmd = u32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                 pt.x, pt.y, 0, self.hwnd, None)
        u32.DestroyMenu(menu)
        if cmd:
            u32.SendMessageW(self.hwnd, WM_COMMAND, cmd, 0)

    def run(self):
        hinst = k32.GetModuleHandleW(None)
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.lpszClassName = "AttackSharkTrayWnd"
        u32.RegisterClassW(C.byref(cls))
        self.hwnd = u32.CreateWindowExW(0, "AttackSharkTrayWnd", APP_NAME,
                                        0, 0, 0, 0, 0, None, None, hinst, None)

        ico = icon_path()
        hicon = None
        if ico:
            hicon = u32.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                                   LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            hicon = u32.LoadIconW(None, C.c_wchar_p(IDI_APPLICATION))

        self.nid = NOTIFYICONDATA()
        self.nid.cbSize = C.sizeof(NOTIFYICONDATA)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAY
        self.nid.hIcon = hicon
        self.nid.szTip = f"{APP_NAME} - {self.url}"
        shell32.Shell_NotifyIconW(NIM_ADD, C.byref(self.nid))

        msg = W.MSG()
        while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(C.byref(msg))
            u32.DispatchMessageW(C.byref(msg))


def main(open_browser=True):
    # one instance only - a second launch just reopens the page
    k32.CreateMutexW(None, False, MUTEX_NAME)
    if k32.GetLastError() == 183:            # ERROR_ALREADY_EXISTS
        webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/")
        return 0

    port = free_port()
    threading.Thread(target=serve, kwargs={"port": port, "open_browser": False},
                     daemon=True).start()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    Tray(port).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
