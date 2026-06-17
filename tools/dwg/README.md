# `tools/dwg/` — DWG / DXF 模具资料处理脚本

LLM 在对话中针对 DWG / DXF 模具资料(`模具图`、`ICE`、`吉利` 等)生成的
调试 / 提取 / 转换 / 生成-Excel 脚本的统一落地目录。

## 目录用途

- **范围**:DWG / DXF 文件解析、几何提取、文本提取、表格生成、Excel 报表生成、
  Aspose / ezdxf 库能力探测等。
- **不存放在这里**:生产代码(放 `modules/` 或 `bridge/`)、
  hermes 框架本身(放 `hermes-agent/`)、一次性 mock(放 `tools/_scratch/`)。

## 迁移历史

- **2026-06-16** — 根目录累积的 51 个 DWG/DXF 相关 `.py` 一次性收敛到此。
  - 这些脚本都是对话中 LLM 通过 `execute_code` / `write_file` / `terminal`
    工具直接写出的,**未被 git 追踪**(从未 `git add`),所以走的是普通
    `Move-Item` 而非 `git mv` — **没有 git rename 历史可保留**。
  - 根目录 `validate_portable_python.py` / `verify_skills_source.py`
    保留为系统级工具,与 DWG 无关。

## 命名规范(2026-06-16+ 起适用)

| 类型 | 命名模式 | 用途 | 是否 git 跟踪 |
|------|----------|------|---------------|
| **稳定工具** | `tool_<verb>.py` | 生产级可复用脚本(后续 Agent 会 `import` 或调用) | ✅ |
| **调试/实验** | `debug_<aspect>.py` | 一次性探针(能力探测 / 字段位置 / 行号定位) | ✅ |
| **迭代版本** | `spatial_extract_v2.py ~ v12.py` | 同一脚本的多版本演进,**保留最有用的一个**,其余移到 `_archive/` | ✅ 当前版本 / `_archive/` gitignore |
| **废弃脚本** | `_archive/<name>.py` | 历史版本、实验分支、失败的尝试 | ❌ gitignore(保留可恢复) |

**判断规则**:
- 如果后续 Agent 还会调用 / `import` → `tool_<verb>.py`
- 如果只是当时诊断用 → `debug_<aspect>.py`
- 如果是 v2/v3 替代 v1 → 把 v1 移到 `_archive/`,**不要堆在主目录**

## 落地规则(继承自 `bridge_pool.py` 的 `PROJECT_CONVENTIONS`)

- **永远不要**把新 `.py` / `.ps1` / `.bat` / `.sh` 写到 `HERMES_ROOT` 根目录。
- DWG / DXF 脚本 → `tools/dwg/`(本目录)。
- 非 DWG 临时脚本 → `tools/_scratch/`(gitignore)。
- 拿不准 → `tools/dwg/` 是默认。

## 当前清单(51 个,2026-06-16 一次性迁移)

### DWG 解析 / 元数据(`dwg_*` / `deep_probe_dwg.py`)

- `dwg_metadata.py` — 提取 DWG 文件元数据(header / 版本 / 编码)
- `dwg_parse_r2000.py` — DWG R2000 版本二进制解析
- `dwg_structure_scan.py` — DWG 整体结构扫描(对象表 / 段表)
- `deep_probe_dwg.py` — DWG 深层探测(字段反推 / 位移扫描)

### DXF 转换 / 文件头(`dxf_*` / `ezdxf_*`)

- `dxf_to_excel.py` — DXF → Excel 转换
- `ezdxf_fileheader.py` — ezdxf 库读取 DXF 文件头探测

### 文本 / 表格 / 批量提取(`extract_*`)

- `extract_dwg_text.py` — 从 DWG 提取 TEXT / MTEXT 实体
- `extract_dxf_batch.py` — DXF 文件夹批量提取
- `extract_dxf_deep.py` — DXF 深层提取(嵌套块 / 属性)
- `extract_dxf_full.py` — DXF 全量提取
- `extract_dxf_tables.py` — DXF 表格区域提取(TABLE 实体)

### 调试探针(`debug_*`)

- `debug_desc_num.py` — 描述字段编号定位
- `debug_geely.py` / `debug_geely2.py` — 吉利模具图专项调试(v1 / v2)
- `debug_ice.py` — ICE 模具图调试
- `debug_lines.py` — LINE 实体坐标调试
- `debug_parse.py` — 解析失败现场调试
- `debug_path.py` — 文件路径 / 编码路径调试
- `debug_range.py` / `debug_range2.py` — 实体 ID 范围探测(v1 / v2)
- `debug_rows.py` — 表格行号调试
- `debug_table1.py` / `debug_table1_v2.py` — 表格 #1 调试(v1 / v2)
- `debug_xpos.py` — X 坐标位置调试

### Aspose 能力探测(`test_aspose*` / `test_*`)

- `test_aspose.py` ~ `test_aspose4.py` — Aspose.CAD 库 4 轮能力探测
- `test_clean.py` — 清洗脚本验证
- `test_dwg_capability.py` — DWG 解析能力探测

### 几何 / 空间提取(`spatial_extract*`)

- `spatial_extract.py` — 空间几何提取基础版(后续 v2+ 演进)
- `spatial_extract_v2.py` ~ `spatial_extract_v12.py` — 12 个迭代版本
  > 后续整理:把非最终版移到 `_archive/`,只留最有用的 1 个

### 实体探针(`probe_*`)

- `probe_dwg_entities.py` — DWG 实体类型探针

### 生成-Excel 报表(`generate_*`)

- `generate_geely_ice_inventory.py` — 吉利 ICE 模具清单
- `generate_geely_inventory.py` — 吉利模具清单
- `generate_mold_inventory.py` — 通用模具清单

### 单步流水线(`step*`)

- `step1_explore.py` — Step 1: 探索 DWG 结构
- `step2_generate_excel.py` — Step 2: 生成 Excel
- `step3_validate.py` — Step 3: 校验输出

### 杂项

- `bin_scan_dwg.py` — 扫描 DWG 文件夹二进制清单
- `check_y.py` — Y 轴坐标检查

---

> **下次见到 LLM 把脚本写到根目录**,请引用 `PROJECT_CONVENTIONS`
> (在 `bin/fix-eol.py` / `modules/bridge/sitecustomize.py`) 提示它重写到 `tools/dwg/`。