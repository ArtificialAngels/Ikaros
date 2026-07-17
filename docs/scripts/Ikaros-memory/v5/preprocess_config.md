# preprocess_config.py

> 源文件：`Ikaros-memory/v5/preprocess_config.py`

v5.preprocess_config — 思考前处理工厂阈值配置 (fail-open).

设计原则:
  - 所有 spec 4.2 要求的阈值集中在这里, 不写死在模块里
  - 读取失败 / 文件缺失 → 用内置 _DEFAULTS, 绝不阻塞调用方
  - 仅依赖标准库 + 可选 pyyaml (cloud_chat 已确认 portable-python 装了 yaml)
