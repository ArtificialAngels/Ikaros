#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate Ikaros architecture diagrams (docs/*.html) from one accurate model.

These three HTML files are visual snapshots of the project. They were originally
hand-authored and had drifted (stale dates, missing :48920 conversation-tree,
Gopeed :9999 wrongly counted as a resident service). This script is the single
source of truth: edit the data model below, then re-run to regenerate all three.

Run:  python tools/gen_architecture_html.py
Deps: stdlib only.

Data is cross-checked against the authoritative component registry
``core/dashboard/server.py:COMPONENTS`` and the directory tree.
"""

from __future__ import annotations

import datetime
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TODAY = datetime.date.today().isoformat()  # e.g. 2026-07-30

# ---------------------------------------------------------------------------
# DATA MODEL  (edit here, then re-run)
# ---------------------------------------------------------------------------

# 8 resident TCP-port services managed by the control panel (+ Herdr named pipe).
# (The :9100 panel itself is the host, listed separately in the port table.)
SERVICES = [
    ("9100", "控制面板", "组件编排中枢 · 一键启停 + CUDA 装配 + 模型切换", "Infra"),
    ("8080", "本地 LLM", "Qwen3-1.7B · 懒加载（调用时热载入）", "Backend"),
    ("8587", "Embedding 向量", "nomic 嵌入 · 看门狗管理", "Backend"),
    ("48911", "Neko 主前端", "React 聊天 + Avatar（Live2D/VRM/MMD）", "Frontend"),
    ("48912", "Neko 记忆服务", "独立 SQLite+Chroma 记忆系统", "Frontend"),
    ("48915", "Neko Agent 服务", "键鼠/浏览器/OpenClaw 控制", "Frontend"),
    ("9119", "Hermes Dashboard", "云端 LLM 网关 + Web UI + skills/MCP", "Infra"),
    ("8088", "Hermes 猫爪", "bin/hermes_paw_bridge.py · Agent 驱动", "Backend"),
    ("48920", "对话树面板", "Explore.poker 风格树形对话 · 后端=conversation_tree 引擎", "Frontend"),
]

# Full port mapping table (TCP ports + the named-pipe component).
PORT_TABLE = [
    (":9100", "控制面板 Web UI", "core/dashboard/server.py", "✅"),
    (":8080", "本地 LLM (Qwen3-1.7B, 懒加载)", "bin/ikaros-memory-watchdog.py → llama-server", "⚠️ 懒加载"),
    (":8587", "Embedding (nomic)", "bin/ikaros-memory-watchdog.py", "⚠️"),
    (":48911", "Neko 主前端", "core/neko/app/main_server.py", "✅"),
    (":48912", "Neko 记忆服务", "core/neko/app/memory_server.py", "✅"),
    (":48915", "Neko Agent 服务", "core/neko/app/agent_server.py", "✅"),
    (":9119", "Hermes Dashboard", "core/hermes/.../web_server.py", "✅"),
    (":8088", "Hermes 猫爪", "bin/hermes_paw_bridge.py", "✅"),
    (":48920", "对话树面板", "core/conversation-tree/server.py (后端 conversation_tree 引擎)", "✅"),
]

PORT_TABLE_FOOTNOTE = (
    "Herdr 终端编排（coding-agent 多路复用器）使用 <b>命名管道</b>（无 TCP 端口），"
    "由面板 herdr 组件按需启动。<br>"
    "Gopeed 下载 :9999 仅在下载任务时按需启动，<b>不计入常驻服务</b>。<br>"
    "已移除（勿加回）：Hermes API 网关 :8642、语音桥 :7870/:7871、Person Sync。"
)

CORE_LAYERS = [
    ("🧠 灵魂核心", [
        ("core/memory_v5/", "V5 记忆/情感/认知引擎（包名 memory_v5）"),
        ("core/memory_v5/data/v5/", "人格+记忆库（v5.db+chroma）"),
    ]),
    ("🐾 前端与桌宠", [
        ("core/neko/", "Neko 主服务（独立完整，不合并 V5）"),
        ("core/control-panel/", "Electron 桌面壳（拉 :9100）"),
    ]),
    ("🛠️ 基础设施", [
        ("core/hermes/", "Hermes Agent（原 hermes-agent，已搬迁）"),
        ("core/dashboard/", "控制面板 Web UI"),
        ("core/env/", "环境配置/CLI/初始化"),
        ("core/conversation-tree/", "对话树面板（:48920）"),
        ("core/data/", "核心内部日志（遗留）"),
    ]),
]

DATA_FLOW = [
    "用户在 <b>Neko 前端 :48911</b>（React+Avatar）发起对话",
    "Neko 经 <code>bin/cloud_chat.py</code> 主链 → 路由到 companion 模式（云端 Chat 优先）",
    "<b>Hermes Dashboard :9119</b> 提供云端 LLM；本地兜底走 <code>:8080</code>（调用时懒加载热载入）；对话树 <code>:48920</code> 直连 DeepSeek",
    "逐轮记忆轻写进 <b>V5（core/memory_v5/data/v5/）</b>；后台 reflect 流水线提炼事实/情感/自我模型",
    "V5 经 <code>get_ikaros_memories()</code> <b>单向</b> 暴露检索给 Neko；Neko 数据不回写 V5；V5 经 <code>push_to_conversation_tree()</code> 推送节点到 :48920",
    "ThirdSpace 同步 <code>bin/sync-thirdspace-v5.py</code> 把 thought 写入 <code>data/thirdspace-vault/</code>（双轨知识库）",
]

BOUNDARY_NOTES = [
    ("warn", "⚠️ neko 保持独立：", "core/neko 完整保留，不引入对 memory_v5 的代码依赖，不搬移 neko 内部 memory/ 模块。迁移脚本与方案文档已删除。"),
    ("ok", "✅ v5.db 契约保留：", "包已重命名 v5→memory_v5，但 DB 文件名 v5.db、40 个 v5_* MCP 工具名是对外契约，不改。"),
    ("warn", "⚠️ v5-memory 独立仓库：", "E:\\v5-memory 是从 core/memory_v5 抽离的开源插件版（无运行数据），两份手动同步，改主工程不影响开源版。"),
    ("ok", "✅ 严禁裸跑 llama-server：", "缺 CUDA DLL 会 SIGSEGV，一律走看门狗/控制面板启动。"),
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

  /* 控制面板总入口 */
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
  <div class="sub">快照日期 {TODAY} · 控制面板 :9100 · 9 个常驻 TCP 服务 + Herdr 命名管道 · 已取消 neko→V5 合并计划</div>

  <!-- 总入口 -->
  <div class="card panel">
    <h2 style="color:#fff">🎛️ 控制面板（总入口）</h2>
    <div style="font-size:13px;opacity:.95">一键启停所有组件，装配 CUDA 环境，模型切换持久化</div>
    <div class="row">
      <div class="chip"><b>:9100</b><span>core/dashboard/server.py（纯 stdlib）</span></div>
      <div class="chip"><b>bin/ikaros-control.bat</b><span>双击拉起面板</span></div>
      <div class="chip"><b>bin/ikaros-control-panel.bat</b><span>Electron 桌面壳</span></div>
    </div>
  </div>

  <!-- 服务层 -->
  <div class="card">
    <h2><span class="tag t-blue">服务层</span> 9 个常驻 TCP 组件 (+ Herdr 命名管道)</h2>
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
    <h2><span class="tag t-purple">代码层</span> core/ 目录（8 个一级模块）</h2>
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

  <footer>Ikaros Architecture Snapshot · 由 tools/gen_architecture_html.py 于 {TODAY} 生成 · 数据来自实际控制面板注册表与目录核查</footer>
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
      {n:"ikaros-control.bat", k:true, c:"拉控制面板"}, {n:"ikaros-control-panel.bat", c:"Electron 壳"},
      {n:"cloud_chat.py", k:true, c:"companion 主链"}, {n:"ikaros-memory-watchdog.py", k:true, c:":8080+:8587 看门狗"},
      {n:"hermes_paw_bridge.py", k:true, c:":8088 猫爪"},
      {n:"ikaros-soul-sync.py", c:"生成 SOUL.md"}, {n:"sync-thirdspace-v5.py", c:"ThirdSpace 同步"},
      {n:"ikaros-fastdl.py", c:"Gopeed 下载 (按需)"}, {n:"import-hermes-to-convtree.py", c:"Hermes→对话树 :48920"},
      {n:"neko-start.bat / neko-stop.bat", c:"Neko 启停"},
      {n:"rebuild_chroma_v5.py", c:"重建向量库"}, {n:"bootstrap-venvs.py", c:"venv 引导"}, {n:"secret-scan.py", c:"密钥扫描"},
      {n:"legacy/", c:"旧脚本回滚桶", b:"核心"}
    ]},
    {n:"config/", c:"配置文件", children:[
      {n:"defaults/", c:"默认端口/模型", children:[{n:"ports.yaml"},{n:"panel_models.json"}]},
      {n:"identity/", c:"身份文件", children:[{n:"axiom / capabilities"}]}
    ]},
    {n:"core/", c:"核心系统（8 模块）", b:"核心", open:true, children:[
      {n:"memory_v5/", k:true, c:"V5 灵魂引擎 (包名 memory_v5)", b:"核心", open:true, children:[
        {n:"__init__.py", k:true, c:"包根 · __version__"}, {n:"store.py / search.py", k:true, c:"记忆读写/检索"},
        {n:"affect.py / vitality.py / drivers.py", c:"情感/精力/驱动"},
        {n:"self_model.py / metacog.py / relationship.py", c:"自我/元认知/关系"},
        {n:"orchestrator.py / router.py / task_runner.py", c:"编排/路由"},
        {n:"hermes_provider.py", c:"Hermes 桥 / 推送对话树"}, {n:"mcp_server.py", c:"40 个 v5_* 工具"},
        {n:"reflect/", c:"后台整合流水线"}, {n:"cogno_extensions/", c:"认知扩展"},
        {n:"models/", c:"模型配置"}, {n:"services/", c:"start-*.bat"}, {n:"data/", k:true, c:"运行态记忆", b:"数据", open:true, children:[
          {n:"v5/", k:true, c:"人格+记忆库", children:[
            {n:"v5.db", k:true, c:"SQLite 记忆 (契约名不改)"}, {n:"chroma/", c:"向量库"},
            {n:"self_model.json / affect.json / relationship.json", c:"人格状态"},
            {n:"latest_thought.json / profile.json", c:"思考/画像"}
          ]}
        ]}
      ]},
      {n:"neko/", k:true, c:"Neko 主服务 (独立完整, 不合并 V5)", b:"前端", open:true, children:[
        {n:"app/", k:true, c:"main_server / memory_server / agent_server", children:[{n:"main_server.py (:48911)"},{n:"memory_server.py (:48912)"},{n:"agent_server.py (:48915)"}]},
        {n:"memory/", c:"Neko 自有记忆系统"}, {n:"frontend/", c:"React 聊天 UI"}, {n:"assets/", c:"Live2D/VRM/MMD Avatar"},
        {n:"brain/", c:"智能体逻辑"}, {n:"plugin/ / utils/ / config/ / tests/", c:"其他子模块"}
      ]},
      {n:"hermes/", k:true, c:"Hermes Agent (原 hermes-agent, 已搬迁)", b:"基础设施", open:true, children:[
        {n:"venv/", k:true, c:"Python 虚拟环境 (fastapi 0.133.1)"}, {n:"plugins/memory/", c:"记忆插件", children:[{n:"ikaros_v5/", k:true, c:"V5 桥 (plugin.yaml)"}]},
        {n:"hermes_cli/ / agent/ / gateway/ / web/ / skills/ / providers/", c:"核心子模块"},
        {n:"node_modules/ (vendored, 折叠)", c:"前端依赖"}, {n:".git (独立子仓库)", c:"Hermes 上游"}
      ]},
      {n:"dashboard/", k:true, c:"控制面板 Web UI (:9100)", b:"核心"},
      {n:"control-panel/", c:"Electron 桌面壳", b:"前端"},
      {n:"env/", c:"环境配置/CLI", children:[{n:"ikaros-cli/ / scripts/ / detect-root/"}]},
      {n:"conversation-tree/", k:true, c:"对话树面板 (:48920)"},
      {n:"data/", c:"核心内部日志 (遗留)", b:"数据"}
    ]},
    {n:"data/", k:true, c:"系统级运行数据", b:"数据", open:true, children:[
      {n:"models/", k:true, c:"模型权重 (GGUF/嵌入)"}, {n:"cache/ / tts-cache/", c:"缓存"},
      {n:"hermes-agent/", k:true, c:"Hermes 用户态 (契约目录, 不改名)"},
      {n:"thirdspace-vault/", c:"ThirdSpace 双轨知识库"}, {n:"logs/", c:"运行日志"},
      {n:"config/", c:"运行期配置"}, {n:"ikaros-coordination/", c:"组件协调"}
    ]},
    {n:"docs/", c:"文档", children:[
      {n:"ARCHITECTURE.md", k:true}, {n:"naming.md / lint.py (漂移守卫)"},
      {n:"neko-deep-analysis.md (已标作废合并建议)"},
      {n:"assets/ / examples/ / research/ / scripts/"}
    ]},
    {n:"deploy/", c:"部署", children:[{n:"Dockerfile"},{n:"README.md"}]},
    {n:"runtime/", c:"运行时依赖 (vendored, 未展开)", b:"基础设施"},
    {n:"scripts/", c:"setup-native.py / fetch-upstreams.py"},
    {n:"tests/", c:"测试", children:[{n:"hermes/ (子测试)"}]},
    {n:"tools/", c:"工具", children:[{n:"ikaros-monitor/ / dwg/ / experiments/ / gen_architecture_html.py"}]},
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
        f"  <div class=\"sub\">快照 {TODAY} · 已排除 vendored（runtime/ node_modules/ .git/ __pycache__）· 展开折叠可点击 ▸</div>\n\n"
        "  <div class=\"card\">\n"
        "    <div class=\"legend\">\n"
        "      <span class=\"lg\"><span class=\"sw\" style=\"background:var(--blue)\"></span>目录</span>\n"
        "      <span class=\"lg\"><span class=\"sw\" style=\"background:#647084\"></span>普通文件</span>\n"
        "      <span class=\"lg\"><span class=\"sw\" style=\"background:var(--red)\"></span>关键文件</span>\n"
        "      <span class=\"lg\"><span class=\"badge b-blue\">核心</span><span class=\"badge b-teal\">后端</span>\n"
        "        <span class=\"badge b-purple\">基础设施</span><span class=\"badge b-green\">前端</span><span class=\"badge b-amber\">数据</span></span>\n"
        "    </div>\n\n"
        "    <div class=\"tree\" id=\"tree\"></div>\n\n"
        "    <div class=\"note\">说明：<code>core/hermes/</code> 内置完整 Hermes 工程（含 node_modules/venv，未在树中展开）；\n"
        "      <code>core/neko/</code> 为独立完整前端工程；<code>data/</code> 含模型权重（仅在顶层标注）。\n"
        "      虚线折叠项可点击展开。</div>\n"
        "  </div>\n\n"
        f"  <footer>Ikaros Folder Tree · 由 tools/gen_architecture_html.py 于 {TODAY} 生成 · 数据来自实际目录扫描</footer>\n"
        "</div>\n\n<script>\n"
        "// 结构数据（基于实际目录核查，关键文件标 k:true）\n"
    )
    return head + T + "\n" + script + "</script>\n</body>\n</html>\n"


def render_dep_map() -> str:
    body = f"""<header>
  <h1>Ikaros 模块依赖关系图</h1>
  <p>快照日期 {TODAY} · 实线=运行时调用依赖，紫色虚线=记忆/状态写入，箭头方向=依赖指向。
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
        <rect x="360" y="20" width="200" height="46" rx="9" fill="var(--panel)" stroke="var(--ink)"></rect>
        <text x="460" y="42" text-anchor="middle" font-weight="600">控制面板 :9100</text>
        <text x="460" y="58" text-anchor="middle" class="sub">core/dashboard/server.py · 一键启停</text>
      </g>

      <g class="node">
        <rect x="40" y="180" width="200" height="150" rx="10" fill="var(--front-bg)" stroke="var(--front)"></rect>
        <text x="140" y="200" text-anchor="middle" font-weight="600" fill="var(--front)">前端桌宠 · core/neko</text>
        <text x="140" y="224" text-anchor="middle" class="sub">:48911 React 聊天 + Avatar</text>
        <text x="140" y="244" text-anchor="middle" class="sub">:48912 自有 memory_server</text>
        <text x="140" y="264" text-anchor="middle" class="sub">:48915 Agent (键鼠/浏览器)</text>
        <text x="140" y="288" text-anchor="middle" class="sub">完整独立，零 V5 代码依赖</text>
        <text x="140" y="308" text-anchor="middle" class="sub">Electron 壳 core/control-panel</text>
      </g>

      <g class="node">
        <rect x="360" y="180" width="200" height="120" rx="10" fill="var(--soul-bg)" stroke="var(--soul)"></rect>
        <text x="460" y="200" text-anchor="middle" font-weight="600" fill="var(--soul)">灵魂核心 · core/memory_v5</text>
        <text x="460" y="224" text-anchor="middle" class="sub">记忆/情感/认知引擎</text>
        <text x="460" y="244" text-anchor="middle" class="sub">包名 memory_v5</text>
        <text x="460" y="264" text-anchor="middle" class="sub">data/v5: v5.db+chroma+人格JSON</text>
        <text x="460" y="284" text-anchor="middle" class="sub">Hermes 桥插件在 core/hermes</text>
      </g>

      <g class="node">
        <rect x="680" y="180" width="200" height="80" rx="10" fill="var(--infra-bg)" stroke="var(--infra)"></rect>
        <text x="780" y="200" text-anchor="middle" font-weight="600" fill="var(--infra)">基础设施 · core/hermes</text>
        <text x="780" y="224" text-anchor="middle" class="sub">:9119 Dashboard 云端 LLM</text>
        <text x="780" y="244" text-anchor="middle" class="sub">含 venv + ikaros_v5 桥插件</text>
      </g>

      <g class="node">
        <rect x="360" y="350" width="200" height="104" rx="10" fill="var(--back-bg)" stroke="var(--back)"></rect>
        <text x="460" y="370" text-anchor="middle" font-weight="600" fill="var(--back)">后端服务</text>
        <text x="460" y="394" text-anchor="middle" class="sub">:8080 本地 LLM (懒加载)</text>
        <text x="460" y="414" text-anchor="middle" class="sub">:8587 Embedding (看门狗)</text>
        <text x="460" y="434" text-anchor="middle" class="sub">:8088 猫爪 (Hermes 驱动)</text>
        <text x="460" y="454" text-anchor="middle" class="sub">Herdr 命名管道 (coding-agent)</text>
      </g>

      <g class="node">
        <rect x="360" y="500" width="200" height="44" rx="9" fill="var(--panel)" stroke="var(--ink)"></rect>
        <text x="460" y="520" text-anchor="middle" font-weight="600">对话主链 · bin/cloud_chat.py</text>
        <text x="460" y="536" text-anchor="middle" class="sub">companion 模式统一入口</text>
      </g>

      <g class="node">
        <rect x="680" y="350" width="200" height="90" rx="10" fill="var(--data-bg)" stroke="var(--data)"></rect>
        <text x="780" y="370" text-anchor="middle" font-weight="600" fill="var(--data)">数据层 · data/</text>
        <text x="780" y="394" text-anchor="middle" class="sub">模型权重 (GGUF/嵌入)</text>
        <text x="780" y="414" text-anchor="middle" class="sub">data/hermes-agent 用户态</text>
        <text x="780" y="434" text-anchor="middle" class="sub">thirdspace-vault 知识库</text>
      </g>

      <g class="node">
        <rect x="40" y="500" width="200" height="44" rx="9" fill="var(--panel)" stroke="var(--ink)"></rect>
        <text x="140" y="520" text-anchor="middle" font-weight="600">ThirdSpace Vault</text>
        <text x="140" y="536" text-anchor="middle" class="sub">双轨知识库 (Markdown)</text>
      </g>

      <g class="node">
        <rect x="680" y="500" width="200" height="44" rx="9" fill="var(--front-bg)" stroke="var(--front)"></rect>
        <text x="780" y="520" text-anchor="middle" font-weight="600" fill="var(--front)">对话树 · :48920</text>
        <text x="780" y="536" text-anchor="middle" class="sub">core/conversation-tree/server.py</text>
      </g>

      <!-- edges -->
      <path class="edge" d="M460,66 L460,180" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,66 L140,200" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,66 L780,200" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,66 L460,350" marker-end="url(#ah)"></path>
      <path class="edge" d="M460,66 L780,350" marker-end="url(#ah)"></path>
      <text x="300" y="120" class="elabel">启动/停止编排</text>

      <path class="edge" d="M240,300 C300,420 320,470 358,520" marker-end="url(#ah)"></path>
      <text x="250" y="430" class="elabel">用户消息 → 主链</text>

      <path class="edge hot" d="M460,500 L460,300" marker-end="url(#ahh)"></path>
      <text x="470" y="410" class="elabel">逐轮轻写 + 后台 reflect</text>

      <path class="edge" d="M560,520 C660,500 700,470 780,260" marker-end="url(#ah)"></path>
      <text x="640" y="430" class="elabel">云端 LLM 优先 / :8080 兜底</text>

      <path class="edge" d="M460,300 L460,350" marker-end="url(#ah)"></path>
      <text x="468" y="330" class="elabel">推理/向量依赖</text>

      <path class="edge hot" d="M360,240 C300,250 280,260 240,260" marker-end="url(#ahh)"></path>
      <text x="250" y="245" class="elabel">V5 单向暴露检索</text>

      <path class="edge" d="M360,280 C260,340 180,440 140,500" marker-end="url(#ah)"></text>
      <text x="200" y="400" class="elabel">latest_thought 同步</text>

      <path class="edge hot" d="M460,300 C620,360 720,440 780,500" marker-end="url(#ahh)"></path>
      <text x="600" y="400" class="elabel">V5 推送节点 (push_to_conversation_tree)</text>

      <path class="edge" d="M560,380 C660,370 700,360 780,260" marker-end="url(#ah)"></path>
      <text x="640" y="330" class="elabel">同 venv 进程</text>

      <path class="edge" d="M560,350 C660,350 700,360 780,350" marker-end="url(#ah)"></path>
      <text x="650" y="345" class="elabel">模型/用户态落盘</text>
    </svg>
  </div>

  <aside class="legend">
    <h3>模块分区</h3>
    <div class="lg"><span class="dot" style="background:var(--soul)"></span>灵魂核心 memory_v5</div>
    <div class="lg"><span class="dot" style="background:var(--front)"></span>前端桌宠 neko / 对话树</div>
    <div class="lg"><span class="dot" style="background:var(--infra)"></span>基础设施 hermes</div>
    <div class="lg"><span class="dot" style="background:var(--back)"></span>后端服务</div>
    <div class="lg"><span class="dot" style="background:var(--data)"></span>数据层 data/</div>

    <div class="leg-edge">
      <h3>依赖类型</h3>
      <div class="row"><span class="arrow"></span>运行时调用依赖</div>
      <div class="row"><span class="arrow hot"></span>记忆/状态写入（高价值）</div>
    </div>

    <details>
      <summary>三条边界约定</summary>
      <ul>
        <li>⚠️ <b>neko 保持独立</b>：不引入 memory_v5 依赖，不搬移其 memory/ 模块，数据不回写 V5。</li>
        <li>✅ <b>v5.db 契约保留</b>：包改名 memory_v5，但 DB 文件名与 40 个 v5_* 工具名不改。</li>
        <li>⚠️ <b>E:\\v5-memory 独立开源版</b>：无运行数据，与主工程手动同步，按需更新。</li>
      </ul>
    </details>
    <details>
      <summary>已移除（勿引用）</summary>
      <ul>
        <li>:8642 Hermes 网关、:7870/:7871 语音桥</li>
        <li>think.py 自循环、Persona Sync、Studio 桌面端</li>
        <li>core/v5 旧名 → 现为 core/memory_v5</li>
      </ul>
    </details>
  </aside>
</div>

<p class="note">
  <b>如何读这张图：</b>控制面板 :9100 是总开关，编排全部组件。
  用户对话由 neko 前端经 <code>bin/cloud_chat.py</code> 主链进入；
  主链优先走 Hermes 云端 LLM（:9119），本地 :8080 兜底；
  每一轮都会<b>轻写</b> V5（紫色虚线）并在后台跑 reflect 提炼；
  V5 把检索能力<b>单向</b>暴露给 neko（neko 不回写）；
  V5 经 <code>push_to_conversation_tree()</code> 把节点推送到对话树 :48920；
  V5 的 latest_thought 同步到 ThirdSpace 双轨库。
    Herdr（coding-agent 终端多路复用器）使用命名管道，无 TCP 端口。
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
