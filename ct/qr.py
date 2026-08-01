"""A tiny, dependency-free QR encoder (byte mode, EC level L, versions 1-10).

Just enough to put the tailnet URL on screen so a phone can pick it up with
the camera. No pip installs, no CDN, no network.
"""

from __future__ import annotations

# --- GF(256) ---------------------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list[int]:
    poly = [1]
    for i in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= _mul(c, 1)
            nxt[j + 1] ^= _mul(c, _EXP[i])
        poly = nxt
    return poly


def _rs_encode(data: list[int], n: int) -> list[int]:
    gen = _rs_generator(n)
    rem = [0] * n
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i, g in enumerate(gen[1:]):
            rem[i] ^= _mul(g, factor)
    return rem


# --- version tables (EC level L) -------------------------------------------
# version -> (ec codewords per block, g1 blocks, g1 data cw, g2 blocks, g2 data cw)
_BLOCKS_L = {
    1: (7, 1, 19, 0, 0),
    2: (10, 1, 34, 0, 0),
    3: (15, 1, 55, 0, 0),
    4: (20, 1, 80, 0, 0),
    5: (26, 1, 108, 0, 0),
    6: (18, 2, 68, 0, 0),
    7: (20, 2, 78, 0, 0),
    8: (24, 2, 97, 0, 0),
    9: (30, 2, 116, 0, 0),
    10: (18, 2, 68, 2, 69),
}
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


def _capacity(version: int) -> int:
    ec, g1, d1, g2, d2 = _BLOCKS_L[version]
    total_data_bits = (g1 * d1 + g2 * d2) * 8
    count_bits = 8 if version < 10 else 16
    return (total_data_bits - 4 - count_bits) // 8


def _bch(value: int, poly: int, poly_bits: int) -> int:
    v = value << (poly_bits - 1)
    while v.bit_length() >= poly_bits:
        v ^= poly << (v.bit_length() - poly_bits)
    return v


def _format_bits(mask: int) -> int:
    # EC level L == 0b01
    data = (0b01 << 3) | mask
    rem = _bch(data, 0b10100110111, 11)
    return ((data << 10) | rem) ^ 0b101010000010010


def _version_bits(version: int) -> int:
    rem = _bch(version, 0b1111100100101, 13)
    return (version << 12) | rem


class _Matrix:
    def __init__(self, size: int):
        self.size = size
        self.mods = [[0] * size for _ in range(size)]
        self.reserved = [[False] * size for _ in range(size)]

    def set(self, r: int, c: int, val: int, reserve: bool = True) -> None:
        self.mods[r][c] = 1 if val else 0
        if reserve:
            self.reserved[r][c] = True


def _place_function_patterns(m: _Matrix, version: int) -> None:
    size = m.size

    def finder(r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                inner = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) \
                    or (2 <= r <= 4 and 2 <= c <= 4)
                m.set(rr, cc, 1 if inner else 0)

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        m.set(6, i, bit)
        m.set(i, 6, bit)

    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    on = max(abs(dr), abs(dc)) != 1
                    m.set(r + dr, c + dc, 1 if on else 0)

    m.set(size - 8, 8, 1)  # dark module

    for i in range(9):           # reserve format areas
        if i != 6:
            m.set(8, i, 0)
            m.set(i, 8, 0)
    for i in range(8):
        m.set(8, size - 1 - i, 0)
        m.set(size - 1 - i, 8, 0)

    if version >= 7:
        bits = _version_bits(version)
        for i in range(18):
            bit = (bits >> i) & 1
            m.set(size - 11 + i % 3, i // 3, bit)
            m.set(i // 3, size - 11 + i % 3, bit)


def _encode_data(text: str, version: int) -> list[int]:
    payload = text.encode("utf-8")
    ec_cw, g1, d1, g2, d2 = _BLOCKS_L[version]
    total_data = g1 * d1 + g2 * d2
    count_bits = 8 if version < 10 else 16

    bits: list[int] = []

    def push(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)
    push(len(payload), count_bits)
    for b in payload:
        push(b, 8)
    push(0, min(4, total_data * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < total_data:
        codewords.append(pad[i % 2])
        i += 1

    blocks: list[list[int]] = []
    pos = 0
    for _ in range(g1):
        blocks.append(codewords[pos:pos + d1]); pos += d1
    for _ in range(g2):
        blocks.append(codewords[pos:pos + d2]); pos += d2
    ecs = [_rs_encode(b, ec_cw) for b in blocks]

    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_cw):
        for e in ecs:
            out.append(e[i])
    return out


def _place_data(m: _Matrix, codewords: list[int]) -> None:
    size = m.size
    bits = [(cw >> i) & 1 for cw in codewords for i in range(7, -1, -1)]
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if m.reserved[row][c]:
                    continue
                m.mods[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward
        col -= 2


def _mask_fn(mask: int, r: int, c: int) -> bool:
    if mask == 0: return (r + c) % 2 == 0
    if mask == 1: return r % 2 == 0
    if mask == 2: return c % 3 == 0
    if mask == 3: return (r + c) % 3 == 0
    if mask == 4: return (r // 2 + c // 3) % 2 == 0
    if mask == 5: return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6: return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _penalty(mods: list[list[int]]) -> int:
    size = len(mods)
    score = 0
    # rule 1 - runs of 5+
    for line in list(mods) + [list(col) for col in zip(*mods)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    # rule 2 - 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            v = mods[r][c]
            if v == mods[r][c + 1] == mods[r + 1][c] == mods[r + 1][c + 1]:
                score += 3
    # rule 3 - finder-like patterns
    pat_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat_b = list(reversed(pat_a))
    for line in list(mods) + [list(col) for col in zip(*mods)]:
        for i in range(size - 10):
            seg = line[i:i + 11]
            if seg == pat_a or seg == pat_b:
                score += 40
    # rule 4 - dark ratio
    dark = sum(sum(row) for row in mods)
    pct = dark * 100 // (size * size)
    score += 10 * (min(abs(pct - 50) // 5, 10))
    return score


def encode(text: str) -> list[list[int]]:
    """Return the QR modules as a list of rows of 0/1."""
    version = None
    for v in range(1, 11):
        if len(text.encode("utf-8")) <= _capacity(v):
            version = v
            break
    if version is None:
        raise ValueError("payload too long for this tiny encoder")

    size = 17 + 4 * version
    codewords = _encode_data(text, version)

    best = None
    for mask in range(8):
        m = _Matrix(size)
        _place_function_patterns(m, version)
        _place_data(m, codewords)
        for r in range(size):
            for c in range(size):
                if not m.reserved[r][c] and _mask_fn(mask, r, c):
                    m.mods[r][c] ^= 1
        fmt = _format_bits(mask)
        for i in range(15):
            bit = (fmt >> i) & 1
            # copy 1: column 8, top-left downward
            if i < 6:
                m.mods[i][8] = bit
            elif i < 8:
                m.mods[i + 1][8] = bit
            else:
                m.mods[size - 15 + i][8] = bit
            # copy 2: row 8, bottom-right leftward
            if i < 8:
                m.mods[8][size - 1 - i] = bit
            elif i == 8:
                m.mods[8][7] = bit
            else:
                m.mods[8][14 - i] = bit
        m.mods[size - 8][8] = 1
        pen = _penalty(m.mods)
        if best is None or pen < best[0]:
            best = (pen, [row[:] for row in m.mods])
    return best[1]


def svg(text: str, quiet: int = 3, module: int = 6,
        dark: str = "#000", light: str = "#fff") -> str:
    mods = encode(text)
    n = len(mods)
    dim = (n + quiet * 2) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges">',
        f'<rect width="{dim}" height="{dim}" fill="{light}"/>',
    ]
    for r, row in enumerate(mods):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                x = (start + quiet) * module
                y = (r + quiet) * module
                w = (c - start) * module
                parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{module}" fill="{dark}"/>')
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)


def ascii_art(text: str, quiet: int = 2) -> str:
    """Half-block rendering so it stays scannable in a Terminal window."""
    mods = encode(text)
    n = len(mods)
    pad = [0] * (n + quiet * 2)
    rows = [pad[:] for _ in range(quiet)]
    for row in mods:
        rows.append([0] * quiet + row + [0] * quiet)
    rows += [pad[:] for _ in range(quiet + 1)]
    out = []
    for i in range(0, len(rows) - 1, 2):
        top, bot = rows[i], rows[i + 1]
        line = []
        for c in range(len(top)):
            t, b = top[c], bot[c]
            # dark module = light background block (terminals are dark)
            if t and b:
                line.append(" ")
            elif t:
                line.append("▄")
            elif b:
                line.append("▀")
            else:
                line.append("█")
        out.append("".join(line))
    return "\n".join(out)
