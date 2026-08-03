"""Windows (win32) native backend."""

from __future__ import annotations

import subprocess
from typing import List, Optional

import psutil
import win32api
import win32con
import win32gui
import win32process

from .base import NativeBackend, WindowInfo


class WindowsBackend(NativeBackend):
    name = "windows"

    def _process_name(self, pid: int) -> str:
        try:
            return psutil.Process(pid).name().lower()
        except Exception:
            return ""

    def list_windows(self, only_visible: bool = True) -> List[WindowInfo]:
        results: List[WindowInfo] = []

        def callback(hwnd, _):
            if only_visible and not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            results.append(WindowInfo(hwnd, title, pid, self._process_name(pid)))

        win32gui.EnumWindows(callback, None)
        return results

    def activate_window(self, handle: object) -> bool:
        hwnd = handle
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            current_thread = win32api.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            attached = False
            if current_thread != target_thread:
                try:
                    win32process.AttachThreadInput(current_thread, target_thread, True)
                    attached = True
                except Exception:
                    attached = False
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                # Windows often blocks SetForegroundWindow from a background
                # thread — not fatal; the user can still alt-tab.
                pass
            finally:
                if attached:
                    try:
                        win32process.AttachThreadInput(current_thread, target_thread, False)
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    def open_terminal(self) -> bool:
        for exe in ("wt.exe", "cmd.exe"):
            try:
                subprocess.Popen([exe], shell=False, creationflags=subprocess.CREATE_NEW_CONSOLE)
                return True
            except Exception:
                continue
        return False

    def open_terminal_running(self, command, title="", cwd=None) -> bool:
        # CREATE_NEW_CONSOLE gives the child its own console with a live stdin,
        # so the wrapper can pipe the agent and inject Allow/Deny.
        try:
            subprocess.Popen(
                list(command),
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return True
        except Exception as exc:
            print(f"[native] open_terminal_running failed: {exc}")
            return False

    def default_font_family(self) -> str:
        return "Segoe UI"
