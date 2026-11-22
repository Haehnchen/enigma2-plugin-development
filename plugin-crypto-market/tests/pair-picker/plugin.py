from decimal import Decimal
from traceback import print_exc

from enigma import eTimer, quitMainloop
from Plugins.Extensions.CryptoMarket.market import Pair
from Plugins.Extensions.CryptoMarket.plugin import PairPicker
from Plugins.Plugin import PluginDescriptor


def check_picker(session):
    try:
        pair = Pair("BTCUSDT", "BTC", "USDT")
        picker = session.open(PairPicker, [(pair, Decimal(100))], ())
        assert picker["pairs"].getCurrent()[0] == pair.symbol
        picker.set_catalog([], unavailable=True)
        picker["actions"].action("OkCancelActions", "ok")
        assert picker["pairs"].getCurrent() is None
        assert session.current_dialog is picker and picker.execing
    except Exception:
        print_exc()
        quitMainloop(1)
    else:
        print("[PairPickerTest] PASS: OK after an empty refresh leaves the picker open.", flush=True)
        quitMainloop(0)


def start(reason, session=None, **kwargs):
    if reason == PluginDescriptor.REASON_START:
        timer = session.pair_picker_test_timer = eTimer()
        timer.callback.append(lambda: check_picker(session))
        timer.start(100, True)


def Plugins(**kwargs):
    return PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=start)
