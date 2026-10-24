#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pet_runtime.py - macOS 版：实例独占、控制通道与全局热键。

Windows 版用 CreateMutexW + CreateEventW 实现。macOS 上换成 Unix domain
socket：抢占 socket 文件即"独占实例"，往 socket 写一行动作就是"控制通道"。

对外接口与 Windows 版保持一致：
    ACTIONS / project_processes / signal_instance / stop_project
    GlobalHotkeys / InstanceGuard
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time

import psutil

from pet_paths import DATA_ROOT as ROOT

ACTIONS = ("show", "quit", "laugh", "copy", "paste", "say")
_HOTKEY_ACTIONS = {18: "laugh", 8: "copy", 9: "paste", 12: "quit"}

_HOTKEY_LOG = Path("/tmp/naiwa-hotkey.log")


def _ax_trusted(prompt=False):
    """是否已获"辅助功能"授权（决定全局热键能否收到按键）。

    prompt=True 时会调用 AXIsProcessTrustedWithOptions 并带上提示选项，
    macOS 会弹系统对话框，同时把本程序加进"辅助功能"列表。
    """
    try:
        import ctypes
        from ctypes import POINTER, c_bool, c_char_p, c_void_p

        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices")
        ax.AXIsProcessTrusted.restype = c_bool
        if not prompt:
            return ax.AXIsProcessTrusted()

        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, ctypes.c_uint32]
        cf.CFDictionaryCreate.restype = c_void_p
        cf.CFDictionaryCreate.argtypes = [c_void_p, POINTER(c_void_p), POINTER(c_void_p),
                                          ctypes.c_long, c_void_p, c_void_p]
        cf.CFRelease.argtypes = [c_void_p]

        def address(name):
            return ctypes.addressof((ctypes.c_char * 64).in_dll(cf, name))

        key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", 0x08000100)
        keys = (c_void_p * 1)(key)
        values = (c_void_p * 1)(ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value)
        options = cf.CFDictionaryCreate(
            None, keys, values, 1,
            address("kCFTypeDictionaryKeyCallBacks"),
            address("kCFTypeDictionaryValueCallBacks"))
        ax.AXIsProcessTrustedWithOptions.restype = c_bool
        ax.AXIsProcessTrustedWithOptions.argtypes = [c_void_p]
        trusted = ax.AXIsProcessTrustedWithOptions(options)
        cf.CFRelease(options)
        cf.CFRelease(key)
        return trusted
    except Exception:
        return None


_NOTICE_TIMER = None


def _defer_accessibility_notice(delay=2.0):
    """等主窗口和事件循环起来之后再弹窗，避免卡住启动。"""
    global _NOTICE_TIMER
    try:
        from AppKit import NSTimer

        _NOTICE_TIMER = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            delay, False, lambda timer: _ask_accessibility())
    except Exception:
        _ask_accessibility()


def _ask_accessibility():
    """首次发现没有辅助功能授权时，弹一次说明并提供直达设置入口。"""
    marker = ROOT / ".cache" / "ax-prompt-shown"
    if marker.exists():
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
        # 先让系统弹它自己的授权对话框：点"打开系统设置"后本程序会出现在列表里。
        if _ax_trusted(prompt=True):
            return
        from AppKit import NSAlert, NSWorkspace

        alert = NSAlert.alloc().init()
        alert.setMessageText_("奶小蛙需要辅助功能授权")
        alert.setInformativeText_(
            "授权后才能使用全局快捷键：Control+Option+C 复制、V 粘贴、Q 退出、1 显示。\n"
            "右键菜单和其它功能不受影响。\n\n"
            "系统设置 → 隐私与安全性 → 辅助功能，勾选“奶小蛙”。")
        alert.addButtonWithTitle_("打开设置")
        alert.addButtonWithTitle_("稍后")
        if alert.runModal() == 1000:
            NSWorkspace.sharedWorkspace().openURL_(
                __import__("AppKit").NSURL.URLWithString_(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))
    except Exception as error:
        from pet_app import log
        log(f"辅助功能提示弹窗失败: {error}")


def _hotkey_debug(text):
    """NAIWA_HOTKEY_DEBUG=1 时记录热键注册与收到的按键，便于排查授权问题。"""
    if os.environ.get("NAIWA_HOTKEY_DEBUG") != "1":
        return
    try:
        with open(_HOTKEY_LOG, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    except OSError:
        pass


def _socket_path(root=ROOT) -> Path:
    # 放在 /tmp：AF_UNIX 的路径上限约 103 字节，项目目录可能很长。
    identity = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path("/tmp") / ("naiwa-%s.sock" % identity)


def project_processes(root=ROOT):
    """当前项目的 pet_app.py / run_mac.py 进程（psutil，跨平台）。"""
    root = Path(root).resolve()
    entries = {str(root / "pet_app.py"), str(root / "run_mac.py"),
               str(root / "pet.pyw")}
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = (process.info["name"] or "").lower()
            if not name.startswith(("python", "naiwa")):
                continue
            for arg in process.cmdline()[1:]:
                if not arg.lower().endswith((".py", ".pyw")):
                    continue
                entry = Path(arg)
                if not entry.is_absolute():
                    entry = Path(process.cwd()) / entry
                if str(entry.resolve()) in entries:
                    yield process
                    break
        except (OSError, ValueError, psutil.Error):
            continue


def signal_instance(action, root=ROOT) -> bool:
    """给已经在运行的实例发一个动作。"""
    if action not in ACTIONS:
        raise ValueError(action)
    path = _socket_path(root)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(path))
            client.sendall((action + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def stop_project(root=ROOT, timeout=4.0) -> bool:
    processes = list(project_processes(root))
    signal_instance("quit", root)
    _, alive = psutil.wait_procs(processes, timeout=timeout)
    valid = {p.pid: p.create_time() for p in project_processes(root)}
    for process in alive:
        try:
            if valid.get(process.pid) != process.create_time():
                continue
            children = process.children(recursive=True)
            process.terminate()
            for child in children:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(alive, timeout=2)
    return not alive


class GlobalHotkeys:
    """Control+Option+1/C/V/Q。

    主路径用 CGEventTap 的"只监听"模式：能收到其它 App 的按键，且不改写事件。
    没有"辅助功能"授权时 tap 建不起来，退回 NSEvent 全局监听。
    两者都不可用时程序照常运行，只是快捷键无效（右键菜单不受影响）。
    """

    def __init__(self, keys=(18, 12, 8, 9)):
        self.keys = tuple(keys)
        self.messages = queue.SimpleQueue()
        self.monitor = None
        self.tap = None
        self.source = None
        self._callback = None
        self._retry = None
        self.available = False
        self._start()

    def _start(self):
        if self._start_carbon():
            return
        if self._start_tap():
            return
        self._start_monitor()
        _defer_accessibility_notice()
        self._watch_permission()

    def _start_carbon(self):
        """Carbon 的 RegisterEventHotKey：系统级热键，不需要"辅助功能"授权。"""
        try:
            import ctypes
            from ctypes import (CFUNCTYPE, POINTER, Structure, byref, c_int32,
                                c_ulong, c_uint32, c_void_p)

            carbon = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/Carbon.framework/Carbon")

            def fourcc(text):
                return int.from_bytes(text.encode("ascii"), "big")

            class EventHotKeyID(Structure):
                _fields_ = [("signature", c_uint32), ("id", c_uint32)]

            class EventTypeSpec(Structure):
                _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]

            handler_type = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
            signature = fourcc("naiw")

            carbon.GetApplicationEventTarget.restype = c_void_p
            carbon.InstallEventHandler.restype = c_int32
            carbon.InstallEventHandler.argtypes = [
                c_void_p, handler_type, c_ulong, POINTER(EventTypeSpec),
                c_void_p, POINTER(c_void_p)]
            carbon.RegisterEventHotKey.restype = c_int32
            carbon.RegisterEventHotKey.argtypes = [
                c_uint32, c_uint32, EventHotKeyID, c_void_p, c_uint32,
                POINTER(c_void_p)]
            carbon.GetEventParameter.restype = c_int32
            carbon.GetEventParameter.argtypes = [
                c_void_p, c_uint32, c_uint32, c_void_p, c_ulong, c_void_p, c_void_p]
            # 与 Windows 版一致：1=大笑、Q=退出、C=复制、V=粘贴。
            pairs = ((18, "laugh"), (12, "quit"), (8, "copy"), (9, "paste"))
            self._carbon_ids = {index + 1: action
                                for index, (_code, action) in enumerate(pairs)}

            def on_hotkey(call_ref, event, user_data):
                try:
                    hotkey = EventHotKeyID()
                    carbon.GetEventParameter(
                        event, fourcc("----"), fourcc("hkid"), None,
                        ctypes.sizeof(hotkey), None, byref(hotkey))
                    action = self._carbon_ids.get(hotkey.id)
                    if action is not None:
                        _hotkey_debug("carbon key id=%s action=%s" % (hotkey.id, action))
                        self.messages.put(action)
                except Exception:
                    pass
                return 0

            self._carbon_handler = handler_type(on_hotkey)
            target = carbon.GetApplicationEventTarget()
            event_types = (EventTypeSpec * 1)(
                EventTypeSpec(fourcc("keyb"), 5))       # kEventClassKeyboard / kEventHotKeyPressed
            target_ref = c_void_p()
            status = carbon.InstallEventHandler(
                c_void_p(target), self._carbon_handler, 1, event_types, None,
                byref(target_ref))
            if status != 0:
                _hotkey_debug("carbon InstallEventHandler failed status=%s" % status)
                return False
            self._carbon_target = target_ref

            self._carbon_keys = []
            for index, (code, _action) in enumerate(pairs):
                option_ref = c_void_p()
                status = carbon.RegisterEventHotKey(
                    c_uint32(code), c_uint32(0x1000 | 0x0800),   # control | option
                    EventHotKeyID(signature, c_uint32(index + 1)), c_void_p(target),
                    c_uint32(0), byref(option_ref))
                if status != 0:
                    _hotkey_debug("carbon RegisterEventHotKey code=%s failed status=%s"
                                  % (code, status))
                    return False
                self._carbon_keys.append(option_ref)
            self.available = True
            _hotkey_debug("carbon registered trusted=%s" % _ax_trusted())
            return True
        except Exception as error:
            _hotkey_debug("carbon unavailable %r" % error)
            return False

    def _watch_permission(self):
        """用户授权后无需重启：每 5 秒试建一次 tap，成功就停。"""
        try:
            from AppKit import NSTimer

            def tick(timer):
                if self._start_tap():
                    self._retry = None
                    timer.invalidate()

            self._retry = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                5.0, True, tick)
        except Exception:
            pass

    def _start_tap(self):
        try:
            import Quartz
            from CoreFoundation import (CFRunLoopAddSource, CFRunLoopGetMain,
                                        kCFRunLoopCommonModes)

            def callback(proxy, event_type, event, refcon):
                try:
                    if event_type == Quartz.kCGEventKeyDown:
                        flags = Quartz.CGEventGetFlags(event)
                        repeat = Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGKeyboardEventAutorepeat)
                        both = (flags & Quartz.kCGEventFlagMaskControl
                                and flags & Quartz.kCGEventFlagMaskAlternate)
                        if both and not repeat:
                            code = int(Quartz.CGEventGetIntegerValueField(
                                event, Quartz.kCGKeyboardEventKeycode))
                            action = _HOTKEY_ACTIONS.get(code)
                            if action is not None:
                                _hotkey_debug("tap key code=%s flags=%s" % (code, flags))
                                self.messages.put(action)
                except Exception:
                    pass
                return event

            mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
            if tap is None:
                _hotkey_debug("tap create failed trusted=%s" % _ax_trusted())
                return False
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self.tap, self.source, self._callback = tap, source, callback
            self.available = True
            _hotkey_debug("tap registered trusted=%s" % _ax_trusted())
            return True
        except Exception as error:
            _hotkey_debug("tap unavailable %r" % error)
            return False

    def _start_monitor(self):
        try:
            from AppKit import (NSEvent, NSEventMaskKeyDown, NSEventModifierFlagControl,
                                NSEventModifierFlagOption)

            def handler(event):
                try:
                    _hotkey_debug(
                        "key code=%s flags=%s" % (event.keyCode(), event.modifierFlags()))
                    flags = event.modifierFlags()
                    if not (flags & NSEventModifierFlagControl):
                        return
                    if not (flags & NSEventModifierFlagOption):
                        return
                    action = _HOTKEY_ACTIONS.get(event.keyCode())
                    if action is not None:
                        self.messages.put(action)
                except Exception:
                    pass

            self.monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, handler)
            self.available = self.monitor is not None
            _hotkey_debug("monitor registered available=%s" % self.available)
        except Exception as error:
            from pet_app import log
            log(f"全局热键不可用(不影响右键菜单): {error}")

    def close(self):
        tap, self.tap = self.tap, None
        source, self.source = self.source, None
        if tap is not None:
            try:
                import Quartz
                from CoreFoundation import (CFRunLoopGetMain, CFRunLoopRemoveSource,
                                            kCFRunLoopCommonModes)

                Quartz.CGEventTapEnable(tap, False)
                if source is not None:
                    CFRunLoopRemoveSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
            except Exception:
                pass
        self._callback = None
        monitor, self.monitor = self.monitor, None
        if monitor is not None:
            try:
                from AppKit import NSEvent
                NSEvent.removeMonitor_(monitor)
            except Exception:
                pass


class InstanceGuard:
    """单实例锁 + 控制通道。第二个实例启动时把"显示"转发给第一个并退出。"""

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = _socket_path(self.root)
        self.metadata = self.root / ".cache" / "pet-instance.json"
        self.hotkeys = None
        self._server = None
        self._thread = None
        self._stop = threading.Event()
        self._inbox = queue.SimpleQueue()

    # ------------------------------------------------------------------ 获取
    def acquire(self) -> bool:
        if self._probe():
            signal_instance("show", self.root)
            return False
        try:
            self.path.unlink()
        except OSError:
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.path))
            server.listen(8)
            server.settimeout(0.5)
        except OSError:
            server.close()
            return False
        self._server = server
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="pet-control")
        self._thread.start()
        self._write_metadata()
        return True

    @staticmethod
    def _probe() -> bool:
        path = _socket_path()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.5)
                client.connect(str(path))
                return True
        except OSError:
            return False

    def _write_metadata(self):
        try:
            self.metadata.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.metadata.with_suffix(".tmp")
            temporary.write_text(json.dumps(
                {"pid": os.getpid(), "created": time.time(),
                 "root": str(self.root.resolve())}), encoding="utf-8")
            temporary.replace(self.metadata)
        except OSError:
            pass

    def _serve(self):
        server = self._server
        while not self._stop.is_set() and server is not None:
            try:
                connection, _address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(0.5)
                try:
                    data = connection.recv(64)
                except OSError:
                    data = b""
            text = data.decode("utf-8", "replace").strip()
            if text in ACTIONS:
                self._inbox.put(text)

    # ------------------------------------------------------------------ 热键
    def start_hotkeys(self):
        self.hotkeys = GlobalHotkeys()

    # ------------------------------------------------------------------ 查询
    def pending(self):
        actions = []
        while True:
            try:
                actions.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        if self.hotkeys is not None:
            while not self.hotkeys.messages.empty():
                actions.append(self.hotkeys.messages.get())
        if actions:
            _hotkey_debug("pending %s" % actions)
        return actions

    # ------------------------------------------------------------------ 释放
    def close(self):
        if self.hotkeys is not None:
            self.hotkeys.close()
            self.hotkeys = None
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        try:
            self.path.unlink()
        except OSError:
            pass
        try:
            data = json.loads(self.metadata.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.metadata.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
