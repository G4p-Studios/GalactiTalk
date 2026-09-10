# GalactiTalk

[![CI](https://github.com/G4p-Studios/GalactiTalk/actions/workflows/ci.yml/badge.svg)](https://github.com/G4p-Studios/GalactiTalk/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

GalactiTalk is a headless, multi-server TeamTalk bot that combines ChatGPT,
STAR text-to-speech, and YouTube media playback in one process. Every configured
TeamTalk connection runs independently, including its media queue and audio
route.

## Highlights

- Connects to multiple TeamTalk servers and channels at the same time.
- Provides explicit, prefix-free commands with detailed `help COMMAND` topics.
- Uses `gpt PROMPT` in private messages and case-insensitive `@chatgpt` mentions
  in channels; ordinary private messages are never treated as prompts.
- Connects to one or more STAR WebSocket services for speech synthesis.
- Searches and plays YouTube plus direct URLs supported by yt-dlp. Other legacy
  TTMediaBot search services are intentionally excluded.
- Creates a separate PulseAudio null sink for each active media player, routes
  it into the correct TeamTalk connection, and removes it after playback.
- Updates TeamTalk status to `Playing: TITLE` or `Paused: TITLE`, then restores
  the configured status when playback ends. YouTube titles include the uploader,
  while direct/local metadata follows TTMediaBot's `artist - title - station`
  order.
- Keeps all settings in one INI file and includes an interactive configuration
  wizard.
- Creates its own virtual environment and installs missing Python, yt-dlp, MPV,
  and PulseAudio command-line dependencies.
- Discovers and installs the current TeamTalk Standard SDK through a
  consent-based flow.

## Requirements

- Linux with Python 3.10 or newer.
- PulseAudio, or PipeWire with its PulseAudio compatibility service.
- A TeamTalk server account for each desired connection.
- A separately licensed TeamTalk 5 SDK. GalactiTalk can download the Standard
  SDK after showing its licensing notice and receiving consent.
- Optional: an OpenAI API key for ChatGPT and a reachable STAR WebSocket server
  for speech synthesis.

Automatic system-package installation recognizes apt, dnf, pacman, and zypper.
It may ask for your sudo password when MPV, `pactl`, or Python venv support is
missing.

## Quick start

```bash
git clone https://github.com/G4p-Studios/GalactiTalk.git
cd GalactiTalk
python3 bot.py --install-deps
python3 bot.py --configure
python3 bot.py --check-config
python3 bot.py
```

Normal startup also checks dependencies, so `--install-deps` may be omitted.
The first TeamTalk SDK installation is interactive because its separate license
requires explicit consent. To use an already unpacked SDK, set
`TEAMTALK_SDK_DIR` to the SDK root before starting GalactiTalk.

To use a configuration outside the repository, append
`--config /path/to/config.ini` to `--configure`, `--check-config`, or a normal
start.

## Configuration

Run `python3 bot.py --configure` for the full wizard. It edits existing values,
hides secrets while prompting, and supports any number of `[server:NAME]`
sections. [config.example.ini](config.example.ini) documents the complete file
shape:

```ini
[server:main]
enabled = yes
host = teamtalk.example.com
tcp_port = 10333
udp_port = 10333
encrypted = no
nickname = GalactiTalk
username = bot-account
password =
channel = /Music/
channel_password =
status =
gender = n
start_commands =

[server:second]
enabled = yes
host = another.example.com
tcp_port = 10333
udp_port = 10333
encrypted = no
nickname = GalactiTalk
channel = /
```

Keep `config.ini` owner-readable because it can contain TeamTalk passwords,
channel passwords, and API credentials. The file is ignored by Git. Prefer the
`OPENAI_API_KEY` environment variable over writing an OpenAI key in the INI.

Some YouTube videos require a signed-in session. Export cookies in Netscape
format, make the file private, and set `youtube_cookiefile` in `[media]`. Do
not commit that file.

## Commands

Commands are sent to the bot in a private TeamTalk message and never begin with
`/`. Send `help` for the unified list or `help COMMAND` for exact usage.

### Core and ChatGPT

| Command | Purpose |
| --- | --- |
| `help [COMMAND]` | Show the unified help or one detailed help topic. |
| `gpt PROMPT` | Ask ChatGPT using your private conversation context. |
| `clear` | Clear your private ChatGPT context on this server. |
| `clear-all` | Clear all ChatGPT contexts; TeamTalk administrators only. |
| `status` | Show ChatGPT, STAR, queue, and media status. |
| `restart` | Restart GalactiTalk; TeamTalk administrators only. |
| `quit` | Stop GalactiTalk; TeamTalk administrators only. |

In a channel, use `@chatgpt PROMPT` instead of `gpt`. The mention is
case-insensitive. Channel and private histories are separate, bounded, held in
memory, and sent with OpenAI storage disabled.

### STAR speech

| Command | Purpose |
| --- | --- |
| `say TEXT` | Synthesize speech in the bot's current channel. |
| `voices [FILTER]` | List voices, optionally filtered by name. |
| `voice NAME\|NUMBER` | Select a voice by name or a previous result number. |
| `rate NUMBER\|none` | Set or clear your speech rate. |
| `pitch NUMBER\|none` | Set or clear your speech pitch. |
| `preview [VOICE]` | Preview a voice with the configured sentence. |
| `current` | Show your current voice, rate, and pitch. |
| `stop` | Stop STAR audio and clear its queue for this server. |

Users must be in the bot's current channel before requesting speech. STAR audio
uses TeamTalk's media-file streaming and waits if media is active on that same
connection.

### YouTube and media

| Command | Purpose |
| --- | --- |
| `a` | Show information about GalactiTalk. |
| `p QUERY` | Search YouTube and play the first result. |
| `p` | Pause or resume playback. |
| `u URL\|PATH` | Load a supported URL or playlist; administrators may use local paths. |
| `s` | Stop playback and clear the current track. |
| `b`, `n` | Move to the previous or next item. |
| `c NUMBER` | Select a numbered result or playlist item. |
| `sb [SECONDS]`, `sf [SECONDS]` | Seek backward or forward. |
| `v [VOLUME]` | Show or set media volume. |
| `sp [SPEED]` | Show or set playback speed from 0.25 through 4. |
| `m [st\|rt\|tl\|rtl\|rnd]` | Show or select the playback mode. |
| `gl` | Return the current item's original link or path. |
| `dl` | Download and upload the current non-live track to the channel. |
| `sv` | Show the sole searchable service, `yt`. |

The retired favorites, recents, cache, moderation, ban, lock, and media-command
blocking features are not present.

## Automatic media routing

Before each media item starts, GalactiTalk loads a uniquely named PulseAudio
null sink. MPV outputs to that sink while the corresponding TeamTalk connection
opens its monitor source as voice input. Pause, seek, volume, and speed operate
through MPV. On stop, failure, replacement, or completion, GalactiTalk closes
the TeamTalk input and unloads the null sink. Users do not create or select audio
devices manually.

## Run as a user service

After completing the interactive SDK setup, copy
[galactitalk.service.example](galactitalk.service.example) into
`~/.config/systemd/user/galactitalk.service`, replace both
`/path/to/GalactiTalk` placeholders with the absolute clone path, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now galactitalk.service
journalctl --user -u galactitalk.service -f
```

Add `OPENAI_API_KEY` through a private systemd environment file or service
override; avoid committing credentials to the unit.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
GALACTITALK_TEST_STUB_TEAMTALK=1 .venv/bin/python -m unittest discover -v
```

The test-only stub avoids downloading the separately licensed TeamTalk SDK in
CI. Production startup never enables it. See [CONTRIBUTING.md](CONTRIBUTING.md)
for contribution guidelines and [CHANGELOG.md](CHANGELOG.md) for release notes.

## Security and licensing

Do not publish `config.ini`, YouTube cookies, generated media, chat logs, or
runtime logs. Please report vulnerabilities as described in
[SECURITY.md](SECURITY.md).

GalactiTalk is available under the [MIT License](LICENSE). TeamTalk and the
other dependencies retain their own licenses; see
[THIRD_PARTY.md](THIRD_PARTY.md). GalactiTalk is independent and is not endorsed
by OpenAI or BearWare.dk.
