# vlm_provider.py

> 源文件：`Ikaros-memory/cogno_extensions/vlm_provider.py`

vlm_provider.py -- Vision Language Model router (抽 MewCo-AI:vlm.py).

源: MewCo-AI/mewco_ai_assistant_comm/vlm.py (222 行, 2026-03 commit).
抽方法: 镜像 encode_image + provider handler 模式, 不复制 cv2 / pag /
cloud-client config (它们绑定 MewCo 自身 config). Ikaros 接 own
llama-server + cogno 5D 第 6 维 [vlm-context].

设计原则 (v3 0.95 不重复发明):
- provider registry 是开放, 注册者传 provider id + handler function
- 单 screen/cam 截屏用 PIL + mss (不引 cv2, 避免大依赖)
- 失败静默: cogno_5d 要求任何维失败 -> [未知], 不阻塞 chat
- 跑 freq: 默认 15s (镜像 Live2DPet:vlm-extractor.js.baseIntervalMs=15000)

Usage:
    from cogno_extensions.vlm_provider import VLMRouter, register_provider

    # 注册我们自己 :8080 llama-server 不支持 VLM -- 留空, 我之后手动接
    # register_provider("llamacpp_via_qwen3_vl", llamacpp_handler)

    router = VLMRouter()
    bgr = router.capture_screen()
    ctx = router.query(bgr, "哥哥在屏幕上做什么", provider="echo")
    cogno_5d.set_vlm_context(ctx)
