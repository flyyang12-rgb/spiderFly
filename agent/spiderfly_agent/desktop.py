"""Current-user tray and compact run bar for the Windows Agent."""

from __future__ import annotations

import queue
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw
import pystray


GREEN = "#009139"
INK = "#221814"
MUTED = "#757A75"
BORDER = "#DFE3DF"
SURFACE = "#FFFFFF"
PALE = "#F7F8F6"
CORAL = "#E16B51"


def elapsed_label(started_at) -> str:
    seconds = max(0, int(time.monotonic() - started_at)) if started_at else 0
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def tray_label(snapshot: dict) -> str:
    if snapshot.get("run_id"):
        return f"运行中 · {snapshot.get('task_name') or '任务'}"
    state = snapshot.get("host_state")
    if state == "pending":
        return "等待管理员批准"
    if state in {"rejected", "revoked"}:
        return "接入已撤销"
    if state == "uncertain":
        return "状态待确认"
    if snapshot.get("connection") == "error":
        if "升级" in snapshot.get("error", "") or "已过旧" in snapshot.get("error", ""):
            return "需要升级 Agent"
        return "连接异常"
    if snapshot.get("connection") == "connecting":
        return "正在连接"
    if not snapshot.get("interactive"):
        return "桌面不可用"
    return "调度空闲" if snapshot.get("dispatch") else "非调度"


def _icon_image(color=GREEN):
    image = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((7, 7, 57, 57), fill=color)
    draw.ellipse((20, 17, 36, 33), fill="white")
    draw.ellipse((30, 29, 46, 45), fill="white")
    draw.line((27, 32, 38, 43), fill="white", width=5)
    return image


class DesktopApp:
    """Tk owns the user session UI; Client remains the execution authority."""

    def __init__(self, client):
        self.client = client
        self.commands = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("SpiderFly Agent")
        self.root.configure(bg=SURFACE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", lambda: self.commands.put(("exit", None)))
        self.task = tk.StringVar(value="")
        self.detail = tk.StringVar(value="")
        self.elapsed = tk.StringVar(value="00:00:00")
        self._build_bar()
        self.icon = pystray.Icon("SpiderFlyAgent", _icon_image(), "SpiderFly Agent", self._menu())
        self.thread = threading.Thread(target=self.client.run, name="spiderfly-client", daemon=True)
        self.closing = False
        self.visible = False

    def _build_bar(self):
        shell = tk.Frame(self.root, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        shell.pack(fill="both", expand=True)
        tk.Frame(shell, bg=GREEN, width=5).pack(side="left", fill="y")
        content = tk.Frame(shell, bg=SURFACE)
        content.pack(side="left", fill="both", expand=True, padx=(14, 10), pady=8)
        tk.Label(content, textvariable=self.task, bg=SURFACE, fg=INK,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(content, textvariable=self.detail, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(2, 0))
        tk.Label(shell, textvariable=self.elapsed, bg=SURFACE, fg=INK,
                 font=("Consolas", 10), width=9).pack(side="left", padx=8)
        self._button(shell, "停止", self._stop, PALE, INK).pack(side="left", padx=4, pady=10)
        self._button(shell, "紧急接管", self._takeover, "#FFF1ED", CORAL).pack(
            side="left", padx=(4, 12), pady=10)

    @staticmethod
    def _button(parent, text, command, background, foreground):
        return tk.Button(parent, text=text, command=command, bg=background, fg=foreground,
                         activebackground=background, activeforeground=foreground,
                         relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                         font=("Segoe UI", 9), highlightthickness=1,
                         highlightbackground=BORDER, takefocus=True)

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda _item: tray_label(self.client.snapshot()), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开启调度", lambda *_: self.commands.put(("dispatch", True)),
                             checked=lambda _item: self.client.snapshot()["dispatch"], radio=True),
            pystray.MenuItem("非调度", lambda *_: self.commands.put(("dispatch", False)),
                             checked=lambda _item: not self.client.snapshot()["dispatch"], radio=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("停止当前任务", lambda *_: self.commands.put(("stop", None)),
                             enabled=lambda _item: bool(self.client.snapshot()["run_id"])),
            pystray.MenuItem("紧急接管", lambda *_: self.commands.put(("takeover", None)),
                             enabled=lambda _item: bool(self.client.snapshot()["run_id"])),
            pystray.MenuItem("退出 Agent", lambda *_: self.commands.put(("exit", None))),
        )

    def _stop(self):
        if self.client.request_stop():
            self.detail.set("正在安全停止任务并等待进程树退出…")

    def _takeover(self):
        if not messagebox.askyesno("紧急接管", "将停止当前任务并切换为非调度模式。是否继续？",
                                   parent=self.root):
            return
        if self.client.request_stop(emergency=True):
            self.detail.set("正在停止任务；确认进程树退出后交还桌面…")

    def _dispatch(self, enabled):
        if self.client.set_dispatch(enabled):
            self.icon.update_menu()
            return
        messagebox.showwarning("运行中无法切换", "请先停止当前任务，或使用“紧急接管”。", parent=self.root)

    def _exit(self):
        if self.client.snapshot().get("run_id") and not messagebox.askyesno(
                "退出 Agent", "退出会停止当前任务。是否继续？", parent=self.root):
            return
        self.closing = True
        self.client.shutdown.set()

    def _position(self):
        self.root.update_idletasks()
        width = min(760, max(560, self.root.winfo_screenwidth() - 48))
        height = 62
        x = max(16, (self.root.winfo_screenwidth() - width) // 2)
        self.root.geometry(f"{width}x{height}+{x}+12")
        self.root.update_idletasks()

    def _show_without_activation(self):
        if self.visible:
            return
        if os.name != "nt":
            self.root.deiconify()
            self.visible = True
            return
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetParent.argtypes = [wintypes.HWND]
        user.GetParent.restype = wintypes.HWND
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        hwnd = wintypes.HWND(self.root.winfo_id())
        while True:
            parent = user.GetParent(hwnd)
            if not parent:
                break
            hwnd = parent
        # SW_SHOWNOACTIVATE plus SWP_NOACTIVATE keeps browser/Excel focus intact.
        user.ShowWindow(hwnd, 4)
        user.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)
        self.visible = True

    def _tick(self):
        while True:
            try:
                action, value = self.commands.get_nowait()
            except queue.Empty:
                break
            if action == "dispatch":
                self._dispatch(value)
            elif action == "stop":
                self._stop()
            elif action == "takeover":
                self._takeover()
            elif action == "exit":
                self._exit()
        snapshot = self.client.snapshot()
        if snapshot.get("run_id"):
            self.task.set(snapshot.get("task_name") or f"远程运行 #{snapshot['run_id']}")
            self.detail.set(snapshot.get("progress") or snapshot.get("run_state") or "正在处理")
            self.elapsed.set(elapsed_label(snapshot.get("started_at")))
            self._position()
            self._show_without_activation()
        else:
            if self.visible:
                self.root.withdraw()
                self.visible = False
        self.icon.title = f"SpiderFly · {tray_label(snapshot)}"[:63]
        if self.closing and not self.thread.is_alive():
            self.icon.stop()
            self.root.destroy()
            return
        self.root.after(250, self._tick)

    def run(self):
        self.thread.start()
        threading.Thread(target=self.icon.run, name="spiderfly-tray", daemon=True).start()
        self.root.after(100, self._tick)
        self.root.mainloop()
        self.thread.join(timeout=15)
