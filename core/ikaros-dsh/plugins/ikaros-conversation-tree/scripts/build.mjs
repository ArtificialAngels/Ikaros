// ikaros-conversation-tree 构建：Node 侧 tsc → dist/index.js；client 侧 esbuild CJS → dist/client.js
// client bundle 必须包成 window.__ModuleLoader__.load({id, factory}) 形态（dsh client-modules 协议），
// factory 内提供 var module/exports 骨架 + require 参数（Loader 注入模块词表）。
//
// 工具链复用（避免重复安装）：
//   tsc     ← E:/Ikaros/core/ikaros-dsh/plugins/ikaros-memory/node_modules/typescript（同款 devDeps）
//   esbuild ← E:/Ikaros/runtime/node/node_modules/esbuild（runtime 自带）
// esbuild ← runtime 自带（NODE_PATH 对 ESM 无效，用绝对路径导入）
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { build } = require('E:/Ikaros/runtime/node/node_modules/esbuild/lib/main.js')
import { execFileSync } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = `${here}/..`
const dist = join(root, 'dist')
mkdirSync(dist, { recursive: true })

const IKAROS = join(root, '..', '..', '..', '..')   // plugins/ikaros-conversation-tree → ikaros-dsh → core → E:/Ikaros
const TSC = join(IKAROS, 'core', 'ikaros-dsh', 'plugins', 'ikaros-memory', 'node_modules', 'typescript', 'bin', 'tsc')

// ── 1) Node 侧：tsc（ESM 输出，cordis 保持外部解析，与 ikaros-memory 同款） ──
try {
  execFileSync(process.execPath, [
    TSC, 'src/index.ts',
    '--outDir', 'dist',
    '--module', 'esnext',
    '--moduleResolution', 'bundler',
    '--target', 'es2022',
    '--skipLibCheck',
    '--allowSyntheticDefaultImports',
    '--declaration',
  ], { cwd: root, stdio: 'inherit' })
  console.log('[build] node side OK (dist/index.js)')
} catch (e) {
  console.error('[build] node side FAILED:', e instanceof Error ? e.message : String(e))
  process.exitCode = 1
}

// ── 2) client 侧：esbuild 打 CJS，词表 external（运行时由 __ModuleLoader__ 的 require 提供） ──
const clientExternal = [
  'react', 'react/jsx-runtime', 'react-dom', 'react-dom/client',
  '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-web-react',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-ui-attachment',
  '@deepseek-ai/dsh-client-schema-form',
]
const clientId = '@ikaros/dsh-conversation-tree'

const bodyResult = await build({
  entryPoints: [`${root}/src/client.tsx`],
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  target: 'es2020',
  jsx: 'automatic',
  external: clientExternal,
  write: false,
  logLevel: 'silent',
})
const body = bodyResult.outputFiles[0].text

// 官方 client.js 形态：factory 体内需自备 var module/exports（Loader 只注入 require 词表）
const wrapped = `window.__ModuleLoader__.load({
\tid: ${JSON.stringify(clientId)},
\tfactory: (require) => {
\t\tvar module = { exports: {} };
\t\tvar exports = module.exports;
${body}
\t\treturn module.exports;
\t}
});
`
writeFileSync(join(dist, 'client.js'), wrapped)
// 最小 d.ts（client 侧类型声明，供 exports["./client"].types 指向）
writeFileSync(join(dist, 'client.d.ts'), `export declare const inject: string[];
export declare function apply(ctx: any): void;
declare const _default: { apply: typeof apply; inject: typeof inject };
export default _default;
`)
console.log(`[build] client side OK (dist/client.js, ${body.length} bytes body)`)