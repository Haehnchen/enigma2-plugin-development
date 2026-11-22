from contextlib import suppress
from dataclasses import dataclass
from json import dumps, loads
from pathlib import Path
from tempfile import NamedTemporaryFile

from .market import DEFAULT_CANDLE_COUNT, DEFAULT_INTERVAL, DEFAULT_WATCHLIST, INTERVALS


MIN_CANDLE_COUNT = 12
MAX_CANDLE_COUNT = 240


@dataclass(frozen=True)
class ChartSettings:
    interval: str = DEFAULT_INTERVAL
    candle_count: int = DEFAULT_CANDLE_COUNT


def load_preferences(path):
    try:
        return parse_preferences(loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return DEFAULT_WATCHLIST, ChartSettings()


def write_preferences(path, watchlist, settings):
    data = {"pairs": watchlist, "interval": settings.interval, "candle_count": settings.candle_count}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary file on the same filesystem for atomic replacement.
    handle = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(dumps(data, ensure_ascii=False) + "\n")
        temporary.replace(path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def parse_preferences(values):
    if not isinstance(values, dict):
        return DEFAULT_WATCHLIST, ChartSettings()

    if "pairs" in values:
        if not isinstance(values["pairs"], list):
            return DEFAULT_WATCHLIST, ChartSettings()

        symbols = tuple(dict.fromkeys(
            symbol for symbol in values["pairs"] if isinstance(symbol, str) and symbol
        ))
        interval = values.get("interval")
        candle_count = values.get("candle_count")
        if type(candle_count) is not int or not MIN_CANDLE_COUNT <= candle_count <= MAX_CANDLE_COUNT:
            candle_count = DEFAULT_CANDLE_COUNT

        return symbols, ChartSettings(interval if interval in INTERVALS else DEFAULT_INTERVAL, candle_count)

    # Older versions stored a separate interval for each pair.
    saved = {symbol: interval for symbol, interval in values.items() if symbol and interval in INTERVALS}
    interval = next((value for value in saved.values() if value != DEFAULT_INTERVAL), DEFAULT_INTERVAL)
    return tuple(saved), ChartSettings(interval)
