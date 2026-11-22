# Hello World

Open **Extensions → Hello World**. The entry has a pink terminal icon. Press `Enter` to show “Hello World!” and `Esc` to return to the plugin list.

## Tests

Run `make test` from this directory. The test starts OpenATV in a disposable Compose container and checks the plugin browser entry, the PNG icon, the dialog text, and the return to the plugin list. The test scripts live in [tests/](tests/).

## Package

Run `make dist` from this directory to build the IPK in `../dist/`. The package version is set in [package.control](package.control).
