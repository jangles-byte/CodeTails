"""Two things the desktop app shows that a phone otherwise can't reach:

* **Activity** — every background agent and every live Claude Code session on
  this machine, including ones the desktop app started. Straight from
  `claude agents --json`.
* **Ports** — the local dev servers you are actually working on. Most bind
  127.0.0.1, so they are invisible from your phone; we can open a relay on the
  tailnet address only, which makes `http://<tailnet-ip>:3000` just work,
  websockets and hot reload included, because it is a raw TCP relay rather than
  an HTTP proxy.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time

from . import engine, net

_agents_cache: tuple[float, list] = (0.0, [])
_relays: dict[int, "PortRelay"] = {}
_lock = threading.RLock()


# --------------------------------------------------------------------------- agents
def agents(include_done: bool = True, max_age: float = 5.0) -> list[dict]:
    """Active background + interactive Claude Code sessions on this machine."""
    global _agents_cache
    now = time.time()
    if now - _agents_cache[0] < max_age:
        return _agents_cache[1]
    argv = [engine.find_claude(), "agents", "--json"]
    if include_done:
        argv.append("--all")
    out = []
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=12,
                           env=os.environ, cwd=os.path.expanduser("~"))
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            if isinstance(data, list):
                out = data
    except Exception:
        out = []
    for a in out:
        started = a.get("startedAt")
        if isinstance(started, (int, float)):
            a["age"] = max(0, now - started / 1000.0)
    out.sort(key=lambda a: a.get("startedAt") or 0, reverse=True)
    _agents_cache = (now, out)
    return out


# --------------------------------------------------------------------------- ports
_SKIP_PROCS = {"rapportd", "ControlCe", "IPNExtens", "sharingd", "AirPlayXPC"}
_LSOF = re.compile(r"^(?P<cmd>\S+)\s+(?P<pid>\d+)\s.*?(?P<addr>\S+):(?P<port>\d+)\s+\(LISTEN\)")


def local_ports() -> list[dict]:
    """Everything listening on this machine, with a note on who can reach it."""
    seen: dict[int, dict] = {}
    try:
        r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=8)
        lines = r.stdout.splitlines()[1:]
    except Exception:
        lines = []

    for line in lines:
        m = _LSOF.match(line)
        if not m:
            continue
        cmd, port, addr = m.group("cmd"), int(m.group("port")), m.group("addr")
        if cmd in _SKIP_PROCS or port < 1024:
            continue
        entry = seen.setdefault(port, {
            "port": port, "proc": cmd, "pid": int(m.group("pid")),
            "loopback_only": True, "self": False,
        })
        if addr in ("*", "0.0.0.0", "::") or not addr.startswith(("127.", "[::1]", "::1")):
            entry["loopback_only"] = False

    ts = net.tailnet()
    ip = ts.get("ip")
    from . import config
    mine = config.load().get("port")

    out = []
    for port, e in sorted(seen.items()):
        e["self"] = port == mine
        with _lock:
            e["relayed"] = port in _relays and _relays[port].alive
        e["url"] = f"http://{ip}:{port}/" if ip else None
        # reachable already if it binds every interface; otherwise it needs a relay
        e["reachable"] = (not e["loopback_only"]) and bool(ip)
        out.append(e)
    return out


def launch_configs(cwd: str) -> list[dict]:
    """Whatever `.claude/launch.json` in this project says it can run."""
    path = os.path.join(cwd or "", ".claude", "launch.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    out = []
    for c in data.get("configurations", []) or []:
        if not isinstance(c, dict):
            continue
        out.append({"name": c.get("name"), "port": c.get("port"), "url": c.get("url")})
    return out


# --------------------------------------------------------------------------- relay
class PortRelay:
    """A raw TCP relay bound to the tailnet address only.

    Protocol-agnostic on purpose — HTTP, websockets and hot-reload sockets all
    pass through untouched, which an HTTP proxy would mangle.
    """

    def __init__(self, port: int, bind_ip: str):
        self.port = port
        self.bind_ip = bind_ip
        self.alive = False
        self.error: str | None = None
        self.conns = 0
        self._srv: socket.socket | None = None

    def start(self) -> bool:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.bind_ip, self.port))
            srv.listen(64)
        except OSError as exc:
            self.error = str(exc)
            return False
        self._srv = srv
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()
        return True

    def _accept(self) -> None:
        while self.alive and self._srv:
            try:
                client, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._pipe, args=(client,), daemon=True).start()

    def _pipe(self, client: socket.socket) -> None:
        self.conns += 1
        upstream = None
        try:
            upstream = socket.create_connection(("127.0.0.1", self.port), timeout=8)
            for a, b in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._shovel, args=(a, b), daemon=True).start()
            return                      # threads own the sockets from here
        except Exception:
            for s in (client, upstream):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass

    @staticmethod
    def _shovel(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass

    def stop(self) -> None:
        self.alive = False
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass


def relay_start(port: int) -> dict:
    ts = net.tailnet()
    ip = ts.get("ip")
    if not ip:
        return {"ok": False, "error": "Tailscale is not running — nothing to bind to."}
    with _lock:
        existing = _relays.get(port)
        if existing and existing.alive:
            return {"ok": True, "url": f"http://{ip}:{port}/", "already": True}
        r = PortRelay(port, ip)
        if not r.start():
            return {"ok": False, "error": r.error or "could not bind"}
        _relays[port] = r
    return {"ok": True, "url": f"http://{ip}:{port}/"}


def relay_stop(port: int) -> dict:
    with _lock:
        r = _relays.pop(port, None)
    if r:
        r.stop()
    return {"ok": True}


def relay_list() -> list[dict]:
    with _lock:
        return [{"port": p, "alive": r.alive, "conns": r.conns, "bind": r.bind_ip}
                for p, r in sorted(_relays.items())]


def relay_stop_all() -> None:
    with _lock:
        items = list(_relays.values())
        _relays.clear()
    for r in items:
        r.stop()
