# Examples — Custom Hermes Skills

This directory contains **reference / example implementations** of
custom Hermes skills, kept here as code samples for users who want to
write their own.

These files are **not loaded by the runtime**. To use a skill in
production:

1. Drop your own `.py` file into `data/skills/` (or whichever directory
   you have configured in `hermes.yaml`).
2. Implement a `run(args: dict) -> str` function plus the
   `SKILL_NAME` / `SKILL_DESCRIPTION` module attributes.
3. Restart the gateway so it picks up the new file.

## What's here

| File | Description |
|------|-------------|
| `note.py`   | Save / retrieve / list / delete short notes in a per-user directory. Demonstrates `Path` handling and the `args` dict pattern. |
| `weather.py`| Tiny placeholder showing a "fetch from external API" shape. Replace the placeholder with a real HTTP call (e.g. `requests.get`) when integrating a real weather service. |

## Why these aren't shipped under `hermes/data/skills/`

`hermes/data/skills/` is **per-user state**: it gets created on first
run and may accumulate user-written skills over time. To keep your
clone clean and to avoid committing user code, the example files live
in `docs/examples/skills/` and you copy them into `data/skills/`
yourself when you want to play with them.
