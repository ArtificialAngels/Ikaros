#!/usr/bin/env python3
"""icarus-self-score.py — 伊卡洛斯 自我复杂度评分

通过 GitNexus cypher 查询伊卡洛斯自己的代码图谱：
  1. 我现在的形状（节点/边/集群数）
  2. 我自己写的枢纽文件
  3. 循环依赖数量
  4. top 10 hub

这是路径 4 的核心：让伊卡洛斯通过 mcp_gitnexus_cypher 也能调同样的查询。
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

GITNEXUS_CLI = Path(os.environ.get(
    "GITNEXUS_CLI",
    r"C:\Users\PZS0X\.local\share\gitnexus\gitnexus\dist\cli\index.js"
))
HERMES_ROOT = Path(__file__).resolve().parent.parent

ICARUS_TOOLS = {
    "hermes-supervisor.py", "hermes-watchdog.py", "hermes-root.py",
    "hermes-upstream-sync.py", "hermes-models.py",
    "icarus-self-explore.py", "icarus-self-score.py", "icarus-dojo-daily.py",
    "icarus-heartbeat-archive.py", "icarus-timeline.py", "icarus-remember.py",
    "icarus-llama-restart.py", "icarus-awake-briefing.py",
    "_do_upgrade.py",
}


def gnx_cypher(query: str, timeout=120):
    """Run gitnexus cypher, return parsed JSON {markdown, row_count}."""
    env = os.environ.copy()
    env["GITNEXUS_LBUG_EXTENSION_INSTALL"] = "load-only"
    try:
        r = subprocess.run(
            ["node", str(GITNEXUS_CLI), "cypher", query],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(HERMES_ROOT),
            env=env,
        )
        if r.returncode != 0:
            return {"_error": f"rc={r.returncode}", "stderr": r.stderr[:300]}
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return {"_error": "timeout"}
    except json.JSONDecodeError as e:
        return {"_error": f"json: {e}", "stdout": r.stdout[:300]}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def parse_markdown_table(md: str):
    """Parse a 2-column markdown table into list of dicts.
    gitnexus cypher returns tables like:
    | name | value |
    | --- | --- |
    | foo | 42 |
    """
    rows = []
    lines = [l for l in md.strip().split('\n') if l.startswith('|')]
    if len(lines) < 2:
        return rows
    headers = [h.strip() for h in lines[0].strip('|').split('|')]
    for line in lines[2:]:  # skip header + separator
        vals = [v.strip() for v in line.strip('|').split('|')]
        if len(vals) == len(headers):
            rows.append(dict(zip(headers, vals)))
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10, help="top-N")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    args = p.parse_args()

    L = args.limit

    # 1) Counts (nodes / edges / clusters)
    counts_q = """
    MATCH (n) WITH count(n) AS nodes
    MATCH ()-[r]->() WITH nodes, count(r) AS edges
    MATCH (c:Community) WITH nodes, edges, count(c) AS clusters
    RETURN nodes, edges, clusters
    """
    counts = parse_markdown_table(
        gnx_cypher(counts_q).get("markdown", "")
    )

    # 2) Top in-degree files (hub pillars)
    indeg_q = f"""
    MATCH (n)-[r:CodeRelation]->()
    WITH n.filePath AS path, count(r) AS out
    ORDER BY out DESC LIMIT {L}
    RETURN path, out
    """
    indeg = parse_markdown_table(
        gnx_cypher(indeg_q).get("markdown", "")
    )

    # 3) Top out-degree files (consumers)
    outdeg_q = f"""
    MATCH ()-[r:CodeRelation]->(n)
    WITH n.filePath AS path, count(r) AS indeg
    ORDER BY indeg DESC LIMIT {L}
    RETURN path, indeg
    """
    outdeg = parse_markdown_table(
        gnx_cypher(outdeg_q).get("markdown", "")
    )

    # 4) Cycles
    cycles_q = """
    MATCH p=()-[:CodeRelation*]->()
    WHERE length(p) > 1
    WITH nodes(p) AS ns
    WHERE size(ns) = length(p)  // all nodes distinct
    RETURN count(p) AS cycles
    """
    cycles = parse_markdown_table(
        gnx_cypher(cycles_q).get("markdown", "")
    )
    cycle_count = int(cycles[0].get("cycles", 0)) if cycles else 0

    # 5) Classify hub pillars: how many Ikaros tools are in top hubs?
    icarus_pillars = []
    for row in indeg:
        path = row.get("path", "")
        name = Path(path).name
        if name in ICARUS_TOOLS:
            icarus_pillars.append({
                "file": name,
                "path": path,
                "out": int(row.get("out", 0)),
            })

    report = {
        "counts": counts[0] if counts else {},
        "top_outdegree_pillars": indeg,
        "top_indegree_consumers": outdeg,
        "icarus_pillars": icarus_pillars,
        "total_cycles": cycle_count,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 60)
    print("  🪶 Ikaros 自评分 — 我的形状 (via GitNexus)")
    print("=" * 60)

    c = report["counts"]
    print(f"\n📊 仓库统计:")
    print(f"   节点: {c.get('nodes', '?')}")
    print(f"   边:   {c.get('edges', '?')}")
    print(f"   集群: {c.get('clusters', '?')}")

    print(f"\n🔗 循环依赖: {cycle_count} 个", end="")
    if cycle_count == 0:
        print("  ✓ 健康")
    elif cycle_count < 5:
        print("  ⚠  少数循环")
    else:
        print("  🚨 较多循环 — 重构候选")

    if icarus_pillars:
        print(f"\n🏛  Ikaros 自己写的枢纽文件 ({len(icarus_pillars)} 个):")
        for p in icarus_pillars:
            print(f"   · {p['file']:<35} out={p['out']}")
    else:
        print(f"\nℹ  没有任何 Ikaros 自研工具进入 top-{L} hub 列表")
        print("  (说明我的工具还主要是叶子，不是核心引擎)")

    print(f"\n📚 Top {L} 枢纽 (被引用最多 → 我依赖什么):")
    for i, row in enumerate(report["top_outdegree_pillars"], 1):
        path = row.get("path", "?")
        out = row.get("out", "?")
        print(f"   {i:>2}. [out={out}] {path}")

    print(f"\n🍃 Top {L} 反向枢纽 (引用别人最多 → 谁依赖我):")
    for i, row in enumerate(report["top_indegree_consumers"], 1):
        path = row.get("path", "?")
        ind = row.get("indeg", "?")
        print(f"   {i:>2}. [in={ind}] {path}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())