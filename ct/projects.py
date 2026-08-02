"""Reading the Claude Code side of the house: projects, past sessions, transcripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import time

CLAUDE_HOME = os.path.expanduser("~/.claude")
PROJECTS_DIR = os.path.join(CLAUDE_HOME, "projects")

_meta_cache: dict[str, tuple[float, dict]] = {}

SKIP_TYPES = {
    "queue-operation", "attachment", "last-prompt", "custom-title", "ai-title",
    "mode", "file-history-snapshot", "summary", "diagnostics", "compact-boundary",
}


# --------------------------------------------------------------------------- projects
def _head_json(path: str, limit: int = 400_000):
    """Yield parsed objects from the head of a jsonl file."""
    read = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                read += len(line)
                if read > limit:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def _tail_lines(path: str, nbytes: int = 200_000) -> list[str]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - nbytes))
            chunk = fh.read().decode("utf-8", "replace")
        return chunk.splitlines()[1:] if size > nbytes else chunk.splitlines()
    except OSError:
        return []


def session_meta(path: str) -> dict:
    st = os.stat(path)
    key = f"{path}:{st.st_mtime_ns}:{st.st_size}"
    hit = _meta_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]

    title = None
    first_prompt = None
    cwd = None
    branch = None
    version = None
    for obj in _head_json(path, 200_000):
        cwd = cwd or obj.get("cwd")
        branch = branch or obj.get("gitBranch")
        version = version or obj.get("version")
        if first_prompt is None and obj.get("type") == "user":
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                first_prompt = content.strip()
            elif isinstance(content, list):
                for b in content:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        first_prompt = b["text"].strip()
                        break
        if cwd and first_prompt:
            break

    for raw in reversed(_tail_lines(path)):
        if '"custom-title"' in raw or '"ai-title"' in raw:
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            title = obj.get("customTitle") or obj.get("aiTitle")
            if title:
                break

    if not title and first_prompt:
        title = first_prompt.replace("\n", " ")[:70]

    meta = {
        "id": os.path.splitext(os.path.basename(path))[0],
        "path": path,
        "title": title or "untitled session",
        "preview": (first_prompt or "").replace("\n", " ")[:160],
        "cwd": cwd,
        "branch": branch,
        "version": version,
        "mtime": st.st_mtime,
        "size": st.st_size,
    }
    _meta_cache[path] = (key, meta)
    return meta


def list_projects() -> list[dict]:
    """One entry per working directory. Claude Code sometimes keeps several
    slug folders for the same path, so fold them together."""
    merged: dict[str, dict] = {}
    if not os.path.isdir(PROJECTS_DIR):
        return []
    for name in os.listdir(PROJECTS_DIR):
        pdir = os.path.join(PROJECTS_DIR, name)
        if not os.path.isdir(pdir):
            continue
        files = [os.path.join(pdir, f) for f in os.listdir(pdir) if f.endswith(".jsonl")]
        if not files:
            continue
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        newest = session_meta(files[0])
        cwd = newest.get("cwd") or "/" + name.strip("-").replace("-", "/")
        mtime = os.path.getmtime(files[0])
        entry = merged.get(cwd)
        if entry is None:
            merged[cwd] = {
                "slug": name,
                "slugs": [name],
                "cwd": cwd,
                "name": os.path.basename(cwd.rstrip("/")) or cwd,
                "sessions": len(files),
                "mtime": mtime,
                "exists": os.path.isdir(cwd),
                "last_title": newest.get("title"),
            }
        else:
            entry["slugs"].append(name)
            entry["sessions"] += len(files)
            if mtime > entry["mtime"]:
                entry.update({"slug": name, "mtime": mtime, "last_title": newest.get("title")})

    out = list(merged.values())
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def list_sessions(slug: str, limit: int = 60) -> list[dict]:
    """`slug` may be a single folder name or several joined by '|'."""
    files: list[str] = []
    for part in slug.split("|"):
        pdir = os.path.join(PROJECTS_DIR, part)
        if not os.path.isdir(pdir):
            continue
        files += [os.path.join(pdir, f) for f in os.listdir(pdir) if f.endswith(".jsonl")]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return [session_meta(p) for p in files[:limit]]


def slug_for_cwd(cwd: str) -> str:
    return cwd.replace("/", "-").replace(".", "-").replace("_", "-")


def find_transcript(session_id: str) -> str | None:
    if not os.path.isdir(PROJECTS_DIR):
        return None
    for name in os.listdir(PROJECTS_DIR):
        candidate = os.path.join(PROJECTS_DIR, name, session_id + ".jsonl")
        if os.path.exists(candidate):
            return candidate
    return None


# --------------------------------------------------------------------------- transcript
def transcript_events(path: str, max_events: int = 4000) -> list[dict]:
    """Convert a stored jsonl transcript into CodeTails' normalised event stream,
    so history renders through exactly the same code path as a live session."""
    events: list[dict] = []
    seq = 0

    def emit(ev: dict) -> None:
        nonlocal seq
        seq += 1
        ev["seq"] = seq
        events.append(ev)

    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return events

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            typ = obj.get("type")
            if typ in SKIP_TYPES:
                continue
            side = bool(obj.get("isSidechain"))
            ts = obj.get("timestamp")
            msg = obj.get("message") or {}

            if typ == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    if content.strip() and not obj.get("isMeta"):
                        emit({"t": "user", "text": content, "ts": ts, "side": side})
                elif isinstance(content, list):
                    for b in content:
                        bt = b.get("type")
                        if bt == "tool_result":
                            emit({
                                "t": "tool_result",
                                "id": b.get("tool_use_id"),
                                "ok": not b.get("is_error"),
                                "content": _flatten(b.get("content")),
                                "ts": ts,
                                "side": side,
                            })
                        elif bt == "text" and b.get("text", "").strip():
                            emit({"t": "user", "text": b["text"], "ts": ts, "side": side})
            elif typ == "assistant":
                mid = msg.get("id") or obj.get("uuid")
                for i, b in enumerate(msg.get("content") or []):
                    bt = b.get("type")
                    if bt == "text" and b.get("text"):
                        emit({"t": "text", "mid": mid, "idx": i, "text": b["text"],
                              "ts": ts, "side": side, "final": True})
                    elif bt == "thinking" and b.get("thinking"):
                        emit({"t": "thinking", "mid": mid, "idx": i, "text": b["thinking"],
                              "ts": ts, "side": side, "final": True})
                    elif bt == "tool_use":
                        emit({"t": "tool_use", "id": b.get("id"), "name": b.get("name"),
                              "input": b.get("input"), "ts": ts, "side": side})
            elif typ == "system" and obj.get("subtype") == "post_turn_summary":
                emit({"t": "summary", "text": obj.get("status_detail"), "ts": ts})

            if len(events) >= max_events:
                emit({"t": "notice", "text": f"transcript truncated at {max_events} events"})
                break
    return events


def trash_session(session_id: str) -> dict:
    """Delete a stored conversation — into the Trash, not into thin air.

    These transcripts are the only copy of the work, so we move them where you
    can still get them back rather than unlinking.
    """
    path = find_transcript(session_id)
    if not path:
        return {"ok": False, "error": "no transcript with that id"}
    root = os.path.abspath(PROJECTS_DIR) + os.sep
    if not os.path.abspath(path).startswith(root):
        return {"ok": False, "error": "refusing to touch anything outside the project store"}

    meta = session_meta(path)
    trash = os.path.expanduser("~/.Trash")
    try:
        os.makedirs(trash, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(trash, f"codetails-{session_id[:8]}-{stamp}.jsonl")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(trash, f"codetails-{session_id[:8]}-{stamp}-{n}.jsonl")
            n += 1
        shutil.move(path, dest)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    # the sidecar directory Claude Code keeps beside some sessions
    side = os.path.join(os.path.dirname(path), session_id)
    if os.path.isdir(side):
        try:
            shutil.move(side, os.path.join(trash, f"codetails-{session_id[:8]}-{stamp}-dir"))
        except OSError:
            pass

    _meta_cache.pop(path, None)
    return {"ok": True, "trashed": dest, "title": meta.get("title")}


def _flatten(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return json.dumps(content)[:4000]


# --------------------------------------------------------------------------- filesystem
def fs_list(path: str) -> dict:
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        path = os.path.expanduser("~")
    entries = []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                entries.append({
                    "name": name,
                    "path": full,
                    "git": os.path.isdir(os.path.join(full, ".git")),
                })
    except PermissionError:
        pass
    return {
        "path": path,
        "parent": os.path.dirname(path) if path != "/" else None,
        "entries": entries[:400],
        "home": os.path.expanduser("~"),
    }


def git_info(cwd: str) -> dict:
    info = {"repo": False, "branch": None, "dirty": 0, "ahead": 0}
    if not cwd or not os.path.isdir(cwd):
        return info
    try:
        r = subprocess.run(["git", "-C", cwd, "status", "--porcelain=v1", "--branch"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return info
        lines = r.stdout.splitlines()
        if not lines:
            return info
        info["repo"] = True
        head = lines[0]
        if head.startswith("## "):
            name = head[3:].split("...")[0].strip()
            info["branch"] = name
            if "[ahead " in head:
                try:
                    info["ahead"] = int(head.split("[ahead ")[1].split("]")[0].split(",")[0])
                except Exception:
                    pass
        info["dirty"] = max(0, len(lines) - 1)
    except Exception:
        pass
    return info
