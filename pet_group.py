"""Shared desktop-pet resources, window placement, and group operations."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import copy
import ctypes
from ctypes import wintypes
import sys
import time
import random
import math
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageChops
from pet_physics import PhysicsWorld, TIERS, burst_scales, tier_of

MAX_PETS = 80
CLONE_SCALE = 0.5
REUNIFY_SEC = 0.45

USAGE_TEXT = """欢迎广大奶家人饲养奶小蛙，本产品专为奶家人设计，下为使用说明：
1.本体与菜单
右键哪一只，哪一只就成为本体，关闭菜单也会保留。“捧腹大笑”和“停止大笑”只控制这一只；“全部大笑”和“停止全部大笑”控制全体。各只声音独立叠加。“大小”只调整当前本体。

2.点击、长按与拖动
奶家人的身体总是Q弹的，单击可见身体会局部回弹，长按约 1.5 秒蓄满后松开，回弹更明显。按住左键移动即可拎起，松手后放下回弹。

3.连续点击大笑
在 3 秒内有效点击同一只的身体 10 次，会触发有声大笑。任何大笑期间，继续在 2 秒内点击 8 次，就喷出一波更小分身；继续连点可连续喷射。普通单击不会停止动作。普通表演可以拖起中断，喷射分身的循环只能用右键停止。

分裂与物理
一至四档大小为 0.8、0.7、0.6、0.5。一档每波随机 2～4 只，二档 2～3 只，三档 2 只，四档同尺寸 2～4 只。每只随机选更小档（到四档不再缩小），左右交错，每隔 0.1 秒喷一只。
喷射分身从第 54 帧循环到最后一帧（当前第 432 帧），对应音频同步循环，继承来源是否有声。母体结束、拖动及缩放不打断循环，合体或归一保留的桌宠也继续播放；“停止大笑”或“停止全部大笑”才停止。分身仍能递归分裂。分身受到重力，撞墙反弹、落地弹跳，互相阻挡并堆积。每块屏幕独立，任务栏上方为地面。身体碰撞优先贴合，手脚偶尔重叠属于正常现象。
只有喷出的分身受到重力；拖过后改为悬停，可以手动摆放。停止大笑只停止动作和声音，物理继续。来源停止、拖动、缩放或重新播放时，取消尚未喷出的分身；已出生分身继续。

4.复制与粘贴
右键选择“复制分身”，立即在旁边生成一只，可自由拖动。也可以先按 Ctrl+Alt+C 复制，再用 Ctrl+Alt+V 在鼠标位置反复粘贴。
普通复制分身为四档 0.5，从待机开始并保持悬停。全体最多 80 只，包含已预留的待喷出分身；不足一波时只喷剩余名额，满额不保留欠账。合体或归一释放名额后可再次触发。

5拖动合体
将一只奶小蛙拖到同档奶小蛙身上，松手后合体升一档；不同档和一档不能合体，物理碰撞不会自动合体。合体结果固定悬停，任一参与者为本体时结果继承本体身份。

6.形态
右键“形态”可以切换成奶蛋、奶狗、奶杯、奶豆、奶鼠。奶蛋、奶狗、奶杯用原片大笑；奶豆、奶鼠暂无原片大笑，笑时用蹦跳代替。切换时位置和大小不变，下次启动会记住。

7.全体形态
右键“全体形态（一起出来）”，剩余形态会各出来一只，在鼠标附近排成一行；再用“全部大笑”可以让它们一起笑。它们同样可以点击分裂、拖动合体，右键“归一”会全部合回。
“收起其它形态”只留下当前这一只，把其它形态收回去；已经分裂出来的同形态分身不受影响。

8.语音
右键“说一句”会随机播放一句原声。奶蛙用低声线那组，奶蛋等尖锐声线的形态用另一组；没有对应组的形态会退回奶蛙语音。

归一
右键任意一只奶小蛙，再选择“归一”。所有分身约在 0.45 秒内回到选择的奶小蛙并消失，归一期间暂停其他交互，退出仍立即有效。

快捷键与自动表演
Ctrl+Alt+1：全体捧腹大笑。
Ctrl+Alt+C：复制；Ctrl+Alt+V：在鼠标处粘贴；Ctrl+Alt+Q：退出所有桌宠。
奶小蛙有键盘焦点时，也可用 1 播放全体大笑，Q 或 Esc 退出。各只空闲 3～4 分钟会独立静音大笑，分裂出的分身也继承本轮静音。

退出与重启
菜单“退出”会关闭所有奶小蛙。下次启动只出现一只，沿用最后保留奶小蛙的大小；
"""


def work_area(point, fallback=(1920, 1080)):
    """Nearest monitor's work area in the same screen coordinates as Tk."""
    if sys.platform == "win32":
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user.MonitorFromPoint.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
        monitor = user.MonitorFromPoint(wintypes.POINT(round(point[0]), round(point[1])), 2)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    return 0, 0, *fallback


def constrain_origin(x, y, box, area):
    """Keep visible content in a monitor's work area, ignoring transparent pads."""
    left, top, right, bottom = area
    bx0, by0, bx1, by1 = box
    x = min(max(x, left - bx0), max(left - bx0, right - bx1))
    y = min(max(y, top - by0), max(top - by0, bottom - by1))
    return round(x), round(y)


def place_window(window, x, y, size=None, *, flush=True):
    """Move to absolute coordinates; Tk's negative geometry means right-relative."""
    x, y = round(x), round(y)
    native_batch = (not flush and sys.platform == "win32" and isinstance(window, tk.Misc)
                    and window.winfo_ismapped())
    prefix = f"{size[0]}x{size[1]}" if size is not None else ""
    # A deferred negative Tk geometry would overwrite SetWindowPos on the next
    # idle flush. Mapped physics windows therefore move natively in both axes.
    if not native_batch:
        window.geometry(f"{prefix}{x:+d}{y:+d}")
    if flush:
        window.update_idletasks()
    if sys.platform == "win32" and isinstance(window, tk.Misc) and (native_batch or x < 0 or y < 0):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        user.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        hwnd = user.GetAncestor(window.winfo_id(), 2)
        flags = 0x0004 | 0x0010 | (0x0001 if size is None else 0)
        w, h = size or (0, 0)
        if not user.SetWindowPos(hwnd, None, x, y, w, h, flags):
            raise ctypes.WinError(ctypes.get_last_error())
        if flush:
            window.update_idletasks()


def place_windows(moves):
    """Commit a physics frame together, without one native redraw per pet."""
    moves = list(moves)
    if not moves:
        return
    if sys.platform == "win32" and all(isinstance(w, tk.Misc) and w.winfo_ismapped() for w, _, _ in moves):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        user.BeginDeferWindowPos.argtypes = (ctypes.c_int,)
        user.BeginDeferWindowPos.restype = wintypes.HANDLE
        user.DeferWindowPos.argtypes = (wintypes.HANDLE, wintypes.HWND, wintypes.HWND,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user.DeferWindowPos.restype = wintypes.HANDLE
        user.EndDeferWindowPos.argtypes = (wintypes.HANDLE,)
        user.EndDeferWindowPos.restype = wintypes.BOOL
        batch = user.BeginDeferWindowPos(len(moves))
        for window, x, y in moves:
            if not batch:
                break
            hwnd = user.GetAncestor(window.winfo_id(), 2)
            batch = user.DeferWindowPos(batch, hwnd, None, round(x), round(y), 0, 0, 0x0015)
        if batch and user.EndDeferWindowPos(batch):
            return
    for window, x, y in moves:
        place_window(window, x, y, flush=False)


class SharedCache:
    def __init__(self, budget):
        self.budget = budget
        self.bytes = 0
        self.items = OrderedDict()

    def get(self, key):
        entry = self.items.get(key)
        if entry is None:
            return None
        self.items.move_to_end(key)
        return entry[0]

    def put(self, key, value, nbytes):
        previous = self.items.pop(key, None)
        if previous is not None:
            self.bytes -= previous[1]
        # Oversized entries are used by the caller without evicting the entire cache.
        if nbytes > self.budget:
            return value
        self.items[key] = (value, nbytes)
        self.bytes += nbytes
        while self.bytes > self.budget:
            _, (_, weight) = self.items.popitem(last=False)
            self.bytes -= weight
        return value

    def clear(self):
        self.items.clear()
        self.bytes = 0


class FrameCacheView(Mapping):
    """Legacy frame-cache inspection for a pet's current size, without ownership."""
    def __init__(self, pet):
        self.pet = pet

    def _entries(self):
        for key, (value, weight) in self.pet.group.cache.items.items():
            if key[0] == "photo":
                _, cid, index, width, height = key
                clip = self.pet.clips[cid]
                if (width, height) == (clip.fw, clip.fh):
                    yield (cid, index), value, weight

    def __iter__(self):
        return (key for key, _, _ in self._entries())

    def __len__(self):
        return sum(1 for _ in self._entries())

    def __getitem__(self, key):
        cid, index = key
        clip = self.pet.clips[cid]
        photo = self.pet.group.cache.get(("photo", cid, index, clip.fw, clip.fh))
        if photo is None:
            raise KeyError(key)
        return photo

    @property
    def bytes(self):
        return sum(weight for _, _, weight in self._entries())


class PetVoice:
    """A pet releases its own voice or its membership in the shared soundtrack."""
    def __init__(self, group, pet):
        self.group, self.pet = group, pet

    @property
    def output_latency_ms(self):
        return getattr(self.group.audio, "output_latency_ms", 0)

    def play(self, clip_id, start_ms=None, *, end_ms=None, loops=0):
        self.stop()
        player = self.group.audio
        if hasattr(player, "play_voice"):
            if end_ms is not None or loops:
                base = getattr(player, "offset_ms", {}).get(clip_id, 0)
                return player.play_voice(self.pet, clip_id, start_ms=base + (start_ms or 0),
                                         end_ms=None if end_ms is None else base + end_ms, loops=loops)
            if start_ms is None:
                return player.play_voice(self.pet, clip_id)
            base = getattr(player, "offset_ms", {}).get(clip_id, 0)
            return player.play_voice(self.pet, clip_id, start_ms=base + start_ms)
        return player.play(clip_id) if start_ms is None else player.play(clip_id, start_ms=start_ms)

    def duration_ms(self, clip_id):
        return self.group.audio.duration_ms(clip_id)

    def stop(self):
        group = self.group
        if self.pet in group.audio_members:
            group.audio_members.remove(self.pet)
            if not group.audio_members:
                group.audio.stop()
        if hasattr(group.audio, "stop_voice"):
            group.audio.stop_voice(self.pet)
        elif not group.audio_members:
            group.audio.stop()

    def quit(self):
        self.stop()
        if hasattr(self.group.audio, "release_voice"):
            self.group.audio.release_voice(self.pet)


class PetGroup:
    def __init__(self, manifest, settings, audio, cache_budget):
        self.root = tk.Tk()
        self.root.withdraw()
        self.manifest, self.settings, self.audio = manifest, settings, audio
        self.cache = SharedCache(cache_budget)
        self.pets = []  # bottom to top, refreshed from native stacking before a merge
        self.primary = None
        self.copy_ready = False
        self.audio_members = set()
        self.runtime = None
        self.closed = False
        self.reunifying = False
        self._reunify_after = None
        self._eye_source = None
        self.help_window = None
        self.physics = PhysicsWorld()
        self.pending_bursts = []
        self._physics_after = None
        self._physics_started = False
        self._physics_timer = None
        self._physics_last = time.monotonic()
        try:
            self.audio.prewarm("laugh")
        except Exception as error:
            from pet_app import log
            log(f"音频预热异常(不影响画面): {error}")

    def register(self, pet):
        self.pets.append(pet)
        if self.primary is None:
            self.primary = pet
        self.set_hover(pet)
        if not self._physics_started:
            self._physics_started = True
            self._physics_after = self.root.after(16, self._physics_tick)

    def cancel_emission(self, pet):
        self.pending_bursts = [entry for entry in getattr(self, "pending_bursts", []) if entry[1] is not pet]

    def set_hover(self, pet):
        if not hasattr(self, "physics") or self.closed:
            return
        origin = (pet.root.winfo_x(), pet.root.winfo_y())
        self.physics.add(pet, pet.visible_box(), origin, work_area(pet.foot_position()))

    def begin_drag(self, pet):
        self.cancel_emission(pet)
        if hasattr(self, "physics"):
            self.physics.remove(pet)

    def resize_physics(self, pet):
        if not hasattr(self, "physics"):
            return
        entry = self.physics.particles.get(pet)
        dynamic = self.physics.is_dynamic(pet)
        velocity = tuple(entry.body.velocity) if entry is not None else (0, 0)
        self.physics.add(pet, pet.visible_box(), (pet.root.winfo_x(), pet.root.winfo_y()),
                         work_area(pet.foot_position()), dynamic=dynamic, velocity=velocity)

    def request_split(self, source, now=None):
        if self.closed or self.reunifying or source not in self.pets:
            return 0
        now = time.monotonic() if now is None else now
        snapshot = source.animation_snapshot(now)
        if snapshot is None or not getattr(source, "_laughing", False):
            return 0
        scales = burst_scales(source.global_scale)
        room = max(0, MAX_PETS - len(self.pets) - len(self.pending_bursts))
        scales = scales[:room]
        direction = random.choice((-1, 1))
        serial = source._action_serial
        for index, scale in enumerate(scales):
            self.pending_bursts.append((now + index * 0.1, source, scale, direction, serial))
            direction *= -1
        if hasattr(self, "physics"):
            self._sync_physics_timer()
        return len(scales)

    def _sync_physics_timer(self):
        active = bool(self.pending_bursts) or any(
            self.physics.is_dynamic(pet) and not entry.body.is_sleeping
            for pet, entry in self.physics.particles.items())
        if active and self._physics_timer is None:
            from pet_app import _begin_jelly_timer
            self._physics_timer = _begin_jelly_timer()
        elif not active and self._physics_timer is not None:
            self._physics_timer.timeEndPeriod(1)
            self._physics_timer = None

    def _emit_due(self, now):
        self.pending_bursts.sort(key=lambda entry: entry[0])
        while self.pending_bursts and self.pending_bursts[0][0] <= now:
            _, source, scale, direction, serial = self.pending_bursts.pop(0)
            if (source not in self.pets or source._action_serial != serial
                    or source.animation_snapshot(now) is None):
                continue
            try:
                self.spawn(source=source, scale=scale, emitted=True, direction=direction)
            except Exception as error:
                from pet_app import log
                log(f"喷射分身失败(名额已释放): {error}")

    def _physics_tick(self):
        self._physics_after = None
        if self.closed:
            return
        now = time.monotonic()
        elapsed, self._physics_last = now - self._physics_last, now
        if not self.reunifying:
            self._emit_due(now)
            for pet in tuple(self.pets):
                self.physics.sync_hover(pet, (pet.root.winfo_x(), pet.root.winfo_y()))
            self.physics.advance(elapsed)
            place_windows((pet.root, x, y) for pet, (x, y) in self.physics.positions()
                          if pet in self.pets and not pet._closed)
            self.root.update_idletasks()
        if not self.closed:
            self._sync_physics_timer()
            delay = max(1, math.ceil((now + 1 / 60 - time.monotonic()) * 1000))
            self._physics_after = self.root.after(delay, self._physics_tick)

    def show_help(self):
        """One non-modal help window, owned by the group rather than a pet."""
        if self.closed:
            return
        if self.help_window is not None and self.help_window.winfo_exists():
            self.help_window.deiconify()
            self.help_window.lift()
            self.help_window.focus_force()
            return

        window = self.help_window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("奶小蛙使用说明")
        window.attributes("-topmost", True)

        def close_help(event=None):
            self.help_window = None
            window.destroy()
            return "break"

        window.protocol("WM_DELETE_WINDOW", close_help)
        window.bind("<Escape>", close_help)
        content = ttk.Frame(window, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="桌宠使用说明", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(0, 12))
        text = ScrolledText(content, wrap="word", font=("Microsoft YaHei UI", 10),
                            padx=12, pady=10, relief="flat", borderwidth=0)
        text.insert("1.0", USAGE_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        footer = ttk.Frame(content)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, text="Esc 关闭本说明窗口").pack(side="left")
        ttk.Button(footer, text="关闭", command=close_help).pack(side="right")

        left, top, right, bottom = work_area(self.root.winfo_pointerxy())
        width, height = min(640, right - left - 32), min(680, bottom - top - 48)
        window.minsize(min(420, width), min(300, height))
        window.deiconify()
        place_window(window, (left + right - width) // 2, (top + bottom - height) // 2, (width, height))
        window.focus_force()

    def eyes(self, size):
        from pet_eyes import EyeFollow
        key = ("eyes", *size)
        prototype = self.cache.get(key)
        if prototype is None:
            if self._eye_source is None:
                self._eye_source = EyeFollow(size)
                prototype = self._eye_source
            else:
                prototype = copy.copy(self._eye_source)
                prototype.size = tuple(size)
                prototype._prepare()
            weight = sum(im.width * im.height * len(im.getbands())
                         for im in (prototype.static, prototype.mask, prototype.pupil_sprite))
            self.cache.put(key, prototype, weight)
        eyes = copy.copy(prototype)
        eyes.reset()
        return eyes

    def set_primary(self, pet):
        if self.closed or self.reunifying or pet not in self.pets:
            return
        self.primary = pet
        pet._save_scale(pet.global_scale)
        pet._write_geometry_file()

    def copy(self):
        if not self.closed and not self.reunifying:
            self.copy_ready = True

    def duplicate(self, source):
        self.copy()
        return self.spawn(source=source)

    def paste(self):
        if self.copy_ready:
            return self.spawn(point=self.root.winfo_pointerxy())

    def spawn(self, source=None, point=None, *, scale=CLONE_SCALE, emitted=False, direction=1, form=None):
        if self.closed or self.reunifying or len(self.pets) + len(getattr(self, "pending_bursts", [])) >= MAX_PETS:
            return None
        from pet_app import PetApp, log
        pet = PetApp.__new__(PetApp)
        try:
            pet.__init__(group=self, scale=scale,
                         form=form or (source.form if source is not None else getattr(self.primary, "form", "naiwa")))
            box = pet.visible_box()
            if source is not None:
                sx, sy = source.root.winfo_x(), source.root.winfo_y()
                sb = source.visible_box()
                point = (sx + (sb[0] + sb[2]) / 2, sy + sb[3])
                area = work_area(point)
                if emitted:
                    x = sx + (sb[0] + sb[2] - box[0] - box[2]) / 2
                    y = sy + (sb[1] + sb[3] - box[1] - box[3]) / 2
                else:
                    x, y = sx + sb[2] + 12 - box[0], sy + sb[3] - box[3]
                    if x + box[2] > area[2]:
                        x = sx + sb[0] - 12 - box[2]
            else:
                point = point or self.root.winfo_pointerxy()
                area = work_area(point)
                x, y = point[0] - (box[0] + box[2]) / 2, point[1] - (box[1] + box[3]) / 2
            x, y = constrain_origin(x, y, box, area)
            place_window(pet.root, x, y)
            if emitted:
                pet.inherit_animation(source)
                self.physics.add(pet, box, (x, y), area, dynamic=True, source=source,
                                 velocity=self.physics.launch_velocity(area, direction))
            else:
                self.set_hover(pet)
            pet.draw()
            pet.tick()
            log(f"新增分身: {len(self.pets)}/{MAX_PETS}, 尺寸={pet.global_scale}")
            return pet
        except Exception:
            if hasattr(pet, "root"):
                self.destroy_pet(pet)
            raise


    def spawn_form(self, form, point=None, scale=CLONE_SCALE):
        """召唤指定形态的一只；用于「全体形态」和分形态召唤。"""
        return self.spawn(point=point, scale=scale, form=form)

    def show_all_forms(self):
        """把还没出场的形态各召唤一只，排成一行放在鼠标附近。"""
        from pet_app import FORMS, log
        if self.closed or self.reunifying:
            return 0
        have = {getattr(pet, "form", None) for pet in self.pets}
        missing = [fid for fid, _ in FORMS if fid not in have]
        if not missing:
            return 0
        px, py = self.root.winfo_pointerxy()
        area = work_area((px, py))
        span = 210
        start = px - (len(missing) - 1) * span / 2
        made = 0
        for i, fid in enumerate(missing):
            x = max(120, min(start + i * span, area[2] - 120))
            try:
                pet = self.spawn_form(fid, point=(x, py))
            except Exception as error:
                log(f"召唤形态 {fid} 失败: {error}")
                continue
            if pet is not None:
                made += 1
        return made

    def hide_extra_forms(self):
        """只保留本体这一只，把其它形态收回（对应「全体形态」的反向操作）。"""
        keep = self.primary
        if keep is None:
            return 0
        removed = 0
        for pet in tuple(self.pets):
            if pet is keep:
                continue
            try:
                self.destroy_pet(pet)
                removed += 1
            except Exception:
                pass
        return removed

    def play_all(self, clip_id, with_audio=True):
        from pet_app import log
        if self.closed or self.reunifying:
            log(f"群笑取消: closed={self.closed} reunifying={self.reunifying}")
            return
        # 归一/合并后 primary 可能已经换人;只要还有宠物就挑一只当基准,
        # 不能因为 primary 是 None 就让整桌复制体一起不动。
        source = self.primary or (self.pets[0] if self.pets else None)
        if source is None:
            log("群笑取消: 桌上一只都没有")
            return
        if clip_id not in source.clips:
            log(f"群笑取消: 形态 {getattr(source, 'form', '?')} 没有动作 {clip_id}")
            return
        now = time.monotonic()
        # 一起笑只让一只播「群笑」成品:同一段原版笑声按 0/120/260ms 错开三层再限幅。
        # 原版是每只各播同一段、自然叠加;错开等距(如 15ms)会形成梳状滤波=金属白噪。
        chorus = (with_audio and clip_id == "laugh" and len(self.pets) > 1
                  and self.audio.available("laugh_chorus"))
        # 形态自己的大笑只有 1-2 秒,群笑要按音频长度反复播,所有复制体才会一起笑到最后。
        loop_for = None
        if chorus:
            loop_for = max(0.5, self.audio.duration_ms("laugh_chorus") / 1000.0)
        failed = 0
        for pet in tuple(self.pets):
            heard = with_audio and (not chorus or pet is source)
            try:
                pet.play(clip_id, with_audio=heard, started_at=now,
                         audio_id="laugh_chorus" if (chorus and heard) else None,
                         loop_for=loop_for)
            except Exception as error:
                # 一只出错不能把后面的都带停：以前循环里抛异常 = 后面的复制体全不笑。
                failed += 1
                log(f"群笑失败 {getattr(pet, 'form', '?')}: {error!r}")
        if len(self.pets) > 1 or failed:
            acting = sum(1 for p in self.pets
                         if p.mode == "playing" and getattr(p, "_laughing", False))
            log(f"群笑({clip_id}): 目标 {len(self.pets)} 只, 已在笑 {acting} 只, "
                f"失败 {failed} 只, chorus={chorus}")

    def stop_all_laughs(self):
        if self.closed or self.reunifying:
            return
        for pet in tuple(self.pets):
            pet.stop_laugh()

    def dispatch(self):
        if self.runtime is None or self.closed:
            return
        for action in self.runtime.pending():
            if action == "quit":
                self.quit()
                return
            if action == "show":
                for pet in self.pets:
                    pet.root.deiconify()
                    pet.root.lift()
            elif action == "copy":
                self.copy()
            elif action == "paste":
                self.paste()
            elif action == "say":
                primary = self.primary
                if primary is not None:
                    primary.say()
            else:
                self.play_all(action)

    @staticmethod
    def overlap(a, b):
        ma, ax, ay = a.screen_mask()
        mb, bx, by = b.screen_mask()
        left, top = max(ax, bx), max(ay, by)
        right, bottom = min(ax + ma.width, bx + mb.width), min(ay + ma.height, by + mb.height)
        if right <= left or bottom <= top:
            return 0
        am = ma.crop((left - ax, top - ay, right - ax, bottom - ay))
        bm = mb.crop((left - bx, top - by, right - bx, bottom - by))
        return ImageChops.multiply(am, bm).histogram()[255]

    def try_merge(self, source):
        if self.closed or self.reunifying or source not in self.pets:
            return False
        # Tk reports its toplevels in native bottom-to-top stacking order.
        order = list(self.root.tk.call("wm", "stackorder", self.root))
        ranks = {str(window): i for i, window in enumerate(order)}
        candidates = [(self.overlap(source, p), ranks.get(str(p.root), i), p)
                      for i, p in enumerate(self.pets) if p is not source]
        if not candidates:
            return False
        area, _, target = max(candidates, key=lambda item: item[:2])
        from pet_app import log
        tier = tier_of(source.global_scale)
        if not area or tier == 1 or tier != tier_of(target.global_scale):
            return False
        scale = TIERS[tier - 2]
        anchor = target.foot_position()
        was_primary = self.primary in (source, target)
        keep_loop = getattr(target, "_loop_start", None) is not None
        if not keep_loop:
            target._to_idle()
        target.set_scale(scale, anchor=anchor, clamp=False)
        if was_primary:
            self.primary = target
            target._save_scale(scale)
        self.destroy_pet(source)
        self.set_hover(target)
        if not keep_loop:
            target.land()
        target._write_geometry_file()
        log(f"合体完成: 尺寸={scale}, 剩余 {len(self.pets)} 只")
        return True

    def destroy_pet(self, pet):
        self.cancel_emission(pet)
        if hasattr(self, "physics"):
            self.physics.remove(pet)
        if pet in self.pets:
            self.pets.remove(pet)
        pet._close_window()

    def reunify(self):
        if self.closed or self.reunifying or len(self.pets) < 2:
            return
        self.reunifying = True
        self.pending_bursts.clear()
        self.physics.clear()
        target = self.primary
        keep_loop = getattr(target, "_loop_start", None) is not None
        if not keep_loop:
            target._to_idle()
        destination = target.foot_position()
        starts = []
        for pet in tuple(self.pets):
            if pet is target:
                continue
            pet._to_idle()
            pet.pickup_for_return()
            starts.append((pet, pet.root.winfo_x(), pet.root.winfo_y(), pet.foot_position()))
        began = time.monotonic()

        def advance():
            self._reunify_after = None
            if self.closed:
                return
            fraction = min(1.0, max(0.0, (time.monotonic() - began) / REUNIFY_SEC))
            eased = 1 - (1 - fraction) ** 3
            for pet, x, y, foot in starts:
                place_window(pet.root, x + (destination[0] - foot[0]) * eased,
                             y + (destination[1] - foot[1]) * eased)
            if fraction >= 1:
                for pet, *_ in starts:
                    self.destroy_pet(pet)
                self.reunifying = False
                self.set_hover(target)
                if not keep_loop:
                    target.land()
                target._write_geometry_file()
            else:
                self._reunify_after = self.root.after(10, advance)
        advance()

    def quit(self):
        if self.closed:
            return
        self.closed = True
        self.pending_bursts.clear()
        if self._physics_after is not None:
            self.root.after_cancel(self._physics_after)
            self._physics_after = None
        if self._physics_timer is not None:
            self._physics_timer.timeEndPeriod(1)
            self._physics_timer = None
        self.physics.clear()
        if self.help_window is not None:
            self.help_window.destroy()
            self.help_window = None
        if self.primary is not None:
            self.primary._save_scale(self.primary.global_scale)
        if self._reunify_after is not None:
            self.root.after_cancel(self._reunify_after)
            self._reunify_after = None
        for pet in tuple(self.pets):
            self.destroy_pet(pet)
        self.audio.quit()
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None
        self._eye_source = None
        self.cache.clear()
        self.root.destroy()
