"""icon.svg と同じデザインの PNG アイコンを numpy だけで描く（PWAインストール要件用）。"""
import struct, sys, zlib
from pathlib import Path
import numpy as np

def png(path, rgba):
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

def hexc(h): h = h.lstrip("#"); return np.array([int(h[i:i+2], 16) for i in (0, 2, 4)] + [255], np.float32)

def render(size, ss=3):
    n = size * ss
    y, x = np.mgrid[0:n, 0:n].astype(np.float32) * (512 / n)
    img = np.zeros((n, n, 4), np.float32)
    def paint(mask, col): img[mask] = hexc(col)
    def rrect(x0, y0, w, h, r):
        cx = np.clip(x, x0 + r, x0 + w - r); cy = np.clip(y, y0 + r, y0 + h - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    def circ(cx, cy, r): return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    def seg(x1, y1, x2, y2, wdt):
        dx, dy = x2 - x1, y2 - y1; t = np.clip(((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy), 0, 1)
        return (x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2 <= (wdt / 2) ** 2
    INK = "#1D1A2F"
    paint(rrect(12, 12, 488, 488, 122), INK); paint(rrect(36, 36, 440, 440, 98), "#FFD23F")
    paint(seg(180, 150, 330, 80, 22), INK)
    paint(circ(330, 80, 29), INK); paint(circ(330, 80, 15), "#FF5DA2")
    paint(rrect(75, 139, 362, 272, 71), INK); paint(rrect(97, 161, 318, 228, 49), "#FF7A3D")
    paint(circ(190, 275, 72), INK); paint(circ(190, 275, 52), "#FFFFFF"); paint(circ(190, 275, 22), INK)
    paint(rrect(285, 215, 95, 26, 13), INK); paint(rrect(280, 257, 105, 36, 18), INK); paint(rrect(290, 267, 85, 16, 8), "#2EC4B6")
    paint(rrect(285, 309, 95, 26, 13), INK)
    img = img.reshape(size, ss, size, ss, 4).mean(axis=(1, 3))
    return np.clip(img, 0, 255).astype(np.uint8)

out = Path(sys.argv[1] if len(sys.argv) > 1 else "web/icons")
for s in (192, 512):
    png(out / f"icon-{s}.png", render(s))
print("icons ok")
