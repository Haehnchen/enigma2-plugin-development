# Joyn Player

Watch Joyn's live TV channels and browse its on-demand catalog of shows, episodes, and films on Enigma2.

## Installation

Copy the IPK to a compatible ARM hard-float receiver with the FFmpeg 9 libraries and run `opkg install /path/to/package.ipk`. Restart the Enigma2 GUI, then open **Extensions → Joyn**.

## Controls

- **Red** opens Live TV, **Green** opens the catalog, **Yellow** searches and **Blue** refreshes.
- **OK** opens a folder or starts playback. During playback, **OK** or **Info** toggles the info bar. **Back** returns to the list.
- For on-demand videos, tap **Left** or **Right** to move the target by 10 seconds. Hold the key for progressively larger steps of 30, 60 and 120 seconds. The player seeks when you release the key and stop adjusting the target. **Yellow** pauses or resumes.

## DRM playback

- The Enigma2 plugin uses the upstream Joyn Kodi add-on through a small [Python adapter](backend/joyn.py) to browse the catalog and resolve live and on-demand streams.
- For protected DASH streams, `dash2ts` loads Kodi's InputStream Adaptive and the ARM Widevine CDM. The plugin downloads and verifies Widevine on first use. Playback runs on the receiver, where a local HTTP relay feeds the MPEG transport stream to Enigma2's 4097 player.
- The [dash2ts seek patch](tools/0001-add-start-position.patch) enables seeking in on-demand videos by passing the target playback position to the helper.

## Build from source

```sh
cd plugin-joyn-player
make test
make test-gui
make dist
```

The IPK is written to `../dist/`. The [package metadata](package.control) sets the version and receiver dependencies.
