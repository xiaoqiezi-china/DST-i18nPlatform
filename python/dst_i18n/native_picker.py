"""Windows native folder / file pickers.

Dialogs are shown by a separate Windows PowerShell STA process.  The app's
HTTP server thread is not guaranteed to be an STA thread, and creating the
dialog in-process would also block the server thread.
"""

from __future__ import annotations

import base64
import os
import subprocess
from typing import Optional


_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;

public static class NativeDialogZOrder
{
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int cmd);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    private const uint SWP_NOSIZE = 0x0001;
    private const uint SWP_NOMOVE = 0x0002;
    private const uint SWP_SHOWWINDOW = 0x0040;

    public static void BringToFrontSoon(int targetPid)
    {
        Thread worker = new Thread(delegate()
        {
            for (int i = 0; i < 80; i++)
            {
                Thread.Sleep(100);
                if (BringOnce(targetPid)) return;
            }
        });
        worker.IsBackground = true;
        worker.Start();
    }

    private static bool BringOnce(int targetPid)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hwnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hwnd)) return true;
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            if ((int)pid != targetPid) return true;
            StringBuilder cls = new StringBuilder(256);
            GetClassName(hwnd, cls, cls.Capacity);
            if (cls.ToString().StartsWith("#32770"))
            {
                found = hwnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);

        if (found == IntPtr.Zero) return false;
        SetWindowPos(found, new IntPtr(-1), 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW);
        ShowWindow(found, 5);
        SetForegroundWindow(found);
        return true;
    }
}
'@

[System.Windows.Forms.Application]::EnableVisualStyles()

$initial = [string]$env:DST_PICK_INIT
$description = [string]$env:DST_PICK_DESC
if ([string]::IsNullOrWhiteSpace($description)) { $description = '选择文件夹' }

$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $description
$dialog.ShowNewFolderButton = $true
$dialog.RootFolder = [System.Environment+SpecialFolder]::MyComputer
if (-not [string]::IsNullOrWhiteSpace($initial) -and (Test-Path -LiteralPath $initial -PathType Container)) {
    $dialog.SelectedPath = $initial
}

[NativeDialogZOrder]::BringToFrontSoon([System.Diagnostics.Process]::GetCurrentProcess().Id)
$result = $dialog.ShowDialog()

if ($result -eq [System.Windows.Forms.DialogResult]::OK -and -not [string]::IsNullOrWhiteSpace($dialog.SelectedPath)) {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::Write($dialog.SelectedPath)
}
"""


_PS_FILE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;

public static class NativeFileDialogZOrder
{
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int cmd);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    private const uint SWP_NOSIZE = 0x0001;
    private const uint SWP_NOMOVE = 0x0002;
    private const uint SWP_SHOWWINDOW = 0x0040;

    public static void BringToFrontSoon(int targetPid)
    {
        Thread worker = new Thread(delegate()
        {
            for (int i = 0; i < 80; i++)
            {
                Thread.Sleep(100);
                if (BringOnce(targetPid)) return;
            }
        });
        worker.IsBackground = true;
        worker.Start();
    }

    private static bool BringOnce(int targetPid)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hwnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hwnd)) return true;
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            if ((int)pid != targetPid) return true;
            StringBuilder cls = new StringBuilder(256);
            GetClassName(hwnd, cls, cls.Capacity);
            if (cls.ToString().StartsWith("#32770"))
            {
                found = hwnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);

        if (found == IntPtr.Zero) return false;
        SetWindowPos(found, new IntPtr(-1), 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW);
        ShowWindow(found, 5);
        SetForegroundWindow(found);
        return true;
    }
}
'@

[System.Windows.Forms.Application]::EnableVisualStyles()

$initial = [string]$env:DST_PICK_INIT
$description = [string]$env:DST_PICK_DESC
$filter = [string]$env:DST_PICK_FILTER
if ([string]::IsNullOrWhiteSpace($description)) { $description = '选择材质包' }
if ([string]::IsNullOrWhiteSpace($filter)) { $filter = 'Minecraft 材质包 (*.zip)|*.zip|所有文件 (*.*)|*.*' }

$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = $description
$dialog.Filter = $filter
$dialog.FilterIndex = 1
$dialog.Multiselect = $false
$dialog.CheckFileExists = $true
$dialog.RestoreDirectory = $true
if (-not [string]::IsNullOrWhiteSpace($initial)) {
    if (Test-Path -LiteralPath $initial -PathType Leaf) {
        $dialog.InitialDirectory = Split-Path -Parent $initial
        $dialog.FileName = $initial
    } elseif (Test-Path -LiteralPath $initial -PathType Container) {
        $dialog.InitialDirectory = $initial
    }
}

[NativeFileDialogZOrder]::BringToFrontSoon([System.Diagnostics.Process]::GetCurrentProcess().Id)
$result = $dialog.ShowDialog()

if ($result -eq [System.Windows.Forms.DialogResult]::OK -and -not [string]::IsNullOrWhiteSpace($dialog.FileName)) {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::Write($dialog.FileName)
}
"""


def pick_folder(initial: str = "", description: str = "", timeout_seconds: float = 300) -> Optional[str]:
    """Show the Windows folder picker and return the selected path.

    Returns ``None`` when the user cancels.  On non-Windows platforms this
    raises ``RuntimeError`` because the app is Windows-only.
    """
    if os.name != "nt":
        raise RuntimeError("系统文件夹选择框仅支持 Windows")

    encoded = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = os.environ.copy()
    env["DST_PICK_INIT"] = str(initial or "")
    env["DST_PICK_DESC"] = str(description or "")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=float(timeout_seconds),
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError("系统文件夹选择框打开失败" + (("：" + detail) if detail else ""))

    selected = (completed.stdout or "").strip().strip('"')
    if not selected:
        return None
    selected = os.path.abspath(selected)
    if not os.path.isdir(selected):
        raise RuntimeError("选择的路径不是文件夹：" + selected)
    return selected


def pick_file(
    initial: str = "",
    description: str = "",
    filter_text: str = "",
    timeout_seconds: float = 300,
) -> Optional[str]:
    """Show the Windows file picker and return the selected path.

    Used for standalone resource packs, which are normally distributed as
    ``.zip`` files.
    """
    if os.name != "nt":
        raise RuntimeError("系统文件选择框仅支持 Windows")

    encoded = base64.b64encode(_PS_FILE_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = os.environ.copy()
    env["DST_PICK_INIT"] = str(initial or "")
    env["DST_PICK_DESC"] = str(description or "")
    env["DST_PICK_FILTER"] = str(filter_text or "")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=float(timeout_seconds),
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError("系统文件选择框打开失败" + (("：" + detail) if detail else ""))

    selected = (completed.stdout or "").strip().strip('"')
    if not selected:
        return None
    selected = os.path.abspath(selected)
    if not os.path.isfile(selected):
        raise RuntimeError("选择的路径不是文件：" + selected)
    return selected
