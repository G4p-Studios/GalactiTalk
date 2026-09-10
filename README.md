# GalactiTalk

GalactiTalk is one headless, multi-server TeamTalk client combining the former
TTMediaBot, tt-star, and tt-chatgpt installations. One process connects to all
configured servers concurrently and every connection supports ChatGPT, STAR
speech synthesis, and YouTube media playback.

The migrated live configuration is `config.ini`. It contains eight unique
server/channel connections; duplicate old bot accounts are retained as disabled
`[legacy-account:*]` sections. The file is mode `0600` and is ignored by Git.

## Install and run

Python 3.11 or newer and FFmpeg are recommended. Create an environment and run:

```bash
cd ~/golactitalk
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 bot.py --check-config
python3 bot.py
```

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

GalactiTalk does not initialize a TeamTalk sound input device, launch mpv, or
depend on PulseAudio sinks. Each TeamTalk connection owns an independent SDK
`StartStreamingMediaFileToChannelEx` session. YouTube audio is resolved and
downloaded by yt-dlp, while STAR audio is written to a private temporary file;
both are streamed directly by that connection. Pause, seek, and gain use the
SDK's per-stream `MediaFilePlayback` controls. Speed changes are rendered with
FFmpeg before streaming.

Because TeamTalk provides one outbound media stream per connection, STAR audio
is queued while that connection is playing a media track. Other configured
servers remain entirely independent.

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

In a channel, address the bot and include the command:

```text
@[GalactiTalk] gpt Explain why the sky is blue.
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
names. YouTube is the only searchable media service. Direct URLs and admin-only
local files/directories are still accepted by `u`.

- `a` about; `p QUERY` search/play; bare `p` pause/resume.
- `u URL`, `s`, `b`, `n`, `c NUMBER` — URL, stop, previous, next, selection.
- `sb [SECONDS]`, `sf [SECONDS]`, `v [VALUE]`, `sp [0.25-4]` — playback control.
- `m [st|rt|tl|rtl|rnd]` — single/repeat/list/repeat-list/random mode.
- `f`, `r`, `gl`, `dl` — favorites, recents, direct link, channel upload.
- `sv` — reports the sole `yt` service.

Original administrative controls are retained: `bc`, `cg`, `cl`, `cn`, `cs`,
`cc`, `cm`, `jc`, `l`, `ua`, `ub`, `eh`, `sc`, `va`, `rs`, `q`, and `gcid`.
Native TeamTalk administrators and usernames listed in each server's `admins`
setting receive access. The `sc` command saves runtime changes back to the same
INI while preserving disabled legacy-account sections.

All of these commands have individual help topics—for example, `help p`,
`help voice`, `help f`, and `help jc`.

## Configuration and security

Prefer an `OPENAI_API_KEY` environment variable. `config.ini` also contains
TeamTalk passwords and possibly an SDK license, so keep it owner-readable only.
The shared `galactitalk-cache.json` contains migrated recents and favorites.

GalactiTalk is independent and is not affiliated with OpenAI or BearWare.dk.
See [THIRD_PARTY.md](THIRD_PARTY.md) for licensing boundaries.
