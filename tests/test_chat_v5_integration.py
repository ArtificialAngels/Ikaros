"""chat 接入 Ikaros V5 的隔离测试 (不触发真实 LLM / V5 检索后端).

验证 server.py 新增的:
  - build_ikaros_persona(): 从 axiom/SOUL/self_model 组装人格 (fail-open).
  - build_v5_memory_block(): 树域语义检索包装 (后端不可用时返回空串, 不崩).
  - build_chat_messages_v5(): 人格 + 树感知压缩 + 记忆 组装成 messages (fail-open 回退).
"""

import sys
import types
import importlib.util
from pathlib import Path

# taskbus 在便携 venv 不可用 -> mock, 不影响被测函数
_tb = types.ModuleType("taskbus")
_tb.EventBus = object
_tb.exec_state_event = object
sys.modules["taskbus"] = _tb

# 盘符无关: 从脚本位置推导 core/ (tests/ 在项目根下 -> parents[1] 即根)
_CORE = Path(__file__).resolve().parents[1] / "core"
sys.path.insert(0, str(_CORE))

spec = importlib.util.spec_from_file_location(
    "ct_server", str(_CORE / "conversation-tree" / "server.py")
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

from memory_v5.conversation_tree import ConversationTree, MemoryRetriever


class FakeStore:
    def __init__(self):
        self.records = {}
        self.next_id = 1

    def store(self, content, type="conversation", tags=""):
        mid = self.next_id
        self.next_id += 1
        self.records[mid] = (content, type, tags)
        return mid

    def load(self, ids):
        return {mid: self.records[mid][0] for mid in ids if mid in self.records}


def _setup():
    fake = FakeStore()
    tree = ConversationTree(_store=fake.store, _load=fake.load)
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn(
        [{"role": "user", "content": "more"}, {"role": "assistant", "content": "ok"}],
        branch_label="ml",
    )
    retriever = MemoryRetriever(tree)
    server._tree = tree
    server._retriever = retriever
    return tree, child


def test_persona():
    p = server.build_ikaros_persona()
    assert isinstance(p, str) and len(p) > 20, "persona 不应为空"
    # axiom 核心指令必含
    assert "伊卡洛斯" in p or "哥哥" in p, p
    # 树模式说明必含
    assert "树" in p, p


def test_memory_block_fail_open():
    tree, child = _setup()
    # 无 V5 检索后端 -> 必须返回 str 且不崩 (fail-open)
    mb = server.build_v5_memory_block(child.id, "test query")
    assert isinstance(mb, str)


def test_chat_messages_v5():
    tree, child = _setup()
    msgs = server.build_chat_messages_v5(child.id, "hello?")
    assert isinstance(msgs, list) and len(msgs) >= 2
    assert msgs[0]["role"] == "system", msgs[0]
    assert msgs[-1] == {"role": "user", "content": "hello?"}
    # system 必须包含人格文本 (伊卡洛斯/哥哥)
    assert "伊卡洛斯" in msgs[0]["content"] or "哥哥" in msgs[0]["content"], msgs[0]["content"]
    # 必须包含历史 (assistant 'ok' 出现在某条消息里)
    joined = " ".join(m.get("content", "") for m in msgs)
    assert "ok" in joined, "树历史应进入上下文"


def test_fallback_when_tree_none():
    # _tree 为 None 时 build_v5_memory_block 应安全返回空
    server._tree = None
    assert server.build_v5_memory_block(None, "q") == ""
    server._tree = None
    # build_chat_messages_v5 在 _tree None 时走内部 try 仍应产出 system + user
    msgs = server.build_chat_messages_v5(None, "hi")
    assert msgs[-1] == {"role": "user", "content": "hi"}
    assert msgs[0]["role"] == "system"


if __name__ == "__main__":
    test_persona()
    test_memory_block_fail_open()
    _setup()  # re-set _tree for chat test
    test_chat_messages_v5()
    test_fallback_when_tree_none()
    print("ALL CHAT V5 INTEGRATION TESTS PASSED")
