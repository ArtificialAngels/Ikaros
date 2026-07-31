import sys, tempfile
sys.path.insert(0, "E:/Ikaros/core")
from memory_v5.conversation_tree import ConversationTree

class FakeStore:
    def __init__(self):
        self.records = {}
        self.next_id = 1
    def store(self, content, type="conversation", tags=""):
        mid = self.next_id; self.next_id += 1
        self.records[mid] = (content, type, tags)
        return mid
    def load(self, ids):
        return {mid: self.records[mid][0] for mid in ids if mid in self.records}

def new_tree():
    fake = FakeStore()
    tmp = tempfile.mkdtemp(prefix="tree_agent_")
    return fake, ConversationTree(persist_key="t", data_dir=tmp, _store=fake.store, _load=fake.load)

def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond: raise SystemExit("FAILED: " + name)

# --- 旧 A1-A3 (不带 cascade, 默认 False) ---
fake, tree = new_tree()
root = tree.init(seed_messages=[{"role":"user","content":"hi"}])
child = tree.add_turn([{"role":"user","content":"more"}])
tree.set_agent(child.id, "hermes")
check("A1 writes hermes", tree.nodes[child.id].agent == "hermes")
raw = tree.serialize()
t2 = ConversationTree.deserialize(raw, persist_key="t", data_dir=tree.data_dir)
check("A1 persists across reload", t2.nodes[child.id].agent == "hermes")

fake, tree = new_tree()
root = tree.init(seed_messages=[{"role":"user","content":"hi"}])
child = tree.add_turn([{"role":"user","content":"more"}])
tree.set_agent(child.id, "bogus-agent")
check("A2 invalid -> ikaros", tree.nodes[child.id].agent == "ikaros")
tree.set_agent(child.id, "  HERMES ")
check("A2 normalize HERMES", tree.nodes[child.id].agent == "hermes")

fake, tree = new_tree()
tree.init(seed_messages=[{"role":"user","content":"hi"}])
try:
    tree.set_agent("nope", "hermes")
    check("A3 missing raises", False)
except ValueError:
    check("A3 missing raises", True)

# --- 新增: 新建子节点继承父 agent ---
fake, tree = new_tree()
root = tree.init(seed_messages=[{"role":"user","content":"hi"}])
tree.set_agent(root.id, "hermes", cascade=False)
child = tree.add_turn([{"role":"user","content":"x"}])
check("add_turn inherits parent.agent", child.agent == "hermes")
# fork 也继承
fb = tree.fork_branch(fork_point_id=root.id, branch_label="b", messages=[{"role":"user","content":"y"}])
check("fork_branch inherits parent.agent", fb.agent == "hermes")

# --- 新增: cascade 同步已存在子树 ---
fake, tree = new_tree()
root = tree.init(seed_messages=[{"role":"user","content":"hi"}])
c1 = tree.add_turn([{"role":"user","content":"a"}])      # root child, 此时 root 默认 ikaros
c2 = tree.add_turn([{"role":"user","content":"b"}])      # c1 child
check("children created before set = ikaros", c1.agent == "ikaros" and c2.agent == "ikaros")

# 非级联: 改 root 不影响已存子树
tree.set_agent(root.id, "hermes", cascade=False)
check("root hermes (cascade=False)", tree.nodes[root.id].agent == "hermes")
check("c1 unchanged (cascade=False)", c1.agent == "ikaros")
check("c2 unchanged (cascade=False)", c2.agent == "ikaros")

# 级联: 改 c1 -> hermes, 其子树 c2 同步
tree.set_agent(c1.id, "hermes", cascade=True)
check("c1 cascade -> hermes", c1.agent == "hermes")
check("c2 cascade -> hermes", c2.agent == "hermes")
check("root unaffected by child cascade", tree.nodes[root.id].agent == "hermes")

# 级联: 改 root -> ikaros, 整棵子树同步
tree.set_agent(root.id, "ikaros", cascade=True)
check("root cascade -> ikaros", tree.nodes[root.id].agent == "ikaros")
check("c1 cascade -> ikaros", c1.agent == "ikaros")
check("c2 cascade -> ikaros", c2.agent == "ikaros")

print("ALL AGENT INHERIT/CASCADE TESTS PASSED")
