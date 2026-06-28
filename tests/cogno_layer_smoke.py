"""cogno_layer 自检脚本"""
import sys
from pathlib import Path

sys.path.insert(0, r"E:\Hermes Agent")
# DEPRECATED (2026-06-28): bridge/cogno_layer.py removed.
# cogno_layer.py is now archived in data/_backup_bridge_removed/20260628/bridge/.
# TODO: Port cogno_layer to bridge-rs/workers/ or inline import via sys.path.
# For now, this test is disabled. See handshake.2026-06-28.repo-rename-complete.json
# for the migration plan.
#
# from bridge.cogno_layer import enrich, enrich_reply, _get_machine_id, _get_geo_location
import sys
SKIP_REASON = "cogno_layer archived in 2026-06-28 bridge/ removal — re-enable after port"


print("=" * 70)
print("=== enrich 测试 ===")
print("=" * 70)

# ⚠️ SKIPPED (2026-06-28 bridge/ removal)
print("⚠️ cogno_layer_smoke test SKIPPED — bridge.cogno_layer archived")
print("  Restore: cp data/_backup_bridge_removed/20260628/bridge/cogno_layer.py . && git revert")
sys.exit(0)

# Original test code (disabled):
if False:
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