# 48920 主题色 / Settings 页 / 左下角设置按钮 — 完成概览

## 完成内容
- **主题色与 8099 完全一致**: 将 48920 主题系统从 5 个扩展为 8099 同款的 9 个主题 (Default/Warm/Midnight Forest/Sakura/Memphis/Sunset/Purple/Blue/Orange), 全部使用 8099 `color.css` 的精确色值; 80 处硬编码绿色发光/背景已替换为 `color-mix(in srgb,var(--brand) …)`, 任意主题下发光与描边都会随 brand 变化。
- **左下角仅保留 Settings 按钮**: 删除原 theme-switcher、accent picker、reset 按钮、Account 卡, 改为单个 glass 风格 Settings 入口 (齿轮 SVG + Settings 文字); 展开/收缩态均保持可点击, 收缩时为 44×44 图标方格。
- **复刻 8099 Settings 页**: 新增 `#settingsModal` 居中浮窗 (75%×75%, rounded-3xl, 左侧 9 项导航 + 右侧内容区), 复制 Available Models / Model Assignment / API Keys / Color Theme / Edit Permissions / Shortcuts / Auto Citation / Language / Layout 的视觉结构。
- **删除 "?" 按钮与弹窗**: Settings 标题栏没有 "?" 帮助按钮, Layout 区说明该弹窗已被移除。
- **Color Theme = 全局主题控制中枢**: 设置页 Color Theme 区展示 9 个主题 swatch, 点击立即调用 `applyTheme(id)` 切换全局 `data-theme`, 全应用实时换色。
- **其他设置页**: 已按 8099 视觉复制为 placeholder, 标注后续再改。

## 关键文件
- `core/conversation-tree/index.html`: 主题 CSS、`THEMES` 数组、Settings modal HTML/CSS/JS、左下角 Settings 按钮。

## 验证
- puppeteer: 左下角仅 `Settings`; 设置模态正常打开; 无 "?" 按钮; 9 导航项 / 9 主题 swatch; Sakura 主题点击后全局生效; 收缩态 Settings 按钮 44×44 可见。
- 无 JS pageerror / console error。

## 参考截图
- `tmp/convtree-fusion/screenshots/verify-settings/`

## 后续待做
- 其他设置页内容接真实业务 (模型/API/权限等)。
- 聊天区卡片化 (用户此前提到, 本次未涉及)。
