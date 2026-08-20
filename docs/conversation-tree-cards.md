# 对话树万用工具卡组 (Artifact Deck) — Agent 语法参考

> 位置：`core/conversation-tree/index.html`（`mdRender` 抽取 + `#cardDeck` 卡组渲染）
> 生效范围：对话树面板 :48920。**agent 在 markdown 正文输出卡片块即可，前端自动抽取进独立卡组**（流式实时 + 持久化重渲染双路径，降级链路同样可用）。

## 基本语法

```
:::card TYPE
key: value
key2: value

BODY（可选，原样保留）
:::
```

- `TYPE` 不区分大小写；属性键小写化；前段连续 `key: value` 行解析为属性，遇到非键值行后全部归为正文。
- 未闭合的 `:::card` 块在流式中不显示（等闭合后进卡组），不会闪半截。
- 所有内容经 HTML 转义，`src` 只允许 http(s) 协议，零脚本执行。

## 卡组三态（与 chat 卡同构）

| 态 | 形态 | 说明 |
|----|------|------|
| L1 | 90×60 小卡 | 未调用态；多卡时堆叠（只露 3 张 +N 徽章） |
| L2 | 中等面板 | 只读展示（小功能，无需用户交互） |
| L3 | 全功能面板 | 可交互（browser 有地址栏/导航，其余卡预留） |

- **自动布局**：1~3 张卡 → 全 L2 展开；>3 张 → 被调用的卡展开、其余收缩 L1。
- **调用**：点击小卡 → L2 展开并标记 active（淡蓝→粉流光阴影环绕）。
- **让位**：卡组展开时 chat 卡（L2/L3）宽度收缩 `--deck-w` 往左挤，卡组可拖拽悬浮。
- **正文占位**：卡片块位置渲染为 chip（如 `✨ 表情`），点击 chip 聚焦对应卡。

## 类型清单

### 1. `browser` — Mini 浏览器
```
:::card browser
src: https://example.com
title: 参考文档
:::
```
iframe + 地址栏（可改地址跳转）+ 后退/前进/刷新/新标签打开。站点禁止内嵌时显示空白（有提示条）。

### 2. `file` — 文件预览（自动按扩展名分派）
```
:::card file
src: /assets/ikaros-logo.png
title: Logo
:::
```
`image`(img) / `pdf`(iframe) / `audio` / `video` / `text`(同源 fetch，跨域显示下载链接) / `markdown`（fetch 后 mdRender）。可显式 `type:` 覆盖。

### 3. `whiteboard` — SVG 白板（流程图/框架图 DSL）
```
:::card whiteboard
title: 系统架构

node fe 前端 48920
node dsh 工作引擎 :3080
node mem 记忆 V5
link fe -> dsh 请求
link dsh -> mem 查询
:::
```
- `node <id> <标签>` — 节点（id 供 link 引用，标签可含空格）
- `link <from> -> <to> <说明>` — 贝塞尔连线 + 箭头标签
- 自动网格布局（3 列），节点按出现顺序排列；`#` 开头为注释行。

### 4. `emoji` — 大表情
```
:::card emoji
char: 🎉
title: 任务完成
size: 80
:::
```
`size` 32–120px，默认 72。

### 5. `animation` — CSS 动画文字
```
:::card animation
type: rainbow
text: 你好，我是伊卡洛斯
size: 30
:::
```
`type`: `typing`（打字机）/ `float` / `pulse` / `spin` / `bounce` / `rainbow`（渐变流光）/ `wave`（逐字波浪）/ `heartbeat`。

### 6. `model` — 3D 模型卡
```
:::card model
src: /models/robot.glb
poster: /models/robot.png
title: 机器人
desc: GLB 模型
:::
```
poster 预览图 + 信息 + 下载按钮（three.js 交互查看器为后续项）。

### 7. `audio` / `video` — 媒体播放
```
:::card audio
src: /audio/hello.mp3
title: 语音
:::
```
HTML5 播放器，`video` 支持 `poster:`。

### 8. `code` — 大代码框
```
:::card code
lang: python
title: 主函数

def main():
    print('hello')
:::
```
带语言徽章 + 复制按钮 + 滚动。普通 markdown 代码块（```）仍走原有样式。

### 9. `note` / `info` / `warn` / `ok` — 提示条
```
:::card warn
title: 注意
这里是补充说明
:::
```
四色左边框提示条。

## 工具生命周期卡（自动，无需 agent 输出）

`tool_call` / `tool_result` SSE 事件渲染为 `tool-card`（调用中 spinner / ✓ 完成 / ✗ 失败 三态），emoji 取自工具事件（缺省 🔧），持久化后随节点重渲染。

## 约定

- 一个回复里卡片数量适度（≤3 为宜），卡片应承载"文字难以表达"的信息，不要为装饰而用。
- 与正文混排时卡片自动按块顺序插入，无需额外分隔。
- 修改渲染器后跑 `python -m pytest tests/`（56 tests）+ 浏览器实测 `mdRender` 输出。
