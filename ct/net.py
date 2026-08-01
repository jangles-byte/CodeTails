"""Network identity: where can this box be reached from?"""

from __future__ import annotations

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
    out["best"] = out["tailnet_url"] or out["lan"] or out["local"]
    return out
