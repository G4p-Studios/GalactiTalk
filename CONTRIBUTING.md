# Contributing to GalactiTalk

Thanks for helping improve GalactiTalk.

## Before opening a change

- Search existing issues and pull requests for related work.
- Open an issue first for large behavior changes so the design can be discussed.
- Never include TeamTalk passwords, OpenAI keys, YouTube cookies, private chat
  content, SDK binaries, or runtime logs containing user data.
- Keep the TeamTalk SDK out of the repository; it is licensed separately.

## Development setup

```bash
git clone https://github.com/G4p-Studios/GalactiTalk.git
cd GalactiTalk
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Run the suite without requiring the separately licensed SDK:

```bash
GALACTITALK_TEST_STUB_TEAMTALK=1 .venv/bin/python -m unittest discover -v
```

Run a syntax check with:

```bash
.venv/bin/python -m compileall -q bot.py dependency_manager.py migrate_legacy.py teamtalk_sdk.py galactitalk tests
```

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests for observable behavior.
- Update the README, example configuration, and changelog when user-facing
  behavior changes.
- Write concise commit messages that describe the outcome.
- Confirm that CI passes before requesting review.

By contributing, you agree that your contribution is licensed under the MIT
License included with this project.
