// ikaros-conversation-tree 构建：Node 侧 tsc → dist/index.js；client 侧 esbuild CJS → dist/client.js
// client bundle 必须包成 window.__ModuleLoader__.load({id, factory}) 形态（dsh client-modules 协议），
// factory 内提供 var module/exports 骨架 + require 参数（Loader 注入模块词表）。
//
// 可移植构建: esbuild / typescript 优先从本插件 node_modules 解析 (devDependencies),
// 回退到 Ikaros runtime 路径 (本地开发时免重复安装). 从 git 安装时 prepare 脚本自动构建.
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)

// 解析 esbuild: 优先本插件 node_modules, 回退 Ikaros runtime
let esbuild
try {
  esbuild = require('esbuild')
} catch {
  esbuild = require('E:/Ikaros/runtime/node/node_modules/esbuild/lib/main.js')
}
const { build } = esbuild

import { execFileSync } from 'node:child_process'
import { writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = `${here}/..`
const dist = join(root, 'dist')
mkdirSync(dist, { recursive: true })

// 解析 tsc: 优先本插件 node_modules, 回退 memory 插件的 typescript
let TSC
try {
  TSC = require.resolve('typescript/bin/tsc', { paths: [root] })
} catch {
  const IKAROS = join(root, '..', '..', '..', '..')
  TSC = join(IKAROS, 'core', 'ikaros-dsh', 'plugins', 'ikaros-memory', 'node_modules', 'typescript', 'bin', 'tsc')
}

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
  '@deepseek-ai/dsh-client-ui-settings',
  '@deepseek-ai/dsh-client-locale',
  '@deepseek-ai/dsh-client-runtime',
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
