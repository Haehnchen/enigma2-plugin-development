from datetime import datetime, timezone
from decimal import Decimal

from .market import format_price


BACKGROUND = 0x101925
GRID = 0x253346
MUTED = 0xA0B1C6
UP = 0x43D6AD
DOWN = 0xFC7D8A
DAY_MS = 86_400_000


def draw_chart(width, height, candles, interval, fill, text, message="", volume_label="Volume"):
    if not candles:
        text(0, 0, width, height, message, "center", emphasis=True)
        return

    axis = round(width * 0.125)
    left, right = round(width * 0.023), width - axis - 5
    top, bottom = round(height * 0.055), round(height * 0.74)
    volume_top, volume_bottom = round(height * 0.78), round(height * 0.92)
    tick_height = round(height * 0.055)

    low = min(candle.low for candle in candles)
    high = max(candle.high for candle in candles)
    # A flat series still needs a nonzero price range.
    margin = (high - low) * Decimal("0.09") or abs(high) * Decimal("0.01") or Decimal("0.01")
    low, high = max(Decimal(0), low - margin), high + margin
    span = high - low

    def price_y(price):
        return bottom - round((price - low) / span * (bottom - top))

    price_step = span / 5
    precision = max(0, 1 - price_step.adjusted())
    for index in range(6):
        price = low + price_step * index
        y = price_y(price)
        fill(left, y, right - left, 1, GRID)
        label = format_price(price, precision) if precision <= 12 else f"{price:.2E}"
        text(right + 6, y - tick_height // 2, axis - 10, tick_height, label, "right")

    step = (right - left) / len(candles)
    bar_width = max(1, round(step * 0.64))
    max_volume = max(candle.quote_volume for candle in candles) or Decimal(1)

    duration = candles[-1].timestamp - candles[0].timestamp
    tick_format = "%d.%m %H:%M"
    if interval == "15m" and duration < DAY_MS:
        tick_format = "%H:%M"
    elif interval == "1d" or (interval == "4h" and duration >= 4 * DAY_MS):
        tick_format = "%d.%m"

    for index in sorted({0, len(candles) // 4, len(candles) // 2, len(candles) * 3 // 4, len(candles) - 1}):
        candle = candles[index]
        x = left + round((index + 0.5) * step)
        fill(x, top, 1, volume_bottom - top, GRID)
        label = datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc).strftime(tick_format)
        label_width = round(width * 0.13)
        x_label = max(left, min(x - label_width // 2, right - label_width))
        text(x_label, volume_bottom + 4, label_width, tick_height, label, "center")

    fill(left, volume_bottom, right - left, 1, GRID)
    text(right + 6, volume_bottom + 4, axis - 10, tick_height, "UTC", "right")

    for index, candle in enumerate(candles):
        x = left + round((index + 0.5) * step)
        rising = candle.close >= candle.open
        color = UP if rising else DOWN
        wick_top, wick_bottom = price_y(candle.high), price_y(candle.low)
        fill(x, wick_top, max(1, width // 750), max(1, wick_bottom - wick_top + 1), color)

        body_top, body_bottom = sorted((price_y(candle.open), price_y(candle.close)))
        fill(x - bar_width // 2, body_top, bar_width, max(2, body_bottom - body_top), color)

        volume_height = round(candle.quote_volume / max_volume * (volume_bottom - volume_top))
        if volume_height:
            fill(
                x - bar_width // 2,
                volume_bottom - volume_height,
                bar_width,
                volume_height,
                0x246752 if rising else 0x703C48,
            )

    text(right + 6, volume_top, axis - 10, tick_height, volume_label, "right")
