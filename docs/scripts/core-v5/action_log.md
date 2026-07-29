# action_log.py

> 源文件：`Ikaros-memory/action_log.py`

action_log.py — 伊卡洛斯工具函数统一审计日志

所有 subprocess / file.write / terminal 调用过此包装,
审计日志写到 data/ikaros-coordination/action_log/{YYYY-MM-DD}.jsonl

用法:
  from action_log import log_subprocess, log_file_write, log_terminal

  # 替代裸 subprocess.Popen
  proc = log_subprocess(["ffmpeg", ...], label="tts_decode")

  # 替代裸 subprocess.run
  result = log_subprocess_run(["tasklist", ...], label="check_pid")

  # 替代裸 open().write()
  nbytes = log_file_write(path, content, label="screenshot")

  # 替代裸 os.system / subprocess shell
  result = log_terminal("dir E:\Ikaros", label="list_files")

设计:
  - 每次调用记一条 JSONL: {ts, action, label, cmd/path, status, duration_ms, error?}
  - 失败不阻塞: 包装器内部异常静默传播, 但审计记录一定写
  - 日志按天分文件, 自动创建目录
