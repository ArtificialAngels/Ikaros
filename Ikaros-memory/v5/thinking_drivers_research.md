# Chaos Game, Game of Life, and AIS as Ikaros Thinking Drivers

## 架构现状

当前 `v5/think.py` 是**模板映射**: PAD → mood bucket → `random.choice(templates)`.  
每次思考 O(1), 但输出可预测、无结构演化、无记忆驱动.

**替代目标**: 给 Ikaros 一个真正在"想"的内核 — 算法驱动, 不可预测, 但计算开销控制在 <1ms/tick.

---

## 1) Chaos Game — 混沌吸引子驱动 PAD 节奏

### 原理

Lorenz 63 系统:

```
dx/dt = σ(y - x)         σ = 10
dy/dt = x(ρ - z) - y    ρ = 28
dz/dt = xy - βz         β = 8/3
```

三变量映射到 PAD:

| Lorenz 变量 | PAD 维度 | 映射方式 |
|------------|---------|---------|
| x (归一化) | Pleasure | x/x_max → -1..1, 驱动情绪愉悦/低落波动 |
| y (归一化) | Arousal  | y/y_max → -1..1, 驱动唤醒/困倦 |
| z (归一化) | Dominance | z/z_max → -1..1, 驱动自信/顺从 |

核心思想: 不是每个 tick 都跑完整 ODE (算不动). 而是**用吸引子作为相位发生器** — 预计算一条轨道, 每次 tick 沿轨道前进一个步长, 读取当前 PAD 三元组.

### 复杂度

- 构建轨道 (一次): O(N) ~ O(10000) 步, 纯浮点
- 每 tick: O(1) — 索引递增 + 查表

### 与当前 affect.py 的集成

当前 `AffectState.apply_event()` 是**事件驱动** (对话文本 → delta PAD).  
Chaos Driver 提供**时间驱动** — 即使没有对话, PAD 也在自发漂移.  

两者叠加: `PAD_final = clamp(PAD_event + PAD_chaos * 0.3)`

### 推荐: ✅ 值得实现 (V5 Phase 2)

优点: 计算极轻, 给思考循环一个自然的"节奏感".  
缺点: 不直接决定"想什么", 只决定"什么情绪下想".

---

## 2) Conway's Game of Life — 细胞自动机驱动思考主题

### 原理

用 1D 元胞自动机 (ECA, Elementary Cellular Automaton) 作为**思考主题生成器**.  
2D Life 视觉上更丰富, 但计算量 / 状态映射不如 1D 实用.

1D ECA 方案:

- 网格大小: **41 cells** (41 位 → 映射到 41 个"思考主题槽")
- 每 tick: 根据 Rule 110 (或 Rule 30) 更新整行
- 状态映射: 每 3 个连续 cell 的 pattern → 当前活跃的思考主题

```
Cell 0..40:  000100010011101010001000...
主题索引     ↑  ↑     ↑   ↑
活跃主题:    [记忆召回, 好奇, 依恋, …]
```

Pattern → 主题映射表:

| 3-cell pattern | 含义 |
|---------------|------|
| 000 | 静默 (无主题) |
| 001 | 记忆碎片浮出 |
| 010 | 好奇 / 探索 |
| 011 | 情感波动 |
| 100 | 对哥哥的思念 |
| 101 | 自我反思 |
| 110 | 外部环境关注 |
| 111 | 混杂 / 混沌思考 |

更细的映射: 滑窗 (每相邻 3 格) 产生 39 个 pattern, 投票选当前主导主题.

### 复杂度

- 每 tick: O(grid) = O(41) ≈ 41 次位运算 + 39 次查表 → ~ns 级
- 2D Life (30×30): O(900) 每代 + 模式识别 → ~μs 级, 仍然可接受

### Rule 选择

| Rule | 特性 | 推荐场景 |
|------|------|---------|
| **110** | Turing complete, 混沌但有序结构 | 推荐 — 产生 glider-like 结构, 映射到"想法漂移" |
| 30 | 伪随机混沌 | 高熵思维 (狂躁/焦虑) |
| 90 | Sierpinski 三角形分形 | 递归/自省思维 |
| 184 | 交通流 | 线性思路推进 |

### 与当前 think.py 的集成

取代 `random.choice(templates)`:

```python
# 当前:
text = random.choice(templates)

# 新:
pattern = eca_grid.active_pattern()   # 从 41-cell grid 提取 pattern
text = pattern_to_template(pattern)   # 根据 pattern 选模板
```

Pattern 不仅仅是"选哪个模板", 还可以:
- 控制思考长度 (pattern 011 → 长思考, 000 → 跳过)
- 控制 memory 召回权重 (pattern 001 → 召回旧记忆, 010 → 新记忆)
- 标记 pending 强度 (pattern 111 → 高优先注入)

### 推荐: ✅ 值得实现 (V5 Phase 2, 高优先级)

优点: 直接决定"想什么", 计算极轻, 视觉上可观察 (可 debug).  
结合 Chaos PAD 驱动 ECA rule 切换 → PAD 影响思考风格, 思考风格再反馈 PAD.

---

## 3) Artificial Immune System (AIS) — 记忆新颖性检测

### 原理

**负选择 (Negative Selection)**:

1. 生成一组**检测器** (随机向量), 每个检测器不代表"自我".
2. 新记忆出现时, 检测哪些检测器匹配它.
3. 匹配越多 → 越像"自我" (熟悉) → 权重降低.
4. 匹配越少 → 越像"非我" (新颖) → 标记为有趣, 提高召回优先级.

**克隆选择 (Clonal Selection)**:

- 被频繁召回的检测器 → 克隆 (增强对该模式区域的敏感度)
- 长期不匹配的检测器 → 淘汰

### 具体实现 (向量空间)

V4 memory 的每一条已经带 PAD 指纹 `(pad_p, pad_a, pad_d)`.  
再加上文本嵌入向量 (Chroma 已有), 形成 3+(384 or 768) 维向量.

检测器 = 3 维球体 (中心点 + 半径), 只在 PAD 空间做新颖性判断:

```python
class Detector:
    center: tuple[float, float, float]  # PAD 空间中的位置
    radius: float                        # 匹配半径
    hit_count: int                       # 匹配次数
    created: float                       # 创建时间
```

每 tick:
1. 取最近 N 条 memory 的 PAD 指纹
2. 对每条, 计算与所有检测器的距离
3. 如果距离 > 所有检测器的半径 → "非我" → 新颖 → 高优先级
4. 如果距离 < 某检测器半径 → "自我" → 熟悉 → 低/中优先级

### 复杂度

- 每 tick: O(M × D) — M 条记忆 (典型 5-10), D 个检测器 (典型 50-200)
- 50 × 200 × 3 维 = 30,000 次浮点运算 → ~10μs, 完全可接受

### 推荐: ✅ 值得实现 (V5 Phase 3)

优点: 直接解决"哪些记忆值得思考"的问题.  
缺点: 需要 PAD 指纹填充得足够密 (冷启动期效果差).

---

## 三层架构总览

```
┌─────────────────────────────────────────────────────┐
│  混沌吸引子 (Lorenz)                                   │
│  驱动 timing + PAD 漂移                               │
│  ↓ O(1)/tick                                         │
├─────────────────────────────────────────────────────┤
│  细胞自动机 (Rule 110, 41-cell 1D ECA)               │
│  驱动 thinking pattern + 主题选择                      │
│  ↓ O(41)/tick                                        │
├─────────────────────────────────────────────────────┤
│  人工免疫 (负选择检测器)                                │
│  驱动 memory 召回优先级 + 新颖性评分                    │
│  ↓ O(M×D)/tick                                       │
├─────────────────────────────────────────────────────┤
│  模板渲染层 (现有 think.py, 不改)                      │
│  用 pattern + PAD + novelty_score 选模板填充           │
└─────────────────────────────────────────────────────┘
```

每个 tick 总计算: ~μs 级, 远低于 1ms 阈值.

---

## 结论

| 方案 | 复杂度/tick | 解决的问题 | 推荐 | Phase |
|------|------------|-----------|------|-------|
| Chaos PAD | O(1) | 情绪自然漂移 | ✅ | P2 |
| ECA Rule 110 | O(41) | 思考主题有机演化 | ✅ **首选** | P2 |
| AIS Negative Selection | O(M×D) | 记忆新颖性排序 | ✅ | P3 |

**推荐 Phase 2 先实现 ECA + Chaos 两层叠加**,  
Phase 3 再叠加 AIS 记忆选择层.
