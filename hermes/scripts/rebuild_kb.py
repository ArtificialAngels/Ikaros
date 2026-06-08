"""Rebuild the knowledge base cleanly.

Wipes data/knowledge/index.jsonl + sources/, then re-ingests every
*.md file in data/knowledge/ with sane limits.

Usage:
    python hermes/scripts/rebuild_kb.py
    python hermes/scripts/rebuild_kb.py --max-chunks 500
"""
import argparse
import os
import sys
from pathlib import Path

HERMES_ROOT = Path(__file__).resolve().parents[2]  # hermes-agent/  (scripts/ -> hermes/ -> root)
sys.path.insert(0, str(HERMES_ROOT))
os.environ.setdefault("HERMES_DATA_DIR", str(HERMES_ROOT / "hermes" / "data"))

import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HERMES_EMBEDDER"] = "hash"  # use hash for rebuild (faster, no model dep)

from hermes.knowledge import KnowledgeBase
from hermes.config import load_config
from hermes.memory import build_embedder_from_config

cfg = load_config(str(HERMES_ROOT / "config" / "hermes.yaml"))
data_paths_cfg = cfg.knowledge
# KnowledgeBase takes a path, not a config
KB_PATH = HERMES_ROOT / "hermes" / "data" / "knowledge"
embedder = build_embedder_from_config(cfg, prefer=os.environ.get("HERMES_EMBEDDER", "hash"))
kb = KnowledgeBase(
    path=KB_PATH,
    embedder=embedder,
    chunk_size=data_paths_cfg.chunk_size,
    chunk_overlap=data_paths_cfg.chunk_overlap,
    max_results=data_paths_cfg.max_results,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-chunks", type=int, default=1000,
                    help="Hard cap per document (default 1000)")
    ap.add_argument("--no-wipe", action="store_true",
                    help="Don't wipe existing index, just re-ingest (additive)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    kb_dir = HERMES_ROOT / "hermes" / "data" / "knowledge"
    index_path = kb_dir / "index.jsonl"
    sources_path = kb_dir / "sources"

    if not args.no_wipe:
        print(f"[rebuild] wiping {index_path}")
        if index_path.exists():
            index_path.unlink()
        if sources_path.exists():
            for f in sources_path.iterdir():
                if f.is_file():
                    f.unlink()
        kb.chunks.clear()  # in-memory state

    md_files = sorted(kb_dir.glob("*.md"))
    print(f"[rebuild] found {len(md_files)} markdown files in {kb_dir}")
    if not md_files:
        print("  (none)")
        return

    total_chunks = 0
    for md in md_files:
        if md.name == "index.jsonl" or md.name.startswith("."):
            continue
        text = md.read_text(encoding="utf-8")
        # Estimate chunks without actually ingesting
        est = max(1, len(text) // data_paths_cfg.chunk_size)
        if est > args.max_chunks:
            print(f"  [SKIP] {md.name} would create ~{est} chunks (>{args.max_chunks})")
            print(f"         chunk_size={data_paths_cfg.chunk_size} is too small for this doc")
            print(f"         increase chunk_size in config, or split the doc")
            continue
        before = len(kb.chunks)
        kb.ingest(md, tag="hermes-docs")
        after = len(kb.chunks)
        added = after - before
        total_chunks += added
        print(f"  [OK] {md.name:30s} -> {added:>5} chunks  (total={after})")

    # Save
    if hasattr(kb, "_save_index"):
        kb._save_index()
    print()
    print(f"[rebuild] done. total {len(kb.chunks)} chunks from {len(md_files)} files")
    print(f"          stats={kb.stats()}")


if __name__ == "__main__":
    main()
