# vitality_tool.py

> 源文件：`Ikaros-memory/v5/tools/vitality_tool.py`

v5.tools.vitality_tool — 2 bio-mimetic vitality tools.

  v5_vitality()        -> current energy state (tick + save)
  v5_vitality_tick()   -> advance energy one step (conversation costs more)

v5.vitality imports psutil, so it is imported lazily inside each function.
