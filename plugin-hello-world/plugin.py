from Plugins.Plugin import PluginDescriptor
from Screens.MessageBox import MessageBox


def main(session, **kwargs):
    session.open(
        MessageBox,
        "Hello World!",
        type=MessageBox.TYPE_INFO,
        title="Hello World",
    )


def Plugins(**kwargs):
    return PluginDescriptor(
        name="Hello World",
        description="Opens a Hello World dialog.",
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon="plugin.png",
        fnc=main,
    )
