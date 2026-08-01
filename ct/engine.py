"""The engine: long-lived `claude` processes speaking stream-json, normalised
into a replayable event log that any number of browsers can subscribe to.

One CodeTails session == one Claude Code session id. If the child process dies
(or you change model / permission mode) we respawn with `--resume`, so the
conversation survives and your phone never notices.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import shutil
import subprocess
import threading
import time
import uuid

from . import projects

MAX_EVENTS = 20000

MODELS = [
    {"id": "default", "label": "Default"},
    {"id": "fable", "label": "Fable 5"},
    {"id": "opus", "label": "Opus 5"},
    {"id": "sonnet", "label": "Sonnet 5"},
    {"id": "haiku", "label": "Haiku 4.5"},
]

PERMISSION_MODES = [
    {"id": "default", "label": "Default", "hint": "asks — denies in headless"},
    {"id": "plan", "label": "Plan", "hint": "read-only, plans first"},
    {"id": "acceptEdits", "label": "Accept edits", "hint": "file edits auto-approved"},
    {"id": "bypassPermissions", "label": "Bypass", "hint": "no guardrails"},
]

EFFORTS = ["default", "low", "medium", "high", "xhigh", "max"]


def find_claude() -> str:
    candidates = [
        shutil.which("claude"),
        os.path.expanduser("~/.npm-global/bin/claude"),
        os.path.expanduser("~/.claude/local/claude"),
        os.path.expanduser("~/.local/bin/claude"),
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("Could not find the `claude` CLI on this machine.")


def _child_env() -> dict:
    env = dict(os.environ)
    extra = [os.path.expanduser("~/.npm-global/bin"), os.path.expanduser("~/.claude/local"),
             "/opt/homebrew/bin", "/usr/local/bin"]
    path = env.get("PATH", "")
    for p in extra:
        if p not in path:
            path = path + ":" + p
    env["PATH"] = path
    env["CLAUDE_CODE_ENTRYPOINT"] = "codetails"
    return env


class Session:
    def __init__(self, manager: "SessionManager", cwd: str, model: str = "default",
                 permission_mode: str = "acceptEdits", resume: str | None = None,
                 allowed_tools: list[str] | None = None, effort: str = "default",
                 title: str | None = None):
        self.manager = manager
        self.id = uuid.uuid4().hex[:12]
        self.cwd = os.path.abspath(os.path.expanduser(cwd))
        self.model = model
        self.permission_mode = permission_mode
        self.effort = effort
        self.allowed_tools = list(allowed_tools or [])
        self.claude_session_id = resume or str(uuid.uuid4())
        self._resume_next = bool(resume)
        self.title = title or os.path.basename(self.cwd) or self.cwd
        self.created = time.time()
        self.last_activity = time.time()

        self.status = "starting"          # starting | idle | running | exited
        self.activity = ""                # e.g. "requesting"
        self.turn_started: float | None = None
        self.stats = {
            "cost": 0.0, "turns": 0, "input": 0, "output": 0,
            "cache_read": 0, "context": 0, "window": 0, "duration": 0,
        }
        self.rate_limit: dict | None = None
        self.slash_commands: list[dict] = []
        self.tools: list[str] = []
        self.stderr_tail: list[str] = []

        self.events: list[dict] = []
        self.seq = 0
        self._subs: set[queue.Queue] = set()
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._blocks: dict[int, dict] = {}
        self._cur_mid: str | None = None
        self._pending_perms: dict[str, dict] = {}
        self._closed = False

        self.start()

    # ------------------------------------------------------------------ plumbing
    def _argv(self) -> list[str]:
        argv = [find_claude(), "--print",
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--verbose", "--include-partial-messages"]
        if self._resume_next:
            argv += ["--resume", self.claude_session_id]
        else:
            argv += ["--session-id", self.claude_session_id]
        if self.model and self.model != "default":
            argv += ["--model", self.model]
        if self.permission_mode and self.permission_mode != "default":
            argv += ["--permission-mode", self.permission_mode]
        if self.effort and self.effort != "default":
            argv += ["--effort", self.effort]
        if self.allowed_tools:
            argv += ["--allowedTools"] + self.allowed_tools
        return argv

    def start(self) -> None:
        argv = self._argv()
        self.emit({"t": "notice", "kind": "spawn",
                   "text": ("resuming " if self._resume_next else "starting ") + self.claude_session_id[:8],
                   "argv": " ".join(a.replace(os.path.expanduser("~"), "~") for a in argv)})
        try:
            self._proc = subprocess.Popen(
                argv, cwd=self.cwd, env=_child_env(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, errors="replace", start_new_session=True,
            )
        except Exception as exc:  # pragma: no cover - depends on host
            self.status = "exited"
            self.emit({"t": "error", "text": f"could not start claude: {exc}"})
            return

        self._resume_next = True     # every future respawn resumes
        self.status = "idle"
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self._send_raw({"type": "control_request", "request_id": "ct-init",
                        "request": {"subtype": "initialize", "hooks": None}})
        self.emit({"t": "session", **self.meta()})

    def _pump_stdout(self) -> None:
        proc = self._proc
        assert proc and proc.stdout
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    self.emit({"t": "notice", "kind": "raw", "text": line[:2000]})
                    continue
                try:
                    self._handle(obj)
                except Exception as exc:
                    self.emit({"t": "error", "text": f"event handling failed: {exc}"})
        except Exception:
            pass
        code = proc.poll()
        if proc is self._proc and not self._closed:
            self.status = "exited"
            self.turn_started = None
            tail = " / ".join(self.stderr_tail[-3:])
            self.emit({"t": "exit", "code": code, "text": tail})
            self.emit({"t": "session", **self.meta()})

    def _pump_stderr(self) -> None:
        proc = self._proc
        assert proc and proc.stderr
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                self.stderr_tail = (self.stderr_tail + [line])[-40:]
                self.emit({"t": "stderr", "text": line[:1000]})
        except Exception:
            pass

    def _send_raw(self, obj: dict) -> bool:
        with self._write_lock:
            proc = self._proc
            if not proc or proc.poll() is not None or not proc.stdin:
                return False
            try:
                proc.stdin.write(json.dumps(obj) + "\n")
                proc.stdin.flush()
                return True
            except Exception:
                return False

    # ------------------------------------------------------------------ events
    def emit(self, ev: dict) -> None:
        with self._lock:
            self.seq += 1
            ev["seq"] = self.seq
            ev["sid"] = self.id
            ev.setdefault("ts", time.time())
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                del self.events[: len(self.events) - MAX_EVENTS]
            self.last_activity = time.time()
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(ev)
                except Exception:
                    dead.append(q)
            for q in dead:
                self._subs.discard(q)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=4000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def replay(self, since: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] > since]

    # ------------------------------------------------------------------ handling
    def _handle(self, obj: dict) -> None:
        typ = obj.get("type")

        if typ == "system":
            sub = obj.get("subtype")
            if sub == "init":
                self.claude_session_id = obj.get("session_id") or self.claude_session_id
                self.tools = obj.get("tools") or self.tools
                cmds = obj.get("slash_commands") or []
                if cmds and not self.slash_commands:
                    self.slash_commands = [{"name": c} for c in cmds]
                self.model_actual = obj.get("model")
                self.emit({"t": "session", **self.meta()})
            elif sub == "status":
                self.activity = obj.get("status") or ""
                if self.activity:
                    self.status = "running"
                    self.turn_started = self.turn_started or time.time()
                self.emit({"t": "status", "status": self.activity})
            elif sub == "post_turn_summary":
                self.emit({"t": "summary", "text": obj.get("status_detail"),
                           "category": obj.get("status_category"),
                           "needs_action": obj.get("needs_action")})
            return

        if typ == "stream_event":
            self._handle_stream(obj.get("event") or {}, obj.get("parent_tool_use_id"))
            return

        if typ == "assistant":
            msg = obj.get("message") or {}
            mid = msg.get("id")
            parent = obj.get("parent_tool_use_id")
            self.status = "running"
            self.turn_started = self.turn_started or time.time()
            usage = msg.get("usage") or {}
            if usage:
                ctx = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                       + usage.get("cache_creation_input_tokens", 0) + usage.get("output_tokens", 0))
                self.stats["context"] = max(self.stats["context"], ctx)
            for i, b in enumerate(msg.get("content") or []):
                bt = b.get("type")
                if bt == "text" and b.get("text"):
                    self.emit({"t": "text", "mid": mid, "idx": i, "text": b["text"],
                               "final": True, "parent": parent})
                elif bt == "thinking" and b.get("thinking"):
                    self.emit({"t": "thinking", "mid": mid, "idx": i, "text": b["thinking"],
                               "final": True, "parent": parent})
                elif bt == "tool_use":
                    self.emit({"t": "tool_use", "id": b.get("id"), "name": b.get("name"),
                               "input": b.get("input") or {}, "parent": parent, "final": True})
            self._blocks.clear()
            return

        if typ == "user":
            msg = obj.get("message") or {}
            content = msg.get("content")
            details = obj.get("tool_use_result")
            if isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_result":
                        self.emit({
                            "t": "tool_result",
                            "id": b.get("tool_use_id"),
                            "ok": not b.get("is_error"),
                            "content": projects._flatten(b.get("content")),
                            "details": details if isinstance(details, dict) else None,
                            "parent": obj.get("parent_tool_use_id"),
                        })
            return

        if typ == "result":
            self.status = "idle"
            self.activity = ""
            self.turn_started = None
            usage = obj.get("usage") or {}
            self.stats["cost"] += float(obj.get("total_cost_usd") or 0)
            self.stats["turns"] += int(obj.get("num_turns") or 0)
            self.stats["input"] += int(usage.get("input_tokens") or 0)
            self.stats["output"] += int(usage.get("output_tokens") or 0)
            self.stats["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            self.stats["duration"] += int(obj.get("duration_ms") or 0)
            for mu in (obj.get("modelUsage") or {}).values():
                if mu.get("contextWindow"):
                    self.stats["window"] = max(self.stats["window"], int(mu["contextWindow"]))
            self.emit({"t": "result",
                       "is_error": bool(obj.get("is_error")),
                       "subtype": obj.get("subtype"),
                       "text": obj.get("result") if obj.get("is_error") else None,
                       "cost": obj.get("total_cost_usd"),
                       "duration": obj.get("duration_ms"),
                       "stop_reason": obj.get("stop_reason"),
                       "denials": obj.get("permission_denials") or [],
                       "stats": dict(self.stats)})
            self.emit({"t": "session", **self.meta()})
            return

        if typ == "rate_limit_event":
            self.rate_limit = obj.get("rate_limit_info")
            self.emit({"t": "rate_limit", "info": self.rate_limit})
            return

        if typ == "control_request":
            self._handle_control_request(obj)
            return

        if typ == "control_response":
            resp = (obj.get("response") or {}).get("response") or {}
            cmds = resp.get("commands")
            if cmds:
                self.slash_commands = [
                    {"name": c.get("name"), "description": (c.get("description") or "")[:160]}
                    for c in cmds if isinstance(c, dict) and c.get("name")
                ]
                self.emit({"t": "commands", "commands": self.slash_commands})
            return

    def _handle_stream(self, ev: dict, parent: str | None) -> None:
        etype = ev.get("type")
        if etype == "message_start":
            self._cur_mid = (ev.get("message") or {}).get("id")
            self._blocks.clear()
            self.status = "running"
            self.turn_started = self.turn_started or time.time()
            self.emit({"t": "turn_start", "mid": self._cur_mid})
        elif etype == "content_block_start":
            idx = ev.get("index", 0)
            block = ev.get("content_block") or {}
            self._blocks[idx] = {"type": block.get("type"), "json": "",
                                 "id": block.get("id"), "name": block.get("name")}
            if block.get("type") == "tool_use":
                self.emit({"t": "tool_use", "id": block.get("id"), "name": block.get("name"),
                           "input": {}, "pending": True, "parent": parent})
        elif etype == "content_block_delta":
            idx = ev.get("index", 0)
            delta = ev.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta" and delta.get("text"):
                self.emit({"t": "delta", "kind": "text", "mid": self._cur_mid,
                           "idx": idx, "text": delta["text"], "parent": parent})
            elif dtype == "thinking_delta" and delta.get("thinking"):
                self.emit({"t": "delta", "kind": "thinking", "mid": self._cur_mid,
                           "idx": idx, "text": delta["thinking"], "parent": parent})
            elif dtype == "input_json_delta":
                slot = self._blocks.setdefault(idx, {"type": "tool_use", "json": ""})
                slot["json"] = slot.get("json", "") + (delta.get("partial_json") or "")
        elif etype == "content_block_stop":
            idx = ev.get("index", 0)
            slot = self._blocks.get(idx) or {}
            if slot.get("type") == "tool_use" and slot.get("id"):
                try:
                    parsed = json.loads(slot.get("json") or "{}")
                except Exception:
                    parsed = {"_raw": slot.get("json", "")[:4000]}
                self.emit({"t": "tool_use", "id": slot["id"], "name": slot.get("name"),
                           "input": parsed, "parent": parent})
        elif etype == "message_delta":
            usage = ev.get("usage") or {}
            if usage.get("output_tokens"):
                self.emit({"t": "usage", "output": usage.get("output_tokens")})

    def _handle_control_request(self, obj: dict) -> None:
        req = obj.get("request") or {}
        rid = obj.get("request_id")
        sub = req.get("subtype")
        if sub == "can_use_tool":
            auto = self.permission_mode in ("bypassPermissions", "acceptEdits")
            if auto:
                self._send_raw({"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid,
                    "response": {"behavior": "allow", "updatedInput": req.get("input", {})}}})
                return
            self._pending_perms[rid] = {"tool": req.get("tool_name"), "input": req.get("input")}
            self.emit({"t": "permission", "id": rid, "tool": req.get("tool_name"),
                       "input": req.get("input"), "suggestions": req.get("permission_suggestions")})
        else:
            self._send_raw({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": {}}})

    def answer_permission(self, rid: str, allow: bool) -> bool:
        pending = self._pending_perms.pop(rid, None)
        if pending is None:
            return False
        if allow:
            body = {"behavior": "allow", "updatedInput": pending.get("input") or {}}
        else:
            body = {"behavior": "deny", "message": "Denied from CodeTails"}
        ok = self._send_raw({"type": "control_response", "response": {
            "subtype": "success", "request_id": rid, "response": body}})
        self.emit({"t": "permission_done", "id": rid, "allow": allow})
        return ok

    # ------------------------------------------------------------------ actions
    def meta(self) -> dict:
        return {
            "id": self.id,
            "claude_session_id": self.claude_session_id,
            "cwd": self.cwd,
            "title": self.title,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "effort": self.effort,
            "allowed_tools": self.allowed_tools,
            "status": self.status,
            "activity": self.activity,
            "turn_started": self.turn_started,
            "stats": dict(self.stats),
            "rate_limit": self.rate_limit,
            "created": self.created,
            "last_activity": self.last_activity,
            "alive": bool(self._proc and self._proc.poll() is None),
            "commands": self.slash_commands,
        }

    def send(self, text: str) -> None:
        if not text.strip():
            return
        if self.title in ("", None) or self.title == os.path.basename(self.cwd):
            self.title = text.strip().replace("\n", " ")[:60] or self.title
        self.emit({"t": "user", "text": text})
        self.status = "running"
        self.turn_started = time.time()
        self.emit({"t": "session", **self.meta()})
        payload = {"type": "user", "message": {"role": "user", "content": text}}
        if not self._send_raw(payload):
            self.emit({"t": "notice", "kind": "respawn", "text": "process was gone — resuming session"})
            self.start()
            for _ in range(60):
                if self._proc and self._proc.poll() is None:
                    break
                time.sleep(0.05)
            if not self._send_raw(payload):
                self.status = "exited"
                self.emit({"t": "error", "text": "could not deliver message to claude"})

    def interrupt(self) -> None:
        self.emit({"t": "notice", "kind": "interrupt", "text": "interrupting…"})
        sent = self._send_raw({"type": "control_request",
                               "request_id": f"ct-int-{int(time.time()*1000)}",
                               "request": {"subtype": "interrupt"}})

        def escalate() -> None:
            time.sleep(2.5)
            if self.status == "running" and self._proc and self._proc.poll() is None:
                try:
                    self._proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
                time.sleep(2.0)
                if self.status == "running" and self._proc and self._proc.poll() is None:
                    try:
                        os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                    except Exception:
                        try:
                            self._proc.terminate()
                        except Exception:
                            pass
                    self.status = "exited"
                    self.emit({"t": "notice", "kind": "interrupt",
                               "text": "stopped — next message resumes the session"})
                    self.emit({"t": "session", **self.meta()})

        if sent:
            threading.Thread(target=escalate, daemon=True).start()
        else:
            threading.Thread(target=escalate, daemon=True).start()
        self.status = "idle"
        self.turn_started = None

    def reconfigure(self, model: str | None = None, permission_mode: str | None = None,
                    effort: str | None = None, allow_tool: str | None = None) -> None:
        changed = []
        if model and model != self.model:
            self.model = model
            changed.append(f"model → {model}")
        if permission_mode and permission_mode != self.permission_mode:
            self.permission_mode = permission_mode
            changed.append(f"permissions → {permission_mode}")
        if effort and effort != self.effort:
            self.effort = effort
            changed.append(f"effort → {effort}")
        if allow_tool and allow_tool not in self.allowed_tools:
            self.allowed_tools.append(allow_tool)
            changed.append(f"allowed {allow_tool}")
        if not changed:
            return
        self._kill_proc()
        self.emit({"t": "notice", "kind": "config", "text": ", ".join(changed) + " (session resumed)"})
        self.start()

    def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def close(self) -> None:
        self._closed = True
        self._kill_proc()
        self.status = "closed"
        self.emit({"t": "notice", "kind": "closed", "text": "session closed"})


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def create(self, **kwargs) -> Session:
        s = Session(self, **kwargs)
        with self._lock:
            self.sessions[s.id] = s
        return s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def list(self) -> list[dict]:
        with self._lock:
            items = [s.meta() for s in self.sessions.values()]
        items.sort(key=lambda m: m["last_activity"], reverse=True)
        return items

    def close(self, sid: str) -> bool:
        with self._lock:
            s = self.sessions.pop(sid, None)
        if s:
            s.close()
            return True
        return False

    def close_all(self) -> None:
        with self._lock:
            items = list(self.sessions.values())
            self.sessions.clear()
        for s in items:
            s.close()
