# Enigma2 plugin development

- Keep code, assets, tests, and package metadata in the plugin's `plugin-*` directory. Register it with `PluginDescriptor` in `plugin.py` and add a read-only mount in `compose.yaml`.
- `make start` runs OpenATV 8.0. `make ui` opens noVNC. Use `make restart` after Python edits and `make stop` to shut down.
- Run `make test` from the plugin directory. Check GUI changes in noVNC. `make screenshot` saves `enigma2.png`.
- Update `.po` and rebuild `.mo` with `msgfmt` for translation changes.
- `make dist` writes IPKs to `dist/`
