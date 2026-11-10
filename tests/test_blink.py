#!/usr/bin/env python3
"""验证黄灯闪烁逻辑，不依赖真实的 DSH 状态。

用 object.__new__ 绕过 TrayApp.__init__（那会去连 AppIndicator / DBus），
只把 indicator 换成记录调用的假对象，然后直接调用 _blink_tick。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("DSH_ROOT", str(Path.home() / "opt" / "DSH"))
import tray  # noqa: E402


class FakeIndicator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def set_icon_full(self, path: str, desc: str) -> None:
        self.calls.append((os.path.basename(path), desc))


def make_app(state: str) -> tray.TrayApp:
    app = object.__new__(tray.TrayApp)
    app.status = {"state": state, "pending": [], "running": []}
    app.blink_on = True
    app.indicator = FakeIndicator()
    return app


def names(app: tray.TrayApp) -> list[str]:
    """把版本号从文件名里去掉，只看是亮黄还是暗黄。"""
    out = []
    for base, _desc in app.indicator.calls:
        for state in ("yellow-off", "yellow", "grey", "green"):
            if base.startswith(f".dot-{state}-") or base == f"dot-{state}.png":
                out.append(state)
                break
    return out


def main() -> int:
    ok = True

    app = make_app("yellow")
    for _ in range(6):
        app._blink_tick()
    got = names(app)
    want = ["yellow-off", "yellow", "yellow-off", "yellow", "yellow-off", "yellow"]
    ok &= got == want
    print(f"{'PASS' if got == want else 'FAIL'} 黄灯在亮帧/透明帧之间交替: {got}")

    # 非黄色状态不许动图标
    for state in ("grey", "green"):
        app = make_app(state)
        for _ in range(4):
            app._blink_tick()
        ok &= app.indicator.calls == []
        print(f"{'PASS' if not app.indicator.calls else 'FAIL'} {state} 不干预: {len(app.indicator.calls)} 次调用")

    # 黄灯期间相位要复位，避免状态切回来时从暗相开始
    app = make_app("yellow")
    app._blink_tick()
    app.status["state"] = "grey"
    app._blink_tick()
    reset = app.blink_on is True
    print(f"{'PASS' if reset else 'FAIL'} 离开黄色后相位复位为亮: blink_on={app.blink_on}")
    ok &= reset

    # 回到黄色时先显示亮黄，再翻到暗黄
    app.indicator.calls.clear()
    app.status["state"] = "yellow"
    app._blink_tick()
    back = app.blink_on is False and names(app) == ["yellow-off"]
    print(f"{'PASS' if back else 'FAIL'} 回到黄色继续闪烁: {names(app)}")
    ok &= back

    print("\n全部通过" if ok else "\n有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
