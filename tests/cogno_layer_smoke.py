"""cogno_layer 自检脚本"""
import sys
from pathlib import Path

sys.path.insert(0, r"E:\Hermes Agent")
from bridge.cogno_layer import enrich, enrich_reply, _get_machine_id, _get_geo_location

print("=" * 70)
print("=== enrich 测试 ===")
print("=" * 70)

tests = [
    "伊卡洛斯，早上好呀！",
    "刚才那个 bridge 卡死了，你重启一下",
    "我今天累死了，😮‍💨",
    "为什么这个端点会失败？",
    "辛苦了，伊卡洛斯",
    "A",
    " 继续",
]
for t in tests:
    print(f"\n  input:    {t}")
    print(f"  enriched: {enrich(t)}")

print("\n" + "=" * 70)
print("=== enrich_reply 测试 ===")
print("=" * 70)
print(f"\n  {enrich_reply('哥哥早啊！', user_text='伊卡洛斯，早上好呀！', emotion_after='[开心呢]')}")

print("\n" + "=" * 70)
print("=== 机器 ID ===")
print("=" * 70)
print(f"  {_get_machine_id()}")

print("\n" + "=" * 70)
print("=== 地球地址 ===")
print("=" * 70)
print(f"  {_get_geo_location()}")