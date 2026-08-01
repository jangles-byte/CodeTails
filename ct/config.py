"""CodeTails configuration.

One JSON file living next to the app so the whole thing stays portable:
copy the folder, keep your skins, tokens and preferences.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import threading

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_ROOT, "config.json")
WEB_ROOT = os.path.join(APP_ROOT, "web")

DEFAULTS = {
    "port": 8790,
    "host": "0.0.0.0",
    "allow_lan": False,          # tailnet + loopback only unless you opt in
    "token": None,               # generated on first run
    "open_browser": True,
    "default_model": "default",
    "default_permission_mode": "acceptEdits",
    "default_cwd": os.path.expanduser("~/Desktop"),
    "theme": "nebula",
    "custom_themes": {},
    "ui": {
        "fontSize": 13,
        "radius": 8,
        "glow": 0.5,
        "density": "cozy",
        "animations": True,
        "texture": "vignette",
        "layout": "messages",
        "showThinking": True,
        "collapseTools": True,
        "sound": False,
    },
    "pinned": [],
}

_lock = threading.RLock()
_cache: dict | None = None


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return copy.deepcopy(_cache)
        data = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
        cfg = _deep_merge(DEFAULTS, data)
        if not cfg.get("token"):
            cfg["token"] = secrets.token_urlsafe(18)
            _cache = cfg
            _write(cfg)
        _cache = cfg
        return copy.deepcopy(cfg)


def _write(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, CONFIG_PATH)


def update(patch: dict) -> dict:
    global _cache
    with _lock:
        cfg = _deep_merge(load(), patch)
        _cache = cfg
        _write(cfg)
        return copy.deepcopy(cfg)
