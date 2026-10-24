# 奶小蛙 · macOS 移植版说明

原项目 `jiasjdia/naiwa` 只发行 Windows 版。本目录是其在 Apple Silicon macOS 上原生运行的移植版本，
不需要 Wine 或虚拟机。

## 已安装的位置

| 项目 | 路径 |
| --- | --- |
| 源码与素材 | `~/Applications/naiwa/` |
| 应用包（可在访达双击） | `~/Applications/奶小蛙.app` |
| 登录自启项 | `~/Library/LaunchAgents/com.jiasjdia.naiwa.plist` |
| 运行日志 | `~/Applications/naiwa/_preview/pet.log` |
| 启动器日志 | `~/Applications/naiwa/_preview/launcher.log` |
| 位置记忆 | `~/Applications/naiwa/_preview/pet_geometry.json` |

依赖装在 `~/Applications/naiwa/.venv`（Python 3.12 + Pillow / pyobjc / pygame / psutil / pymunk）。

## 启动与退出

- 启动：在“应用程序”里双击 **奶小蛙**，或执行 `open -a ~/Applications/奶小蛙.app`。
- 退出：右键点奶小蛙 → **退出**。
- 同一时间只允许一个实例；再次启动会唤醒已在运行的那只，不会多开。

## 操作

- 右键奶小蛙：捧腹大笑 / 全部大笑 / 停止大笑 / 停止全部大笑 / 复制分身 / 归一 / 大小 / 使用说明 / 退出。
- 左键按住拖动：移动位置；松手后落到屏幕底部（会做落体回弹动画）。
- 单击身体：播放一次捧腹大笑。
- 3 秒内单击 10 次：分裂出更多分身，最多 40 只。
- 复制分身 / 归一：增加或合并分身，也绑定在快捷键上。

## 键盘快捷键

和 Windows 版一致，全部为 **Control + Option + 按键**：

| 快捷键 | 作用 |
| --- | --- |
| Control+Option+1 | 播放一次大笑 |
| Control+Option+C | 复制（准备一只分身） |
| Control+Option+V | 粘贴（把准备好的分身放到鼠标位置） |
| Control+Option+Q | 退出 |

先用 +C 再用 +V 才会出现新的一只，这与 Windows 版的复制/粘贴语义相同。

快捷键用 Carbon 的 RegisterEventHotKey 注册，**不需要辅助功能授权**，也不需要
输入监控授权。如果某个组合已被其它 App 占用，注册会失败，日志里会写明是哪个键；
其余功能不受影响。

## 开机自启

已装好登录项，登录后自动出现一只。管理命令：

```bash
# 停止自启
launchctl bootout gui/$UID/com.jiasjdia.naiwa

# 恢复自启
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.jiasjdia.naiwa.plist
```

自启只是登录时拉起一次，从菜单退出后不会自动重启。

## 与原版的差异

Windows 专有的三处已重写：

1. `pet_runtime.py` —— 原版用 kernel32 命名互斥量和 RegisterHotKey。macOS 版改成
   UNIX 域套接字（`/tmp/naiwa-<hash>.sock`）做单实例与动作传递，热键用 Carbon 的
   RegisterEventHotKey（无授权要求）。CGEventTap / NSEvent 全局监听作为后备路径。
   原始 Windows 实现保留为 `pet_runtime_win32.py`。
2. `mac_tk.py`（新增）—— 用 AppKit 实现原代码调用的那部分 tkinter：无边框透明置顶窗口、
   CALayer 逐帧贴图、按 alpha 做鼠标穿透判定、原生右键菜单、说明窗口、`after` 定时器。
3. `run_mac.py`（新增）—— 启动器，把 tkinter 模块替换成 `mac_tk`，并替换
   `work_area`（工作区换算）、`_chroma_frame`（不再需要色键抠图）、`_initial_position`（右下角出生）、
   `PetGroup.show_help`（原生说明窗口）。

其余业务代码（动画、物理、音频、分身逻辑）沿用上游源码，未改动。

## 调试开关

- `NAIWA_DEBUG=1`：把鼠标命中判定、事件分发写到 `/tmp/naiwa-mac-debug.log`；
  同时启用菜单钩子——把菜单项文字写入 `/tmp/naiwa-menu-cmd` 即可触发该项，用于无法稳定模拟点击时验收。
- `NAIWA_TRACE=1`：包装 `PetGroup.request_split`，把调用栈写入 `/tmp/naiwa-split-trace.log`，用于排查分身激增。
- `NAIWA_CACHE_MB`：帧缓存上限，默认 128。

## 卸载

```bash
launchctl bootout gui/$UID/com.jiasjdia.naiwa
rm ~/Library/LaunchAgents/com.jiasjdia.naiwa.plist
rm -rf ~/Applications/奶小蛙.app ~/Applications/naiwa
rm -f /tmp/naiwa-*.sock
```
