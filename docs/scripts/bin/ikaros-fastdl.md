# bin/ikaros-fastdl.py — Ikaros 高速下载器

## 用途（原模块 docstring）
用项目内置的 gopeed-web（多线）做主力下载引擎，aria2c 做兜底，可选镜像重写（HF → hf-mirror.com），吃满带宽，下载落点由你指定。

## 为什么不用默认下载器
- 默认下载器通常单线程 + 固定目录（如 `C:\Users\xxx\Downloads`），慢且乱。
- gopeed 每文件 32 线程、最多 8 并发，单文件即可打满 300Mbps；镜像（hf-mirror.com）解决 HuggingFace 在国内被墙/慢的问题。

## 用法
```
python ikaros-fastdl.py <URL> [-o 输出路径] [--mirror hf] [--engine gopeed|aria2]
python ikaros-fastdl.py URL1 URL2 -d 输出目录
```
依赖：仅 Python 标准库（urllib / subprocess / json）。

## 配置（fastdl.json 覆盖默认）
- gopeed：`runtime/gopeed/gopeed-web.exe`，:9999，storage=`tmp/gopeed-store`，whitelist=ROOT，connections=32，max_running=8
- aria2：`runtime/aria2/aria2c.exe`，connections=16，split=16
- mirrors：`hf` → huggingface.co→hf-mirror.com

## 引擎优先级
gopeed → aria2 → 单线程 urllib（最慢保底）。`Gopeed.locate` 优先按任务名精确匹配落盘文件，否则退而求其次取目录下最新同名文件。
