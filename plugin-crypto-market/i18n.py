from functools import lru_cache
from gettext import translation
from pathlib import Path

from Components.International import international


@lru_cache(maxsize=16)
def catalog(locale):
    return translation("CryptoMarket", Path(__file__).with_name("locale"), languages=[locale], fallback=True)


def _(message):
    return catalog(international.getLocale()).gettext(message)
