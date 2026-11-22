"""Verify the rendered glyphs and arrange the two native LCD captures side by side."""

from pathlib import Path
from sys import argv

from PIL import Image, ImageDraw, ImageFont


def ink_bounds(image, y1, y2):
    mask = image.crop((0, y1, image.width, y2)).convert("L")
    bounds = mask.point(lambda value: 255 if value > 100 else 0).getbbox()
    assert bounds, "No visible text in this row"
    left, top, right, bottom = bounds
    assert left > 0 and right < image.width and top > 0 and bottom < mask.height, bounds
    return left, top + y1, right, bottom + y1


def compare(directory):
    standard = Image.open(directory / "standard.png").convert("RGB")
    xl = Image.open(directory / "xl.png").convert("RGB")
    assert standard.size == xl.size == (480, 320)
    # Compare visible glyphs and whitespace, since label boxes include font padding.
    clock = ink_bounds(xl, 0, 180)
    line = ink_bounds(xl, 180, 210)
    date = ink_bounds(xl, 210, 274)
    stock_clock = ink_bounds(standard, 0, 150)
    stock_date = ink_bounds(standard, 175, 265)
    assert 1.45 <= (clock[3] - clock[1]) / (stock_clock[3] - stock_clock[1]) <= 1.6
    assert date[3] - date[1] == stock_date[3] - stock_date[1]
    assert line[2] - line[0] == 240 and line[3] - line[1] == 3
    for left, _, right, _ in (clock, line, date):
        assert abs(left - (xl.width - right)) <= 1, (left, right)
    assert abs(clock[1] - (xl.height - date[3])) <= 1, (clock, date)
    upper_gap = line[1] - clock[3]
    lower_gap = date[1] - line[3]
    assert abs(upper_gap - lower_gap) <= 1, (upper_gap, lower_gap)
    assert abs(upper_gap - 40) <= 1, upper_gap

    comparison = Image.new("RGB", (972, 364), "#303238")
    draw = ImageDraw.Draw(comparison)
    font = ImageFont.truetype("/usr/share/fonts/DejaVuSans.ttf", 21)
    for x, title, panel in [(0, "Standard", standard), (492, "Standby XL", xl)]:
        draw.text((x + 240, 22), title, font=font, fill="white", anchor="mm")
        comparison.paste(panel, (x, 44))
    comparison.save(directory / "comparison.webp", quality=60)


if __name__ == "__main__":
    compare(Path(argv[1]))
