"""生成 CursorHub 的应用图标。

直接运行：
    python generate_icon.py

输出：
    icon.png  512x512 预览图
    icon.ico  多尺寸 Windows 图标（16/24/32/48/64/128/256）
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 512
SUPERSAMPLE = 4
CANVAS = SIZE * SUPERSAMPLE

OUTPUT_DIR = Path(__file__).resolve().parent
PNG_PATH = OUTPUT_DIR / "icon.png"
ICO_PATH = OUTPUT_DIR / "icon.ico"
ICO_SIZES = [
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
]

# 配色
BG_TOP = (36, 48, 70)         # 深蓝灰
BG_BOTTOM = (7, 11, 22)       # 近黑
AURA_COLOR = (37, 99, 235)    # 背景光晕
GLOW_COLOR = (56, 189, 248)   # 箭头外发光
ARROW_WHITE = (255, 255, 255)
ARROW_BLUE = (96, 165, 250)
ARROW_EDGE = (30, 64, 175)
RING_COLOR = (147, 197, 253)

# 经典鼠标指针轮廓，坐标范围 0~1，尖端在左上角
ARROW_POINTS = [
    (0.00, 0.00),
    (0.00, 0.74),
    (0.19, 0.57),
    (0.30, 0.87),
    (0.45, 0.80),
    (0.33, 0.51),
    (0.57, 0.51),
]


def lerp_color(start, end, t):
    """在两个 RGB 颜色之间线性插值。"""
    return tuple(round(a + (b - a) * t) for a, b in zip(start, end))


def vertical_gradient(size, top, bottom):
    """生成从上到下的线性渐变。"""
    gradient = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel((0, y), lerp_color(top, bottom, t))
    return gradient.resize((size, size), Image.Resampling.BILINEAR)


def diagonal_gradient(width, height, start, end):
    """生成从左上到右下的线性渐变（先用小图计算再放大，速度快）。"""
    small = 256
    gradient = Image.new("RGB", (small, small))
    pixels = gradient.load()
    denominator = 2 * (small - 1)
    for y in range(small):
        for x in range(small):
            pixels[x, y] = lerp_color(start, end, (x + y) / denominator)
    return gradient.resize((width, height), Image.Resampling.BILINEAR)


def scaled_arrow_points(canvas, height_ratio=0.60):
    """把归一化箭头轮廓缩放并居中到画布上。"""
    xs = [p[0] for p in ARROW_POINTS]
    ys = [p[1] for p in ARROW_POINTS]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    height = (max_y - min_y) * canvas * height_ratio
    scale = height / (max_y - min_y)
    width = (max_x - min_x) * scale

    offset_x = (canvas - width) / 2 - min_x * scale
    offset_y = (canvas - height) / 2 - min_y * scale

    return [(x * scale + offset_x, y * scale + offset_y) for x, y in ARROW_POINTS]


def render_icon(size=SIZE):
    """绘制一张 RGBA 图标。"""
    canvas = size * SUPERSAMPLE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))

    # 1. 深色渐变背景
    image.paste(
        vertical_gradient(canvas, BG_TOP, BG_BOTTOM).convert("RGBA"),
        (0, 0),
    )

    # 2. 背景中央的蓝色光晕
    aura = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    aura_draw = ImageDraw.Draw(aura)
    radius = canvas * 0.36
    center = canvas / 2
    aura_draw.ellipse(
        (
            center - radius,
            center - radius,
            center + radius,
            center + radius,
        ),
        fill=AURA_COLOR + (110,),
    )
    aura = aura.filter(ImageFilter.GaussianBlur(canvas * 0.09))
    image = Image.alpha_composite(image, aura)

    # 3. 箭头
    arrow_points = scaled_arrow_points(canvas)

    # 3.1 阴影
    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    offset = canvas * 0.014
    ImageDraw.Draw(shadow).polygon(
        [(x + offset, y + offset) for x, y in arrow_points],
        fill=(0, 0, 0, 150),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(canvas * 0.022))
    image = Image.alpha_composite(image, shadow)

    # 3.2 外发光（两层，内紧外松）
    for blur_ratio, alpha in ((0.028, 210), (0.055, 120)):
        glow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        ImageDraw.Draw(glow).polygon(
            arrow_points, fill=GLOW_COLOR + (alpha,)
        )
        glow = glow.filter(ImageFilter.GaussianBlur(canvas * blur_ratio))
        image = Image.alpha_composite(image, glow)

    # 3.3 白蓝渐变填充
    arrow_mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(arrow_mask).polygon(arrow_points, fill=255)

    bbox = arrow_mask.getbbox()
    bbox_width = bbox[2] - bbox[0]
    bbox_height = bbox[3] - bbox[1]

    # 渐变只铺满箭头自身范围，让「纯白 -> 亮蓝」的过渡更明显
    arrow_gradient = diagonal_gradient(
        bbox_width, bbox_height, ARROW_WHITE, ARROW_BLUE
    ).convert("RGBA")
    arrow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    arrow.paste(
        arrow_gradient,
        (bbox[0], bbox[1]),
        arrow_mask.crop(bbox),
    )
    image = Image.alpha_composite(image, arrow)

    # 3.4 箭头描边，提升小尺寸下的清晰度
    edge = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(edge).polygon(
        arrow_points,
        outline=ARROW_EDGE + (190,),
        width=max(2, int(canvas * 0.0035)),
    )
    image = Image.alpha_composite(image, edge)

    # 4. 圆角边缘的高光细线
    ring = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    inset = canvas * 0.008
    ImageDraw.Draw(ring).rounded_rectangle(
        (inset, inset, canvas - 1 - inset, canvas - 1 - inset),
        radius=canvas * 0.21,
        outline=RING_COLOR + (70,),
        width=max(2, int(canvas * 0.003)),
    )
    image = Image.alpha_composite(image, ring)

    # 5. 圆角遮罩：把所有溢出部分裁掉
    rounded = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(rounded).rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1),
        radius=int(canvas * 0.22),
        fill=255,
    )
    image.putalpha(ImageChops.multiply(image.getchannel("A"), rounded))

    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    icon = render_icon(SIZE)

    icon.save(PNG_PATH, format="PNG")
    print(f"已生成：{PNG_PATH} ({icon.width}x{icon.height})")

    icon.save(ICO_PATH, format="ICO", sizes=ICO_SIZES)
    print(f"已生成：{ICO_PATH} (包含 {len(ICO_SIZES)} 种尺寸)")


if __name__ == "__main__":
    main()
