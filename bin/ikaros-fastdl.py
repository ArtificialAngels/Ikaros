#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 详细说明见 docs/scripts/bin/ikaros-fastdl.md
import sys, os, json, time, shutil, subprocess, urllib.request, urllib.error, argparse
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_CFG = {
    "gopeed": {
        "exe": os.path.join(ROOT, "runtime", "gopeed", "gopeed-web.exe"),
        "port": 9999,
        "storage": os.path.join(ROOT, "tmp", "gopeed-store"),
        "whitelist": ROOT,                       # 允许下载到的根目录 (白名单)
        "download_dir": os.path.join(ROOT, "downloads"),  # 暂存/落地目录
        "connections": 32,                       # 每文件线程数 (300M 够用且不被多数服务器限流)
        "max_running": 8,                        # 最大并发任务
    },
    "aria2": {
        "exe": os.path.join(ROOT, "runtime", "aria2", "aria2c.exe"),
        "connections": 16,
        "split": 16,
    },
    # 镜像重写: 命中 match(子串) 则把该子串替换成 replace
    "mirrors": {
        "hf":  {"match": "huggingface.co",        "replace": "hf-mirror.com"},
        "hf2": {"match": "https://huggingface.co","replace": "https://hf-mirror.com"},
    },
}

def load_cfg():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fastdl.json")
    cfg = json.loads(json.dumps(DEF_CFG))
    if os.path.isfile(p):
        try:
            user = json.load(open(p, encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[warn] 读取 fastdl.json 失败, 用默认配置: {e}", file=sys.stderr)
    # 盘符无关: download_dir 若为相对路径, 按项目根(脚本位置推导)解析
    dd = cfg.get("gopeed", {}).get("download_dir", "")
    if dd and not os.path.isabs(dd):
        root = Path(os.path.dirname(os.path.abspath(__file__))).parent
        cfg["gopeed"]["download_dir"] = str(root / dd)
    return cfg

def winpath(p):
    return os.path.normpath(p).replace("/", "\\")

# ---------------- gopeed 控制 ----------------
class Gopeed:
    def __init__(self, cfg):
        self.cfg = cfg["gopeed"]
        self.base = f"http://127.0.0.1:{self.cfg['port']}"

    def _call(self, method, path, body=None, timeout=15):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def is_up(self):
        try:
            self._call("GET", "/api/v1/config", timeout=3)
            return True
        except Exception:
            return False

    def ensure_running(self):
        if self.is_up():
            return True
        exe = winpath(self.cfg["exe"])
        if not os.path.isfile(exe):
            print(f"[warn] gopeed 不存在: {exe}", file=sys.stderr)
            return False
        os.makedirs(self.cfg["storage"], exist_ok=True)
        os.makedirs(self.cfg["download_dir"], exist_ok=True)
        args = [exe, "-d", winpath(self.cfg["storage"]), "-w", winpath(self.cfg["whitelist"])]
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen(args, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[warn] 启动 gopeed 失败: {e}", file=sys.stderr)
            return False
        for _ in range(40):           # 最多等 ~10s
            if self.is_up():
                return True
            time.sleep(0.25)
        return False

    def apply_config(self):
        try:
            cfg = self._call("GET", "/api/v1/config")["data"]
            changed = False
            if cfg.get("downloadDir") != self.cfg["download_dir"]:
                cfg["downloadDir"] = self.cfg["download_dir"]; changed = True
            if cfg.get("maxRunning") != self.cfg["max_running"]:
                cfg["maxRunning"] = self.cfg["max_running"]; changed = True
            pc = cfg.setdefault("protocolConfig", {}).setdefault("http", {})
            if pc.get("connections") != self.cfg["connections"]:
                pc["connections"] = self.cfg["connections"]; changed = True
            if changed:
                self._call("PUT", "/api/v1/config", cfg)
        except Exception as e:
            print(f"[warn] 配置 gopeed 失败: {e}", file=sys.stderr)

    def add(self, url):
        r = self._call("POST", "/api/v1/tasks", {"req": {"url": url}, "type": "http"})
        return r.get("data")

    def get(self, tid):
        return self._call("GET", f"/api/v1/tasks/{tid}")["data"]

    def remove(self, tid):
        try:
            self._call("DELETE", f"/api/v1/tasks/{tid}")
        except Exception:
            pass

    def wait(self, tid, timeout=3600, poll=0.5):
        start = time.time()
        last = 0
        while time.time() - start < timeout:
            t = self.get(tid)
            st = t["status"]
            sp = t["progress"]["speed"]
            if sp != last:
                last = sp
            if st in ("done", "success"):
                return t, True
            if st == "error":
                return t, False
            time.sleep(poll)
        return self.get(tid), False

    def locate(self, task_name):
        d = self.cfg["download_dir"]
        # 优先按任务名精确匹配
        cand = os.path.join(d, task_name)
        if os.path.isfile(cand):
            return cand
        # 退而求其次: 同名(去扩展名)或目录下最新文件
        best = None; bt = 0
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if os.path.isfile(fp) and f.startswith(os.path.splitext(task_name)[0]):
                mt = os.path.getmtime(fp)
                if mt > bt:
                    bt = mt; best = fp
        return best

# ---------------- aria2 兜底 ----------------
def aria2_download(cfg, url, out_path, connections):
    exe = winpath(cfg["aria2"]["exe"])
    if not os.path.isfile(exe):
        return None
    d = os.path.dirname(out_path) or "."
    o = os.path.basename(out_path)
    args = [exe, "-x", str(connections), "-s", str(connections),
            "--min-split-size=1M", "-d", winpath(d), "-o", o, url]
    try:
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None
    return out_path if os.path.isfile(out_path) else None

# ---------------- 镜像 ----------------
def apply_mirror(url, name, cfg):
    m = cfg["mirrors"].get(name)
    if not m:
        return url
    return url.replace(m["match"], m["replace"])

def derive_name(url):
    path = urllib.parse.urlparse(url).path
    base = os.path.basename(path)
    return base or "download.bin"

import urllib.parse

# ---------------- 主流程 ----------------
def human_speed(bps):
    return f"{bps/1024/1024:.2f} MB/s ({bps*8/1_000_000:.1f} Mbps)"

def download_one(url, out_path, mirror, cfg, engine):
    gp = Gopeed(cfg)
    final_name = out_path
    # 镜像
    eff = apply_mirror(url, mirror, cfg) if mirror else url
    if eff != url:
        print(f"  [mirror:{mirror}] {url}\n         -> {eff}")
        url = eff

    # 决定暂存文件名
    stage_name = derive_name(url)

    # gopeed
    if engine in (None, "gopeed") and gp.ensure_running():
        gp.apply_config()
        try:
            tid = gp.add(url)
            t, ok = gp.wait(tid)
            if ok:
                src = gp.locate(t.get("name") or stage_name)
                if src and os.path.isfile(src):
                    os.makedirs(os.path.dirname(final_name) or ".", exist_ok=True)
                    if os.path.abspath(src) != os.path.abspath(final_name):
                        shutil.move(src, final_name)
                    size = os.path.getsize(final_name)
                    print(f"  [gopeed OK] {final_name}  ({size/1024/1024:.2f} MB)")
                    gp.remove(tid)
                    return True
                else:
                    print(f"  [gopeed] 找不到落盘文件: {stage_name}", file=sys.stderr)
            else:
                print(f"  [gopeed] 任务失败: {t.get('status')}", file=sys.stderr)
            gp.remove(tid)
        except Exception as e:
            print(f"  [gopeed] 异常: {e}", file=sys.stderr)

    # aria2 兜底
    if engine in (None, "aria2"):
        print("  [fallback] aria2 ...")
        r = aria2_download(cfg, url, final_name, cfg["aria2"]["connections"])
        if r:
            print(f"  [aria2 OK] {r}  ({os.path.getsize(r)/1024/1024:.2f} MB)")
            return True

    # 最后兜底: 单线程 urllib (慢, 仅保底)
    print("  [fallback] 单线程 urllib (最慢) ...")
    try:
        os.makedirs(os.path.dirname(final_name) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, final_name)
        print(f"  [urllib OK] {final_name}")
        return True
    except Exception as e:
        print(f"  [FAIL] 全部引擎失败: {e}", file=sys.stderr)
        return False

def main():
    ap = argparse.ArgumentParser(description="Ikaros 高速下载器 (gopeed/aria2 + 镜像)")
    ap.add_argument("urls", nargs="+", help="下载链接")
    ap.add_argument("-o", "--output", help="输出文件完整路径 (单链接时)")
    ap.add_argument("-d", "--dir", help="输出目录 (多链接/批量)")
    ap.add_argument("--mirror", help="镜像名: hf / hf2 (见 fastdl.json)")
    ap.add_argument("--engine", choices=["gopeed", "aria2"], help="强制指定引擎")
    ap.add_argument("--connections", type=int, help="覆盖每文件线程数")
    args = ap.parse_args()
    cfg = load_cfg()
    if args.connections:
        cfg["gopeed"]["connections"] = args.connections
        cfg["aria2"]["connections"] = args.connections
        cfg["aria2"]["split"] = args.connections

    out_dir = os.path.abspath(args.dir) if args.dir else cfg["gopeed"]["download_dir"]
    ok = 0
    for i, url in enumerate(args.urls):
        if args.output:
            op = os.path.abspath(args.output)
            out = os.path.join(op, derive_name(url)) if os.path.isdir(op) else op
        else:
            out = os.path.join(out_dir, derive_name(url))
        print(f"[{(i+1)}/{len(args.urls)}] {url}")
        if download_one(url, out, args.mirror, cfg, args.engine):
            ok += 1
    print(f"\n完成: {ok}/{len(args.urls)} 成功。落点: {out_dir}")
    sys.exit(0 if ok == len(args.urls) else 1)

if __name__ == "__main__":
    main()
