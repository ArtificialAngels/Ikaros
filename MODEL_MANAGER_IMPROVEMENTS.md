# Hermes 模型管理改进说明

## 问题分析

### 原始问题
1. **模型显示不完整**：只显示3B、7B、35B，没有1.8B
2. **35B显示为外部链接**：与local的35B重复
3. **其他模型无法运行**：对话显示找不到模型
4. **GPU加速未正常运行**

### 根本原因
1. **llama-server 一次只能加载一个模型**：Open WebUI 显示的是当前运行的模型
2. **bootstrap 脚本硬编码**：只添加固定的 `qwen2.5-7b-instruct`，不检测当前模型
3. **Open WebUI 缓存**：模型列表在启动时缓存，切换模型后不更新
4. **GPU 检测不完善**：缺少智能的 NGL 计算和 GPU 信息展示

---

## 改进方案

### 1. 模型管理器 (model_manager.py)

类似 ComfyUI-aki-v3 的启动器，提供完整的模型管理功能：

```bash
# 列出所有模型及其 GPU 配置
bin\model-manager.bat list

# 切换模型（自动重启 llama-server）
bin\model-manager.bat switch Qwen1.5-1.8B-Chat-Q4_K_M.gguf

# 清理 Open WebUI 中的无效模型
bin\model-manager.bat clean

# 显示 GPU 信息
bin\model-manager.bat gpu
```

**功能特点：**
- 自动扫描 `data/models/` 中的所有 GGUF 模型
- 显示每个模型的大小、参数量、推荐显存
- 智能计算 NGL (GPU layers)
- 一键切换模型（自动重启 llama-server）
- 自动清理 Open WebUI 中的旧模型
- 自动添加当前模型到 Open WebUI

### 2. GPU 检测器 (gpu_detector.py)

提供专业的 GPU 检测和配置：

```bash
# 显示 GPU 状态
bin\gpu-detect.bat status

# 输出 JSON 格式
bin\gpu-detect.bat json

# 显示所有模型的 NGL 配置
bin\gpu-detect.bat models

# 计算指定模型的 NGL
bin\gpu-detect.bat ngl Qwen2.5-7B-Instruct-Q4_K_M.gguf
```

**智能 NGL 计算策略（类似 ComfyUI-aki-v3）：**
- `model < vram*0.7` → NGL=99 (全部 GPU)
- `model < vram*1.2` → NGL=99 (全部 GPU + KV cache)
- `model > vram*3` → NGL=0 (全部 CPU)
- 其他 → 部分卸载

### 3. Bootstrap 脚本改进

修复了 `bootstrap_openwebui.py`：

- 自动检测当前运行的模型
- 清理 Open WebUI 中的旧模型
- 只添加当前运行的模型
- 避免模型重复和"外部链接"问题

---

## 使用方法

### 快速开始

1. **查看所有模型**
   ```bash
   bin\model-manager.bat list
   ```

   输出示例：
   ```
   ============================================================
     Hermes Model List
   ============================================================

   GPU: NVIDIA GeForce RTX 3070 Laptop GPU
   Free VRAM: 6.25 GB / 8.00 GB

   Model directory: E:\Hermes Agent\data\models
   Found 4 models:

   [GPU] [1] Qwen1.5-1.8B-Chat-Q4_K_M.gguf
       Size: 1.13 GB  |  Params: 1.8B  |  VRAM req: 2-4 GB
       NGL: 99 (GPU accelerated)

   [GPU] [2] Qwen2.5-3B-Instruct-Q4_K_M.gguf
       Size: 1.96 GB  |  Params: 3B  |  VRAM req: 4-6 GB
       NGL: 99 (GPU accelerated)

   [GPU] [3] Qwen2.5-7B-Instruct-Q4_K_M.gguf
       Size: 4.36 GB  |  Params: 7B  |  VRAM req: 8-12 GB
       NGL: 99 (GPU accelerated)

   [CPU] [4] Qwen3.5-35B-A3B-Q4_K_M.gguf
       Size: 20.50 GB  |  Params: 35B  |  VRAM req: 20-24 GB
       NGL: 0 (CPU mode)
   ```

2. **切换模型**
   ```bash
   bin\model-manager.bat switch Qwen1.5-1.8B-Chat-Q4_K_M.gguf
   ```

3. **查看 GPU 状态**
   ```bash
   bin\gpu-detect.bat status
   ```

### 清理旧的模型记录

如果 Open WebUI 中有重复或无效的模型：

```bash
# 删除 Open WebUI 数据库
del hermes\data\openwebui\webui.db

# 重新启动
bin\hermes-all.bat
```

---

## 模型推荐

根据你的 GPU (RTX 3070 Laptop, 8GB VRAM)：

| 模型 | 大小 | 推荐场景 | NGL |
|------|------|----------|-----|
| **Qwen1.5-1.8B** | 1.13 GB | 快速响应、低配置设备 | 99 (GPU) |
| **Qwen2.5-3B** | 1.96 GB | 日常使用、平衡性能 | 99 (GPU) |
| **Qwen2.5-7B** | 4.36 GB | 更好的推理能力 | 99 (GPU) |
| **Qwen3.5-35B** | 20.50 GB | 最强性能（需要更多显存） | 0 (CPU) |

**推荐：**
- 日常使用：**Qwen2.5-3B** 或 **Qwen2.5-7B**
- 快速测试：**Qwen1.5-1.8B**
- 复杂任务：**Qwen3.5-35B** (CPU 模式，速度较慢)

---

## 技术细节

### GPU 加速工作原理

1. **NGL (n-gpu-layers)**：指定多少层模型加载到 GPU
   - NGL=99：全部层加载到 GPU（最快）
   - NGL=0：全部层在 CPU（最慢）
   - NGL=N：前 N 层在 GPU，其余在 CPU（混合）

2. **智能计算**：
   ```python
   if model_mb <= vram_free * 0.7:
       ngl = 99  # 全部 GPU
   elif model_mb <= vram_free * 1.2:
       ngl = 99  # 全部 GPU + KV cache
   elif model_mb > vram_free * 3:
       ngl = 0   # 全部 CPU
   else:
       ngl = int(vram_free * 0.7 / (model_mb / 80))
   ```

3. **二进制选择**：
   - `llama-server-cuda-12.4.exe` (CUDA 12.4)
   - `llama-server-cuda-11.8.exe` (CUDA 11.8)
   - `llama-server-cuda.exe` (通用 CUDA)
   - `llama-server-vulkan.exe` (Vulkan)
   - `llama-server.exe` (CPU)

### Open WebUI 模型管理

1. **模型列表缓存**：Open WebUI 在启动时从 llama-server 获取模型列表
2. **模型切换**：需要重启 Open WebUI 或使用 API 更新
3. **模型重复**：bootstrap 脚本现在会自动清理旧模型

---

## 与 ComfyUI-aki-v3 的对比

| 功能 | ComfyUI-aki-v3 | Hermes (改进后) |
|------|----------------|-----------------|
| 启动器 | 绘世启动器.exe | model-manager.bat |
| GPU 检测 | 自动 | 自动 (gpu_detector.py) |
| 模型切换 | GUI | CLI (类似) |
| NGL 计算 | 自动 | 自动 (智能) |
| 模型列表 | GUI | CLI (详细) |
| 便携环境 | Python + Git | Python + llama-server |

---

## 故障排查

### 问题 1：模型切换后 Open WebUI 仍显示旧模型

**解决方案：**
```bash
# 清理模型列表
bin\model-manager.bat clean

# 或删除数据库
del hermes\data\openwebui\webui.db

# 重新启动
bin\hermes-all.bat
```

### 问题 2：GPU 加速未生效

**检查步骤：**
```bash
# 1. 检查 GPU
bin\gpu-detect.bat status

# 2. 检查 NGL
bin\gpu-detect.bat models

# 3. 查看日志
type hermes\data\logs\llm-server.err
```

### 问题 3：模型无法加载

**可能原因：**
1. 模型文件损坏
2. 显存不足
3. llama-server 二进制不匹配

**解决方案：**
```bash
# 1. 检查模型文件
dir data\models\*.gguf

# 2. 切换到更小的模型
bin\model-manager.bat switch Qwen1.5-1.8B-Chat-Q4_K_M.gguf

# 3. 检查日志
type hermes\data\logs\llm-server.err
```

---

## 文件清单

新增文件：
- `hermes/scripts/model_manager.py` - 模型管理器
- `hermes/scripts/gpu_detector.py` - GPU 检测器
- `bin/model-manager.bat` - 模型管理启动脚本
- `bin/gpu-detect.bat` - GPU 检测启动脚本

修改文件：
- `hermes/scripts/bootstrap_openwebui.py` - 修复模型添加逻辑

---

## 总结

通过学习 ComfyUI-aki-v3 的设计理念，我们实现了：

1. ✅ **完整的模型管理**：类似 ComfyUI 启动器的模型切换功能
2. ✅ **智能 GPU 加速**：自动计算 NGL，优化性能
3. ✅ **修复模型显示问题**：清理重复模型，正确显示当前模型
4. ✅ **改进用户体验**：清晰的命令行界面，详细的模型信息

现在你可以轻松管理所有模型，GPU 加速也能正常工作了！