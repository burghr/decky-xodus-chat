# XBOX Chat for Decky

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that
shows your Xbox friends and Xbox text chat in the Quick Access menu. You can
start a new chat or reply to a message without a return to the
[Xodus Nexus](https://github.com/domsvoboda/xodus-nexus) launcher.

> Unofficial side project, provided as is. Not affiliated with or endorsed by
> Microsoft or Xbox, or by the Xodus Nexus project.

## Features

- Friends list, sorted by the newest message, with a preview of the last message.
- Chat view with the newest message at the bottom and the text box under it.
  Enter or the arrow button sends the message.
- B goes back from a chat to the friends list.
- Count of new messages for each friend, and an optional background check
  every 2 minutes with a notification.
- The last friends and messages show at once after a restart (see Privacy).

## Requirements

- Decky Loader.
- Xodus Nexus 0.4.0 or later with backend 0.2.12 or later, signed in.
  The plugin uses the backend of Nexus. Nexus does not need to run.

Tested on a Steam Deck (SteamOS 3.9). Not yet tested on ARM devices
(for example the AYN Odin 3 with the
[arm64 port](https://github.com/burghr/xodus-nexus-arm-port)).

## Install

1. Download the ZIP from the releases page, or build it (see Build).
2. In Decky, open Settings > Developer.
3. Select "Install Plugin from ZIP" and select the ZIP.

## How it works

The plugin does not talk to Xbox itself. Its Python backend (`main.py`) runs the
Nexus backend CLI, `xodus-cli social --json`, with a JSON request on standard
input. The CLI reads the signed-in account from the system wallet, so the
plugin never sees a token. The plugin finds the CLI through
`~/.config/xodus-nexus/xodus-backends/current.json`.

The CLI has three operations: `friends`, `conversation`, and `send`. It has no
inbox operation. To find new messages, the plugin reads the conversation of each
friend and compares it with the last message that it saw. Each CLI call takes
about 2 seconds, so the plugin runs up to 4 calls at the same time.

When a chat opens, the plugin moves the Steam gamepad focus to the text box.
This uses Steam internals that are not a public API. If a Steam update changes
them, the plugin falls back to a normal focus.

## Privacy

- The plugin keeps friends and the last messages of each conversation in
  `~/homebrew/data/decky-xodus-chat/cache.b64`, so that they show at once after
  a restart. The file is base64 encoded. **Base64 is not encryption**: it only
  stops a casual read. The file mode is 0600 (your user only).
- The cache is cleared when the CLI reports a different Xbox account.
- An uninstall of the plugin removes the cache and the state file.
- The plugin does not write message text or friends to the Decky log.

## Build

You need Node.js and pnpm 9 (`npx -y pnpm@9` works without a global install).

```sh
./scripts/package.sh    # builds and writes out/decky-xodus-chat-<version>.zip
python3 tests/test_main.py
```

## License

BSD 3-Clause. See [LICENSE](LICENSE). Based on the
[decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template).
