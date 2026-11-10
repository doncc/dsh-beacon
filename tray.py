#!/usr/bin/env python3
"""dsh-beacon · DSH 顶栏状态灯。

一个 AppIndicator 图标，三种颜色表达本机 DSH 的对话状态：
  绿 = 全部空闲完成      黄 = 有对话在等我确认      灰 = 有对话正在执行

交互（GNOME 的托盘只能这样）：
  中键单击 → 直接跳到「最早待确认」的那个对话
  左键/右键 → 弹出菜单：状态摘要 + 待确认列表（每条可点跳转）+ 常用开关

两个目录是分开的：
  DSH_ROOT          DSH 的安装目录，只用来读 dsh.log（取 token）和调 stop-dsh.sh
  DSH_BEACON_HOME  本插件自己的目录，放图标和 pid 文件，默认就是脚本所在目录
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dsh_state  # noqa: E402

HERE = Path(__file__).resolve().parent
# DSH 本体装在哪：默认按作者本机的布局，可用 DSH_ROOT 覆盖
DSH_ROOT = Path(os.environ.get("DSH_ROOT", str(Path.home() / "opt" / "DSH")))
# 状态灯自己的文件放哪：默认就是本脚本所在目录，可用 DSH_BEACON_HOME 覆盖
BEACON_HOME = Path(os.environ.get("DSH_BEACON_HOME", str(HERE)))
LOG = DSH_ROOT / "dsh.log"
TRAY_PIDFILE = BEACON_HOME / "tray.pid"
STOP_SCRIPT = DSH_ROOT / "stop-dsh.sh"
ICON_DIR = BEACON_HOME / "icons"
# 插件在 DSH 设置页里写的开关；文件不存在时默认开启
DSH_DIR = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
SETTINGS = DSH_DIR / "beacon-settings.json"
FOCUS = DSH_DIR / "beacon-focus.json"
PORT = int(os.environ.get("DSH_PORT", "3080"))
URL_RE = re.compile(rf"http://127\.0\.0\.1:{PORT}/\?token=\S+")
TICK_MS = 1500
# 黄灯闪烁的半周期：亮帧和全透明帧各停这么久，一个来回 700ms
BLINK_MS = 350


def port_in_use() -> bool:
    try:
        out = subprocess.check_output(
            ["ss", "-ltn", f"( sport = :{PORT} )"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return "LISTEN" in out
    except Exception:
        return False


def latest_token() -> str:
    try:
        text = LOG.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = URL_RE.findall(text)
    if not m:
        return ""
    return m[-1].split("token=", 1)[-1]


def session_url(session_id: str | None) -> str:
    """带 token 的会话深链；session 参数由 dsh-tray 客户端插件识别。"""
    token = latest_token()
    base = f"http://127.0.0.1:{PORT}/"
    params = []
    if token:
        params.append(f"token={token}")
    if session_id:
        params.append(f"session={session_id}")
    return base + ("?" + "&".join(params) if params else "")


def write_focus(session_id: str) -> None:
    """把「要跳去的会话」写成焦点文件，由 DSH 客户端插件轮询取走。

    不走 URL 参数：带 token 的地址会被服务端 303 到 ./，query 会被丢掉。
    """
    payload = json.dumps({"sessionId": session_id, "at": int(time.time() * 1000)})
    tmp = FOCUS.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(FOCUS)  # 原子替换，避免客户端读到半个文件


BROWSER_TITLE = "DeepSeek Harness"


GEOM_RE = re.compile(r"(\d+)x(\d+)\+-?\d+\+-?\d+")


def _find_browser_window() -> int | None:
    """找浏览器主窗口：优先标题就是 DSH 页面的那个，其次任意够大的 Chrome 窗口。"""
    try:
        out = subprocess.check_output(["xwininfo", "-root", "-tree"], text=True,
                                      stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    fallback = None
    for line in out.splitlines():
        low = line.lower()
        if "chrome" not in low and "chromium" not in low:
            continue
        m = GEOM_RE.search(line)
        if m is None or int(m.group(1)) < 400 or int(m.group(2)) < 300:
            continue  # 跳过剪贴板之类的隐藏小窗口
        try:
            wid = int(line.strip().split()[0], 16)
        except ValueError:
            continue
        if BROWSER_TITLE in line:
            return wid
        if fallback is None:
            fallback = wid
    return fallback


def activate_browser_window() -> bool:
    """把已打开的 DSH 页面窗口提到前台，而不是新开标签。

    走 EWMH 的 _NET_ACTIVE_WINDOW ClientMessage，和 wmctrl -a 是同一套协议。
    """
    wid = _find_browser_window()
    if wid is None:
        return False
    try:
        import ctypes
        import ctypes.util

        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        # 这几个必须声明参数类型：不声明时指针会被截成 int，直接段错误
        x11.XSendEvent.restype = ctypes.c_int
        x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                   ctypes.c_long, ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]

        class XClientMessageEvent(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int),
                ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int),
                ("data", ctypes.c_long * 5),
            ]

        dpy = x11.XOpenDisplay(None)
        if not dpy:
            return False
        root = x11.XDefaultRootWindow(dpy)
        atom = x11.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", False)

        ev = XClientMessageEvent()
        ev.type = 33  # ClientMessage
        ev.window = wid
        ev.message_type = atom
        ev.format = 32
        ev.data[0] = 1  # 来源：普通应用
        ev.data[1] = 0  # 时间戳
        x11.XSendEvent(dpy, root, False, ctypes.c_long(0xFFFFFF), ctypes.byref(ev))
        x11.XFlush(dpy)
        return True
    except Exception:
        return False


def focus_pending(session_id: str) -> bool:
    """焦点文件还没被页面消费，说明当前没有打开的 DSH 页面。"""
    try:
        doc = json.loads(FOCUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return doc.get("sessionId") == session_id

def jump_to(session_id: str | None) -> None:
    """跳到某个对话：有打开的页面就复用，没有才新开。"""
    if not session_id:
        open_url(None)
        return
    try:
        write_focus(session_id)
    except OSError as exc:
        print(f"[dsh-tray] 写焦点文件失败: {exc}", file=sys.stderr, flush=True)
        return
    # 页面在后台标签时定时器会被浏览器节流，所以先把窗口提到前台，它一可见就会立刻切
    activate_browser_window()

    def fallback() -> bool:
        if not focus_pending(session_id):
            return False  # 页面已经取走焦点，不需要再开浏览器
        if _find_browser_window() is None:
            # 确实没有浏览器窗口，才新开
            open_url(None)
        else:
            # 浏览器开着，只是 DSH 标签在后台被节流；再提一次窗口，等页面自己取
            activate_browser_window()
        return False

    GLib.timeout_add(2500, fallback)


def open_url(session_id: str | None = None) -> None:
    url = session_url(session_id)
    subprocess.Popen(
        ["xdg-open", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def read_settings() -> dict:
    try:
        with SETTINGS.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled() -> bool:
    return bool(read_settings().get("enabled", True))


STATE_TEXT = {"green": "全部完成", "yellow": "等你确认", "grey": "正在执行"}


class TrayApp:
    def __init__(self) -> None:
        self.status = {"state": "green", "pending": [], "running": []}
        self.last_key: tuple | None = None
        self.last_stamp: tuple = ()
        self.jump_item: Gtk.MenuItem | None = None
        # 黄灯闪烁的相位：True 是亮帧，False 是全透明帧
        self.blink_on = True

        self.indicator = AppIndicator3.Indicator.new(
            "dsh-beacon",
            self._icon_path("green"),
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title("dsh-beacon")
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._rebuild_menu()
        self.refresh()
        GLib.timeout_add(TICK_MS, self.refresh)
        GLib.timeout_add(BLINK_MS, self._blink_tick)

    # ---------- 图标 ----------

    @staticmethod
    def _icon_path(state: str) -> str:
        """返回带版本号的图标路径。

        关键是必须换路径：libayatana 的 set_icon_full(路径, 描述) 里第二个参数只是描述文本，
        Shell 侧按路径缓存图标内容，同一个路径换内容不会刷新。
        """
        path = ICON_DIR / f"dot-{state}.png"
        try:
            stamp = int(path.stat().st_mtime)
        except OSError:
            return {"green": "avatar-default-symbolic", "yellow": "dialog-warning-symbolic",
                    "yellow-off": "dialog-warning-symbolic",
                    "grey": "media-playback-pause-symbolic"}.get(state, "avatar-default-symbolic")
        versioned = ICON_DIR / f".dot-{state}-{stamp}.png"
        if not versioned.exists():
            try:
                shutil.copyfile(path, versioned)
            except OSError:
                return str(path)
            # 只留最近两个版本，别让图标目录无限增长。
            # 用 [0-9] 锚定版本号，避免 yellow 的清理把 yellow-off 的副本算进来
            olds = sorted(ICON_DIR.glob(f".dot-{state}-[0-9]*.png"),
                          key=lambda f: f.stat().st_mtime, reverse=True)
            for old in olds[2:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        return str(versioned)

    def _icon_stamp(self) -> tuple:
        """几张图标的修改时间，用来发现「图换了」。"""
        stamp = []
        for name in ("green", "yellow", "yellow-off", "grey"):
            try:
                stamp.append(int((ICON_DIR / f"dot-{name}.png").stat().st_mtime))
            except OSError:
                stamp.append(0)
        return tuple(stamp)

    def _apply_icon(self, state: str) -> None:
        # 名字里带上 mtime：GNOME 的 iconCache 按名字缓存，名字不变就不会重新读图
        self.indicator.set_icon_full(self._icon_path(state), f"dsh-beacon-{state}")

    def _blink_tick(self) -> bool:
        """待确认时快速闪烁：亮黄和全透明帧交替。其他状态不干预图标。"""
        if self.status.get("state") != "yellow":
            self.blink_on = True
            return True
        self.blink_on = not self.blink_on
        self._apply_icon("yellow" if self.blink_on else "yellow-off")
        return True

    # ---------- 菜单 ----------

    def _jump_to(self, session_id: str | None):
        def handler(_w=None):
            jump_to(session_id)
        return handler

    def _rebuild_menu(self) -> None:
        status = self.status
        state = status["state"]
        pending = status.get("pending") or []

        menu = Gtk.Menu()

        summary = Gtk.MenuItem(label=f"{STATE_TEXT.get(state, state)}（{len(pending)} 项待确认）"
                               if pending else STATE_TEXT.get(state, state))
        summary.set_sensitive(False)
        menu.append(summary)

        # 第一条就是「最早待确认」，中键触发的也是它
        if pending:
            first = pending[0]
            self.jump_item = Gtk.MenuItem(
                label=f"跳到：{first['title'][:28]}"
            )
            self.jump_item.connect("activate", self._jump_to(first["sessionId"]))
            menu.append(self.jump_item)

            if len(pending) > 1:
                for item in pending[1:6]:
                    sub = Gtk.MenuItem(label=f"· {item['title'][:28]}")
                    sub.connect("activate", self._jump_to(item["sessionId"]))
                    menu.append(sub)

        menu.append(Gtk.SeparatorMenuItem())

        item_open = Gtk.MenuItem(label="打开 DSH 网页")
        item_open.connect("activate", lambda *_: open_url(None))
        menu.append(item_open)

        item_state = Gtk.MenuItem(label="刷新状态")
        item_state.connect("activate", lambda *_: self.refresh())
        menu.append(item_state)

        menu.append(Gtk.SeparatorMenuItem())

        item_stop = None
        if STOP_SCRIPT.exists():
            item_stop = Gtk.MenuItem(label="停止 DSH")
            item_stop.connect("activate", self._stop_dsh)
            menu.append(item_stop)

        item_quit = Gtk.MenuItem(label="仅关闭状态灯")
        item_quit.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        self.indicator.set_menu(menu)
        # 中键走 SecondaryActivate，要显式指定激活哪个菜单项。
        # 必须等菜单真正挂到 indicator 上再设，否则 libayatana 记不住（会退回第一项）。
        self._secondary_target = self.jump_item or item_open
        GLib.idle_add(self._arm_secondary)

    def _arm_secondary(self) -> bool:
        self.indicator.set_secondary_activate_target(self._secondary_target)
        return False  # 只跑一次

    def _stop_dsh(self, _w=None) -> None:
        if STOP_SCRIPT.exists():
            subprocess.call([str(STOP_SCRIPT)])
        Gtk.main_quit()

    # ---------- 刷新 ----------

    def refresh(self) -> bool:
        if not port_in_use():
            Gtk.main_quit()  # DSH 退出了，灯也没必要留
            return False

        if not enabled():
            self.indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
            return True

        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        try:
            status = dsh_state.collect()
        except Exception as exc:  # 内部格式变了也不能把灯弄崩
            print(f"[dsh-tray] collect failed: {exc}", file=sys.stderr, flush=True)
            status = {"state": "grey", "pending": [], "running": []}

        key = (
            status["state"],
            tuple(p["sessionId"] + p["kind"] for p in status.get("pending") or []),
            tuple(r["sessionId"] for r in status.get("running") or []),
        )
        # 状态或图标文件有变化才重画，避免每 1.5 秒空转
        stamp = self._icon_stamp()
        if key == self.last_key and stamp == self.last_stamp:
            return True
        self.last_key = key
        self.last_stamp = stamp
        self.status = status
        self._apply_icon(status["state"])
        self._rebuild_menu()
        return True


def already_running() -> bool:
    try:
        pid = int(TRAY_PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return pid != os.getpid()
    except (OSError, ValueError):
        return False


def main() -> int:
    if already_running():
        return 0
    if not port_in_use():
        return 0

    TRAY_PIDFILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())

    TrayApp()
    try:
        Gtk.main()
    finally:
        try:
            if TRAY_PIDFILE.exists() and TRAY_PIDFILE.read_text().strip() == str(os.getpid()):
                TRAY_PIDFILE.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
