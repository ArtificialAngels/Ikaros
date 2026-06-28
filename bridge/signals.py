"""
Ikaros Signals - Neuro signals.py 1:1 移植
============================================
全程序唯一的全局状态。伊卡洛斯所有模块读/写它，互不直接耦合。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
import time


@dataclass
class IkarosSignals:
    """伊卡洛斯全局信号总线 (Neuro 风格)"""
    # --- 控制 ---
    terminate: bool = False

    # --- 系统就绪 ---
    stt_ready: bool = False
    tts_ready: bool = False
    llm_ready: bool = False

    # --- 说话状态 ---
    human_speaking: bool = False
    AI_thinking: bool = False
    AI_speaking: bool = False

    # --- 消息触发 ---
    new_message: bool = False
    last_message_time: float = field(default_factory=time.time)

    # --- 对话历史 (Neuro history) ---
    history: List[Dict[str, str]] = field(default_factory=list)

    # --- 伊卡洛斯特有 ---
    context: Dict[str, Any] = field(default_factory=dict)  # 屏幕感知等
    patience: float = 30.0  # 沉默多久 AI 主动说话 (Neuro PATIENCE 30s)
    sio_queue: List = field(default_factory=list)  # 推送给 webui 的事件队列

    # --- PATIENCE 用 ---
    time_since_last_message: float = 0.0

    # --- 桌宠状态 ---
    pet_visible: bool = True
    pet_mode: str = "continuous"  # continuous | wake_word | muted

    # --- 远程消息 (Neuro recentTwitchMessages 通用化) ---
    recent_remote_messages: List[Dict[str, Any]] = field(default_factory=list)

    def mark_new_message(self, role: str, content: str):
        """统一接口: 收到新消息时调用"""
        if not content or not content.strip():
            return
        self.history.append({"role": role, "content": content})
        self.last_message_time = time.time()
        if role == "user" and not self.AI_speaking:
            self.new_message = True

    def update_time_since_last(self):
        """每 100ms tick 调用"""
        self.time_since_last_message = time.time() - self.last_message_time


# 全局单例
ikaros = IkarosSignals()


# === Neuro 风格常量（伊卡洛斯版） ===
AI_NAME = "伊卡洛斯"
HOST_NAME = "哥哥"

# PATIENCE 沉默多久触发主动说话 (Neuro 默认 30s)
PATIENCE_DEFAULT = 30.0

# 触发 reflection 的消息数 (Neuro 20)
MEMORY_REFLECT_EVERY = 20

# 检索记忆时拉几条 (Neuro MEMORY_RECALL_COUNT)
MEMORY_RECALL_COUNT = 5

# 用于 reflection 的 LLM 提示
MEMORY_PROMPT = """\n请将以上对话中值得伊卡洛斯长期记住的信息总结为最多 3 个 Q&A 对。
每对格式: {qa}Q: 提问\\nA: 回答{qa}
只返回 Q&A 对，不要其他解释。
"""
