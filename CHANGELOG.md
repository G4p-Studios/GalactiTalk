# Changelog

All notable changes to GalactiTalk are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Matched TTMediaBot's status metadata behavior, including YouTube uploader
  names and live direct/local stream metadata in `artist - title - station`
  order.

## [0.1.0] - 2026-09-10

### Added

- One concurrent process for multiple TeamTalk servers.
- Unified prefix-free commands and detailed per-command help.
- Explicit private `gpt` prompts and case-insensitive channel `@chatgpt`
  mentions.
- STAR synthesis, voice selection, preview, rate, pitch, queueing, and status.
- Fast YouTube search, direct URL/playlist loading, and MPV playback controls.
- Automatically created, isolated PulseAudio routes for every media player.
- Dynamic `Playing:` and `Paused:` TeamTalk status messages.
- One INI configuration and a combined interactive wizard.
- Automatic dependency setup and consent-based TeamTalk Standard SDK discovery.

### Removed

- Non-YouTube legacy media search services.
- Recents, favorites, and media cache commands.
- Legacy media ban, lock, administrator-list, and command-block features.

[Unreleased]: https://github.com/G4p-Studios/GalactiTalk/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/G4p-Studios/GalactiTalk/releases/tag/v0.1.0
