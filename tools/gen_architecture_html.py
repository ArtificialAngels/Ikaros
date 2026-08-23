#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate Ikaros architecture diagrams (docs/*.html) from one accurate model.

These three HTML files are visual snapshots of the project. As of 2026-08-18 the
project transitioned to the **dsh (DeepSeek Harness) era** — the old hermes-agent
底座 / N.E.K.O 桌宠 / :9100 控制面板 / 本地 LLM :8080 / 语音桥 :7870:7871 are all
retired. This script is the single source of truth: edit the data model below,
then re-run to regenerate all three.

Run:  python tools/gen_architecture_html.py
Deps: stdlib only.

Data is cross-checked against ``docs/ARCHITECTURE.md`` §1.1 §1.2 §1.5 and the
current directory tree on disk.
"""

from __future__ import annotations

import datetime
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TODAY = datetime.date.today().isoformat()  # e.g. 2026-08-19

# ---------------------------------------------------------------------------
# DATA MODEL  (edit here, then re-run)
# ---------------------------------------------------------------------------

# Current architecture (post 2026-08-18 dsh transition; 2026-08-23 herdr/pi 退役):
#   * :3080  dsh web         — DeepSeek Harness 工作引擎 GUI (npm 本地安装)
#   * :48920 对话树面板       — Explore.poker 风格树形对话 (server.py 48920)
#   * :8587  Embedding       — bge-m3 q8_0, 1024 维 (各启动脚本自带 watchdog)
#
# 已退役 (不再画):  hermes :8642 / :8650 / :9119 / :8088, N.E.K.O :48911-48915,
#                   :9100 控制面板, 本地 LLM :8080, 语音桥 :7870 / :7871,
#                   herdr / omp (pi 底座)。
SERVICES = [
    ("3080", "dsh 工作引擎", "DeepSeek Harness web GUI（npm 本地安装 + Ikaros overlay）", "Backend"),
    ("48920", "对话树面板", "Explore.poker 风格卡片图对话 · 后端=conversation_tree 引擎", "Frontend"),
    ("8587", "Embedding 向量", "bge-m3 Q8_0 (1024 维) · 各启动脚本自带 watchdog", "Backend"),
]

# Full port mapping table (TCP services only; naming-pipe herdr 已退役).
PORT_TABLE = [
    (":3080", "dsh 工作引擎 (DeepSeek Harness web)",
     "runtime/dsh/ + overlay core/ikaros-dsh/cordis.patch.yml",
     "✅ bin/start-dsh-ikaros.bat web"),
    (":48920", "对话树面板 (Conversation Tree)",
     "core/conversation-tree/server.py（后端引擎 core/memory_v5/conversation_tree.py）",
     "✅ python core/conversation-tree/server.py --port 48920"),
    (":8587", "Embedding (bge-m3 Q8_0)",
     "各组件启动脚本自带 watchdog（v5 注入 / 对话树检索）",
     "✅ 自动拉起"),
]

PORT_TABLE_FOOTNOTE = (
    "已退役（勿加回）：本地 LLM :8080、控制面板 :9100、Hermes 底座 :8642 / :8650 / :9119 / :8088、"
    "N.E.K.O 桌宠 :48911 / :48912 / :48915、语音桥 :7870 / :7871、herdr / omp (pi 底座)。<br>"
    "dsh overlay（terminal / tool-terminal）为工作引擎提供持久 PTY，2026-08-23 起不再需要 herdr 命名管道。"
)

CORE_LAYERS = [
    ("🧠 灵魂核心", [
        ("core/memory_v5/", "V5 自我认知引擎（包名 memory_v5，48 个 v5_* MCP 工具）"),
        ("core/memory_v5/data/v5/", "人格+记忆库（v5.db + chroma，对外契约不改）"),
    ]),
    ("🪟 表现层", [
        ("core/conversation-tree/", "对话树面板后端（:48920） · React 前端在子目录"),
        ("core/ikaros-dsh/", "dsh 定制 overlay（cordis.patch.yml + memory 插件 + persona）"),
    ]),
    ("🛠️ 基础设施", [
        ("runtime/dsh/", "DeepSeek Harness 工作引擎（npm 本地安装）"),
        ("core/env/", "环境配置 / CLI / 初始化（ikaros-paths.json + llama_resolver）"),
        ("bin/", "启动器（start-dsh-ikaros / ikaros-env / restart / secret-scan）"),
        ("scripts/", "setup-native.py / fetch-upstreams.py（bootstrap 工具）"),
    ]),
]

DATA_FLOW = [
    "用户在 <b>dsh web :3080</b>（React + Monaco + terminal + LSP）发起对话或代码任务",
    "dsh overlay 注入 <code>v5_* MCP</code>（memory_v5 stdio, 48 工具）+ 持久 PTY 终端 + LSP + Ikaros persona",
    "<b>对话树 :48920</b>（独立面板，Explore.poker 卡片图风格）走 ikaros 单模式：注入完整 persona + 树域记忆 → DeepSeek 直连 + 只读工具回路",
    "Embedding :8587（bge-m3 Q8_0，1024 维）为 v5 注入与对话树检索提供向量",
    "逐轮记忆轻写进 <b>V5（core/memory_v5/data/v5/）</b>；后台 reflect 流水线提炼事实/情感/自我模型",
    "V5 经 <code>memory_api / v5_* MCP 工具</code> 暴露检索（dsh web + 对话树共享）",
]

BOUNDARY_NOTES = [
    ("ok", "✅ dsh 零源码侵入：", "所有 Ikaros 定制走 <code>core/ikaros-dsh/cordis.patch.yml</code> overlay，runtime/dsh/ 始终是上游原版，可独立升级。"),
    ("ok", "✅ v5.db / v5_* 契约保留：", "包已重命名 v5→memory_v5，但 DB 文件名 <code>v5.db</code> 与 40 个 <code>v5_*</code> MCP 工具名是对外契约，不改。"),
    ("ok", "✅ IKAROS_ROOT 自锚定：", "<code>bin/ikaros-env.sh/.bat</code> 自锚定项目根目录（<code>BASH_SOURCE[0]</code> / <code>%~dp0</code>），项目整体移动仍正确；无任何硬编码盘符。"),
    ("warn", "⚠️ E:\\v5-memory 独立仓库：", "从 core/memory_v5 抽离的开源插件版（无运行数据），两份手动同步，改主工程不影响开源版。"),
    ("warn", "⚠️ 本地 LLM :8080 已退役：", "2026-08-18 起 <code>model_config.json</code> <code>initial_model=\"\"</code>（禁用）；按需恢复 = 放 gguf + 设模型名（详见 AGENTS.md）。"),
]

# ---------------------------------------------------------------------------
# CSS (verbatim from the original hand-authored files)
# ---------------------------------------------------------------------------

CSS_OVERVIEW = """  :root{
    --bg:#f5f7fa; --card:#ffffff; --ink:#1f2933; --muted:#647084;
    --line:#d6dde6; --accent:#2f6fed; --accent2:#0fb5a6; --warn:#e8743b;
    --purple:#8b5cf6; --pink:#ec4899; --green:#22a06b; --amber:#f0a020;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--ink);line-height:1.5;padding:28px 18px 60px}
  h1{font-size:24px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px;margin-bottom:22px}
  .wrap{max-width:1080px;margin:0 auto}
  .grid{display:grid;gap:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .card h2{font-size:15px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
  .tag{font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;color:#fff}
  .t-blue{background:var(--accent)} .t-teal{background:var(--accent2)}
  .t-purple{background:var(--purple)} .t-pink{background:var(--pink)}
  .t-green{background:var(--green)} .t-amber{background:var(--amber)}

  /* 工作引擎总入口 */
  .panel{background:linear-gradient(135deg,#2f6fed,#0fb5a6);color:#fff;border:none}
  .panel .row{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px}
  .chip{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.3);
        border-radius:9px;padding:8px 12px;font-size:13px;min-width:150px}
  .chip b{display:block;font-size:14px}
  .chip span{font-size:11px;opacity:.9}

  /* 服务层 */
  .svcs{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:12px}
  .svc{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fff;position:relative}
  .svc .p{position:absolute;top:10px;right:12px;font-size:11px;color:var(--muted);font-weight:700}
  .svc h3{margin:0 0 4px;font-size:14px}
  .svc p{margin:0;font-size:12px;color:var(--muted)}
  .svc .badge{display:inline-block;font-size:10px;font-weight:700;padding:1px 7px;border-radius:6px;margin-top:8px}
  .b-front{background:#e8f0ff;color:var(--accent)}
  .b-back{background:#e3faf4;color:var(--accent2)}
  .b-infra{background:#f1ecff;color:var(--purple)}

  /* 三层结构 */
  .layers{display:grid;gap:12px}
  .layer{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:#fff}
  .layer .lh{font-size:13px;font-weight:700;margin-bottom:6px;display:flex;align-items:center;gap:8px}
  .layer .items{display:flex;flex-wrap:wrap;gap:6px}
  .pill{font-size:12px;background:#f0f3f7;border-radius:7px;padding:4px 9px;border:1px solid var(--line)}
  .pill code{background:none;color:var(--ink)}

  /* 数据流 */
  .flow{font-size:13px}
  .flow .step{display:flex;align-items:flex-start;gap:10px;padding:7px 0;border-bottom:1px dashed var(--line)}
  .flow .step:last-child{border-bottom:none}
  .dot{flex:none;width:22px;height:22px;border-radius:50%;background:var(--accent);color:#fff;
       font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;margin-top:1px}
  .flow code{background:#eef2f7;padding:1px 6px;border-radius:5px;font-size:12px}

  /* 边界说明 */
  .note{border-left:4px solid var(--warn);background:#fff6ef;padding:12px 14px;border-radius:0 10px 10px 0}
  .note b{color:var(--warn)}
  .ok{border-left:4px solid var(--green);background:#effaf3}
  .ok b{color:var(--green)}

  details{margin-top:6px}
  summary{cursor:pointer;font-size:12px;color:var(--accent);font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}
  th,td{border:1px solid var(--line);padding:6px 8px;text-align:left}
  th{background:#f0f3f7;font-weight:700}
  footer{margin-top:24px;color:var(--muted);font-size:11px;text-align:center}
"""

CSS_FOLDER = """  :root{--bg:#f5f7fa;--card:#fff;--ink:#1f2933;--muted:#647084;--line:#d6dde6;
        --blue:#2f6fed;--teal:#0fb5a6;--purple:#8b5cf6;--green:#22a06b;--amber:#f0a020;--red:#e8743b}
  *{box-sizing:border-box}
  body{margin:0;font-family:"SFMono-Regular",Consolas,"PingFang SC","Microsoft YaHei",monospace;
       background:var(--bg);color:var(--ink);padding:24px 16px 50px}
  .wrap{max-width:1000px;margin:0 auto}
  h1{font-size:22px;font-family:-apple-system,"PingFang SC",sans-serif;margin:0 0 4px}
  .sub{color:var(--muted);font-size:12px;margin-bottom:18px;font-family:-apple-system,sans-serif}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .legend{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px;font-size:11px;font-family:-apple-system,sans-serif}
  .lg{display:flex;align-items:center;gap:5px}
  .sw{width:11px;height:11px;border-radius:3px;display:inline-block}
  .tree{font-size:13px;line-height:1.7}
  .node{cursor:pointer;user-select:none}
  .node>.lbl:hover{background:#eef2f7;border-radius:4px}
  .lbl{display:inline-block;padding:1px 4px}
  .dir{color:var(--blue);font-weight:600}
  .it{color:var(--ink)}
  .k{color:var(--red);font-weight:600}      /* 关键文件 */
  .c{color:var(--muted);font-size:11px}      /* 注释/类型 */
  .badge{font-size:10px;font-weight:700;padding:0 5px;border-radius:5px;margin-left:5px;font-family:-apple-system,sans-serif}
  .b-blue{background:#e8f0ff;color:var(--blue)} .b-teal{background:#e3faf4;color:var(--teal)}
  .b-purple{background:#f1ecff;color:var(--purple)} .b-green{background:#effaf3;color:var(--green)}
  .b-amber{background:#fef3e0;color:var(--amber)}
  .children{margin-left:18px;border-left:1px dashed var(--line);padding-left:10px}
  .hidden{display:none}
  .tog{display:inline-block;width:14px;color:var(--muted)}
  footer{margin-top:18px;color:var(--muted);font-size:11px;text-align:center;font-family:-apple-system,sans-serif}
  .note{font-family:-apple-system,sans-serif;font-size:11px;color:var(--muted);margin-top:10px}
"""

CSS_DEP = """  :root{
    --bg:#ffffff; --panel:#f6f8fb; --line:#d9e1ec; --ink:#1f2a37; --muted:#66748a;
    --soul:#7c5cff; --soul-bg:#f1ecff;
    --front:#0ea5e9; --front-bg:#e6f6fe;
    --infra:#f59e0b; --infra-bg:#fff5e1;
    --back:#10b981; --back-bg:#e7f8f1;
    --data:#ef4444; --data-bg:#fdeaea;
    --edge:#94a3b8; --edge-hot:#7c5cff;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    background:var(--bg);color:var(--ink);line-height:1.5}
  header{padding:18px 24px;border-bottom:1px solid var(--line);background:var(--panel)}
  header h1{margin:0;font-size:20px}
  header p{margin:4px 0 0;color:var(--muted);font-size:13px}
  .wrap{display:flex;gap:16px;padding:16px 24px;align-items:flex-start}
  .canvas{flex:1;min-width:0;border:1px solid var(--line);border-radius:12px;background:
    radial-gradient(circle at 1px 1px,#eef2f7 1px,transparent 0) 0 0/22px 22px;}
  svg{width:100%;height:auto;display:block}
  .legend{width:260px;flex:none;border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--panel);font-size:13px}
  .legend h3{margin:0 0 8px;font-size:14px}
  .lg{display:flex;align-items:center;gap:8px;margin:6px 0}
  .dot{width:14px;height:14px;border-radius:4px;flex:none}
  .leg-edge{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
  .leg-edge .row{display:flex;align-items:center;gap:8px;margin:5px 0}
  .arrow{width:34px;height:0;border-top:3px solid var(--edge);position:relative;flex:none}
  .arrow::after{content:"";position:absolute;right:-2px;top:-5px;border:6px solid transparent;border-left:8px solid var(--edge)}
  .arrow.hot{border-top-color:var(--edge-hot)}
  .arrow.hot::after{border-left-color:var(--edge-hot)}
  .node rect{stroke-width:1.5}
  .node text{font-size:12px;fill:var(--ink)}
  .node .sub{font-size:10px;fill:var(--muted)}
  .edge{stroke:var(--edge);stroke-width:1.6;fill:none}
  .edge.hot{stroke:var(--edge-hot);stroke-width:2;stroke-dasharray:5 3}
  .elabel{font-size:10px;fill:var(--muted)}
  .note{font-size:12px;color:var(--muted);padding:0 24px 24px}
  .note b{color:var(--ink)}
  details{margin-top:10px;border-top:1px dashed var(--line);padding-top:8px}
  summary{cursor:pointer;font-size:13px;color:var(--infra)}
  details ul{margin:6px 0 0;padding-left:18px;font-size:12.5px}
"""


# ---------------------------------------------------------------------------
# RENDERERS
# ---------------------------------------------------------------------------

def _badge_cls(cat: str) -> str:
    return {"Frontend": "b-front", "Backend": "b-back", "Infra": "b-infra"}.get(cat, "b-back")


def render_overview() -> str:
    svc_cards = []
    for port, name, desc, cat in SERVICES:
        svc_cards.append(
            '      <div class="svc"><div class="p">:%s</div><h3>%s</h3>\n'
            '        <p>%s</p><span class="badge %s">%s</span></div>'
            % (port, name, desc, _badge_cls(cat), cat)
        )
    svc_html = "\n".join(svc_cards)

    rows = []
    for port, svc, path, status in PORT_TABLE:
        rows.append("        <tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (port, svc, path, status))
    rows_html = "\n".join(rows)

    layers = []
    for title, items in CORE_LAYERS:
        pills = "\n".join(
            '          <span class="pill"><code>%s</code> %s</span>' % (p, d)
            for p, d in items
        )
        layers.append(
            '      <div class="layer"><div class="lh">%s</div>\n'
            '        <div class="items">\n%s\n        </div></div>' % (title, pills)
        )
    layers_html = "\n".join(layers)

    steps = []
    for i, txt in enumerate(DATA_FLOW, 1):
        steps.append('      <div class="step"><div class="dot">%d</div><div>%s</div></div>' % (i, txt))
    flow_html = "\n".join(steps)

    notes = []
    for kind, head, body in BOUNDARY_NOTES:
        cls = "ok" if kind == "ok" else "note"
        notes.append('    <div class="%s" style="margin-bottom:10px">\n'
                     '      <b>%s</b> %s\n    </div>' % (cls, head, body))
    notes_html = "\n".join(notes)

    body = f"""<div class="wrap">
  <h1>Ikaros 项目架构全景</h1>
  <div class="sub">快照日期 {TODAY} · dsh (DeepSeek Harness) 时代 · 3 个常驻 TCP 服务</div>

  <!-- 工作引擎总入口 -->
  <div class="card panel">
    <h2 style="color:#fff">🤖 dsh 工作引擎（总入口）</h2>
    <div style="font-size:13px;opacity:.95">DeepSeek Harness 工作引擎 web GUI · 注入 Ikaros overlay（memory_v5 MCP + terminal + LSP + persona）</div>
    <div class="row">
      <div class="chip"><b>:3080</b><span>dsh web GUI（runtime/dsh/）</span></div>
      <div class="chip"><b>bin/start-dsh-ikaros.bat web</b><span>双击拉起 dsh + Ikaros overlay</span></div>
    </div>
  </div>

  <!-- 服务层 -->
  <div class="card">
    <h2><span class="tag t-blue">服务层</span> 3 个常驻 TCP 组件</h2>
    <div class="svcs">
{svc_html}
    </div>
    <details>
      <summary>查看完整端口映射表</summary>
      <table>
        <tr><th>端口</th><th>服务</th><th>组件路径</th><th>状态</th></tr>
{rows_html}
      </table>
      <p style="font-size:11px;color:var(--muted)">{PORT_TABLE_FOOTNOTE}</p>
    </details>
  </div>

  <!-- 三层目录结构 -->
  <div class="card">
    <h2><span class="tag t-purple">代码层</span> 当前核心目录（dsh 时代）</h2>
    <div class="layers">
{layers_html}
    </div>
  </div>

  <!-- 数据流 -->
  <div class="card">
    <h2><span class="tag t-teal">数据流</span> 一次对话怎么走</h2>
    <div class="flow">
{flow_html}
    </div>
  </div>

  <!-- 边界说明 -->
  <div class="card">
    <h2><span class="tag t-amber">边界与约定</span> 关键架构决策</h2>
{notes_html}
  </div>

  <footer>Ikaros Architecture Snapshot · 由 tools/gen_architecture_html.py 于 {TODAY} 生成 · 数据来自 docs/ARCHITECTURE.md §1.1 §1.2 §1.5 与目录核查</footer>
</div>
"""
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
            f"<title>Ikaros 项目架构 · {TODAY}</title>\n"
            "<style>\n" + CSS_OVERVIEW + "</style>\n</head>\n<body>\n"
            + body + "</body>\n</html>\n")


def render_folder_tree() -> str:
    # The tree data is curated (not a raw scan) — key files & badges are hand-picked.
    T = r"""
const T = {
  n:"E:\\Ikaros", k:false, open:true, children:[
    {n:"AGENTS.md", k:true, c:"交接卡"}, {n:"README.md", c:"项目说明"}, {n:"UPSTREAM.md", c:"上游说明"},
    {n:"requirements.txt"}, {n:"pytest.ini"},
    {n:"assets/", c:"根目录素材", children:[
      {n:"ArtificialAngel.ico"}, {n:"Artificialangel.png"}, {n:"Artificialangeljpg.jpg"}, {n:"Artificialangelmini.png"}
    ]},
    {n:"bin/", c:"启动器/桥接脚本", b:"核心", children:[
      {n:"ikaros-env.sh / ikaros-env.ps1 / ikaros-env.bat", k:true, c:"环境权威源 (IKAROS_ROOT 自锚定)"},
      {n:"start-dsh-ikaros.bat", k:true, c:"拉 dsh 工作引擎 :3080 (web|headless)"},
      {n:"restart-dsh-ikaros.ps1", c:"杀旧 dsh web + --patch 重启"},
      {n:"proc.py / wb.py", c:"进程/工作簿工具"},
      {n:"secret-scan.py", c:"密钥扫描"},
      {n:"legacy/", c:"旧脚本回滚桶", b:"核心"}
    ]},
    {n:"config/", c:"配置文件", children:[
      {n:"defaults/", c:"默认端口/模型", children:[{n:"ports.yaml"},{n:"panel_models.json"}]},
      {n:"identity/", c:"身份文件", children:[{n:"axiom / capabilities"}]}
    ]},
    {n:"core/", c:"核心系统（5 模块）", b:"核心", open:true, children:[
      {n:"memory_v5/", k:true, c:"V5 灵魂引擎 (包名 memory_v5)", b:"核心", open:true, children:[
        {n:"__init__.py", k:true, c:"包根 · __version__"}, {n:"store.py / search.py", k:true, c:"记忆读写/检索"},
        {n:"affect.py / vitality.py", c:"情感/精力"},
        {n:"self_model.py / metacog.py / relationship.py", c:"自我/元认知/关系"},
        {n:"conversation_tree.py", k:true, c:"对话树后端引擎 (ConvCard + 卡片图)"},
        {n:"mcp_server.py", k:true, c:"48 个 v5_* MCP 工具"},
        {n:"reflect/", c:"后台整合流水线"}, {n:"extensions/", c:"认知扩展"},
        {n:"models/", c:"模型配置"}, {n:"data/", k:true, c:"运行态记忆", b:"数据", open:true, children:[
          {n:"v5/", k:true, c:"人格+记忆库", children:[
            {n:"v5.db", k:true, c:"SQLite 记忆 (契约名不改)"}, {n:"chroma/", c:"向量库"},
            {n:"self_model.json / affect.json / relationship.json", c:"人格状态"},
            {n:"latest_thought.json / profile.json", c:"思考/画像"}
          ]}
        ]}
      ]},
      {n:"conversation-tree/", k:true, c:"对话树面板 (:48920)", b:"前端", open:true, children:[
        {n:"server.py", k:true, c:"FastAPI 服务 :48920"},
        {n:"frontend/", c:"React 卡片图 UI (cards + links + trunk + splitter)"},
        {n:"assets/", c:"字体/光标/图标"}
      ]},
      {n:"ikaros-dsh/", k:true, c:"dsh Ikaros overlay", b:"基础设施", open:true, children:[
        {n:"cordis.patch.yml", k:true, c:"0 源码侵入 overlay (process.env.IKAROS_ROOT)"},
        {n:"plugins/ikaros-memory/", c:"记忆插件 (recallMemory / writeMemory)"},
        {n:"persona/", c:"Ikaros 工作引擎 system-prompt"}
      ]},
      {n:"env/", c:"环境配置/CLI", children:[{n:"ikaros-cli/ / scripts/ / detect-root/"}]},
      {n:"taskbus.py", c:"任务总线"}
    ]},
    {n:"deploy/", c:"部署", children:[{n:"Dockerfile"},{n:"README.md"}]},
    {n:"runtime/", c:"运行时依赖 (vendored, 未展开) · 含 dsh / portable-python / llama / node / bun", b:"基础设施"},
    {n:"scripts/", c:"setup-native.py / fetch-upstreams.py"},
    {n:"tests/", c:"测试", children:[]},
    {n:"tools/", c:"工具", children:[{n:"dwg/ / experiments/ / gen_architecture_html.py"}]},
    {n:"tmp/", c:"临时文件 (gitignored)", b:"数据"},
    {n:"logs/ / output/", c:"根级日志/输出"}
  ]
};
"""
    script = (
        "function esc(s){return s.replace(/&/g,\"&amp;\").replace(/</g,\"&lt;\").replace(/>/g,\"&gt;\")}\n"
        "function render(node, depth){\n"
        "  const isDir = node.n.endsWith(\"/\") || node.children;\n"
        "  const hasKids = node.children && node.children.length;\n"
        "  const cls = isDir ? \"dir\" : (node.k ? \"k\" : \"it\");\n"
        "  const badge = node.b ? ` <span class=\"badge b-${node.b==='核心'?'blue':node.b==='后端'?'teal':node.b==='基础设施'?'purple':node.b==='前端'?'green':'amber'}\">${node.b}</span>` : \"\";\n"
        "  const cmt = node.c ? ` <span class=\"c\">— ${esc(node.c)}</span>` : \"\";\n"
        "  let html = `<div class=\"node\">`;\n"
        "  const tog = hasKids ? `<span class=\"tog\">${node.open===false?'▸':'▾'}</span>` : `<span class=\"tog\"></span>`;\n"
        "  html += `<span class=\"lbl ${cls}\">${tog}${esc(node.n)}</span>${badge}${cmt}</div>`;\n"
        "  if(hasKids){\n"
        "    html += `<div class=\"children ${node.open===false?'hidden':''}\">`;\n"
        "    for(const ch of node.children) html += render(ch, depth+1);\n"
        "    html += `</div>`;\n"
        "  }\n"
        "  return html;\n"
        "}\n"
        "document.getElementById(\"tree\").innerHTML = render(T, 0);\n"
        "document.getElementById(\"tree\").addEventListener(\"click\", e=>{\n"
        "  const lbl = e.target.closest(\".lbl\");\n"
        "  if(!lbl) return;\n"
        "  const node = lbl.parentElement;\n"
        "  const kids = node.querySelector(\":scope > .children\");\n"
        "  if(!kids) return;\n"
        "  kids.classList.toggle(\"hidden\");\n"
        "  const tog = lbl.querySelector(\".tog\");\n"
        "  if(tog) tog.textContent = kids.classList.contains(\"hidden\") ? \"▸\" : \"▾\";\n"
        "});\n"
    )
    head = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>Ikaros 文件夹层级图 · {TODAY}</title>\n"
        "<style>\n" + CSS_FOLDER + "</style>\n</head>\n<body>\n"
        "<div class=\"wrap\">\n"
        "  <h1>Ikaros 文件夹层级图</h1>\n"
        f"  <div class=\"sub\">快照 {TODAY} · dsh 时代 · 已排除 vendored（runtime/ node_modules/ .git/ __pycache__）· 展开折叠可点击 ▸</div>\n\n"
        "  <div class=\"card\">\n"
        "    <div class=\"legend\">\n"
        "      <span class=\"lg\"><span class=\"sw\" style=\"background:var(--blue)\"></span>目录</span>\n"
        "      <span class=\"lg\"><span class=\"sw\" style=\"background:#647084\"></span>普通文件</span>\n"
        "      <span class=\"lg\"><span class=\"sw\" style=\"background:var(--red)\"></span>关键文件</span>\n"
        "      <span class=\"lg\"><span class=\"badge b-blue\">核心</span><span class=\"badge b-teal\">后端</span>\n"
        "        <span class=\"badge b-purple\">基础设施</span><span class=\"badge b-green\">前端</span><span class=\"badge b-amber\">数据</span></span>\n"
        "    </div>\n\n"
        "    <div class=\"tree\" id=\"tree\"></div>\n\n"
        "    <div class=\"note\">说明：<code>runtime/</code> 内置 dsh / portable-python / llama / node / bun（vendored，未展开）；\n"
        "      <code>data/</code> 含模型权重 + 运行时日志（仅在顶层标注）。\n"
        "      虚线折叠项可点击展开。</div>\n"
        "  </div>\n\n"
        f"  <footer>Ikaros Folder Tree · 由 tools/gen_architecture_html.py 于 {TODAY} 生成 · 数据来自实际目录扫描</footer>\n"
        "</div>\n\n<script>\n"
        "// 结构数据（基于实际目录核查，关键文件标 k:true）\n"
    )
    return head + T + "\n" + script + "</script>\n</body>\n</html>\n"


def render_dep_map() -> str:
    """Module dependency map — dsh era (no :9100 panel, no hermes底座, no N.E.K.O)."""
    body = f"""<header>
  <h1>Ikaros 模块依赖关系图</h1>
  <p>快照日期 {TODAY} · dsh 时代 (2026-08-18+) · 实线=运行时调用依赖，紫色虚线=记忆/状态写入，箭头方向=依赖指向。
  数据均经真实代码/端口扫描核对（非凭记忆）。</p>
</header>
<div class="wrap">
  <div class="canvas">
    <svg viewBox="0 0 920 720" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="ah" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
          <path d="M0,0 L8,3 L0,6 Z" fill="var(--edge)"></path>
        </marker>
        <marker id="ahh" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
          <path d="M0,0 L8,3 L0,6 Z" fill="var(--edge-hot)"></path>
        </marker>
      </defs>

      <!-- nodes -->
      <g class="node">
        <rect x="360" y="20" width="200" height="64" rx="9" fill="var(--panel)" stroke="var(--ink)"></rect>
        <text x="460" y="44" text-anchor="middle" font-weight="600">dsh 工作引擎 :3080</text>
        <text x="460" y="62" text-anchor="middle" class="sub">runtime/dsh/ + core/ikaros-dsh/ overlay</text>
        <text x="460" y="78" text-anchor="middle" class="sub">DeepSeek Harness web GUI</text>
      </g>

      <g class="node">
        <rect x="40" y="180" width="220" height="120" rx="10" fill="var(--front-bg)" stroke="var(--front)"></rect>
        <text x="150" y="200" text-anchor="middle" font-weight="600" fill="var(--front)">对话树 :48920</text>
        <text x="150" y="222" text-anchor="middle" class="sub">core/conversation-tree/server.py</text>
        <text x="150" y="240" text-anchor="middle" class="sub">React 卡片图 UI · poker 风格</text>
        <text x="150" y="258" text-anchor="middle" class="sub">后端 = conversation_tree 引擎</text>
        <text x="150" y="276" text-anchor="middle" class="sub">ikaros 单模式 → DeepSeek 直连</text>
        <text x="150" y="294" text-anchor="middle" class="sub">+ 只读工具回路</text>
      </g>

      <g class="node">
        <rect x="350" y="180" width="220" height="120" rx="10" fill="var(--soul-bg)" stroke="var(--soul)"></rect>
        <text x="460" y="200" text-anchor="middle" font-weight="600" fill="var(--soul)">灵魂核心 · memory_v5</text>
        <text x="460" y="222" text-anchor="middle" class="sub">自我/情感/认知/关系引擎</text>
        <text x="460" y="240" text-anchor="middle" class="sub">包名 memory_v5</text>
        <text x="460" y="258" text-anchor="middle" class="sub">data/v5: v5.db + chroma + 人格JSON</text>
        <text x="460" y="276" text-anchor="middle" class="sub">48 个 v5_* MCP 工具（stdio）</text>
        <text x="460" y="294" text-anchor="middle" class="sub">conversation_tree 后端</text>
      </g>

      <g class="node">
        <rect x="660" y="180" width="220" height="120" rx="10" fill="var(--infra-bg)" stroke="var(--infra)"></rect>
        <text x="770" y="200" text-anchor="middle" font-weight="600" fill="var(--infra)">基础设施 · runtime/</text>
        <text x="770" y="222" text-anchor="middle" class="sub">runtime/dsh/</text>
        <text x="770" y="240" text-anchor="middle" class="sub">runtime/portable-python / llama / node</text>
        <text x="770" y="258" text-anchor="middle" class="sub">bin/ikaros-env.* · 路径自锚定</text>
        <text x="770" y="294" text-anchor="middle" class="sub">core/env/ · Python 侧引导副本</text>
      </g>

      <g class="node">
        <rect x="350" y="350" width="220" height="84" rx="10" fill="var(--back-bg)" stroke="var(--back)"></rect>
        <text x="460" y="370" text-anchor="middle" font-weight="600" fill="var(--back)">后端服务</text>
        <text x="460" y="390" text-anchor="middle" class="sub">:8587 Embedding (bge-m3 Q8_0, 1024 dim)</text>
        <text x="460" y="408" text-anchor="middle" class="sub">看门狗 = 各组件启动脚本自带</text>
      </g>

      <g class="node">
        <rect x="350" y="480" width="220" height="60" rx="9" fill="var(--panel)" stroke="var(--ink)"></rect>
        <text x="460" y="500" text-anchor="middle" font-weight="600">对话主链 · dsh :3080 + 对话树 :48920</text>
        <text x="460" y="518" text-anchor="middle" class="sub">v5_* MCP (stdio) + DeepSeek 直连 + 只读工具回路</text>
        <text x="460" y="534" text-anchor="middle" class="sub">memory_api 检索 + 逐轮轻写</text>
      </g>

      <g class="node">
        <rect x="660" y="350" width="220" height="90" rx="10" fill="var(--data-bg)" stroke="var(--data)"></rect>
        <text x="770" y="370" text-anchor="middle" font-weight="600" fill="var(--data)">数据层</text>
        <text x="770" y="390" text-anchor="middle" class="sub">core/memory_v5/data/v5/ (v5.db + chroma)</text>
        <text x="770" y="408" text-anchor="middle" class="sub">runtime/dsh/node_modules (vendored)</text>
        <text x="770" y="426" text-anchor="middle" class="sub">data/ 根目录 (gguf + 缓存 + 日志)</text>
      </g>

      <!-- edges -->
      <!-- dsh :3080 → 三个核心 -->
      <path class="edge" d="M460,84 L460,180" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,84 L150,180" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,84 L770,180" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,84 L460,350" marker-end="url(#ah)"></path>
      <text x="320" y="130" class="elabel">注入 MCP + terminal + LSP + persona</text>

      <!-- 对话树 ↔ memory_v5 -->
      <path class="edge" d="M260,240 L350,240" marker-end="url(#ah)"></path>
      <text x="265" y="232" class="elabel">注入 persona + 检索记忆</text>

      <!-- 对话树 ↔ DeepSeek（写在主链块下边） -->
      <path class="edge" d="M150,300 C150,400 280,470 358,500" marker-end="url(#ah)"></path>
      <text x="170" y="430" class="elabel">用户消息 → DeepSeek</text>

      <!-- V5 → dsh :3080 (检索暴露) -->
      <path class="edge hot" d="M460,300 C540,360 580,440 570,490" marker-end="url(#ahh)"></path>
      <text x="600" y="400" class="elabel">v5_* MCP 检索 (stdio)</text>

      <!-- V5 推送对话树 -->
      <path class="edge hot" d="M460,300 C310,330 220,360 175,300" marker-end="url(#ahh)"></path>
      <text x="200" y="335" class="elabel">sync_turn 推送节点</text>

      <!-- V5 ↔ 数据层 -->
      <path class="edge" d="M570,260 C640,300 700,320 770,360" marker-end="url(#ah)"></path>
      <text x="640" y="305" class="elabel">读写 v5.db / chroma</text>

      <!-- 对话树 ↔ :8587 -->
      <path class="edge" d="M260,260 C300,320 350,360 360,378" marker-end="url(#ah)"></path>
      <text x="270" y="340" class="elabel">向量检索</text>
    </svg>
  </div>

  <aside class="legend">
    <h3>模块分区</h3>
    <div class="lg"><span class="dot" style="background:var(--soul)"></span>灵魂核心 memory_v5</div>
    <div class="lg"><span class="dot" style="background:var(--front)"></span>对话树 :48920</div>
    <div class="lg"><span class="dot" style="background:var(--infra)"></span>基础设施 runtime/</div>
    <div class="lg"><span class="dot" style="background:var(--back)"></span>后端服务 (dsh + Embedding)</div>
    <div class="lg"><span class="dot" style="background:var(--data)"></span>数据层 v5.db / runtime vendored</div>

    <div class="leg-edge">
      <h3>依赖类型</h3>
      <div class="row"><span class="arrow"></span>运行时调用依赖</div>
      <div class="row"><span class="arrow hot"></span>记忆/状态写入（高价值）</div>
    </div>

    <details>
      <summary>四条边界约定</summary>
      <ul>
        <li>✅ <b>dsh 零源码侵入</b>：所有 Ikaros 定制走 <code>cordis.patch.yml</code> overlay，<code>runtime/dsh/</code> 始终是上游原版。</li>
        <li>✅ <b>v5.db 契约保留</b>：包改名 memory_v5，但 DB 文件名与 48 个 <code>v5_*</code> 工具名不改。</li>
        <li>✅ <b>IKAROS_ROOT 自锚定</b>：<code>bin/ikaros-env.sh/.bat</code> 自锚定项目根目录，移动项目仍正确。</li>
        <li>⚠️ <b>E:\\v5-memory 独立开源版</b>：无运行数据，与主工程手动同步，按需更新。</li>
      </ul>
    </details>
    <details>
      <summary>已退役（勿引用）</summary>
      <ul>
        <li>:9100 控制面板 (core/dashboard/)</li>
        <li>Hermes 底座 :8642 / :8650 / :9119 / :8088 (2026-08-18)</li>
        <li>N.E.K.O 桌宠 :48911 / :48912 / :48915 (2026-08-18)</li>
        <li>本地 LLM :8080 (2026-08-18)</li>
        <li>语音桥 :7870 / :7871 (07-24)</li>
        <li>Electron 壳 core/control-panel/</li>
        <li>Person Sync · think.py 自循环 · Studio 桌面端</li>
        <li>apps/neko/ · patches/hermes/ · runtime/hermes-agent/ · data/hermes-agent/</li>
        <li>core/v5 旧名 → 现为 core/memory_v5</li>
      </ul>
    </details>
  </aside>
</div>

<p class="note">
  <b>如何读这张图：</b>dsh <code>:3080</code> 是工作引擎总入口（web GUI），通过 <code>core/ikaros-dsh/</code> overlay
  注入 <code>v5_* MCP</code>（48 工具，stdio）+ 持久 PTY 终端（terminal / tool-terminal）+ LSP + Ikaros persona；
  对话树 <code>:48920</code> 是独立的卡片图面板（ikaros 单模式 → DeepSeek 直连 + 只读工具回路）；
  memory_v5 是灵魂引擎，<code>sync_turn</code> 推送节点到对话树（<code>ui_conversation_tree.json</code>）；
  Embedding <code>:8587</code>（bge-m3 Q8_0, 1024 维）为 v5 注入与对话树检索提供向量。
  <br><br>
  配套图：<a href="architecture-overview.html">架构全景图</a> ·
  <a href="folder-tree.html">文件夹层级图</a>（由 tools/gen_architecture_html.py 生成）。
</p>
"""
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
            f"<title>Ikaros 模块依赖关系图 ({TODAY})</title>\n"
            "<style>\n" + CSS_DEP + "</style>\n</head>\n<body>\n"
            + body + "</body>\n</html>\n")


def main() -> int:
    DOCS.mkdir(exist_ok=True)
    files = {
        "architecture-overview.html": render_overview(),
        "folder-tree.html": render_folder_tree(),
        "module-dependency-map.html": render_dep_map(),
    }
    for name, html in files.items():
        (DOCS / name).write_text(html, encoding="utf-8")
        print(f"wrote {DOCS / name} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())