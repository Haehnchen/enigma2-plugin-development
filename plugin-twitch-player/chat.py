from random import randrange
import re

from twisted.application.internet import ClientService
from twisted.internet import reactor
from twisted.internet.endpoints import HostnameEndpoint, wrapClientTLS
from twisted.internet.protocol import Factory
from twisted.internet.ssl import optionsForClientTLS
from twisted.protocols.basic import LineOnlyReceiver

IRC_HEADER = re.compile(rb"^(?:@[^ ]+ )?(?::[^ ]+ )?([^ ]+)(?: ([^ ]+))?")


class ChatProtocol(LineOnlyReceiver):
    def connectionMade(self):
        self.sendLine(b"CAP REQ :twitch.tv/tags twitch.tv/commands")
        self.sendLine(("NICK justinfan%d" % randrange(10000, 9999999)).encode())
        self.sendLine(("JOIN #" + self.factory.login).encode())

    def lineReceived(self, data):
        if data.startswith(b"PING "):
            self.sendLine(b"PONG " + data[5:])
            return

        header = IRC_HEADER.match(data)
        if header is None or not self.factory.listen:
            return

        command, target = header.groups()
        if command == b"PRIVMSG" and target and target.lower() == self.factory.target:
            # Keep the raw IRC tags for emote offsets when the message is drawn.
            if data.startswith(b" :", header.end()) and len(data) > header.end() + 2:
                self.factory.listen(data)
        elif command == b"RECONNECT":
            self.transport.loseConnection()
        elif command in (b"366", b"ROOMSTATE"):
            self.factory.status(True)

    def connectionLost(self, reason):
        if self.factory.listen:
            self.factory.status(False)


class Chat(Factory):
    protocol = ChatProtocol

    def __init__(self, login, listen, status):
        self.login = login
        self.target = b"#" + login.encode("ascii")
        self.listen = listen
        self.status = status

        host = "irc.chat.twitch.tv"
        endpoint = wrapClientTLS(optionsForClientTLS(host), HostnameEndpoint(reactor, host, 6697, timeout=8))
        self.service = ClientService(endpoint, self)
        self.service.startService()

    def close(self):
        self.listen = self.status = None
        self.service.stopService()
