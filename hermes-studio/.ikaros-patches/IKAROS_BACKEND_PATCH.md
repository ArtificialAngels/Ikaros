# Ikaros 后端设置 — Hermes API Key 继承补丁

## 问题

Studio 设置页的「Ikaros 后端设置」界面原本只读独立的
`config/ikaros-backend.json`（`deepseek.api_key` 为空），与 Hermes 实际存放
API key 的 `data/hermes-agent/.env`（`DEEPSEEK_API_KEY`）**完全解耦**，导致
界面无法自动继承 Hermes 已配好的 key。

## 修复（双向同步）

源真理文件：`.ikaros-patches/ikaros-backend.ts`
（对应运行位置：`packages/server/src/controllers/ikaros-backend.ts`）

- **GET `/api/ikaros/backend`**：若 `config/ikaros-backend.json` 的
  `deepseek.api_key` 为空，则从 Hermes `.env` 读 `DEEPSEEK_API_KEY` 回填到
  返回体 → 设置界面自动显示已配 key。
- **PUT `/api/ikaros/backend`**：保存时若 studio 侧 `deepseek.api_key` 非空，
  则同步写回 Hermes `.env` 的 `DEEPSEEK_API_KEY` 行（**原子写，保留其他行**）。
- **安全保护**：空值不写回 → 在界面清空 key 不会误清空 Hermes `.env` 凭据。

新增纯函数：`readHermesEnv()`（解析 `.env` 为 Map）、
`syncHermesDeepseekKey()`（原子写回）。

前端 `IkarosBackendPanel.vue` 无需改动（本就合并 GET 返回值到输入框）。

## 为什么以补丁形式存档

`hermes-studio/packages/` 整体被根仓库 `.gitignore` 忽略（7-2 / 7-4 cleanup
batch 约定：上游源码不进主仓库）。因此 ikaros 对 hermes-studio 的定制统一以
**源码级补丁**存于 `.ikaros-patches/`，由恢复脚本重建本地 `packages`，与
`v5-agent` 补丁机制一致。

## 文件清单

| 文件 | 说明 |
|------|------|
| `.ikaros-patches/ikaros-backend.ts` | 定制版控制器（源真理，含双向同步） |
| `.ikaros-patches/restore-ikaros-backend.sh` | Linux/Mac 恢复脚本 |
| `.ikaros-patches/restore-ikaros-backend.bat` | Windows 恢复脚本 |

## 恢复命令

```bash
cd E:/Ikaros/hermes-studio
bash .ikaros-patches/restore-ikaros-backend.sh
# 或 Windows:
.ikaros-patches\restore-ikaros-backend.bat
```

恢复后重启 Studio（`npm run dev`，ts-node 热重载即生效）。

## 生效前提

- 改动在 Studio **后端**（Koa :8647），需重启 studio 后端进程加载新 `.ts`。
- 重启后打开设置页「Ikaros 后端」→ provider 切 `deepseek` → key 框应已填好
  从 Hermes 继承的值；界面改 key 保存会同步回写 Hermes `.env`。
