# V5 Global Agent package.json 钩子配置

## 在 Hermes Studio 的 package.json 中添加以下内容

### 方式 1：添加 postinstall 钩子（推荐）

```json
{
  "scripts": {
    "postinstall": "bash .ikaros-patches/restore-v5-agent.sh",
    "restore-v5-agent": "bash .ikaros-patches/restore-v5-agent.sh",
    "apply-v5-patch": "bash .ikaros-patches/restore-v5-agent.sh && bash .ikaros-patches/apply-v5-route-patch.sh"
  },
  "engines": {
    "node": ">=18.0.0"
  }
}
```

### 方式 2：Windows 环境

```json
{
  "scripts": {
    "postinstall": "bash .ikaros-patches/restore-v5-agent.sh || .ikaros-patches\\restore-v5-agent.bat",
    "restore-v5-agent": "bash .ikaros-patches/restore-v5-agent.sh || .ikaros-patches\\restore-v5-agent.bat",
    "apply-v5-patch": "bash .ikaros-patches/apply-v5-route-patch.sh"
  }
}
```

## 手动应用路由补丁

如果自动补丁脚本没有成功应用路由修改，可以手动执行：

```bash
# Linux/Mac
bash .ikaros-patches/apply-v5-route-patch.sh

# Windows (Git Bash)
bash .ikaros-patches/apply-v5-route-patch.sh
```

## 完整工作流

### 首次注册

```bash
# 1. 复制补丁文件
cp -r .ikaros-patches /path/to/hermes-studio/

# 2. 应用补丁
cd /path/to/hermes-studio
npm run restore-v5-agent
npm run apply-v5-patch

# 3. 重新构建
pnpm build

# 4. 重启 Studio
# (重启你的 Hermes Studio 进程)
```

### 更新后恢复

```bash
# 1. 更新 Studio
cd /path/to/hermes-studio
git pull

# 2. postinstall 钩子会自动运行 restore-v5-agent.sh

# 3. 手动应用路由补丁（如果需要）
npm run apply-v5-patch

# 4. 重新构建
pnpm build

# 5. 重启 Studio
```

## 注意事项

1. **postinstall 钩子**只恢复文件，不会自动应用路由补丁
2. **路由补丁**需要手动运行 `apply-v5-patch` 或手动编辑
3. 如果 sed/awk 在你的系统上不可用，请手动参考 `ROUTE_PATCH_INSTRUCTIONS.md` 编辑
4. Windows 用户建议使用 Git Bash 运行脚本

## 验证注册

注册完成后，在 Studio 中选择 Agent 时应该能看到 `ikaros-v5` 选项：

```
Select Agent:
- Ekko Agent (ekko-agent)
- Ikaros V5 (ikaros-v5)    ← 新增
- Claude Code (claude-code)
- Codex (codex)
```