"""Network identity: where can this box be reached from?"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess

TAILSCALE_BINS = [
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/usr/bin/tailscale",
    os.path.expanduser("~/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
]


def _run(args: list[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _tailscale_bin() -> str | None:
    for path in TAILSCALE_BINS:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    from shutil import which
    return which("tailscale")


def tailnet() -> dict:
    """Tailnet IPv4 + MagicDNS name, or a best-effort guess from the interfaces."""
    info = {"ip": None, "dns": None, "state": "not-found", "magic_dns": None}
    binary = _tailscale_bin()
    if binary:
        raw = _run([binary, "status", "--json"])
        if raw:
            try:
                data = json.loads(raw)
                self_node = data.get("Self") or {}
                ips = self_node.get("TailscaleIPs") or []
                v4 = next((i for i in ips if ":" not in i), None)
                dns = (self_node.get("DNSName") or "").rstrip(".")
                info.update({
                    "ip": v4,
                    "dns": dns or None,
                    "state": data.get("BackendState", "unknown"),
                    "magic_dns": data.get("MagicDNSSuffix"),
                })
            except Exception:
                pass
        if not info["ip"]:
            ip = _run([binary, "ip", "-4"]).splitlines()
            if ip:
                info["ip"] = ip[0].strip()
                info["state"] = "Running"

    if not info["ip"]:  # fall back to scanning interfaces for a 100.x CGNAT address
        raw = _run(["ifconfig"])
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("inet 100."):
                info["ip"] = line.split()[1]
                info["state"] = "interface"
                break
    return info


def lan_ip() -> str | None:
    """Which local address would the default route use?

    Connecting a UDP socket sends no packets — it only asks the kernel to pick a
    source address. The target is TEST-NET-1 (RFC 5737), which is guaranteed not
    to belong to anyone, so nothing ever leaves this machine.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


_IP_USABLE: bool | None = None


def note_ip_usable(value: bool) -> None:
    """Remember what the pre-bind probe found, so `endpoints` stays honest."""
    global _IP_USABLE
    _IP_USABLE = value


def tailnet_port_free(port: int) -> bool:
    """Can we own <tailnet-ip>:<port>?

    Binding 0.0.0.0 succeeds even when something already holds the specific
    tailnet address — `tailscale serve` does exactly that — and then the phone
    talks to *that* instead of us. So test the address we actually advertise.
    """
    ip = tailnet().get("ip")
    if not ip:
        return True                       # no tailnet, nothing to collide with
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def codetails_on(port: int) -> bool:
    """Is a CodeTails already answering on this port?"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
        conn.request("GET", "/api/health")
        r = conn.getresponse()
        body = r.read(200)
        conn.close()
        return r.status == 200 and b'"ok"' in body
    except Exception:
        return False


def endpoints(port: int, token: str) -> dict:
    ts = tailnet()
    q = f"/?t={token}"
    out = {
        "tailnet": ts,
        "local": f"http://localhost:{port}{q}",
        "lan": None,
        "tailnet_url": None,
        "tailnet_dns_url": None,
        "hostname": socket.gethostname(),
    }
    ip = lan_ip()
    if ip:
        out["lan"] = f"http://{ip}:{port}{q}"
    if ts.get("ip"):
        out["tailnet_url"] = f"http://{ts['ip']}:{port}{q}"
    if ts.get("dns"):
        out["tailnet_dns_url"] = f"http://{ts['dns']}:{port}{q}"

    # If something else owns <tailnet-ip>:<port> (a `tailscale serve` handler,
    # say) the IP URL reaches that instead of us and 404s, while the MagicDNS
    # name it is configured for still works. Advertise the one that lands here.
    # Once we are bound, probing our own port would answer "taken", so main()
    # records what it found before binding.
    ip_usable = (_IP_USABLE if _IP_USABLE is not None
                 else (tailnet_port_free(port) if ts.get("ip") else False))
    out["ip_hijacked"] = bool(ts.get("ip")) and not ip_usable
    out["best"] = ((out["tailnet_dns_url"] if out["ip_hijacked"] else None)
                   or out["tailnet_url"] or out["lan"] or out["local"])
    return out
