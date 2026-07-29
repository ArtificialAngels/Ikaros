# drivers.py

> 源文件：`Ikaros-memory/v5/drivers.py`

v5.drivers — 三种算法驱动内核 (Chaos PAD / ECA / AIS)

每个驱动是一个独立 @dataclass + tick() 方法 (<50 行).
可以直接替换 think.py 的 random.choice(templates) 逻辑.

用法:
    from v5.drivers import LorenzPAD, ECAGrid, AISDetectorSet
    pad = LorenzPAD()
    eca = ECAGrid()
    ais = AISDetectorSet()

    step = 0
    while True:
        p, a, d = pad.tick()               # Lorenz → PAD
        pattern = eca.tick()                # ECA → thinking pattern
        novelty = ais.tick(memory_batch)    # AIS → memory scores

        # 三层叠加 → 决策
        step += 1

## 内联注释摘录

# ═══════════════════════════════════════════════════════════════
# 1) CHAOS PAD — Lorenz 吸引子驱动情绪漂移
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 2) ECA — 1D 元胞自动机驱动思考主题
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 3) AIS — 负选择检测器用于记忆新颖性
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 模块级单例访问器 — 让演化跨调用累积 (修复"每次新建→永远相同"缺陷)
# ═══════════════════════════════════════════════════════════════

