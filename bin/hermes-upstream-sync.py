#!/usr/bin/env python3
"""
hermes-upstream-sync.py — selectively pull upgrades from upstream sources.

Why this exists
===============
`hermes-agent/` in this repo is a stale read-only snapshot. We don't track
upstream changes via git. But we DO want to occasionally harvest selected
upgrades (security fixes, new providers, bug fixes) into our own code.

This script makes that workflow:
  1. PULL    — clone or fetch upstream into upstream/<name>/
  2. DIFF    — show what changed upstream vs our integration point
  3. PICK    — copy a specific file/dir from upstream to our tree (after review)
  4. REPORT  — generate a Markdown candidate-upgrade report

We NEVER auto-overwrite. We never blanket `cp -r`. Every pick is one file/dir
at a time, after the user sees the diff.

Usage
=====
    python bin/hermes-upstream-sync.py pull              # clone all configured upstreams
    python bin/hermes-upstream-sync.py pull hermes-agent # just one
    python bin/hermes-upstream-sync.py status            # what's already cloned?
    python bin/hermes-upstream-sync.py diff hermes-agent  # show upstream HEAD vs our pinned tag
    python bin/hermes-upstream-sync.py candidates        # list upgrade candidates
    python bin/hermes-upstream-sync.py pick hermes-agent agent/foo.py modules/foo/
    python bin/hermes-upstream-sync.py report            # write docs/upstream-candidates.md
    python bin/hermes-upstream-sync.py tag-check         # confirm we're on the expected pinned version

Configuration: see UPSTREAM section below.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ============================================================================
# UPSTREAM configuration
# ============================================================================
# Each upstream has:
#   url:        git clone URL
#   dir:        where we clone it (under upstream/)
#   our_pinned: the tag/commit we currently integrate with (matches the
#               `hermes-agent/` snapshot we vendored)
#   our_integration: where in OUR tree the upstream code lives
#       (None means we don't directly use the upstream — pure reference)
UPSTREAMS = {
    "hermes-agent": {
        "url": "https://github.com/NousResearch/hermes-agent.git",
        "dir": "upstream/hermes-agent",
        # v0.16.0 is the actual project pin per hermes-agent/pyproject.toml,
        # but NousResearch publishes releases as date-stamped tags — v0.16.0
        # is the commit 3c231eb3 which is tagged as v2026.6.5. We pin by
        # the date-tag (the *visible* tag) and trust the v0.16.0 commit message
        # to confirm version correspondence.
        "our_pinned": "v2026.6.5",
        "our_pin_commit": "3c231eb3",       # the actual v0.16.0 release commit
        "our_pinned_version": "0.16.0",    # version string from pyproject.toml
        "our_integration": "hermes-agent/",
        "our_primary_dev": "modules/,bridge/,hermes/",
    },
    "hermes-web-ui": {
        "url": "https://github.com/EKKOLearnAI/hermes-web-ui.git",
        "dir": "upstream/hermes-web-ui",
        "our_pinned": "0.6.14",
        "our_integration": "runtime/node23/node_modules/hermes-web-ui/",
        "our_primary_dev": "modules/webui_proxy/,modules/webui/",
    },
    "llama.cpp": {
        "url": "https://github.com/ggml-org/llama.cpp.git",
        "dir": "upstream/llama.cpp",
        "our_pinned": "b9503",              # current vendored build per README
        "our_integration": "runtime/",
        "our_primary_dev": "modules/llm_engine/",
    },
}


# ============================================================================
# Commands
# ============================================================================

def cmd_pull(name: str | None) -> int:
    targets = [name] if name else list(UPSTREAMS)
    for n in targets:
        cfg = UPSTREAMS.get(n)
        if not cfg:
            print(f"ERROR: unknown upstream '{n}'", file=sys.stderr)
            return 1
        target = Path(cfg["dir"])
        if target.exists() and (target / ".git").is_dir():
            print(f"[pull] {n}: already cloned at {target}, fetching...")
            r = subprocess.run(["git", "-C", str(target), "fetch", "--tags"],
                               check=False)
            if r.returncode != 0:
                print(f"  fetch failed (rc={r.returncode}); try `rm -rf {target}` and re-pull")
                continue
        else:
            print(f"[pull] {n}: cloning {cfg['url']} -> {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", "--depth", "50",
                                cfg["url"], str(target)], check=False)
            if r.returncode != 0:
                print(f"  clone failed (rc={r.returncode})")
                continue
    return 0


def cmd_status() -> int:
    for n, cfg in UPSTREAMS.items():
        target = Path(cfg["dir"])
        if not target.exists():
            print(f"  [{n}] NOT CLONED — run `pull {n}`")
            continue
        if not (target / ".git").is_dir():
            print(f"  [{n}] exists but is not a git repo (stale?) — run `pull {n}`")
            continue
        r = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=False)
        head = r.stdout.strip() if r.returncode == 0 else "?"
        r = subprocess.run(["git", "-C", str(target), "rev-parse",
                            "--short", "v0.16.0"],  # TODO: use cfg["our_pinned"]
                           capture_output=True, text=True, check=False)
        pinned_short = r.stdout.strip() if r.returncode == 0 else "?"
        print(f"  [{n}] HEAD={head[:10]} pinned={pinned_short}")
        print(f"         {cfg['our_pinned']} -> {cfg['our_integration']}")
    return 0


def cmd_diff(name: str) -> int:
    cfg = UPSTREAMS.get(name)
    if not cfg:
        print(f"ERROR: unknown upstream '{name}'", file=sys.stderr)
        return 1
    src = Path(cfg["dir"])
    if not src.exists():
        print(f"ERROR: {src} not cloned — run `pull {name}` first", file=sys.stderr)
        return 1

    pinned = cfg["our_pinned"]
    print(f"[diff] {name}: upstream HEAD vs pinned {pinned}")
    r = subprocess.run(
        ["git", "-C", str(src), "log", "--oneline",
         f"{pinned}..HEAD"],
        capture_output=True, text=True, check=False,
    )
    print(r.stdout or "  (no new commits since pinned)")
    if r.returncode != 0:
        print(f"  (rc={r.returncode}; tag may not exist locally — try `pull {name}`)")
    return 0


def cmd_candidates() -> int:
    """List files that have changed upstream since our pin."""
    print("Not implemented yet — see `diff <name>` for commit-level view")
    return 0


def cmd_pick(upstream_name: str, src_rel: str, dst_rel: str) -> int:
    """
    Copy one file/dir from upstream/<upstream>/<src_rel> to <dst_rel> in our tree.

    Refuses if dst exists (unless --force). Refuses if src doesn't exist.
    """
    cfg = UPSTREAMS.get(upstream_name)
    if not cfg:
        print(f"ERROR: unknown upstream '{upstream_name}'", file=sys.stderr)
        return 1
    src = Path(cfg["dir"]) / src_rel
    dst = Path(dst_rel)
    if not src.exists():
        print(f"ERROR: source not found: {src}", file=sys.stderr)
        return 1
    if dst.exists():
        print(f"ERROR: destination already exists: {dst}", file=sys.stderr)
        print(f"  to overwrite, remove first or use --force", file=sys.stderr)
        return 1
    print(f"[pick] {src} -> {dst}")
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    print(f"  OK. commit when ready; remember to test.")
    return 0


def cmd_report() -> int:
    out = Path("docs/upstream-candidates.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 上游升级候选报告",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report lists what has changed in our upstream sources since we",
        "pinned them. Each candidate needs manual review before picking.",
        "",
    ]
    for n, cfg in UPSTREAMS.items():
        src = Path(cfg["dir"])
        pinned = cfg["our_pinned"]
        lines.append(f"## {n}")
        lines.append("")
        lines.append(f"- URL: {cfg['url']}")
        lines.append(f"- Pinned: {pinned}")
        lines.append(f"- Our integration point: `{cfg['our_integration']}`")
        lines.append(f"- Where our dev lives: `{cfg['our_primary_dev']}`")
        lines.append("")
        if not src.exists():
            lines.append("> Not cloned. Run `python bin/hermes-upstream-sync.py pull "
                         f"{n}`.")
            lines.append("")
            continue
        r = subprocess.run(
            ["git", "-C", str(src), "log", "--oneline", f"{pinned}..HEAD"],
            capture_output=True, text=True, check=False,
        )
        commits = r.stdout.strip() if r.returncode == 0 else ""
        if not commits:
            lines.append("No new commits since pinned.")
        else:
            lines.append(f"### Commits since {pinned}")
            lines.append("")
            lines.append("```")
            lines.append(commits)
            lines.append("```")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] wrote {out}")
    return 0


def cmd_tag_check() -> int:
    for n, cfg in UPSTREAMS.items():
        pinned = cfg["our_pinned"]
        src = Path(cfg["dir"])
        if not src.exists():
            print(f"  [{n}] not cloned; skipping")
            continue
        r = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "--verify", pinned],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            print(f"  [{n}] pinned {pinned} -> {r.stdout.strip()[:10]} OK")
        else:
            print(f"  [{n}] pinned {pinned} NOT FOUND locally — run `pull {n}`")
    return 0


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage upstream upgrades for our Hermes Agent fork."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pull", help="clone or fetch upstream(s)")
    p.add_argument("name", nargs="?", help="upstream name (default: all)")
    p.set_defaults(func=lambda a: cmd_pull(a.name))

    sub.add_parser("status", help="show upstream clone status").set_defaults(
        func=lambda a: cmd_status())

    p = sub.add_parser("diff", help="show commits upstream since our pin")
    p.add_argument("name")
    p.set_defaults(func=lambda a: cmd_diff(a.name))

    sub.add_parser("candidates", help="list upgrade candidate files").set_defaults(
        func=lambda a: cmd_candidates())

    p = sub.add_parser("pick", help="copy one file/dir from upstream to our tree")
    p.add_argument("upstream")
    p.add_argument("src_rel", help="path relative to upstream/<name>/")
    p.add_argument("dst_rel", help="destination path in our tree")
    p.set_defaults(func=lambda a: cmd_pick(a.upstream, a.src_rel, a.dst_rel))

    sub.add_parser("report", help="write docs/upstream-candidates.md").set_defaults(
        func=lambda a: cmd_report())

    sub.add_parser("tag-check", help="verify pinned tags are present").set_defaults(
        func=lambda a: cmd_tag_check())

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
