from functools import lru_cache
from gettext import translation
from pathlib import Path


@lru_cache(maxsize=8)
def messages(locale):
    return translation("JoynPlayer", Path(__file__).with_name("locale"), languages=locale, fallback=True)


def _(message):
    try:
        from Components.International import international
    except ImportError:
        locale = None
    else:
        locale = (international.getLocale(),)
    return messages(locale).gettext(message)
