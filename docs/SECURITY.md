# Security Policy — Ikaros

This document covers secret handling and the lightweight secret-scanning
scaffold. It is **additive scaffolding only** and does not modify running
service code.

## 1. Secrets must come from the environment

All credentials — API keys, tokens, passwords, PATs — MUST be supplied via
environment variables or a local, **gitignored** `.env` file. They must never
be hardcoded in source, config, or docs committed to the repository.

- Local development: copy `.env.example` → `.env` and fill in real values.
  `.env` is already covered by `.gitignore`; verify it stays ignored.
- Production / CI: inject secrets from the platform secret store (env vars).
- Never commit the real `.env`. If it was ever committed, rotate the secret
  immediately and purge history.

## 2. Weak default: `API_SERVER_KEY`

The gateway currently falls back to a hardcoded weak default:

```
API_SERVER_KEY = os.getenv("API_SERVER_KEY", "ikaros-gateway-key")
```

`"ikaros-gateway-key"` is a well-known, guessable default. It must become
**fail-closed**:

- Remove the default. On startup, if `API_SERVER_KEY` is missing or equals the
  old weak value, the service should refuse to start and log a clear error
  (exit non-zero).
- This is a planned code change; tracked here so it is not forgotten. The
  scaffolding in this repo does not implement it yet.

## 3. Pre-commit secret scanning

`bin/secret-scan.py` is a stdlib-only scanner that walks the repo and flags
likely hardcoded secrets (OpenAI `sk-...` keys, `api_key=...`, `password=...`,
`github_pat_`, AWS `AKIA...`, `token=...`). It exits 0 (non-blocking) so it is
safe to add to a pre-commit / CI gate.

### Run manually

```bash
python bin/secret-scan.py
```

Output: `LEAK: <file>:<line>: <snippet>` for each hit, or `OK: no secrets found`.

### Wire into pre-commit

Add to `.pre-commit-config.yaml` (or call it from an existing hook):

```yaml
- repo: local
  hooks:
    - id: ikaros-secret-scan
      name: Ikaros secret scan
      entry: python bin/secret-scan.py
      language: system
      pass_filenames: false
      # non-blocking: always returns 0; treat output as advisory
```

To make it **blocking**, wrap it so a non-empty `LEAK:` line fails the commit,
e.g. `python bin/secret-scan.py | findstr LEAK && exit 1 || exit 0` in a
script step. Recommended once the repo is clean.

## 4. False positives

The scanner skips lines containing placeholder markers (`<...>`, `your_`,
`example`, `placeholder`, `TODO`, `sk-${`). If a legitimate value trips the
scan, move it to `.env` or add the placeholder marker to the line's comment.
