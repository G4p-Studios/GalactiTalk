# Security policy

## Reporting a vulnerability

Please report security issues privately through the repository's
[security-advisory form](https://github.com/G4p-Studios/GalactiTalk/security/advisories/new).
Do not open a public issue for a suspected vulnerability.

Include the affected version or commit, operating system, impact, reproduction
steps, and any suggested mitigation. You should receive an acknowledgment
within seven days. Please allow a reasonable remediation window before public
disclosure.

## Protecting local secrets

`config.ini` can contain TeamTalk credentials, channel passwords, API keys,
and an SDK license. YouTube cookie files also grant account access. Both must
remain private and are excluded from Git. Prefer `OPENAI_API_KEY` and a
protected systemd environment file over storing API credentials in the
repository.

Never attach configuration files, cookie files, private messages, generated
audio, or runtime logs to public bug reports without carefully redacting them.
