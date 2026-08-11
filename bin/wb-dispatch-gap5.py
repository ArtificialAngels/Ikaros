#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb-dispatch-gap5.py — 把 hermes-gap-closure 批次 5 个任务分发给 WorkBuddy。

- 共享一个 codebuddy 会话（第一个任务建会话，后续 keep 延续上下文）
- 每任务独立 subprocess，失败不中断后续任务
- 日志: tmp/wb-dispatch-20260810.log
- 用法: python bin/wb-dispatch-gap5.py [--dry-run]
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = "E:/Ikaros/.workbuddy/handoff/2026-08-10-hermes-gap-closure.md"
LOG = ROOT / "tmp" / "wb-dispatch-20260810.log"

TASKS = [
    ("任务1-会话导出",
     f"请先阅读 {DOC} 的『任务 1：会话导出（conversation-tree）』小节，"
     "然后完整实现：conversation-tree 新增 GET /api/sessions/:id/export 端点"
     "（format=json|txt，json 与 /api/sessions/import 输入兼容形成往返闭环）+ "
     "index.html 会话操作菜单加导出按钮。按验收标准实测：curl 两个格式 + 导出导入往返。"),
    ("任务2-文件上传",
     f"请先阅读 {DOC} 的『任务 2：文件上传 / 附件（conversation-tree）』小节，"
     "然后完整实现：conversation-tree 新增 POST /api/upload（multipart，50MB 上限，"
     "8字节随机hex文件名）+ index.html 拖拽/粘贴/点击上传与附件展示 + 消息发送时把附件"
     "转 OpenAI ContentBlock 协议透传 gateway :8642（图片→image_url base64，文本→读内容）。"
     "按验收标准实测：图片能被 LLM 视觉理解、文本文件能被读取、>50MB 被拒。"),
    ("任务3-用量分析",
     f"继续完成用量分析任务（你已调研过数据源）。请阅读 {DOC} 的『任务 3：用量分析 UI（9100 dashboard）』小节，"
     "在 9100 panel.html 加『用量』卡片（token 明细/预估费用/模型分布/30天趋势柱状图，"
     "原生 JS 不引图表库），server.py 加代理端点。排查 glm 计费记 0 失真根因并修复或明确标注。"
     "验收：面板数据与 9119 /api/analytics/usage 一致。若上轮调研已有结论，直接按结论实现。"),
    ("任务4-CronKanban",
     f"请先阅读 {DOC} 的『任务 4：Cron / Kanban 管理 UI（9100 dashboard）』小节，"
     "先调研 hermes CLI 是否有 cron/kanban 子命令（有则复用，无则直接读写数据："
     "data/hermes-agent/cron/ JSON + kanban.db SQLite，写前备份），"
     "然后在 9100 panel.html + server.py 加 Cron 卡片（列表/创建/暂停/恢复/删除/立即触发）"
     "和 Kanban 卡片（列+卡片，创建/移动/删除）。按验收标准实测。"),
    ("任务5-工具截断",
     f"请先阅读 {DOC} 的『任务 5：工具卡片截断保护 + 执行时间（conversation-tree）』小节，"
     "然后实现：SSE tool_result 事件加 duration 字段（_on_tool_complete 处计时）+ 透出前"
     "JSON 截断保护（>200字符/深度>6/节点>1000）+ index.html toolCardHtml 渲染截断提示与耗时。"
     "按验收标准实测：几十KB大结果不撑爆DOM、耗时可见、小结果不受影响。"),
]


def run_one(idx: int, name: str, prompt: str, session: str) -> int:
    cmd = ["python", "bin/wb.py", prompt,
           "--session", session,
           "--model", "deepseek-v4-flash",
           "--perm", "bypassPermissions",
           "--cwd", "E:/Ikaros",
           "--timeout", "1500"]
    t0 = time.time()
    proc = None
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1560)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    cost = int(time.time() - t0)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n===== [{idx}/5] {name} rc={rc} {cost}s =====\n")
        if proc and proc.stdout:
            f.write(proc.stdout.strip()[-4000:] + "\n")
        if proc and proc.stderr:
            f.write("[stderr] " + proc.stderr.strip()[-1500:] + "\n")
    print(f"[{idx}/5] {name}: rc={rc}, {cost}s -> 日志 {LOG}", flush=True)
    return rc


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-from", type=int, default=1, help="从第几个任务开始（1-5）")
    args = ap.parse_args()
    LOG.parent.mkdir(exist_ok=True)
    results = []
    session = "keep"  # 沿用链路测试刚建的 ikaros-1786366912
    for i, (name, prompt) in enumerate(TASKS, 1):
        if i < args.start_from:
            continue
        rc = run_one(i, name, prompt, session)
        results.append((name, rc))
    print("\n===== 汇总 =====")
    for name, rc in results:
        print(f"  {name}: {'OK' if rc == 0 else f'rc={rc}'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
