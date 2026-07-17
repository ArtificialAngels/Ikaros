# profile.py

> 源文件：`Ikaros-memory/v5/profile.py`

v5.profile — 用户画像 (R5, P2)

追踪哥哥的偏好 / 讨厌, 供云端一句话感知, 避免踩雷.
读取 v4.db 中 type='preference' / 'dislike' 的记忆 (由 cloud_chat._self_review 写入).

设计要点 (spec 2.5):
  - 负面偏好更重要: 讨厌什么比喜欢什么更该注入, 避免踩雷
  - 置信度门控: weight < 0.7 不注入
  - 不准比太准: 只给云端一个"感觉", 不堆档案
  - 每轮最多注入 _MAX_INJECT 条, 只在有内容时注一句
