# Joyn Player

Watch Joyn's live TV channels and browse its on-demand catalog of shows, episodes, and films on Enigma2.

## Installation

Run this command in a terminal on the receiver to install or update the plugin.

```sh
opkg install https://github.com/Haehnchen/enigma2-plugin-development/releases/latest/download/enigma2-plugin-extensions-joynplayer.ipk
```

To install a local build, copy the IPK to the receiver and run `opkg install /path/to/package.ipk`. Restart the Enigma2 GUI, then open **Extensions → Joyn**.

## Features

- Live streaming of Joyn TV channels.
- On-demand playback of episodes and films from the Joyn catalog.
- Search for shows and films by title and play episodes or films from the results.

## Controls

- **Red** opens Live TV, **Green** opens the catalog, **Yellow** searches and **Blue** refreshes.
- **OK** opens a folder or starts playback. During playback, **OK** or **Info** toggles the info bar. **Back** returns to the list.
- For on-demand videos, tap **Left** or **Right** to move the target by 10 seconds. Hold the key for progressively larger steps of 30, 60 and 120 seconds. The player seeks when you release the key and stop adjusting the target. **Yellow** pauses or resumes.

## DRM playback

- The Enigma2 plugin uses the upstream Joyn Kodi add-on through a small [Python adapter](backend/joyn.py) to browse the catalog and resolve live and on-demand streams.
- For protected DASH streams, `dash2ts` loads Kodi's InputStream Adaptive and the ARM Widevine CDM. The plugin downloads and verifies Widevine on first use. Playback runs on the receiver, where a local HTTP relay feeds the MPEG transport stream to Enigma2's 4097 player.
- The [dash2ts seek patch](tools/0001-add-start-position.patch) enables seeking in on-demand videos by passing the target playback position to the helper.
- Live TV uses adaptive quality. On-demand videos use the highest playable rendition for the output resolution. The [transport stream patch](tools/0002-fix-stream-selection-and-timestamps.patch) supplies the receiver with presentation timestamps, decode timestamps and a running MPEG-TS clock.

## Build from source

```sh
cd plugin-joyn-player
make test
make test-gui
make dist
```

The IPK is written to `../dist/`. The [package metadata](package.control) sets the version and receiver dependencies.
