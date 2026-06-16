"""
修正版 clean_mtext 调试
"""
import re


def clean_mtext_v14(text: str) -> str:
    """
    清理 MTEXT 格式控制符 - v14 修正版
    MTEXT 结构:
      {...} 块内: \\Xvalue; 形式是控制参数，最后一个非控制段是显示文本
      块外: \\Xvalue; 形式是控制参数

    控制字符包括:
      \\f, \\F (字体), \\W (宽), \\H (高), \\T (行高), \\C (色), \\P (段落),
      \\Q, \\q, \\L, \\l, \\K, \\k, \\A (堆叠), \\U, \\u, \\S, \\s, \\H, \\p, \\~
      块内 ";" 分隔的段
    """
    # MTEXT 已知控制符（注意：H 不在内，会和 HNC- 冲突）
    CONTROL_CHARS = set('WwFfTtCcPpQqAaLlKkSsUu~')

    def is_control_segment(s: str) -> bool:
        """判断是否控制段（保守：只过滤明显的控制段）"""
        s = s.strip()
        if not s:
            return True
        # 以 \ 开头（控制符前缀）
        if s[0] == '\\':
            return True
        # 以单/双控制字符开头（\T, \W, \f, \C 等）
        if s[0] in CONTROL_CHARS and len(s) <= 2:
            return True
        # 纯单控制字符（不跟其他字符）
        if len(s) == 1 and s[0] in CONTROL_CHARS:
            return True
        # key=value 形式（如 b0, i0, c134, p2）
        if re.match(r'^[a-zA-Z][a-zA-Z0-9]*=', s):
            return True
        # \f字体,|字体,|... 形式（不含中文）
        if '|' in s and not re.search(r'[\u4e00-\u9fff]', s):
            if re.match(r'^[\w\.\-]+(\|[\w\.\-]+)*$', s):
                return True
        return False

    result = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            # 找匹配的 }
            j = text.find('}', i)
            if j == -1:
                break
            block = text[i+1:j]
            parts = block.split(';')
            # 找最后一个"非控制段"
            last = ''
            for p in reversed(parts):
                if not is_control_segment(p):
                    last = p.strip()
                    break
            result.append(last)
            i = j + 1
        else:
            result.append(text[i])
            i += 1
    s = ''.join(result)
    # 处理块外的单字符控制符
    s = re.sub(r'\\[WwFfTtCcPpQqAaLlKkSsUuHh][^;]*;', '', s)
    s = re.sub(r'\\[WwFfTtCcPpQqAaLlKkSsUuHh]', '', s)
    s = s.replace('%%c', 'Ø')
    s = re.sub(r'\\P', '\n', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# 测试
tests = [
    "{\T0.85;1101-1~1101-5}",          # → 1101-1~1101-5
    "{\T0.85;定模型芯1101}",          # → 定模型芯1101
    "{\T0.85;HNC-O4}",                # → HNC-O4
    "{\T0.85;363}",                   # → 363（数字尺寸）
    "{\T0.85;Ø1.8*Ø1.4}",             # → Ø1.8*Ø1.4
    "{\T0.85;Ø5}",                    # → Ø5
    "{\fSimSun|b0|i0|c134|p0;\W0.8;\C7;HNC 型点冷管\P供应商:苏州骏勋}",  # → HNC 型点冷管供应商:苏州骏勋
    "{\W0.7;A}6500",                  # → 6500（A 是堆叠控制）
    "{\W0.7;Geely BHE20-ICE缸体压铸模}",  # → Geely BHE20-ICE缸体压铸模
    "{\W1.12500x;2026-03-05}",        # → 2026-03-05
    "{\C3;邓志恒}",                   # → 邓志恒
    "件号",
    "∅6 0.85",                        # 已清理
]

print("=== clean_mtext_v14 测试 ===")
for t in tests:
    out = clean_mtext_v14(t)
    print(f"  {t!r:75} -> {out!r}")
