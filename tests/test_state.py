#!/usr/bin/env python3
"""状态机的离线测试：造一套假的 DSH 目录，验证三色与排序。

不碰真实 ~/.dsh，只通过 DSH_HOME 指向临时目录。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def build_home(tmp: Path, sessions: list[dict]) -> None:
    proj = tmp / "storages" / "session_projcache" / "sessions"
    proj.mkdir(parents=True, exist_ok=True)
    for s in sessions:
        sid = s["id"]
        doc = {
            "version": 7,
            "record": {
                "rows": {
                    "title": {"val": s.get("title", sid)},
                    "workingDirectory": {"val": "/tmp/ws"},
                    "turnBoundary": {
                        "val": {"lastStepBoundary": {"kind": s.get("step", "end")}}
                    },
                    "userQuestions": {
                        "val": {
                            "questions": {
                                "active": s.get("questions", []),
                                "settled": [],
                            }
                        }
                    },
                }
            },
        }
        (proj / f"{sid}.json").write_text(json.dumps(doc), encoding="utf-8")

        events = []
        for i, ev in enumerate(s.get("events", [])):
            events.append(json.dumps(ev))
        if events:
            logdir = tmp / "sessions" / "--ws--" / sid
            logdir.mkdir(parents=True, exist_ok=True)
            raw = ("\n".join(events) + "\n").encode()
            subprocess.run(
                ["zstd", "-q", "-f", "-o", str(logdir / "session.v4.jsonl.zstd")],
                input=raw,
                check=True,
            )


def run(home: Path) -> dict:
    env = dict(os.environ, DSH_HOME=str(home))
    out = subprocess.run(
        [sys.executable, "-c", "import json,dsh_state;print(json.dumps(dsh_state.collect()))"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


CASES = {
    "全空闲应为绿": {
        "sessions": [{"id": "s-a", "title": "A", "step": "end"}],
        "expect": "green",
    },
    "在跑应为灰": {
        "sessions": [{"id": "s-b", "title": "B", "step": "start"}],
        "expect": "grey",
    },
    "有待答提问应为黄": {
        "sessions": [
            {
                "id": "s-c",
                "title": "C",
                "step": "start",
                "questions": [
                    {"callId": "call-1", "state": "open", "questions": [{"question": "选哪个？"}]}
                ],
                "events": [
                    {"type": "tool/call", "seq": 5, "time": 1000,
                     "data": {"callId": "call-1", "name": "ask_user_question", "arguments": "{}"}}
                ],
            }
        ],
        "expect": "yellow",
    },
    "未决审批应为黄": {
        "sessions": [
            {
                "id": "s-d",
                "title": "D",
                "step": "start",
                "events": [
                    {"type": "approval/asked", "seq": 7, "time": 2000,
                     "data": {"id": "ap-1", "toolName": "bash"}},
                ],
            }
        ],
        "expect": "yellow",
    },
    "已批准的审批不应算黄": {
        "sessions": [
            {
                "id": "s-e",
                "title": "E",
                "step": "end",
                "events": [
                    {"type": "approval/asked", "seq": 7, "time": 2000,
                     "data": {"id": "ap-1", "toolName": "bash"}},
                    {"type": "approval/decided", "seq": 9, "time": 2500,
                     "data": {"id": "ap-1", "outcome": "allowed-once"}},
                ],
            }
        ],
        "expect": "green",
    },
    "多条待确认按最早排序": {
        "sessions": [
            {
                "id": "s-f",
                "title": "晚的",
                "step": "start",
                "questions": [{"callId": "call-late", "state": "open", "questions": [{"question": "2"}]}],
                "events": [
                    {"type": "tool/call", "seq": 5, "time": 9000,
                     "data": {"callId": "call-late", "name": "ask_user_question", "arguments": "{}"}}
                ],
            },
            {
                "id": "s-g",
                "title": "早的",
                "step": "start",
                "questions": [{"callId": "call-early", "state": "open", "questions": [{"question": "1"}]}],
                "events": [
                    {"type": "tool/call", "seq": 3, "time": 1000,
                     "data": {"callId": "call-early", "name": "ask_user_question", "arguments": "{}"}}
                ],
            },
        ],
        "expect": "yellow",
        "first_title": "早的",
    },
}


def main() -> int:
    failed = 0
    for name, case in CASES.items():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            build_home(tmp, case["sessions"])
            got = run(tmp)
        ok = got["state"] == case["expect"]
        extra = ""
        if "first_title" in case:
            first = got["pending"][0]["title"] if got["pending"] else None
            ok = ok and first == case["first_title"]
            extra = f"  第一条={first}"
        print(f"{'PASS' if ok else 'FAIL'}  {name}: state={got['state']} 期望={case['expect']}{extra}")
        failed += 0 if ok else 1
    print(f"\n{len(CASES) - failed}/{len(CASES)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
