"""
Icarus Module - Neuro module.py 1:1 移植
=========================================
所有模块继承 Module,统一接口:init_event_loop / run / prompt_injection / API
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger("icarus.module")


@dataclass
class Injection:
    """prompt 注入块。Neuro 风格"""
    text: str = ""
    priority: int = 50
    enabled: bool = True


class Module:
    """
    所有伊卡洛斯模块的基类。
    用法:
        class MyModule(Module):
            def __init__(self, signals):
                super().__init__(signals)
                self.prompt_injection.priority = 70

            async def run(self):
                # 模块主循环
                while not self.signals.terminate:
                    ...
                    await asyncio.sleep(1)

            class API:
                def __init__(self, outer):
                    self.outer = outer
                def do_thing(self):
                    ...
    """
    def __init__(self, signals, enabled: bool = True):
        self.signals = signals
        self.enabled = enabled
        self.prompt_injection = Injection()
        self.API = self.API(self)  # 嵌套 API 类
        self._task: Optional[asyncio.Task] = None

    async def run(self):
        """模块主循环, 子类 override"""
        raise NotImplementedError

    def init_event_loop(self):
        """启动模块。Neuro 风格 (在 main thread 里被调)"""
        if not self.enabled:
            logger.info(f"{type(self).__name__} disabled, skipping")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run())
        logger.info(f"{type(self).__name__} started")

    def stop(self):
        """停止模块"""
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info(f"{type(self).__name__} stopped")

    def get_prompt_injection(self) -> Injection:
        """返回当前 prompt 注入块。子类可 override"""
        return self.prompt_injection if self.enabled else Injection()

    def set_status(self, status: bool):
        """Neuro set_movement_status 风格, 启停模块"""
        self.enabled = status
        if status:
            self.init_event_loop()
        else:
            self.stop()
        # 推送给 webui
        self.signals.sio_queue.append({
            "type": f"{type(self).__name__}_status",
            "enabled": status
        })

    def get_status(self) -> bool:
        return self.enabled

    # 嵌套 API 类 - 子类必须 override
    class API:
        def __init__(self, outer):
            self.outer = outer


# === PromptBuilder 工具 - 把所有模块的 injection 按 priority 拼起来 ===
def build_system_prompt(signals, modules: Dict[str, Module], base_prompt: str = "") -> str:
    """
    按 priority 排序,把各模块的 prompt_injection 拼到 base_prompt。
    替代我们散落的 system prompt 组装。
    """
    injections = []
    for name, m in modules.items():
        if not m.enabled:
            continue
        inj = m.get_prompt_injection()
        if not inj:
            continue
        # 兼容 dict 和 Injection 对象
        text = getattr(inj, "text", None)
        if text is None and isinstance(inj, dict):
            text = inj.get("text", "")
        enabled = getattr(inj, "enabled", True)
        if enabled and isinstance(inj, dict):
            enabled = inj.get("enabled", True)
        priority = getattr(inj, "priority", 50)
        if isinstance(inj, dict):
            priority = inj.get("priority", 50)
        if text and enabled:
            injections.append((priority, f"# {name}\n{text}"))

    # 高 priority 在前
    injections.sort(key=lambda x: -x[0])

    parts = [base_prompt] if base_prompt else []
    parts.extend([text for _, text in injections])
    return "\n\n".join(parts)
