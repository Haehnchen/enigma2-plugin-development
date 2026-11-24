from Plugins.Plugin import PluginDescriptor

from . import _


def main(session, **kwargs):
    from .browser import Browser

    session.open(Browser)


def Plugins(**kwargs):
    return PluginDescriptor(
        name="Joyn",
        description=_("Watch Joyn live TV and browse the catalog."),
        where=[PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU],
        icon="plugin.png",
        fnc=main,
    )
