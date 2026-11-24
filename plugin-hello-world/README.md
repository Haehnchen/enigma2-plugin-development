# Hello World

Hello World is an example Enigma2 plugin that displays a “Hello World!” dialog.

## Installation

Open a terminal on the receiver and run this command to install or update Hello World.

```sh
opkg install https://github.com/Haehnchen/enigma2-plugin-development/releases/latest/download/enigma2-plugin-extensions-helloworld.ipk
```

To install a local build, copy the IPK to the receiver and run `opkg install /path/to/package.ipk`. Restart the Enigma2 GUI, then open **Extensions → Hello World**.

## Controls

Select the entry with the pink terminal icon. Press `Enter` to show “Hello World!” and `Esc` to return to the plugin list.

## Tests

Run `make test` from this directory. The test starts OpenATV in a disposable Compose container and checks the plugin browser entry, the PNG icon, the dialog text, and the return to the plugin list. The test scripts live in [tests/](tests/).

## Package

Run `make dist` from this directory to build the IPK in `../dist/`. The base version is set in [package.control](package.control).
