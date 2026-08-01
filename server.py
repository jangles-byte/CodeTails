#!/usr/bin/env python3
"""CodeTails server — stdlib only, no pip, no build step.

    python3 server.py [--port 8790] [--no-open]
"""

from __future__ import annotations

import argparse
import hmac
import http.cookies
import ipaddress
import json
import mimetypes
import os
import posixpath
import queue
import re
import signal
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct import config, engine, live, net, projects, qr  # noqa: E402

MANAGER = engine.SessionManager()
START_TIME = time.time()

# A live CodeTails link is equivalent to a shell on this machine, so the door is
# narrow on purpose: tailnet or loopback only, token always, and browser-origin
# checks so a random web page can't drive it behind your back.
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")     # CGNAT range Tailscale uses
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


# --------------------------------------------------------------------------- helpers
def _json_bytes(obj) -> bytes:
    return json.dumps(obj, default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CodeTails"

    # keep the launcher terminal readable
    def log_message(self, fmt, *args):  # noqa: A003
        if os.environ.get("CODETAILS_VERBOSE"):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---------------------------------------------------------------- gatekeeping
    def _peer_allowed(self) -> bool:
        """Only loopback and the tailnet get to knock, unless LAN is opted in."""
        try:
            addr = ipaddress.ip_address((self.client_address[0] or "").split("%")[0])
        except ValueError:
            return False
        if addr.is_loopback:
            return True
        if isinstance(addr, ipaddress.IPv4Address) and addr in TAILSCALE_V4:
            return True
        if isinstance(addr, ipaddress.IPv6Address) and addr in TAILSCALE_V6:
            return True
        if addr.ipv4_mapped and addr.ipv4_mapped in TAILSCALE_V4:
            return True
        if config.load().get("allow_lan") and addr.is_private:
            return True
        return False

    def _host_allowed(self) -> bool:
        """Blocks DNS rebinding: an attacker-controlled name pointed at 127.0.0.1
        would otherwise be same-origin with us."""
        raw = (self.headers.get("Host") or "").strip()
        host = raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw
        host = host.strip("[]").lower()
        if not host:
            return False
        if host in ("localhost", "localhost.localdomain"):
            return True
        try:                       # a bare IP literal can't be rebound
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        if host.endswith(".ts.net") or host.endswith(".local"):
            return True
        return host == socket.gethostname().lower()

    def _origin_ok(self) -> bool:
        """Reject cross-site writes (CSRF) while leaving curl/scripts working."""
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urllib.parse.urlparse(origin)
            except ValueError:
                return False
            if parsed.netloc.lower() != (self.headers.get("Host") or "").lower():
                return False
        return True

    def _token_ok(self) -> bool:
        token = config.load().get("token")
        if not token:
            return True
        candidates = []
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if qs.get("t"):
            candidates.append(qs["t"][0])
        if self.headers.get("X-CT-Token"):
            candidates.append(self.headers["X-CT-Token"])
        raw = self.headers.get("Cookie")
        if raw:
            try:
                jar = http.cookies.SimpleCookie(raw)
                if "ct" in jar:
                    candidates.append(jar["ct"].value)
            except Exception:
                pass
        return any(hmac.compare_digest(str(c), token) for c in candidates)

    def _guard(self) -> bool:
        """Every request passes through here. Returns True when it may proceed."""
        if not self._peer_allowed():
            self._send(403, "text/plain; charset=utf-8",
                       b"CodeTails only answers loopback and tailnet addresses.\n")
            return False
        if not self._host_allowed():
            self._send(421, "text/plain; charset=utf-8", b"Unrecognised Host header.\n")
            return False
        if self.command != "GET" and not self._origin_ok():
            self._send(403, "text/plain; charset=utf-8", b"Cross-site request refused.\n")
            return False
        if not self._token_ok():
            self._deny()
            return False
        return True

    def _deny(self) -> None:
        body = (
            "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<body style='background:#000;color:#d97757;font:14px ui-monospace,Menlo,monospace;"
            "padding:14vh 24px;text-align:center'>"
            "<div style='font-size:34px'>&#10039;</div>"
            "<h1 style='font-weight:600;letter-spacing:.02em'>CodeTails</h1>"
            "<p style='color:#8b8b8f'>This dashboard needs its access token.</p>"
            "<p style='color:#5a5a5f'>Open the link printed by the launcher, or scan its QR code.</p>"
            "</body>"
        ).encode()
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------- responses
    def _send(self, code: int, ctype: str, body: bytes, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, "application/json; charset=utf-8", _json_bytes(obj))

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---------------------------------------------------------------- routing
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/health":
            if not self._peer_allowed():
                return self._send(403, "text/plain; charset=utf-8", b"denied\n")
            return self._json({"ok": True, "uptime": time.time() - START_TIME})

        if not self._guard():
            return

        if path in ("/", "/index.html"):
            return self._serve_index(qs)
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path in ("/manifest.webmanifest", "/icon.svg", "/sw.js", "/favicon.ico"):
            name = "icon.svg" if path == "/favicon.ico" else path.lstrip("/")
            return self._serve_static(name)

        if path == "/api/boot":
            return self._json(self._boot())
        if path == "/api/projects":
            return self._json({"projects": projects.list_projects()})
        if path == "/api/project-sessions":
            slug = qs.get("slug", [""])[0]
            return self._json({"sessions": projects.list_sessions(slug)})
        if path == "/api/history":
            sid = qs.get("id", [""])[0]
            if not SESSION_ID_RE.match(sid):
                return self._json({"error": "bad session id"}, 400)
            tpath = projects.find_transcript(sid)
            if not tpath:
                return self._json({"error": "transcript not found"}, 404)
            meta = projects.session_meta(tpath)
            return self._json({"meta": meta, "events": projects.transcript_events(tpath)})
        if path == "/api/fs":
            return self._json(projects.fs_list(qs.get("path", [""])[0]))
        if path == "/api/git":
            return self._json(projects.git_info(qs.get("cwd", [""])[0]))
        if path == "/api/sessions":
            return self._json({"sessions": MANAGER.list()})
        if path == "/api/activity":
            return self._json({
                "agents": live.agents(),
                "ports": live.local_ports(),
                "relays": live.relay_list(),
                "launch": live.launch_configs(qs.get("cwd", [""])[0]),
                "tailnet": net.tailnet().get("ip"),
            })
        if path == "/api/qr":
            data = qs.get("d", [""])[0]
            try:
                svg = qr.svg(data, dark=qs.get("dark", ["#000000"])[0],
                             light=qs.get("light", ["#ffffff"])[0])
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            return self._send(200, "image/svg+xml; charset=utf-8", svg.encode())
        if path.startswith("/api/sessions/") and path.endswith("/events"):
            sid = path.split("/")[3]
            return self._sse(sid, qs)

        return self._json({"error": "not found"}, 404)

    def do_HEAD(self):  # noqa: N802
        if not self._guard():
            return
        self._send(200, "text/html; charset=utf-8", b"")

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._body()

        if path == "/api/sessions":
            cwd = body.get("cwd") or config.load()["default_cwd"]
            if not os.path.isdir(os.path.expanduser(cwd)):
                return self._json({"error": f"no such directory: {cwd}"}, 400)
            try:
                s = MANAGER.create(
                    cwd=cwd,
                    model=body.get("model") or "default",
                    permission_mode=body.get("permission_mode") or "acceptEdits",
                    effort=body.get("effort") or "default",
                    resume=body.get("resume") or None,
                    title=body.get("title") or None,
                )
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            return self._json({"session": s.meta()})

        if path == "/api/relay":
            try:
                port = int(body.get("port"))
            except (TypeError, ValueError):
                return self._json({"error": "bad port"}, 400)
            if port == config.load().get("port"):
                return self._json({"error": "that is CodeTails itself"}, 400)
            if body.get("stop"):
                return self._json(live.relay_stop(port))
            result = live.relay_start(port)
            return self._json(result, 200 if result.get("ok") else 400)

        if path == "/api/config":
            cfg = config.update(body or {})
            cfg = dict(cfg)
            return self._json({"config": _public_config(cfg)})

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sessions":
            sid, action = parts[2], parts[3]
            s = MANAGER.get(sid)
            if not s:
                return self._json({"error": "unknown session"}, 404)
            if action == "send":
                s.send(body.get("text") or "")
                return self._json({"ok": True, "session": s.meta()})
            if action == "interrupt":
                s.interrupt()
                return self._json({"ok": True})
            if action == "config":
                s.reconfigure(model=body.get("model"),
                              permission_mode=body.get("permission_mode"),
                              effort=body.get("effort"),
                              allow_tool=body.get("allow_tool"))
                return self._json({"ok": True, "session": s.meta()})
            if action == "permission":
                ok = s.answer_permission(body.get("id"), bool(body.get("allow")))
                return self._json({"ok": ok})
            if action == "close":
                MANAGER.close(sid)
                return self._json({"ok": True})
            if action == "rename":
                s.title = (body.get("title") or s.title)[:80]
                s.emit({"t": "session", **s.meta()})
                return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    # ---------------------------------------------------------------- pieces
    def _boot(self) -> dict:
        cfg = config.load()
        return {
            "config": _public_config(cfg),
            "endpoints": net.endpoints(cfg["port"], cfg["token"]),
            "projects": projects.list_projects(),
            "sessions": MANAGER.list(),
            "models": engine.MODELS,
            "permission_modes": engine.PERMISSION_MODES,
            "efforts": engine.EFFORTS,
            "home": os.path.expanduser("~"),
            "host": os.uname().nodename,
            "version": __import__("ct").__version__,
        }

    def _serve_index(self, qs) -> None:
        path = os.path.join(config.WEB_ROOT, "index.html")
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._json({"error": "web assets missing"}, 500)
        extra = {"Content-Security-Policy": CSP}
        tok = qs.get("t", [None])[0]
        if tok and hmac.compare_digest(tok, config.load().get("token") or ""):
            extra["Set-Cookie"] = (f"ct={tok}; Path=/; Max-Age=31536000; "
                                   "SameSite=Lax; HttpOnly")
        self._send(200, "text/html; charset=utf-8", body, extra)

    def _serve_static(self, rel: str) -> None:
        rel = posixpath.normpath("/" + rel).lstrip("/")
        full = os.path.join(config.WEB_ROOT, rel)
        root = os.path.abspath(config.WEB_ROOT) + os.sep
        if not os.path.abspath(full).startswith(root) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if full.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, ctype, fh.read())

    def _sse(self, sid: str, qs) -> None:
        s = MANAGER.get(sid)
        if not s:
            return self._json({"error": "unknown session"}, 404)
        since = 0
        last = self.headers.get("Last-Event-ID")
        if last and last.isdigit():
            since = int(last)
        if qs.get("since", [None])[0] and qs["since"][0].isdigit():
            since = max(since, int(qs["since"][0]))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.close_connection = True

        q = s.subscribe()
        try:
            for ev in s.replay(since):
                self._sse_write(ev)
            self._sse_raw(": hello\n\n")
            last_beat = time.time()
            while True:
                try:
                    ev = q.get(timeout=5)
                    self._sse_write(ev)
                except queue.Empty:
                    pass
                if time.time() - last_beat > 15:
                    self._sse_raw(f": beat {int(time.time())}\n\n")
                    last_beat = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            s.unsubscribe(q)

    def _sse_write(self, ev: dict) -> None:
        payload = json.dumps(ev, default=str)
        self._sse_raw(f"id: {ev.get('seq', 0)}\ndata: {payload}\n\n")

    def _sse_raw(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()


def _public_config(cfg: dict) -> dict:
    out = dict(cfg)
    out.pop("token", None)
    return out


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


BANNER = """
   \033[38;5;173m✻\033[0m  \033[1mCodeTails\033[0m \033[2m— Claude Code, from anywhere\033[0m
"""


def main() -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser(description="CodeTails server")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-qr", action="store_true")
    args = ap.parse_args()

    if args.port != cfg["port"]:
        cfg = config.update({"port": args.port})

    try:
        engine.find_claude()
    except RuntimeError as exc:
        print(f"\033[38;5;131m  {exc}\033[0m")
        print("  Install it with:  npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    httpd = None
    port = args.port
    for attempt in range(12):
        try:
            httpd = Server((args.host, port), Handler)
            break
        except OSError:
            print(f"   \033[2mport {port} busy, trying {port + 1}…\033[0m")
            port += 1
    if httpd is None:
        print("\033[38;5;131m  No free port found near "
              f"{args.port}. Close something or pass --port.\033[0m")
        sys.exit(1)
    if port != cfg["port"]:
        cfg = config.update({"port": port})

    ends = net.endpoints(port, cfg["token"])

    print(BANNER)
    print(f"   \033[2mlocal   \033[0m {ends['local']}")
    if ends.get("lan"):
        print(f"   \033[2mlan     \033[0m {ends['lan']}")
    if ends.get("tailnet_url"):
        print(f"   \033[38;5;173mtailnet \033[0m {ends['tailnet_url']}")
        if ends.get("tailnet_dns_url"):
            print(f"   \033[2m        \033[0m {ends['tailnet_dns_url']}")
    else:
        print("   \033[2mtailnet \033[0m not detected — start Tailscale to reach this from your phone")

    if not args.no_qr and ends.get("best"):
        print()
        try:
            print(qr.ascii_art(ends["best"]))
            print(f"   \033[2mscan with your phone camera\033[0m")
        except Exception:
            pass

    print("\n   \033[2mctrl-c to stop\033[0m\n")

    if cfg.get("open_browser") and not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(ends["local"])).start()

    def shutdown(*_):
        print("\n   \033[2mclosing sessions…\033[0m")
        live.relay_stop_all()
        MANAGER.close_all()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, lambda *a: (shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *a: (shutdown(), sys.exit(0)))

    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
