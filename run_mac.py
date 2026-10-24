#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_mac.py - macOS 启动器。

做三件事：
1. 把 tkinter / PIL.ImageTk 换成 mac_tk 里的原生窗口实现；
2. 给源码打几个必要的平台补丁（工作区、透明帧、说明窗口、初始位置）；
3. 调用未改动的 pet_app.main()。

pet_app.py / pet_group.py 一行都不用改，逻辑与 Windows 版完全共用。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# 便携包（.app 内自带 Python）：可写状态放到用户目录，别写进只读的 app 包。
import pet_paths  # noqa: E402

_data_dir = os.environ.get("NAIWA_DATA_DIR")
if _data_dir:
    pet_paths.DATA_ROOT = Path(_data_dir).expanduser().resolve()

# ---- 1. 换窗口层（必须在 import pet_app 之前）--------------------------------
import mac_tk

sys.modules["tkinter"] = mac_tk
sys.modules["tkinter.ttk"] = mac_tk.ttk
sys.modules["tkinter.scrolledtext"] = mac_tk.scrolledtext
sys.modules["PIL.ImageTk"] = mac_tk

import pet_app
import pet_group
from AppKit import NSScreen


# ---- 2. 平台补丁 -------------------------------------------------------------
def mac_work_area(point, fallback=None):
    """最近的屏幕工作区，返回 (left, top, right, bottom)，左上原点。

    pet_group.work_area 在非 Windows 上直接返回 fallback (1920x1080)，
    物理地面会跑到屏幕外面，所以这里必须替换。
    """
    height = mac_tk.primary_height()
    screens = list(NSScreen.screens())
    chosen = screens[0] if screens else NSScreen.mainScreen()
    cocoa_y = height - float(point[1])
    for screen in screens:
        frame = screen.frame()
        x0, y0 = float(frame.origin.x), float(frame.origin.y)
        if (x0 <= float(point[0]) < x0 + float(frame.size.width)
                and y0 <= cocoa_y < y0 + float(frame.size.height)):
            chosen = screen
            break
    visible = chosen.visibleFrame()
    left = float(visible.origin.x)
    top = height - (float(visible.origin.y) + float(visible.size.height))
    right = left + float(visible.size.width)
    bottom = top + float(visible.size.height)
    return round(left), round(top), round(right), round(bottom)


def mac_initial_position(self):
    """默认停在工作区右下角，脚底贴近工作区底边。"""
    left, top, right, bottom = mac_work_area((0, 0))
    x = max(left, right - self.win_w - 40)
    y = max(top, bottom - self.win_h + self.pad_y - 8)
    return int(x), int(y)


def mac_show_help(self):
    """用原生 NSTextView 窗口替代 ttk + ScrolledText 的说明面板。"""
    if self.closed:
        return
    window = self.help_window
    if window is not None and window.winfo_exists():
        window.deiconify()
        window.lift()
        window.focus_force()
        return

    def close_help():
        self.help_window = None

    self.help_window = mac_tk.show_text_window(
        "奶小蛙使用说明", pet_group.USAGE_TEXT, on_close=close_help)


def mac_chroma_frame(rgba):
    """原版把透明处涂成洋红供 Windows 抠色；这里保留 RGBA 让窗口自己透明。"""
    return rgba, pet_app._opaque_mask(rgba)


def _install_trace():
    """NAIWA_TRACE=1：记录谁触发了分身喷发，用于排查自动增殖。"""
    import traceback

    original = pet_group.PetGroup.request_split

    def traced_split(self, source, now=None):
        with open("/tmp/naiwa-split-trace.log", "a") as handle:
            handle.write("---- request_split %s\n" % time.strftime("%H:%M:%S"))
            handle.write("".join(traceback.format_stack(limit=10)))
        return original(self, source, now)

    pet_group.PetGroup.request_split = traced_split


def _patch_instance_guard():
    """便携包里单实例锁放在用户目录，而不是 app 包内部。"""
    if pet_paths.DATA_ROOT == pet_paths.ROOT:
        return
    import pet_runtime
    original = pet_runtime.InstanceGuard

    class PortableGuard(original):
        def __init__(self, root=None):
            super().__init__(pet_paths.DATA_ROOT if root is None else root)

    pet_runtime.InstanceGuard = PortableGuard


def install():
    _patch_instance_guard()
    pet_app.work_area = mac_work_area
    pet_group.work_area = mac_work_area
    pet_app._chroma_frame = mac_chroma_frame
    pet_app.PetApp._initial_position = mac_initial_position
    pet_group.PetGroup.show_help = mac_show_help
    budget = int(os.environ.get("NAIWA_CACHE_MB", "128"))
    pet_app.FRAME_CACHE_BYTES = budget * 1024 * 1024
    if os.environ.get("NAIWA_TRACE") == "1":
        _install_trace()


def main() -> int:
    install()
    return pet_app.main()


if __name__ == "__main__":
    sys.exit(main())
