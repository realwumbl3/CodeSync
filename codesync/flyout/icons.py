"""Generated tray icon. The frozen exe does not need an external image."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

_SIZE = 32


def load_tray_icon() -> Image.Image:
    image = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, _SIZE - 2, _SIZE - 2), radius=7, fill=(32, 32, 36, 255))
    try:
        font = ImageFont.truetype("segoeui.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    text = "CS"
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text(((_SIZE - width) / 2, (_SIZE - height) / 2 - 1), text, fill=(243, 243, 243, 255), font=font)
    return image
