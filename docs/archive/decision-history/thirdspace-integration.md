# ThirdSpace Vault 集成使用指南

> Ikaros 的外部知识库层。与 V5 记忆系统（内部）双轨并行。
> 整合计划：`handoff/02-thirdspace-integration-plan.md`；执行手记：`handoff/03-thirdspace-detailed-execution.md`

## vault 位置
`E:\Ikaros\data\thirdspace-vault\`

> 注：该目录在 `data/**` 下，已被 `.gitignore` 整体忽略——vault 是本地个人知识库，不进主仓库。

## 架构关系
```
Ikaros（Hermes Agent 运行时）
  ├── V5 Memory System（内部：事实/关系/情感/反思）   ← Ikaros-memory/v5/
  └── ThirdSpace Vault（外部：项目/知识/日志/输出）   ← data/thirdspace-vault/
```
- V5：实时记忆、情感、关系、内心反思 → 由 metacog / cloud_chat 驱动
- ThirdSpace：项目文档、知识卡片、工作日志、输出成品 → 由 `thirdspace-bridge` Skill 操作

## 通过 Hermes Agent 操作 vault

Hermes 重启后会自动发现 `data/hermes-agent/skills/thirdspace-bridge/`（目录扫描，无需改 config）。

### 常用语句
| 你说 | 效果 |
|------|------|
| "存个想法：xxxx" | 写入 `01-收件箱/待整理/YYYYMMDD_xxxx.md` |
| "写工作日志：xxxx" | 追加到 `02-日记/工作日志/YYYYMMDD_工作日志.md` |
| "整理知识：xxxx 到 AI 主题" | 写入 `03-知识/ai/YYYYMMDD_xxxx.md` |
| "建项目：xxx" | 创建 `04-项目/xxx/WORKSPACE.md` |
| "写文章：xxx" | 写入 `06-输出/article/YYYYMMDD_xxx.md` |

### Frontmatter 规范（9 字段，Skill 自动生成）
```yaml
---
title: "文档标题"
type: note            # note | worklog | article | card | project
topic: ikaros         # ai | dev | work | life | ikaros
workspace: "03-知识"    # 必须等于目标工作区目录名
created: "2026-07-20 21:00:00"
modified: "2026-07-20 21:00:00"
tags: ["ikaros", "architecture"]
source: mcp           # mcp | manual | obsidian-clipper | web | import（不支持 agent）
status: draft         # draft | active | processed | archived
---
```

## 手动同步 V5 反思 → vault
```bash
cd E:\Ikaros
python bin/sync-thirdspace-v5.py --latest     # 同步 V5 最新反思到 02-日记/反思/
python bin/sync-thirdspace-v5.py --dry-run    # 预览，不写入
```
- 读取源：`Ikaros-memory/data/v5/latest_thought.json`（字段 text/kind/theme/ts）
- 目标：`data/thirdspace-vault/02-日记/反思/YYYYMMDD_metacog反思_{kind}.md`
- 路径经 `THIRDSPACE_VAULT` / `IKAROS_MEMORY` / `IKAROS_ROOT` 环境变量解析，不硬编码盘符
- 同日幂等：已存在则跳过

## 通过 Obsidian 阅读
用 Obsidian 打开 `E:\Ikaros\data\thirdspace-vault\` 即可。
（注：Obsidian 沙箱不会跳转到 vault 外的 `docs/` 链接，引用卡片中以文本记录原始路径。）

## 工作区目录
- `00-系统`：规范 / Schema / Skills
- `01-收件箱`：临时入口（待整理）
- `02-日记`：工作日志 / 反思 / 复盘 / 人际事件 / todos
- `03-知识`：知识卡片（已建 `ikaros/` 索引与架构摘要）
- `04-项目`：项目文档（已建 `Ikaros/` 工作区）
- `05-资源`：参考资料
- `06-输出`：发布成品
- `99-归档`：已归档

## 已知偏差（相对 handoff 文档）
1. `config.yaml` 无 `skills:` 注册段 —— Hermes 按目录自动发现 Skill，故未改 config（按 03 文档加 skills 段无效）。
2. `data/**` 已整体 gitignore，故 03 文档 T1.4 的细粒度忽略规则为冗余，已省略（保留 vault 在 data/ 下不进仓库的设计）。
3. 同步脚本原假设 `:8080/api/thoughts/latest`（:8080 本地 LLM 已于 2026-08-18 退役）与 `data/v5.db` 均不存在；已改为读取真实 `Ikaros-memory/data/v5/latest_thought.json`。
4. T3.1 按 02 计划"只建引用/摘要、不移动原文件"执行（建 `03-知识/ikaros/` 索引+架构摘要卡片），未 bulk-copy 全部 106 个 `docs/*.md`。
5. session-stop hook（`data/hermes-agent/hooks/session-stop.sh`）已创建，但 Hermes hooks 接线机制未验证（当前版本无 `hooks:` 配置证据）。
