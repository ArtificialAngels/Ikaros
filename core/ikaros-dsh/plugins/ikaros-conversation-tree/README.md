# ikaros-conversation-tree —— 对话树面板 dsh 插件（双面：Node 插件 + client bundle）
#
# 目标：把对话树（core/conversation-tree，独立服务 :48920）按 dsh 标准打包为插件，
#       从 dsh Web UI (3080) 一键打开，对话树自身零改动（独立进程、独立原点，CORS 已全开）。
#
# 结构：
#   src/index.ts    Node 侧 cordis 插件：探活 :48920 + 未启动时用便携 Python 拉起 server.py；
#                   提供 ctx.conversationTree 服务（URL / 健康状态）
#   src/client.tsx  client 侧 bundle：在 dsh UI 侧边栏 footer 挂「对话树」按钮，
#                   点击打开 shell.overlay 全屏 iframe 面板（指向 :48920）
#   scripts/build.mjs 构建：tsc 编 Node 侧 → dist/index.js；esbuild 打 client → dist/client.js
#
# dsh API 要点（实测确认，与 ikaros-memory 同源）：
#   - Node 插件：export { name, inject, apply }；ctx.effect / ctx.provide / ctx.on
#   - client bundle：window.__ModuleLoader__.load({ id, factory(require) })；
#     factory 内可 require 的静态词表（shell-own）：react, react/jsx-runtime,
#     react-dom, react-dom/client, @deepseek-ai/cordis, @deepseek-ai/dsh-client-ui-slots,
#     @deepseek-ai/dsh-client-web-react, @deepseek-ai/dsh-client-ui-primitives 等
#   - UI 挂载：ctx.slots.inject('shell.overlay', () => ctx.slots.register({...}, Comp))
#     shell.overlay = list 槽（可叠加），root 级别的浮动表层，正是放独立面板的位置；
#     sidebar.footer.action = 侧边栏底部动作区（list 槽），放入口按钮
#   - package.json 需声明 dsh.client: { platform: 'web', inject: [...] }（inject 是
#     client 侧服务依赖，进 boot graph 的 entry 边）

# 版本对齐：dsh 当前 @deepseek-ai/cordis ^4.0.1（与 ikaros-memory devDeps 一致）