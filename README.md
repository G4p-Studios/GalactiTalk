# GalactiTalk

GalactiTalk is one headless, multi-server TeamTalk client combining the former
TTMediaBot, tt-star, and tt-chatgpt installations. One process connects to all
configured servers concurrently and every connection supports ChatGPT, STAR
speech synthesis, and YouTube media playback.

The migrated live configuration is `config.ini`. It contains eight unique
server/channel connections; duplicate old bot accounts are retained as disabled
`[legacy-account:*]` sections. The file is mode `0600` and is ignored by Git.

## Install and run

Python 3.10 or newer is required. GalactiTalk creates its own virtual environment,
installs its Python packages (including yt-dlp), and installs missing MPV and
PulseAudio command-line packages through apt, dnf, pacman, or zypper. Run:

```bash
cd ~/golactitalk
python3 bot.py --check-config
python3 bot.py
```

On the first run, the operating system may ask for the sudo password needed to
install system packages. To perform dependency setup by itself, without starting
the bot, run `python3 bot.py --install-deps`. Later starts re-check the dependency
manifest and repair missing packages automatically.

Use the full combined interactive wizard at any time:

```bash
python3 bot.py --configure
```

Both `--configure` and normal startup accept `--config /path/to/config.ini`.
The wizard includes all OpenAI, STAR, media, TeamTalk-global, and per-server
questions from the predecessor bots and keeps secrets hidden when editing.

If TeamTalk is not importable, GalactiTalk uses the same safe SDK flow as
tt-star/tt-chatgpt: it discovers the latest matching Standard SDK on BearWare's
live download page, displays the licensing notice, asks for consent, validates
archive paths, and installs per-user under
`~/.local/share/galactitalk/teamtalk-sdk` (with platform-appropriate locations
on macOS and Windows). `TEAMTALK_SDK_DIR` can select an unpacked SDK.

## Audio routing

YouTube media uses MPV and an entirely automatic per-playback audio route. Before
each item starts, GalactiTalk creates a uniquely named PulseAudio null sink. MPV
plays into that sink and the same server connection opens its monitor source as
the TeamTalk voice input. Pause, seek, gain, and speed are controlled directly
through MPV. When the item finishes, fails, is replaced, or is stopped,
GalactiTalk closes the TeamTalk input and unloads the PulseAudio module, removing
both the sink and its monitor source. PipeWire's PulseAudio compatibility server
is supported. Users do not select or pre-create any audio devices.

This MPV/PulseAudio path is used for media only. STAR keeps its native TeamTalk
media-file stream. STAR requests wait while media is active on the same server;
other configured servers remain entirely independent.

## Command interface

Every action requires a command and commands never begin with `/`. Plain
private-message text is not sent to ChatGPT or STAR. Send `help` for the unified
command list, or `help COMMAND` for detailed usage for any command. The old `h`,
`/help`, and `!help` forms are intentionally not accepted.

## ChatGPT use

- `gpt PROMPT` — ask ChatGPT in a private message.
- `clear` — clear your private context.
- `status` — show ChatGPT, STAR, queue, and media status.
- `clear-all` — TeamTalk/admin-list administrators only.
- `restart`, `quit` — administrators only.

In a channel, the explicit ChatGPT trigger is `@chatgpt` (case-insensitive),
regardless of the bot's current TeamTalk nickname:

```text
@chatgpt Explain why the sky is blue.
```

Channel and private conversation histories are separate, bounded, in memory,
and sent with `store=false`. Channel prompts retain the speaker nickname.

## STAR use

- `say TEXT` — synthesize text with STAR.
- `voices [FILTER]`, `voice NAME|NUMBER` — discover/select voices.
- `rate NUMBER|none`, `pitch NUMBER|none` — per-user voice parameters.
- `preview [VOICE]`, `current`, `status`, `stop` — preview/settings, unified status, and STAR queue controls.

Users must be in GalactiTalk's current channel before requesting speech.

## YouTube/media commands

Media commands are private messages and keep the original TTMediaBot compact
names. YouTube is the only searchable media service. Searches use the original
lightweight search path, and playback resolves an audio stream URL for MPV
instead of downloading the complete file first. Direct URLs and admin-only local
files/directories are accepted by `u`.

- `a` about; `p QUERY` search/play; bare `p` pause/resume.
- `u URL`, `s`, `b`, `n`, `c NUMBER` — URL, stop, previous, next, selection.
- `sb [SECONDS]`, `sf [SECONDS]`, `v [VALUE]`, `sp [0.25-4]` — playback control.
- `m [st|rt|tl|rtl|rnd]` — single/repeat/list/repeat-list/random mode.
- `f`, `r`, `gl`, `dl` — favorites, recents, direct link, channel upload.
- `sv` — reports the sole `yt` service.

The old TTMediaBot moderation and administrative command group is intentionally
disabled for now, including configured administrators, command blocking,
banning, locking, and their list editors. Native TeamTalk administrators still
receive core administrator access. Every active command has an individual help
topic—for example, `help p`, `help voice`, and `help f`.

## Configuration and security

Prefer an `OPENAI_API_KEY` environment variable. `config.ini` also contains
TeamTalk passwords and possibly an SDK license, so keep it owner-readable only.
The shared `galactitalk-cache.json` contains migrated recents and favorites.

GalactiTalk is independent and is not affiliated with OpenAI or BearWare.dk.
See [THIRD_PARTY.md](THIRD_PARTY.md) for licensing boundaries.
