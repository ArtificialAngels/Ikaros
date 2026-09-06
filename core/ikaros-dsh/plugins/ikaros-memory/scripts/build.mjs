// ikaros-memory 构建：Node 侧 tsc → dist/index.js；client 侧 esbuild CJS → dist/client.js
// client bundle 必须包成 window.__ModuleLoader__.load({id, factory}) 形态（dsh client-modules 协议）。
//
// 可移植构建: esbuild / typescript 优先从本插件 node_modules 解析 (devDependencies),
// 回退到 Ikaros runtime 路径. 从 git 安装时 prepare 脚本自动构建.
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
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = `${here}/..`
const dist = join(root, 'dist')
mkdirSync(dist, { recursive: true })

// 解析 tsc: 优先本插件 node_modules
let TSC
try {
  TSC = require.resolve('typescript/bin/tsc', { paths: [root] })
} catch {
  TSC = join(root, 'node_modules', 'typescript', 'bin', 'tsc')
}

// ── 1) Node 侧：tsc（ESM 输出） ──
try {
  execFileSync(process.execPath, [
    TSC, '--project', 'tsconfig.build.json',
  ], { cwd: root, stdio: 'inherit' })
  console.log('[build] node side OK (dist/index.js)')
} catch (e) {
  console.error('[build] node side FAILED:', e instanceof Error ? e.message : String(e))
  process.exitCode = 1
}

// ── 2) client 侧：esbuild 打 CJS ──
const clientExternal = [
  'react', 'react/jsx-runtime', 'react-dom', 'react-dom/client',
  '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-ui-settings',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-locale',
  '@deepseek-ai/dsh-client-runtime',
]
const clientId = '@ikaros/dsh-ikaros-memory'

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
writeFileSync(join(dist, 'client.d.ts'), `export declare const inject: string[];
export declare function apply(ctx: any): void;
declare const _default: { apply: typeof apply; inject: typeof inject };
export default _default;
`)
console.log(`[build] client side OK (dist/client.js, ${body.length} bytes body)`)
