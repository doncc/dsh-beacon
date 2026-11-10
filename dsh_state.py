#!/usr/bin/env python3
"""DSH 状态采集：把「有没有在等我确认 / 有没有在跑」算成一个三态值。

数据来源（全部只读，不改 DSH 任何东西）：
  1. ~/.dsh/storages/session_projcache/sessions/*.json
     · userQuestions.questions.active —— timed 模式（schema 带 timeout）下可继续回答的提问
     · turnBoundary.lastStepBoundary.kind —— start 表示这一步还没结束（在跑 / 在等），end 表示空闲
  2. ~/.dsh/sessions/<workspace>/session-<id>/session.v4.jsonl.zstd
     · approval/asked 与 approval/decided 配对，差集就是还没决定的权限审批
     · tool/call(name=ask_user_question) 与 tool/result(toolCallId) 配对，
       差集就是还没被回答的提问。当前 ask_user_question 的 schema 没有 timeout 参数，
       属于 blocking 模式，这种调用不会写进 userQuestions.active，只能从日志看。
     只有「在跑」或「有待答提问」的会话才解压日志，避免每次把 8MB 全读一遍。

输出：
  {"state": "green|yellow|grey", "pending": [...], "running": [...]}
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

DSH_HOME = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
PROJCACHE = DSH_HOME / "storages" / "session_projcache" / "sessions"
SESSIONS = DSH_HOME / "sessions"

# 会话日志解压结果缓存：path -> (mtime, size, {callId/approval 信息})
_LOG_CACHE: dict[str, tuple[float, int, dict]] = {}


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _log_paths(session_id: str) -> list[Path]:
    """会话日志在 ~/.dsh/sessions/<workspace>/<session-id>/ 下，工作区目录名不好反推，直接找。"""
    try:
        return list(SESSIONS.glob(f"*/{session_id}/session.v4.jsonl.zstd"))
    except OSError:
        return []


def _question_detail(arguments_text) -> str:
    """从 ask_user_question 的调用参数里取出第一个问题的正文，用作托盘菜单文案。"""
    try:
        args = json.loads(arguments_text or "{}")
        questions = args.get("questions") or []
        if questions:
            return questions[0].get("question") or ""
    except (ValueError, TypeError, AttributeError):
        pass
    return ""


def _scan_log(path: Path) -> dict:
    """解压会话日志，抽出审批与提问的时间线。

    返回三个字典：
      approvals      : approval id -> {"toolName", "time"}，asked 有、decided 没有 = 还在等授权
      questions      : callId -> {"time", "detail"}，tool/call 有、tool/result 没有 = 还在等回答
      question_times : callId -> 提问时间（含 timed 模式下已返回 pending 的调用）
    """
    st = path.stat()
    cached = _LOG_CACHE.get(str(path))
    if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]

    asked: dict[str, dict] = {}
    decided: set[str] = set()
    calls: dict[str, dict] = {}
    answered: set[str] = set()
    question_times: dict[str, int] = {}
    try:
        # zstd 能流式解压，这里一次读完；文件是追加写的，所以按 seq 顺序排列
        out = subprocess.run(
            ["zstd", "-dc", str(path)],
            capture_output=True,
            timeout=15,
            check=False,
        ).stdout
        for raw in out.splitlines():
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            kind = ev.get("type")
            data = ev.get("data") or {}
            stamp = int(ev.get("time") or 0)
            if kind == "approval/asked":
                asked[str(data.get("id"))] = {
                    "toolName": data.get("toolName"),
                    "time": stamp,
                }
            elif kind == "approval/decided":
                decided.add(str(data.get("id")))
            elif kind == "tool/call" and data.get("name") == "ask_user_question":
                call_id = str(data.get("callId"))
                calls[call_id] = {
                    "time": stamp,
                    "detail": _question_detail(data.get("arguments")),
                }
                question_times[call_id] = stamp
            elif kind == "tool/result":
                tool_call_id = str((data.get("message") or {}).get("toolCallId"))
                if tool_call_id in calls:
                    answered.add(tool_call_id)
    except (OSError, subprocess.SubprocessError):
        pass

    info = {
        "approvals": {k: v for k, v in asked.items() if k not in decided},
        "questions": {k: v for k, v in calls.items() if k not in answered},
        "question_times": question_times,
    }
    _LOG_CACHE[str(path)] = (st.st_mtime, st.st_size, info)
    return info


def collect() -> dict:
    """扫描全部会话，聚合出全局状态。"""
    pending: list[dict] = []
    running: list[dict] = []

    try:
        files = sorted(PROJCACHE.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []

    for path in files:
        doc = _read_json(path)
        if not doc:
            continue
        rows = (doc.get("record") or {}).get("rows") or {}
        session_id = path.stem

        title = (rows.get("title") or {}).get("val") or session_id
        cwd = (rows.get("workingDirectory") or {}).get("val") or ""
        tb = (rows.get("turnBoundary") or {}).get("val") or {}
        questions = (
            (rows.get("userQuestions") or {}).get("val", {}).get("questions", {}) or {}
        )
        active_questions = questions.get("active") or []
        step_kind = (tb.get("lastStepBoundary") or {}).get("kind")
        is_running = step_kind == "start"

        # 「在跑」同时是「在等」的前提：提问和审批都发生在 step 内部，
        # 只要这一步没结束，日志里没有结果的调用就表示还在等我。
        if not active_questions and not is_running:
            continue  # 这个会话既没在跑也没在等我，跳过（省掉解压）

        log_info = {"approvals": {}, "questions": {}, "question_times": {}}
        for lp in _log_paths(session_id):
            log_info = _scan_log(lp)

        # 提问：按 callId 去重，timed 模式（projection 里挂着）和 blocking 模式（日志里没结果）都能覆盖
        by_call: dict[str, dict] = {}
        for q in active_questions:
            call_id = str(q.get("callId"))
            detail = ""
            if isinstance(q.get("questions"), list) and q["questions"]:
                detail = q["questions"][0].get("question", "")
            by_call[call_id] = {
                "sessionId": session_id,
                "title": title,
                "cwd": cwd,
                "kind": "question",
                "since": log_info["question_times"].get(call_id, 0),
                "detail": detail,
            }
        for call_id, meta in log_info["questions"].items():
            by_call.setdefault(
                call_id,
                {
                    "sessionId": session_id,
                    "title": title,
                    "cwd": cwd,
                    "kind": "question",
                    "since": meta.get("time") or 0,
                    "detail": meta.get("detail") or "",
                },
            )
        pending.extend(by_call.values())

        # 权限审批：日志里 asked 过、还没 decided 的
        for aid, meta in log_info["approvals"].items():
            pending.append(
                {
                    "sessionId": session_id,
                    "title": title,
                    "cwd": cwd,
                    "kind": "approval",
                    "since": meta.get("time") or 0,
                    "detail": meta.get("toolName") or "",
                }
            )

        if is_running:
            running.append({"sessionId": session_id, "title": title, "cwd": cwd})

    pending.sort(key=lambda p: (p["since"] or 0, p["sessionId"]))
    running.sort(key=lambda r: r["title"])

    if pending:
        state = "yellow"
    elif running:
        state = "grey"
    else:
        state = "green"

    return {
        "version": 1,
        "state": state,
        "updatedAt": int(time.time() * 1000),
        "pending": pending,
        "running": running,
    }


if __name__ == "__main__":
    import sys

    t0 = time.time()
    print(json.dumps(collect(), ensure_ascii=False, indent=1))
    print(f"\n耗时 {(time.time() - t0) * 1000:.0f} ms", file=sys.stderr)
