# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico（纯标准库，不依赖 Pillow）。"""

import os
import zlib
import struct

SIZE = 256
TARGETS = [256, 128, 64, 48, 32, 16]

BG_TOP = (59, 130, 246)      # #3B82F6
BG_BOTTOM = (29, 78, 216)    # #1D4ED8
WHITE = (255, 255, 255)


def rounded_rect_mask(x0, y0, x1, y1, r):
    """返回一个 (SIZE x SIZE) 的 0/1 覆盖掩码，抗锯齿用 4x 超采样。"""
    SS = 4
    w = SIZE
    cov = [[0] * w for _ in range(w)]
    for py in range(y0 * SS, y1 * SS):
        fy = (py + 0.5) / SS
        for px in range(x0 * SS, x1 * SS):
            fx = (px + 0.5) / SS
            if fx < x0 or fx >= x1 or fy < y0 or fy >= y1:
                continue
            cx = min(max(fx, x0 + r), x1 - r)
            cy = min(max(fy, y0 + r), y1 - r)
            if (fx - cx) ** 2 + (fy - cy) ** 2 <= r * r:
                cov[int(fy)][int(fx)] += 1
    total = SS * SS
    return [[min(1.0, cov[y][x] / float(total)) for x in range(w)] for y in range(w)]


def blend(dst, src, a):
    return tuple(int(round(dst[i] * (1 - a) + src[i] * a)) for i in range(3))


def render():
    """返回 256x256 的 RGBA 字节串。"""
    px = [[(0, 0, 0, 0)] * SIZE for _ in range(SIZE)]

    bg = rounded_rect_mask(6, 6, 250, 250, 46)
    for y in range(SIZE):
        t = y / float(SIZE - 1)
        col = tuple(int(round(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t)) for i in range(3))
        for x in range(SIZE):
            a = bg[y][x]
            if a > 0:
                px[y][x] = (col[0], col[1], col[2], int(round(a * 255)))

    def paint(mask, color, alpha=255):
        for y in range(SIZE):
            row = mask[y]
            for x in range(SIZE):
                a = row[x]
                if a <= 0:
                    continue
                r, g, b, oa = px[y][x]
                eff = a * (alpha / 255.0)
                base = (r, g, b)
                nr = blend(base, color, eff)
                px[y][x] = (nr[0], nr[1], nr[2],
                            int(round(max(oa, eff * 255))))

    # 左右两块“分卷”
    left = rounded_rect_mask(48, 72, 118, 184, 12)
    right = rounded_rect_mask(138, 72, 208, 184, 12)
    paint(left, WHITE, 255)
    paint(right, WHITE, 255)

    # 中间虚线（分割线）
    y = 46
    while y < 210:
        dash = rounded_rect_mask(125, y, 131, min(y + 12, 210), 3)
        paint(dash, WHITE, 235)
        y += 22

    out = bytearray()
    for y in range(SIZE):
        for x in range(SIZE):
            r, g, b, a = px[y][x]
            out += bytes((r, g, b, a))
    return bytes(out)


def to_png(rgba, w, h):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))


def downscale(rgba, src, dst):
    if src == dst:
        return rgba
    ratio = src // dst
    out = bytearray()
    for y in range(dst):
        for x in range(dst):
            r = g = b = a = 0
            for dy in range(ratio):
                base = ((y * ratio + dy) * src + x * ratio) * 4
                for dx in range(ratio):
                    o = base + dx * 4
                    r += rgba[o]
                    g += rgba[o + 1]
                    b += rgba[o + 2]
                    a += rgba[o + 3]
            n = ratio * ratio
            out += bytes((r // n, g // n, b // n, a // n))
    return bytes(out)


def build_ico(path):
    base = render()
    images = []
    for s in TARGETS:
        img = downscale(base, SIZE, s)
        images.append((s, s, to_png(img, s, s)))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    data = b""
    for w, h, d in images:
        entries += struct.pack("<BBBBHHII",
                               0 if w >= 256 else w,
                               0 if h >= 256 else h,
                               0, 0, 1, 32, len(d), offset)
        offset += len(d)
        data += d
    with open(path, "wb") as f:
        f.write(header + entries + data)
    return os.path.getsize(path)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "icon.ico")
    size = build_ico(out)
    print("icon written:", out, size, "bytes")
