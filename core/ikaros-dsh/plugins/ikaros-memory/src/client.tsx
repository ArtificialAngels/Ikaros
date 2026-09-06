// ikaros-memory —— client 侧
// 合并自 ikaros-memory-settings: dsh 设置面板里的记忆控制卡 (embedding 模型管理)
import { apply as applyMemorySettingsClient, inject as memorySettingsInject } from './settings/client'

const inject = [...memorySettingsInject]

function apply(ctx: any) {
  applyMemorySettingsClient(ctx)
}

export { apply, inject }
export default { apply, inject }
