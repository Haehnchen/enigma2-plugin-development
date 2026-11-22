from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock


from .. import chart_layout as layout
from ..market import Candle


D = Decimal


def candle(timestamp, open_price, high, low, close, volume):
    return Candle(timestamp, *map(D, (open_price, high, low, close, volume)))


class ChartTest(TestCase):
    def test_candles_draw_price_bodies_wicks_volume_and_labels(self):
        candles = [
            candle(0, "100", "108", "98", "106", "12"),
            candle(3_600_000, "105", "109", "100", "101", "4"),
        ]
        fill, text = Mock(), Mock()
        layout.draw_chart(778, 426, candles, "1h", fill, text)

        for color in (layout.UP, layout.DOWN):
            wick, body = [call.args[:4] for call in fill.call_args_list if call.args[-1] == color]
            self.assertGreater(body[2], wick[2])
            self.assertLessEqual(wick[1], body[1])
            self.assertGreaterEqual(wick[1] + wick[3], body[1] + body[3])

        chart_bars = [call.args for call in fill.call_args_list if call.args[-1] in (0x246752, 0x703C48)]
        self.assertEqual(len(chart_bars), 2)
        self.assertGreater(chart_bars[0][3], chart_bars[1][3])
        self.assertEqual(chart_bars[0][1] + chart_bars[0][3], chart_bars[1][1] + chart_bars[1][3])
        labels = [call.args[4] for call in text.call_args_list]
        for label in ("01.01 00:00", "01.01 01:00", "UTC", "Volume"):
            self.assertIn(label, labels)

    def test_flat_tiny_prices_and_zero_volume_stay_in_view(self):
        candles = [candle(0, "0.00000000001", "0.00000000001", "0.00000000001", "0.00000000001", "0")]
        for width, height in ((778, 426), (1167, 639)):
            with self.subTest(width=width, height=height):
                fill, text = Mock(), Mock()
                layout.draw_chart(width, height, candles, "4h", fill, text)
                for call in fill.call_args_list + text.call_args_list:
                    x, y, w, h = call.args[:4]
                    self.assertGreaterEqual(min(x, y), 0)
                    self.assertGreater(min(w, h), 0)
                    self.assertLessEqual(x + w, width)
                    self.assertLessEqual(y + h, height)

    def test_time_axis_distinguishes_dates_and_intraday_ticks_for_configurable_history(self):
        for interval, interval_ms, count, last_label in (
            ("15m", 900_000, 240, "03.01 11:45"),
            ("4h", 14_400_000, 12, "02.01 20:00"),
        ):
            with self.subTest(interval=interval, count=count):
                candles = [candle(index * interval_ms, "100", "108", "98", "106", "12") for index in range(count)]
                fill, text = Mock(), Mock()
                layout.draw_chart(778, 426, candles, interval, fill, text)
                self.assertEqual(sum(call.args[-1] == layout.UP for call in fill.call_args_list), 2 * count)
                labels = [call.args[4] for call in text.call_args_list]
                self.assertIn("01.01 00:00", labels)
                self.assertIn(last_label, labels)

    def test_daily_chart_uses_dates_on_the_time_axis(self):
        candles = [candle(index * 86_400_000, "100", "108", "98", "106", "12") for index in range(12)]
        fill, text = Mock(), Mock()
        layout.draw_chart(778, 426, candles, "1d", fill, text)
        labels = [call.args[4] for call in text.call_args_list]
        self.assertIn("01.01", labels)
        self.assertIn("12.01", labels)

    def test_empty_chart_draws_only_the_status(self):
        fill, text = Mock(), Mock()
        layout.draw_chart(778, 426, [], "15m", fill, text, "Loading candles …")
        fill.assert_not_called()
        text.assert_called_once_with(0, 0, 778, 426, "Loading candles …", "center", emphasis=True)
