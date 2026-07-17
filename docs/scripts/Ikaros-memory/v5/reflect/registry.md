# registry.py

> 源文件：`Ikaros-memory/v5/reflect/registry.py`

v5.reflect.registry — V5.1 反思 op 注册表

把 consolidate / distill / narrative / self_discovery 包装成 ReflectOp,
注入到 scheduler。scheduler 调 run_all() 时, 自动按 trigger 跑到期 op。

(原 v4.reflect.registry → V5.1 unified)
