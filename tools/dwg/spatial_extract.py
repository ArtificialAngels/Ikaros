"""
完整提取 DXF 中的"非 dwg 表格形式"内容
策略：
1. 找出图纸中所有"表格"区域（基于 LINE 形成的矩形 + 附近文字）
2. 用 Y 坐标聚类文字成行，X 坐标聚类成列
3. 输出多张表格（每个图框一张）
4. 去除无效字段（d、L、E、J 是表头单位标识）
"""
import ezdxf
from pathlib import Path
from collections import Counter, defaultdict
import re
import math
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

DXF_PATH = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
OUTPUT_XLSX = DXF_PATH.parent / "DXF表格提取.xlsx"

def clean_mtext(text: str) -> str:
    text = re.sub(r'\{[^}]*\}', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk][^;]*;', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ===== 读 DXF =====
print(f"读取: {DXF_PATH.name}")
doc = ezdxf.readfile(str(DXF_PATH))
msp = doc.modelspace()
entities = list(msp)

# ===== 收集所有 LINE（识别表格边框）=====
print("分析几何...")

# 收集所有文字（带坐标 + 清洗后文本）
text_items = []
for e in entities:
    if e.dxftype() in ('TEXT', 'MTEXT'):
        try:
            raw = e.dxf.text if e.dxftype() == 'TEXT' else e.text
            if not raw or not raw.strip():
                continue
            clean = clean_mtext(raw)
            if not clean:
                continue
            # 文字插入点
            if e.dxftype() == 'TEXT':
                pos = e.dxf.insert
            else:
                pos = e.dxf.insert
            # 文字高度（粗略估算 Y 间距）
            try:
                h = float(e.dxf.get('height', 2.5))
            except:
                h = 2.5
            text_items.append({
                'x': float(pos[0]),
                'y': float(pos[1]),
                'h': h,
                'text': clean,
                'raw': raw,
                'layer': e.dxf.layer,
                'type': e.dxftype(),
            })
        except:
            pass

print(f"  文字总数: {len(text_items)}")

# ===== 收集所有 LINE 端点（用于识别表格）=====
line_endpoints = []
for e in entities:
    if e.dxftype() == 'LINE':
        try:
            s = e.dxf.start
            end = e.dxf.end
            line_endpoints.append({
                'x1': float(s[0]), 'y1': float(s[1]),
                'x2': float(end[0]), 'y2': float(end[1]),
                'layer': e.dxf.layer
            })
        except:
            pass

print(f"  LINE 总数: {len(line_endpoints)}")

# ===== 找所有 LINE 形成的矩形（表格）=====
# 找水平线 (dy 接近 0)
H_TOL = 0.5  # 容差
V_TOL = 0.5
horizontal_lines = [l for l in line_endpoints if abs(l['y1'] - l['y2']) < H_TOL]
vertical_lines = [l for l in line_endpoints if abs(l['x1'] - l['x2']) < V_TOL]
print(f"  水平线: {len(horizontal_lines)}, 垂直线: {len(vertical_lines)}")

# 按 Y 聚类水平线（找水平边）
h_y_groups = defaultdict(list)
for l in horizontal_lines:
    y_key = round(l['y1'], 1)
    h_y_groups[y_key].append(l)

# 找长水平线 (长度 > 50)
long_horizontal = []
for y, lines in h_y_groups.items():
    for l in lines:
        length = abs(l['x2'] - l['x1'])
        if length > 30:
            long_horizontal.append({
                'y': y, 'x1': min(l['x1'], l['x2']), 'x2': max(l['x1'], l['x2']),
                'length': length, 'layer': l['layer']
            })

# 按 Y 排序
long_horizontal.sort(key=lambda l: -l['y'])  # AutoCAD Y 朝上
print(f"  长水平线: {len(long_horizontal)}")

# 按 Y 聚类（相同 Y 容差 < 1mm）
def cluster_by_y(lines, tol=2.0):
    if not lines:
        return []
    lines_sorted = sorted(lines, key=lambda l: l['y'])
    clusters = []
    current = [lines_sorted[0]]
    for l in lines_sorted[1:]:
        if abs(l['y'] - current[-1]['y']) < tol:
            current.append(l)
        else:
            # 合并同一行的线段范围
            xs = [c['x1'] for c in current] + [c['x2'] for c in current]
            clusters.append({
                'y': sum(c['y'] for c in current) / len(current),
                'x_min': min(xs),
                'x_max': max(xs),
                'segments': len(current)
            })
            current = [l]
    xs = [c['x1'] for c in current] + [c['x2'] for c in current]
    clusters.append({
        'y': sum(c['y'] for c in current) / len(current),
        'x_min': min(xs),
        'x_max': max(xs),
        'segments': len(current)
    })
    return clusters

h_clusters = cluster_by_y(long_horizontal, tol=2.0)
print(f"  水平线 Y 簇: {len(h_clusters)}")
# 显示 Y 值范围
if h_clusters:
    ys = [c['y'] for c in h_clusters]
    print(f"    Y 范围: {min(ys):.0f} ~ {max(ys):.0f}")

# ===== 在每条水平线附近找文字 =====
# 关键图层（疑似表格）
target_layers = {'AM_4', '12', '标注', 'AM_5', 'DIM', '0', 'TEXT', '图框层', '排图层', 'b表格', 'b', 'TT', '6文字层', '11111', 'Cool', '1'}

# 候选标题行：包含"件号"/"冷却"/"型号"/"数量"/"使用零件"/"运水"等关键词
header_keywords = ['件号', '冷却编号', '型号', '数量', '使用零件', '运水', 'L', 'E', 'J']

# 在所有文字中找"标题行"
header_texts = [t for t in text_items if any(kw in t['text'] for kw in header_keywords) or t['text'] in ('d',)]
print(f"\n  候选标题行文字: {len(header_texts)}")
for t in header_texts[:20]:
    print(f"    ({t['x']:.1f}, {t['y']:.1f}) [{t['layer']}] {t['text']}")

# ===== 按 Y 坐标聚类文字（聚成"行"）=====
def cluster_texts_by_y(texts, tol=3.0):
    """按 Y 坐标聚类文字成行"""
    if not texts:
        return []
    sorted_texts = sorted(texts, key=lambda t: -t['y'])  # AutoCAD Y 朝上，所以大 Y 在前
    rows = []
    current = [sorted_texts[0]]
    for t in sorted_texts[1:]:
        if abs(t['y'] - current[-1]['y']) < tol:
            current.append(t)
        else:
            # 把这一行的 x 排序
            current.sort(key=lambda t: t['x'])
            rows.append(current)
            current = [t]
    current.sort(key=lambda t: t['x'])
    rows.append(current)
    return rows

# 用所有文字，先看完整的 Y 分布
all_rows = cluster_texts_by_y(text_items, tol=5.0)
print(f"\n  按 Y 聚类后的总行数: {len(all_rows)}")

# 找包含"件号"的行附近的所有文字
# 标题行的特征：在 Y 上，附近有一组行（数据行）
# 找所有可能的"表格区域"

# 方法：找包含'件号'或'冷却编号'的文字作为"表头起点"
header_rows_idx = []
for i, row in enumerate(all_rows):
    row_texts = [t['text'] for t in row]
    if '件号' in row_texts or '冷却编号' in row_texts:
        header_rows_idx.append(i)
        print(f"\n  发现标题行 #{i}: Y={row[0]['y']:.1f}")
        for t in row:
            print(f"    ({t['x']:.1f}, {t['y']:.1f}) {t['text']}")

print(f"\n共发现 {len(header_rows_idx)} 个标题行")
