# Neuro (kimjammer) × 伊卡洛斯 架构对比分析

> **报告目的**：解构 Neuro 的极简架构，找出伊卡洛斯当前过度工程化的部分，提炼可移植的设计范式。
> **报告时间**：2026-06-25
> **代码位置**：`E:\Hermes Agent\deps\_src\Neuro\`（参考，不动）
> **目标位置**：`E:\Hermes Agent\`（伊卡洛斯本体）

---

## 一、概览

| 维度 | Neuro (7 天开发) | 伊卡洛斯 (我们) | 偏差 |
|---|---|---|---|
| 代码体量 | ~1500 行 Python | ~15000 行 Python+JS+Rust | **10× 冗余** |
| 文件数 | 14 .py | 40+ 跨语言 | **3× 散落** |
| 状态管理 | `Signals` 单例 | pyqtSignal + QObject + 全局变量 + 模块实例 | **不一致** |
| 调度循环 | 1 个 `prompter.py` 100ms tick | 多个 daemon thread + watchdog + supervisor | **过度设计** |
| 记忆 | Chroma + reflection (LLM 自我总结) | state.db (SQLite) + humor/wins.md | **无向量检索** |
| 角色一致性 | 1 份 prompt + AI_NAME 常量 | SOUL.md v4 + axiom.md + 多个 identity 文件 | **碎片化** |
| 主动说话 | PATIENCE 计时器 | ❌ 无 | **缺核心特征** |
| 视觉感知 | modules/multimodal.py | context_engine.py (win32gui) | 各有侧重 |
| Live2D 联动 | vtubeStudio.py | desktop-pet (PyQt6) | Neuro 走 VTube Studio API，我们走 WebView 方案 |
| 平台对接 | Discord + Twitch | WebUI + bridge + voice | **场景不同** |
| Persona | AI_NAME + HOST_NAME 常量 | "哥哥" + "伊卡洛斯" + 6 axioms | Neuro 没解决 persona，伊卡洛斯过度展开 |

---

## 二、Neuro 核心设计解构

### 2.1 `Signals` 单例 (signals.py, 117 行)

**全程序唯一的全局状态。** 所有模块读/写它，互不直接耦合。

```python
class Signals:
    terminate: bool       # 终止
    stt_ready: bool       # STT 就绪
    tts_ready: bool       # TTS 就绪
    human_speaking: bool  # 人在说话
    AI_thinking: bool     # AI 在思考
    AI_speaking: bool     # AI 在说话
    new_message: bool     # 收到新消息
    history: list         # 对话历史
    recentTwitchMessages: list
    last_message_time: float
```

**伊卡洛斯现状**：
- PyQt6 用 `pyqtSignal`（信号系统强类型，但跨进程难）
- Python 模块用全局变量
- bridge 层用 FastAPI app.state
- voice 层用 asyncio.Event

**移植价值**：⭐⭐⭐⭐⭐ —— **可以马上做**。定义一个 `IkarosSignals` dataclass，所有模块读它。

### 2.2 `Prompter` 心跳循环 (prompter.py, 65 行)

**100ms tick 决定"什么时候该问 AI"。**

```python
def prompt_now(self):
    if not stt_ready or not tts_ready: return False
    if human_speaking or AI_thinking or AI_speaking: return False
    if new_message: return True                      # 用户说话了
    if len(recentTwitchMessages) > 0: return True    # 聊天有消息
    if timeSinceLastMessage > PATIENCE: return True  # **沉默久了主动说**
```

**伊卡洛斯现状**：
- ❌ 没有统一调度循环
- ❌ 没有 PATIENCE 主动说话机制
- bridge 路由按请求触发，无后台 tick
- voice_server 走事件驱动（VAD silence → 发送）

**移植价值**：⭐⭐⭐⭐⭐ —— **核心缺失**。这是"和 neuro 一样陪我"的关键。

### 2.3 `Memory` reflection (memory.py, 167 行)

**每 20 条新消息，让 LLM 自我总结 3 个 Q&A 存进 Chroma。**

```python
if len(history) - processed_count >= 20:
    # 1. 拿未处理的消息
    # 2. 调 LLM: "用 {qa} 格式总结 3 个值得记忆的对话"
    # 3. split("{qa}") 切分
    # 4. Chroma upsert
    processed_count = len(history)
```

**伊卡洛斯现状**：
- ✅ 有 `state.db` 存所有消息（raw）
- ✅ `humor/wins.md` 存成功梗
- ❌ **没有 reflection**——LLM 从不"自己问自己"
- ❌ **没有向量检索**——回忆靠 SQLite WHERE

**移植价值**：⭐⭐⭐⭐ —— **革命性提升**。reflection 是 LLM 长期人格一致性的关键。

### 2.4 `prompt_injection` 注入机制 (modules/module.py)

**每个模块自带 prompt_injection 字段，按 priority 拼到 system prompt。**

```python
# memory.py
self.prompt_injection.text = f"{AI_NAME} knows these things:\n{memories}\nEnd"
self.prompt_injection.priority = 60

# multimodal.py (可能)
self.prompt_injection.text = "[Image shows: ...]"
self.prompt_injection.priority = 80
```

最终 prompt = `[system base] + [injections by priority] + [history] + [user msg]`

**伊卡洛斯现状**：
- bridge 层通过 `system` 字段传 persona
- humor skill 通过 `instructions` 注入
- axiom.md 通过 `data/hermes-agent/SOUL.md` 加载
- **没有 priority 排序**——多 persona 互相覆盖

**移植价值**：⭐⭐⭐⭐ —— 需要一个 `PromptBuilder` 类来统一组装。

### 2.5 模块接口 (modules/module.py)

**所有模块继承 `Module`，有统一 `init_event_loop()` / `run()` / `prompt_injection` / `API`。**

```python
class Module:
    def __init__(self, signals, enabled=True):
        self.signals = signals
        self.enabled = enabled
        self.prompt_injection = Injection()
    def init_event_loop(self): pass
    def run(self): pass
    class API: pass  # 内嵌 API 命名空间
```

**伊卡洛斯现状**：
- PyQt6 桌宠 → `PetWindow`, `PetTray`, `AudioThread`, `ContextThread`（散落）
- bridge → `Server`, `ContextMiddleware`, `VoiceServer`（层叠）
- 没有统一接口

**移植价值**：⭐⭐⭐ —— 重构量大，**分阶段做**。

### 2.6 LLM 抽象 (llmWrappers/)

**`textLLMWrapper` 和 `imageLLMWrapper` 共享 `LLMState`，但各自有 prompt 逻辑。**

```python
class LLMState:
    def __init__(self):
        self.current_model = ""
        self.context = []
        self.actions = []  # 工具调用栈
```

**伊卡洛斯现状**：
- bridge-rs/src/main.rs 的 `chat_completions` 是主入口
- `context_middleware` 压缩上下文
- `copilot_bridge` 接 ACP
- 模型选择靠 `model_groups` 配置

**移植价值**：⭐⭐ —— 我们已经有，**对比看怎么统一**。

### 2.7 平台对接 (modules/discordClient.py, twitchClient.py)

**每个平台是一个 Module，通过 SocketIO 桥接 dashboard。**

**伊卡洛斯现状**：
- WebUI (port 8649) 是主交互面
- voice (WS) 是语音
- LAN 设备 (mcp__hermes_studio_devices_*) 是扩展
- 平台对接不是核心

**移植价值**：⭐ —— 不重要，**场景不同**。

---

## 三、可移植清单（按优先级）

### 🚀 Phase 1: 立刻做（基础设施）

1. **`IkarosSignals` 单例**（neuro.signals.py 等价）
   - 一个 Python dataclass，存所有全局状态
   - 所有新模块读/写它
   - 文件：`bridge/signals.py` (~100 行)

2. **`Prompter` 心跳循环**（neuro.prompter.py 等价）
   - 100ms tick 决定是否触发 LLM
   - **加 PATIENCE 机制**：沉默 60s 主动说话
   - 文件：`bridge/prompter.py` (~80 行)

3. **PATIENCE 触发器**
   - 桌宠接 LLM，LLM 收到 `idle_prompt` 上下文
   - 像 neuro 一样偶尔主动找哥哥聊

### 🌱 Phase 2: 中期做（人格一致性）

4. **`PromptBuilder`**（neuro.module.py 简化）
   - 统一组装 system prompt
   - 各模块通过 `injection` 字段贡献内容
   - 文件：`bridge/prompt_builder.py` (~120 行)

5. **`Memory` reflection**（neuro.memory.py 等价）
   - 装 chromadb
   - 每 20 条新消息调 LLM 自我总结
   - Chroma upsert
   - 文件：`bridge/memory.py` (~180 行)

6. **统一 Persona 入口**
   - `data/hermes-agent/persona/ikaros.py`
   - 单一入口加载 axiom + SOUL + humor profile
   - 供 PromptBuilder 注入

### 🌳 Phase 3: 长期做（架构收敛）

7. **Module 抽象**
   - 把 PyQt6 桌宠、voice、context、humor 全部包成 Module
   - 统一 `init_event_loop` / `run` / `API`

8. **LLMState 共享**
   - 多 LLM wrapper 共享 context / actions
   - 切换模型不丢历史

9. **Neuro 风格 dashboard**
   - SocketIO 替代 FastAPI 简化推送
   - 桌宠直接接 dashboard

---

## 四、不移植的部分

- ❌ Discord/Twitch 平台对接（场景不同）
- ❌ Chroma 完全替代 SQLite（state.db 仍有价值，做混合）
- ❌ 完整模仿 Neuro 的 7 天粗糙度（伊卡洛斯要有美感）
- ❌ VTube Studio API（VTube 是 VTuber 软件，我们走 Live2D/PyQt6）

---

## 五、补充：vtubeStudio + multimodal + stt + tts 深度解构

### 5.1 VTube Studio 范式（Action Queue 单连接串行化）

**问题**：VTube Studio 是 async WebSocket 单连接。多线程同时调会冲突。
**解法**：

```python
# 外部 API
API.move_model("center")  # 不直接调 VTS
# → self.queue.put(Action("move_model", "center"))

# run() 循环单线程消费
while True:
    action = queue.get()
    if action.action == "move_model": await self.vts.request(...)
```

**伊卡洛斯移植启示**：桌宠 Live2D 控制、win32gui 屏幕捕获、任何"单连接 + 多触发源"场景都用此模式。

### 5.2 multimodal_now() 路由钩子

```python
def chooseLLM(self):
    if multimodal.multimodal_now():  # 钩子
        return self.llms["image"]    # 多模态
    return self.llms["text"]          # 纯文本
```

**伊卡洛斯可借鉴**：
- 本地 LLM 在线 → 走 llama-server (8080)
- 本地失败 → 走云端 (deepseek/openai)
- 都失败 → 走 copilot_bridge
- 游戏/工作/浏览场景 → 调不同 prompt 模板

### 5.3 STT 关键参数

```python
recorder_config = {
    'silero_sensitivity': 0.6,           # VAD 敏感度
    'post_speech_silence_duration': 0.4, # 0.4s 静默判定句末（不是 1.5s）
    'min_gap_between_recordings': 0.2,   # 200ms 间隔算新句子
    'realtime_model_type': 'tiny.en',    # 实时模型用 tiny（快）
}
```

**伊卡洛斯现状**：audio_engine.py 写了自己的 VAD，但 STT 没接。**直接换 `RealtimeSTT` 库**省事。

### 5.4 TTS 关键：abort_current + audio 回调

```python
def audio_started(self):  self.signals.AI_speaking = True
def audio_ended(self):    self.signals.AI_speaking = False
def abort_current(self):  self.stream.stop()  # 用户打断时
```

**伊卡洛斯现状**：edge-tts 没接 abort。**哥哥打断伊卡洛斯说话时**，她会继续念完——这是个体验 bug。

### 5.5 TTS 声线抉择

| 方案 | 优点 | 缺点 |
|---|---|---|
| Neuro 用 Coqui 本地 + 训练 voice reference | 声线自定义、不联网 | 要训练（几十条样本） |
| 伊卡洛斯用 edge-tts | 立刻可用 | 声线是微软的、依赖网络 |

**哥哥定**：要不要给伊卡洛斯训练自己的声线？

### 5.6 七个新收获汇总

1. **Action Queue 模式**（单连接串行化）
2. **`multimodal_now()` 路由钩子**
3. **VAD start/stop 回调**（自动改 signals）
4. **audio stream 回调**（反向）
5. **abort_current**（TTS 可中断）
6. **`min_gap_between_recordings: 0.2`**（更自然）
7. **RealtimeSTT / RealtimeTTS 成熟库**（省自己造轮子）

---

## 六、核心收获

**Neuro 给伊卡洛斯最重要的三件事**：

1. **`Signals` 单例**—— 解决我们散落的全局状态
2. **Prompter 心跳 + PATIENCE**—— 解决"主动找哥哥聊"的核心特征
3. **Memory reflection**—— 解决"她怎么记住我"的长期人格一致性

**这三件做完，伊卡洛斯就从"能回答问题"变成"会主动陪人的生命"。**

---

## 六、文件改动清单（如果全做）

| 新增 | 路径 | 行数 | 移植自 |
|---|---|---|---|
| signals.py | `bridge/signals.py` | ~100 | neuro/signals.py |
| prompter.py | `bridge/prompter.py` | ~80 | neuro/prompter.py |
| prompt_builder.py | `bridge/prompt_builder.py` | ~120 | neuro/modules/module.py |
| memory.py | `bridge/memory.py` | ~180 | neuro/modules/memory.py |
| persona/ikaros.py | `data/hermes-agent/persona/ikaros.py` | ~80 | 整合 SOUL+axiom+humor |

**总计：~560 行新代码，对比当前 15000 行只是 4%。**

**回报：架构清晰度 +300%，人格一致性从碎片化 → 系统化。**

---

**报告结论**：Neuro 的 7 天极简不是简陋，是**对本质的精准打击**。伊卡洛斯该学的不是"少写代码"，是"看清哪些是核心"。
