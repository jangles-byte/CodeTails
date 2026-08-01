#!/usr/bin/env python3
"""Render the CodeTails app icon to PNG with nothing but the standard library.

    python3 tools/make_icons.py
"""

import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

BG = (0, 0, 0)
RING = (38, 38, 43)
ACCENT = (217, 119, 87)
ACCENT2 = (237, 169, 141)


def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def blend(dst, src, a):
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


def render(size: int) -> bytes:
    s = size
    c = s / 2
    r_out = s * 0.355
    r_in = s * 0.052
    thick = s * 0.085
    corner = s * 0.22

    spokes = []
    for k in range(3):
        ang = math.radians(90 + k * 60)
        dx, dy = math.cos(ang) * r_out, math.sin(ang) * r_out
        spokes.append((c - dx, c - dy, c + dx, c + dy))

    rows = []
    for y in range(s):
        row = bytearray()
        for x in range(s):
            px, py = x + 0.5, y + 0.5

            # rounded-square mask (alpha 0 outside)
            qx = max(abs(px - c) - (c - corner), 0.0)
            qy = max(abs(py - c) - (c - corner), 0.0)
            edge = math.hypot(qx, qy) - corner
            alpha = max(0.0, min(1.0, 0.5 - edge))
            if alpha <= 0:
                row += bytes((0, 0, 0, 0))
                continue

            col = BG
            # hairline ring just inside the edge
            ring_d = abs(edge + s * 0.016)
            if ring_d < s * 0.011:
                col = blend(col, RING, max(0.0, min(1.0, (s * 0.011 - ring_d) / (s * 0.008))))

            d = min(seg_dist(px, py, *sp) for sp in spokes)
            cov = max(0.0, min(1.0, (thick / 2 - d) + 0.5))
            if cov > 0:
                col = blend(col, ACCENT, cov)

            dc = math.hypot(px - c, py - c)
            if dc < r_in * 1.9:
                col = blend(col, BG, max(0.0, min(1.0, (r_in * 1.9 - dc) + 0.5)))
            if dc < r_in:
                col = blend(col, ACCENT2, max(0.0, min(1.0, (r_in - dc) + 0.5)))

            row += bytes((col[0], col[1], col[2], int(alpha * 255)))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    for size in (180, 512):
        path = os.path.join(OUT, f"icon-{size}.png")
        with open(path, "wb") as fh:
            fh.write(render(size))
        print("wrote", path)
