"""OSC 7501 reports for the CLI's terminal, separate from response output."""
from __future__ import annotations

import base64
from contextvars import ContextVar
import os
import sys


current_status = ContextVar("yantra_terminal_status", default=None)


class TerminalStatus:
    def __init__(self, stream=None):
        self.stream = sys.stderr if stream is None else stream
        self.enabled = self.stream.isatty() and os.getenv("TERM") != "dumb"

    def report(self, state, message, *, kind=None):
        if not self.enabled:
            return
        # Messages are a single UTF-8 line, capped at the protocol's decoded limit.
        message = "".join(" " if ord(c) < 32 or 127 <= ord(c) <= 159 else c for c in message)
        raw = message.encode("utf-8")[:2048].decode("utf-8", errors="ignore").encode("utf-8")
        pairs = ["state=" + state, "app=yantra"]
        if state == "blocked" and kind:
            pairs.append("kind=" + kind)
        pairs.append("msg=" + base64.b64encode(raw).decode("ascii"))
        try:
            self.stream.write("\x1b]7501;" + ":".join(pairs) + "\x1b\\")
            self.stream.flush()
        except OSError:
            # Optional terminal telemetry must not change command results.
            self.enabled = False

    def finish(self, command, result):
        if command in {"start", "resume", "replay"}:
            status = result.get("status")
            if status == "waiting":
                self.report("blocked", "等待研究参数确认；请使用 resume", kind="permission")
                return
            if status == "cancelled":
                self.report("idle", "研究已取消")
                return
            if status in {"partial", "failed"}:
                self.report("error", "研究未完成；请查看保存的结果及资料缺口")
                return
            if status == "running":
                self.report("working", "研究仍在运行")
                return
        self.report("done", "命令已完成")


def report(state, message, *, kind=None):
    status = current_status.get()
    if status is not None:
        status.report(state, message, kind=kind)
