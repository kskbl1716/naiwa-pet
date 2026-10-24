#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mac_tk.py - macOS 上的 tkinter 兼容层（AppKit + Quartz 原生窗口）。

为什么需要它：Tk 8.6/9 的 aqua 后端不支持 -transparentcolor，无法做"洋红抠色"
式的逐像素透明窗口。这里用同一套 tkinter 风格 API 直接驱动无边框 NSWindow：
窗口不透明关掉、背景清空、位图放在一个 CALayer 里，alpha 天然生效。

支持范围只覆盖 pet_app.py / pet_group.py 真正用到的子集，不是完整 tkinter。
"""

from __future__ import annotations

import os
import re
import sys
import time
import traceback

import objc
import Quartz
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered, NSBezelBorder, NSColor, NSEvent, NSFont,
    NSMakePoint, NSMakeRect, NSMenu, NSMenuItem, NSScreen, NSScrollView,
    NSTextView, NSTimer, NSWindow, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
    NSRunLoop, NSRunLoopCommonModes, NSFloatingWindowLevel,
    NSView, NSViewHeightSizable, NSViewWidthSizable, NSObject,
)
from PIL import Image

# 和 pet_app.ALPHA_CUTOFF 保持一致：命中判定用同一个阈值。
ALPHA_CUT = 110
_TICK_SEC = 0.004          # 定时器轮询间隔：兼顾 10ms 主循环的抖动
_HOVER_SEC = 0.02          # 鼠标穿透判定频率

_APP = None
_WINDOWS: list = []
_MENUS: list = []
_PRIMARY_H = 900.0
_CURRENT_EVENT = None
_PRESSED = None

_HOVER_TARGET = [None]
_DEBUG = bool(os.environ.get("NAIWA_DEBUG"))
_DEBUG_PATH = "/tmp/naiwa-mac-debug.log"


def _dbg(message: str) -> None:
    """调试日志：只在 NAIWA_DEBUG=1 时写文件，正常使用零开销。"""
    if not _DEBUG:
        return
    try:
        with open(_DEBUG_PATH, "a", encoding="utf-8") as handle:
            handle.write("%.3f %s\n" % (time.time(), message))
    except OSError:
        pass


class TclError(Exception):
    """tkinter.TclError 的替身（pet_app 会捕获它）。"""


# --------------------------------------------------------------------------- 屏幕
def primary_height() -> float:
    """主屏高度（point）。Cocoa 原点在左下，Tk/Windows 在左上，换算要用它。"""
    return _PRIMARY_H


def _refresh_primary() -> None:
    global _PRIMARY_H
    try:
        screens = NSScreen.screens()
        if screens:
            _PRIMARY_H = float(screens[0].frame().size.height)
    except Exception:
        pass


def screen_size() -> tuple[int, int]:
    try:
        frame = NSScreen.mainScreen().frame().size
        return int(frame.width), int(frame.height)
    except Exception:
        return 1440, 900


def _ensure_app():
    global _APP
    if _APP is None:
        _APP = NSApplication.sharedApplication()
        # Accessory：不占 Dock、不抢菜单栏，符合桌宠的定位。
        _APP.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        _refresh_primary()
        _start_hover_timer()
    return _APP


# --------------------------------------------------------------------------- 定时器
class _Scheduler:
    """把 tkinter 的 after/after_idle 映射到一个复用的 NSTimer 上。"""

    def __init__(self):
        self._items: dict[str, tuple[float, int, object]] = {}
        self._seq = 0
        self._timer = None

    def _arm(self):
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_repeats_block_(
                _TICK_SEC, True, self._fire)
            NSRunLoop.currentRunLoop().addTimer_forMode_(
                self._timer, NSRunLoopCommonModes)

    def _fire(self, _timer):
        try:
            self._run_due()
        except Exception:
            traceback.print_exc()

    def _run_due(self):
        now = time.monotonic()
        due = [(deadline, seq, ident)
               for ident, (deadline, seq, _cb) in self._items.items()
               if deadline <= now]
        if not due:
            return
        due.sort()
        for _deadline, _seq, ident in due:
            entry = self._items.pop(ident, None)
            if entry is None:
                continue
            try:
                entry[2]()
            except Exception:
                traceback.print_exc()

    def after(self, ms, callback):
        self._seq += 1
        ident = "after#%d" % self._seq
        delay = max(0.0, float(ms)) / 1000.0
        self._items[ident] = (time.monotonic() + delay, self._seq, callback)
        self._arm()
        return ident

    def after_idle(self, callback):
        return self.after(0, callback)

    def after_cancel(self, ident):
        self._items.pop(ident, None)


SCHED = _Scheduler()


# --------------------------------------------------------------------------- 位图
def _to_cgimage(image):
    """PIL RGBA -> CGImage。必须预乘 alpha，否则边缘会发黑。"""
    rgba = image.convert("RGBa")
    data = rgba.tobytes()
    provider = Quartz.CGDataProviderCreateWithCFData(data)
    return Quartz.CGImageCreate(
        rgba.width, rgba.height, 8, 32, rgba.width * 4,
        Quartz.CGColorSpaceCreateDeviceRGB(),
        Quartz.kCGImageAlphaPremultipliedLast,
        provider, None, False, Quartz.kCGRenderingIntentDefault)


class PhotoImage:
    """ImageTk.PhotoImage 的替身：持有 PIL 图，第一次上屏时才转 CGImage。"""

    def __init__(self, image=None, master=None, **kw):
        if image is None:
            raise TclError("PhotoImage 需要 PIL 图像")
        self._width = int(image.width)
        self._height = int(image.height)
        self._image = image
        self._cg = None
        self._alpha = None
        self._master = master

    def width(self):
        return self._width

    def height(self):
        return self._height

    def cgimage(self):
        if self._cg is None:
            image = self._image
            self._alpha = image.getchannel("A").tobytes()
            self._cg = _to_cgimage(image)
            # 只留 alpha 供逐像素命中，RGB 交给 CGImage 持有，省一半内存。
            self._image = None
        return self._cg

    def alpha_at(self, x, y):
        if self._alpha is None:
            if self._image is None:
                return 0
            self.cgimage()
        if 0 <= x < self._width and 0 <= y < self._height:
            return self._alpha[y * self._width + x]
        return 0


class _Event:
    """合成事件：pet_app 只用到 x / y / x_root / y_root 四个属性。"""

    __slots__ = ("x", "y", "x_root", "y_root", "widget", "type")

    def __init__(self, x, y, x_root, y_root, widget=None, type=None):
        self.x, self.y = x, y
        self.x_root, self.y_root = x_root, y_root
        self.widget = widget
        self.type = type


# --------------------------------------------------------------------------- 菜单
class _MenuTarget(NSObject):
    """菜单项回调的 ObjC 目标（NSMenuItem 只持弱引用，Python 侧要保活）。"""

    def initWithCallback_(self, callback):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def fire_(self, _sender):
        callback = self._callback
        if callback is None:
            return
        try:
            callback()
        except Exception:
            traceback.print_exc()


class IntVar:
    def __init__(self, master=None, value=0, **kw):
        self._value = int(value)
        self._master = master

    def get(self):
        return self._value

    def set(self, value):
        self._value = int(value)


class StringVar:
    def __init__(self, master=None, value="", **kw):
        self._value = str(value)
        self._master = master

    def get(self):
        return self._value

    def set(self, value):
        self._value = str(value)


class Menu:
    def __init__(self, master=None, tearoff=0, **kw):
        _ensure_app()
        self.master = master
        self._menu = NSMenu.alloc().initWithTitle_("pet-menu")
        self._menu.setAutoenablesItems_(False)
        _MENUS.append(self)
        self._targets = []
        self._rows = []
        self._by_label = {}

    def _add(self, item, label, kind, extra=None):
        self._menu.addItem_(item)
        row = {"item": item, "label": label, "kind": kind}
        if extra:
            row.update(extra)
        self._rows.append(row)
        if label:
            self._by_label[label] = row
        return item

    def add_command(self, label="", command=None, **kw):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, None, "")
        target = _MenuTarget.alloc().initWithCallback_(command)
        item.setTarget_(target)
        item.setAction_("fire:")
        item.setEnabled_(True)
        self._targets.append(target)
        self._add(item, label, "command", {"command": command})

    def add_separator(self, **kw):
        self._add(NSMenuItem.separatorItem(), "", "separator")

    def add_cascade(self, label="", menu=None, **kw):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, None, "")
        if menu is not None:
            item.setSubmenu_(menu._menu)
        self._add(item, label, "cascade")

    def add_radiobutton(self, label="", value=None, variable=None, command=None, **kw):
        def choose(var=variable, val=value, cb=command):
            if var is not None:
                var.set(val)
            if cb is not None:
                cb()

        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, None, "")
        target = _MenuTarget.alloc().initWithCallback_(choose)
        item.setTarget_(target)
        item.setAction_("fire:")
        item.setEnabled_(True)
        self._targets.append(target)
        self._add(item, label, "radio",
                  {"variable": variable, "value": value, "command": choose})

    def entryconfigure(self, label, state=None, **kw):
        row = self._by_label.get(label) if isinstance(label, str) else None
        if row is None and isinstance(label, int) and 0 <= label < len(self._rows):
            row = self._rows[label]
        if row is None:
            return
        if state is not None:
            row["item"].setEnabled_(state == "normal")

    def _sync(self):
        for row in self._rows:
            if row["kind"] != "radio":
                continue
            var = row.get("variable")
            val = row.get("value")
            try:
                if var is None:
                    on = False
                elif isinstance(val, str):
                    on = str(var.get()) == val
                else:
                    on = int(var.get()) == val
            except Exception:
                on = False
            row["item"].setState_(1 if on else 0)

    def tk_popup(self, x_root=0, y_root=0):
        self._sync()
        event = _CURRENT_EVENT
        try:
            if event is not None and event.window() is not None:
                view = event.window().contentView()
                NSMenu.popUpContextMenu_withEvent_forView_(self._menu, event, view)
                return
            target = [w for w in _WINDOWS if not w._closed and w._visible]
            if not target:
                return
            window = target[-1]._win
            point = NSMakePoint(float(x_root), primary_height() - float(y_root))
            self._menu.popUpMenuPositioningItem_atLocation_inView_(None, point, window.contentView())
        finally:
            pass

    def grab_release(self):
        pass


# --------------------------------------------------------------------------- 画布
class Canvas:
    def __init__(self, master, width=1, height=1, **kw):
        self.master = master
        self._width = int(width)
        self._height = int(height)
        self._items = {}
        self._next = 0
        self._bindings = {}
        master._canvas = self

    def pack(self, **kw):
        pass

    def place(self, **kw):
        pass

    def create_image(self, x, y, anchor="nw", image=None, **kw):
        self._next += 1
        self._items[self._next] = {"image": image, "x": float(x), "y": float(y)}
        self.master._sync_canvas()
        return self._next

    def itemconfigure(self, item, image=None, **kw):
        entry = self._items.get(item)
        if entry is None:
            return
        if image is not None:
            entry["image"] = image
        self.master._sync_canvas()

    config = itemconfig = itemconfigure

    def coords(self, item, x, y, **kw):
        entry = self._items.get(item)
        if entry is None:
            return
        entry["x"], entry["y"] = float(x), float(y)
        self.master._sync_canvas()

    def config(self, width=None, height=None, **kw):
        if width is not None:
            self._width = int(width)
        if height is not None:
            self._height = int(height)

    configure = config

    def bind(self, sequence, func, **kw):
        self._bindings[sequence] = func

    def delete(self, item):
        self._items.pop(item, None)
        self.master._sync_canvas()


# --------------------------------------------------------------------------- 窗口
class Misc:
    """占位基类：pet_group 里 isinstance(window, tk.Misc) 只在 win32 分支出现。"""


class _PetView(NSView):
    """透明的普通 NSView：位图挂在它的子层上，命中交给像素级判定。"""

    def initWithOwner_(self, owner):
        self = objc.super(_PetView, self).init()
        if self is None:
            return None
        self._owner = owner
        self.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        return self

    def acceptsFirstMouse_(self, _event):
        return True

    def mouseDown_(self, event):
        self._owner._dispatch("press", event)

    def mouseDragged_(self, event):
        self._owner._dispatch("motion", event)

    def mouseUp_(self, event):
        self._owner._dispatch("release", event)

    def rightMouseDown_(self, event):
        self._owner._dispatch("right", event)

    def drawRect_(self, rect):
        pass


_GEOMETRY = re.compile(r"^\s*(?:(\d+)x(\d+))?\s*(?:([+-]\d+)\s*([+-]\d+))?\s*$")


class Toplevel(Misc):
    def __init__(self, master=None, **kw):
        _ensure_app()
        self.master = master
        self._width = 1
        self._height = 1
        self._x = 0
        self._y = 0
        self._visible = False
        self._closed = False
        self._bindings = {}
        self._protocols = {}
        self._canvas = None
        self._photo = None
        self._image_pos = (0, 0)
        self._topmost = True

        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 1, 1), NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered, False)
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setHasShadow_(False)
        window.setLevel_(NSFloatingWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setReleasedWhenClosed_(False)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        view = _PetView.alloc().initWithOwner_(self)
        window.setContentView_(view)
        view.setWantsLayer_(True)
        layer = Quartz.CALayer.layer()
        layer.setContentsGravity_(Quartz.kCAGravityResize)
        layer.setMasksToBounds_(False)
        try:
            layer.setContentsScale_(float(window.backingScaleFactor()))
        except Exception:
            pass
        view.layer().addSublayer_(layer)

        self._win = window
        self._view = view
        self._layer = layer
        self._tk = _TkProxy(self)
        _WINDOWS.append(self)

    # ---------------------------------------------------------------- 几何
    def geometry(self, spec=None):
        if spec is None:
            return "%dx%d+%d+%d" % (self._width, self._height, self._x, self._y)
        match = _GEOMETRY.match(str(spec))
        if not match:
            raise TclError('bad geometry specifier "%s"' % spec)
        width, height, x, y = match.groups()
        if width is not None:
            self._width = int(width)
            self._height = int(height)
        if x is not None:
            # Tk 的负坐标表示"距右边缘"，桌宠需要绝对坐标，这里一律按绝对值处理。
            self._x = int(x)
            self._y = int(y)
        self._place()

    def _place(self):
        if self._closed:
            return
        height = primary_height()
        rect = NSMakeRect(self._x, height - self._y - self._height,
                          self._width, self._height)
        self._win.setFrame_display_(rect, True)
        self._view.setFrame_(NSMakeRect(0, 0, self._width, self._height))
        self._sync_canvas()
        # Tk 语义：Toplevel 一旦有了真实尺寸就是"已映射"的。
        # pet_app 不打 deiconify 就直接 geometry，这里补上上屏动作。
        if not self._visible and self._width > 1 and self._height > 1:
            self.deiconify()

    def _sync_canvas(self):
        if self._closed:
            return
        canvas = self._canvas
        if canvas is None or not canvas._items:
            return
        entry = next(iter(canvas._items.values()))
        photo = entry["image"]
        x, y = entry["x"], entry["y"]
        if photo is None:
            self._layer.setContents_(None)
            self._photo = None
            return
        if self._photo is not photo:
            self._layer.setContents_(photo.cgimage())
            self._photo = photo
        self._image_pos = (int(round(x)), int(round(y)))
        self._layer.setFrame_(NSMakeRect(
            x, self._height - y - photo.height(), photo.width(), photo.height()))

    # ---------------------------------------------------------------- 显示
    def title(self, text=""):
        self._win.setTitle_(text)

    def overrideredirect(self, flag=True):
        pass

    def attributes(self, name=None, value=None, **kw):
        if name == "-topmost":
            self._topmost = bool(value)
            self._win.setLevel_(NSFloatingWindowLevel if value else 0)
        elif name == "-alpha" and value is not None:
            self._win.setAlphaValue_(float(value))
        return None

    def config(self, **kw):
        pass

    configure = config

    def iconbitmap(self, default=None, **kw):
        pass

    def protocol(self, name="WM_DELETE_WINDOW", handler=None):
        self._protocols[name] = handler

    def bind(self, sequence, func, **kw):
        self._bindings[sequence] = func

    def deiconify(self):
        if self._closed:
            return
        if not self._visible:
            self._visible = True
            self._win.orderFrontRegardless()

    def withdraw(self):
        if self._closed:
            return
        self._visible = False
        self._win.orderOut_(None)

    def lift(self, above=None):
        if self._closed or not self._visible:
            return
        self._win.orderFrontRegardless()

    def focus_force(self):
        if self._closed:
            return
        self._win.makeKeyAndOrderFront_(None)

    def focus_get(self):
        return None

    def update_idletasks(self):
        pass

    def update(self):
        pass

    def after(self, ms, func=None, *args):
        if func is None:
            time.sleep(max(0.0, float(ms)) / 1000.0)
            return None
        if args:
            callback = lambda: func(*args)
        else:
            callback = func
        return SCHED.after(ms, callback)

    def after_cancel(self, ident):
        if ident is not None:
            SCHED.after_cancel(ident)

    def after_idle(self, func, *args):
        if args:
            return SCHED.after_idle(lambda: func(*args))
        return SCHED.after_idle(func)

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        self._visible = False
        try:
            self._win.orderOut_(None)
            self._win.close()
        except Exception:
            pass
        try:
            _WINDOWS.remove(self)
        except ValueError:
            pass

    # ---------------------------------------------------------------- 查询
    @property
    def tk(self):
        return self._tk

    def winfo_x(self):
        return int(self._x)

    def winfo_y(self):
        return int(self._y)

    def winfo_rootx(self):
        return int(self._x)

    def winfo_rooty(self):
        return int(self._y)

    def winfo_width(self):
        return int(self._width)

    def winfo_height(self):
        return int(self._height)

    def winfo_screenwidth(self):
        return screen_size()[0]

    def winfo_screenheight(self):
        return screen_size()[1]

    def winfo_pointerxy(self):
        point = NSEvent.mouseLocation()
        return int(point.x), int(primary_height() - point.y)

    def winfo_exists(self):
        return 0 if self._closed else 1

    def winfo_ismapped(self):
        return 1 if (self._visible and not self._closed) else 0

    def winfo_toplevel(self):
        return self

    # ---------------------------------------------------------------- 事件
    def _dispatch(self, kind, event):
        global _CURRENT_EVENT, _PRESSED
        if self._closed:
            return
        sequence = {"press": "<ButtonPress-1>", "motion": "<B1-Motion>",
                    "release": "<ButtonRelease-1>", "right": "<Button-3>"}[kind]
        location = event.locationInWindow()
        frame = self._win.frame().origin
        local_x = float(location.x)
        local_y = self._height - float(location.y)
        root_x = float(frame.x) + float(location.x)
        root_y = primary_height() - (float(frame.y) + float(location.y))
        payload = _Event(int(round(local_x)), int(round(local_y)),
                         int(round(root_x)), int(round(root_y)),
                         widget=self._canvas, type=kind)
        _dbg("dispatch %s local=(%d,%d) root=(%d,%d)" % (
            kind, payload.x, payload.y, payload.x_root, payload.y_root))
        if kind == "press":
            _PRESSED = self
        elif kind == "release":
            _PRESSED = None
        _CURRENT_EVENT = event
        handlers = []
        canvas = self._canvas
        if canvas is not None and sequence in canvas._bindings:
            handlers.append(canvas._bindings[sequence])
        if sequence in self._bindings:
            handlers.append(self._bindings[sequence])
        _dbg("  handlers=%d" % len(handlers))
        for handler in handlers:
            if self._closed:
                break
            try:
                handler(payload)
            except Exception:
                traceback.print_exc()
        if kind in ("press", "release", "right"):
            _CURRENT_EVENT = None

    def _hit(self, global_x, global_y):
        photo = self._photo
        if photo is None:
            return False
        local_x = global_x - self._x
        local_y = global_y - self._y
        if not (0 <= local_x < self._width and 0 <= local_y < self._height):
            return False
        offset_x, offset_y = self._image_pos
        alpha = photo.alpha_at(int(local_x) - offset_x,
                               int(local_y) - offset_y)
        if _DEBUG and _HOVER_SEQ[0] % 25 == 0:
            _dbg("hit win=(%d,%d) local=(%d,%d) img=(%d,%d) photo=%s alpha=%d"
                 % (self._x, self._y, local_x, local_y, offset_x, offset_y,
                    photo.width() if photo else None, alpha))
        return alpha >= ALPHA_CUT


class _TkProxy:
    """替身 tk 解释器：只为 wm stackorder 这类调用提供返回值。"""

    def __init__(self, window):
        self._window = window

    def call(self, *args):
        if len(args) >= 2 and args[0] == "wm" and args[1] == "stackorder":
            live = [w for w in _WINDOWS if not w._closed and w._visible]
            if self._window in live:
                # 返回"从下到上"的窗口列表，越靠后越在前面。
                return list(live)
            return []
        return ""

    def eval(self, script):
        return ""


class Tk(Toplevel):
    def __init__(self, **kw):
        super().__init__(None, **kw)
        self.withdraw()

    def mainloop(self, _n=0):
        _ensure_app().run()

    def destroy(self):
        # Tk 根窗口销毁 = 主循环结束。macOS 上必须显式 stop，否则 run() 空转。
        super().destroy()
        try:
            _ensure_app().stop_(None)
        except Exception:
            traceback.print_exc()


# --------------------------------------------------------------------------- 鼠标穿透
def _start_hover_timer():
    global _primary_refresh
    # 任何未捕获异常都会让这个重复 NSTimer 永久停摆（穿透判定停摆＝全窗口点击失效），
    # 所以定时器回调里兜住所有异常，只打印不抛出。
    def _tick(_timer=None):
        try:
            _poll_hover(_timer)
        except Exception:
            traceback.print_exc()
    timer = NSTimer.timerWithTimeInterval_repeats_block_(_HOVER_SEC, True, _tick)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)


_MENU_CMD_PATH = "/tmp/naiwa-menu-cmd"
_HOVER_SEQ = [0]


def _drain_menu_command():
    """调试用：从文件读一个菜单标题并触发它，方便脚本化验收（NAIWA_DEBUG 才生效）。"""
    try:
        with open(_MENU_CMD_PATH, encoding="utf-8") as handle:
            label = handle.read().strip()
    except OSError:
        return
    try:
        os.unlink(_MENU_CMD_PATH)
    except OSError:
        pass
    if not label:
        return
    _dbg("menu-cmd %r" % label)
    for menu in _MENUS:
        row = menu._by_label.get(label)
        if row is None:
            continue
        callback = row.get("command")
        if callback is None:
            continue
        try:
            callback()
        except Exception:
            traceback.print_exc()
        return


def _poll_hover(_timer=None):
    global _PRESSED
    _refresh_primary()
    _HOVER_SEQ[0] += 1
    if _DEBUG and _HOVER_SEQ[0] % 250 == 0:
        _point = NSEvent.mouseLocation()
        _dbg("poll alive n=%d cursor=(%d,%d) pressed=%s windows=%s" % (
            _HOVER_SEQ[0], _point.x, primary_height() - _point.y,
            _PRESSED is not None,
            [(w._closed, w._visible, w._x, w._y, w._width, w._height)
             for w in _WINDOWS]))
    if _DEBUG:
        _drain_menu_command()
    if _PRESSED is not None:
        # 释放事件可能落在别的窗口/桌面上，永远收不到 release；不自己清掉的话
        # 穿透判定会永久停摆，除当时那一只外所有窗口都保持 ignoresMouseEvents，
        # 表现就是"点了没反应、复制体多了更明显"。这里按真实按键状态自愈。
        try:
            still_down = bool(NSEvent.pressedMouseButtons() & 1)
        except Exception:
            still_down = True
        if still_down and not getattr(_PRESSED, "_closed", False):
            return
        _dbg("stale press cleared (closed=%s)" % getattr(_PRESSED, "_closed", None))
        _PRESSED = None
    point = NSEvent.mouseLocation()
    global_x = float(point.x)
    global_y = primary_height() - float(point.y)
    target = None
    for window in reversed(list(_WINDOWS)):
        if window._closed or not window._visible:
            continue
        try:
            if window._hit(global_x, global_y):
                target = window
                break
        except Exception:
            traceback.print_exc()
    if target is not _HOVER_TARGET[0]:
        _HOVER_TARGET[0] = target
        _dbg("hover target=%s cursor=(%d,%d)" % (
            "pet" if target is not None else "none", global_x, global_y))
    for window in list(_WINDOWS):
        if window._closed:
            continue
        want_ignore = window is not target
        try:
            if bool(window._win.ignoresMouseEvents()) != want_ignore:
                window._win.setIgnoresMouseEvents_(want_ignore)
        except Exception:
            pass


# --------------------------------------------------------------------------- 说明窗口
class _HelpDelegate(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_HelpDelegate, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def windowWillClose_(self, _note):
        owner = self._owner
        if owner is not None:
            owner._closed = True
            owner._on_close()


class HelpWindow:
    """原生 tex 视图窗口：替代 ttk + ScrolledText 的说明面板。"""

    def __init__(self, title, text, on_close=None):
        _ensure_app()
        self._closed = False
        self._on_close_cb = on_close
        rect = NSMakeRect(240, 200, 620, 520)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        window.setTitle_(title)
        window.setReleasedWhenClosed_(False)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 12, 596, 496))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        textview = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 596, 496))
        textview.setEditable_(False)
        textview.setSelectable_(True)
        textview.setString_(text)
        textview.setFont_(NSFont.systemFontOfSize_(13))
        textview.setTextContainerInset_((10.0, 10.0))
        textview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setDocumentView_(textview)
        window.contentView().addSubview_(scroll)
        delegate = _HelpDelegate.alloc().initWithOwner_(self)
        window.setDelegate_(delegate)
        self._win = window
        self._delegate = delegate
        self._textview = textview
        self._scroll = scroll
        window.center()
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _on_close(self):
        callback = self._on_close_cb
        if callback is not None:
            try:
                callback()
            except Exception:
                traceback.print_exc()

    def winfo_exists(self):
        return 0 if self._closed else 1

    def winfo_ismapped(self):
        return 0 if self._closed else 1

    def deiconify(self):
        if not self._closed:
            self._win.makeKeyAndOrderFront_(None)

    def lift(self):
        if not self._closed:
            self._win.orderFrontRegardless()

    def focus_force(self):
        if not self._closed:
            self._win.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        self._win.orderOut_(None)
        self._win.close()


# --------------------------------------------------------------------------- ttk 空壳
class _Stub:
    def __init__(self, master=None, **kw):
        self.master = master

    def pack(self, **kw):
        return self

    def grid(self, **kw):
        return self

    def place(self, **kw):
        return self

    def config(self, **kw):
        return self

    configure = config

    def bind(self, *args, **kw):
        return None

    def insert(self, *args, **kw):
        return None

    def destroy(self):
        return None


class _TtkModule:
    Frame = Label = Button = Entry = LabelFrame = _Stub

    @staticmethod
    def Style():
        return _Stub()


ttk = _TtkModule()


class _ScrolledTextModule:
    ScrolledText = _Stub


scrolledtext = _ScrolledTextModule()


def show_text_window(title, text, on_close=None):
    return HelpWindow(title, text, on_close)
