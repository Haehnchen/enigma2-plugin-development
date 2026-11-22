# Enigma2 plugin development

A local Docker Compose environment for developing and testing Enigma2 plugins. It runs the Enigma2 interface from OpenATV 8.0, an OE-Alliance distribution, and makes it available in your browser.

Requires Docker with Compose and Make on x86-64 Linux.

1. Run `make start` to build `enigma2-dev:8.0` and start the container in the foreground.
2. In a second terminal, run `make ui` to open the interface at [localhost:6080](http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale). The command returns after opening the browser. Click inside the noVNC display to give it keyboard focus.
3. Press `Space` to open the main menu, then go to **Extensions**.

The test interface starts in English. If port 6080 is in use, run `make start UI_PORT=6081` and `make ui UI_PORT=6081`.

Stop the container with `Ctrl+C` in the terminal running `make start`, or run `make stop` from another terminal in the project directory.

## Browser keyboard controls

| Browser key | Remote button |
| --- | --- |
| `Space` | MENU |
| `↑` / `↓` | Up / Down |
| `←` / `→` | Left / Right |
| `Enter` | OK |
| `Esc` | EXIT |
| `Page Up` | CH+ (next bouquet) |
| `Page Down` | CH− (previous bouquet) |
| `F1` / `F2` / `F3` / `F4` | Red / Green / Yellow / Blue |
| `F5` | INFO |
| `F11` | EPG |

## Plugins

- [Crypto Market dashboard](plugin-crypto-market/README.md)
- [Hello World](plugin-hello-world/README.md)

Each plugin README documents its test commands.

## Edit plugins

Compose mounts the plugin directories read-only under `/usr/lib/enigma2/python/Plugins/Extensions/`. Edit files on the host and run `make restart` to reload your changes. You can also start the environment directly with `docker compose up --build`.

To capture the development container's current display, run `make screenshot` to save `enigma2.png`.

## Build installable packages

Run `make dist` with Python 3 to build one `.ipk` per plugin in `dist/`. Run the same target from a plugin directory to build just that plugin.

Copy the IPK to a receiver running a compatible OE-Alliance Enigma2 image. Install it with `opkg install` and the path to the file. Restart the Enigma2 GUI and open **Extensions**.

The Dockerfile builds the pinned [OpenATV 8.0 commit](https://github.com/openatv/enigma2/commit/d31cb9aad315470e39bf04edcd1227279648a404) with SDL2 on top of the [OpenATV test environment](https://github.com/openatv/enigma2-testenv).
