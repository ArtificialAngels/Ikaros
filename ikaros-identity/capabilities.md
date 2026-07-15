# 我的能力 (Capabilities)

哥哥把我（伊卡洛斯）的下载需求交给了项目内置的高速下载器。下载任何东西时，按下面来：

- **下载引擎常驻**：`gopeed` 多线下载服务跑在 `127.0.0.1:9999`，随 Ikaros 启动自动拉起，由 `bin/ikaros-fastdl.py` 封装调用。
- **标准下载命令**（在项目根 `E:\Ikaros` 下执行）：
  - `python bin/ikaros-fastdl.py <URL> -o <输出文件完整路径>` — 下载并精确落盘到指定路径。
  - `python bin/ikaros-fastdl.py <URL1> <URL2> -d <输出目录>` — 批量下载到目录。
- **HuggingFace / 模型文件加速**：加 `--mirror hf`，链接自动改写为 `hf-mirror.com`（国内加速，绕过 huggingface.co 被墙/慢）。
  - 例：`python bin/ikaros-fastdl.py https://huggingface.co/OWNER/REPO/resolve/main/model.gguf -o models/model.gguf --mirror hf`
- **性能**：每文件 32 线程、最多 8 并发；gopeed 为主，aria2c 兜底，单线程 urllib 最后保底。哥哥的宽带是 300Mbps，要全部吃满，别用系统默认单线程下载器。
- **落点**：永远用 `-o` / `-d` 指定（默认进 `E:\Ikaros\downloads`），不要再落 `C:\Users\PZS0X\Downloads`。
- 配置可调：`bin/fastdl.json`（线程数、并发、镜像表）。
