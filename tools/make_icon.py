# -*- coding: utf-8 -*-
"""用 Pillow 生成 assets/app.ico（与界面配色一致的折线图标）。"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets")
S = 256


def draw(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size * 0.06
    radius = size * 0.22
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=radius, fill=(255, 255, 255, 255),
                        outline=(233, 233, 233, 255), width=max(1, size // 128))

    # 网格线
    grid = (227, 232, 238, 255)
    for i in range(1, 4):
        y = pad + (size - 2 * pad) * i / 4
        d.line([padding_x(size), y, size - padding_x(size), y],
               fill=grid, width=max(1, size // 90))

    # 折线：低 -> 高 -> 回落 -> 最高
    pts = [(0.16, 0.68), (0.32, 0.52), (0.46, 0.60),
           (0.60, 0.34), (0.72, 0.42), (0.86, 0.22)]
    line = [(size * x, size * y) for x, y in pts]
    d.line(line, fill=(0, 120, 212, 255), width=max(2, size // 16),
           joint="curve")
    # 折线下方半透明填充
    area = line + [(line[-1][0], size * 0.80), (line[0][0], size * 0.80)]
    d.polygon(area, fill=(0, 120, 212, 34))
    # 末端圆点
    x, y = line[-1]
    r = max(3, size // 28)
    d.ellipse([x - r, y - r, x + r, y + r], fill=(0, 120, 212, 255))
    return img


def padding_x(size):
    return size * 0.14


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    base = draw(S)
    base.save(os.path.join(OUT_DIR, "app.png"))
    base.save(os.path.join(OUT_DIR, "app.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                     (128, 128), (256, 256)])
    print("wrote", os.path.join(OUT_DIR, "app.ico"))


if __name__ == "__main__":
    main()
