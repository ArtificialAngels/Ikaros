#!/usr/bin/env python3
"""Hermes Model Manager - 类似 ComfyUI 启动器的模型管理工具

功能：
1. 扫描 data/models/ 目录中的所有 GGUF 模型
2. 显示模型信息（大小、参数量、推荐GPU配置）
3. 一键切换模型（自动重启 llama-server）
4. GPU 加速检测和配置
5. Open WebUI 模型列表清理和同步
"""

import os
import sys
import json
import time
import subprocess
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class ModelInfo:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.size_mb = path.stat().st_size / (1024 * 1024)
        self.size_gb = self.size_mb / 1024
        self.params = self._estimate_params()
        self.vram_required = self._estimate_vram()

    def _estimate_params(self) -> str:
        """从文件名估计参数量"""
        name = self.name.lower()
        if "35b" in name:
            return "35B"
        elif "1.8b" in name or "1_8b" in name:
            return "1.8B"
        elif "3b" in name:
            return "3B"
        elif "7b" in name:
            return "7B"
        else:
            return "Unknown"

    def _estimate_vram(self) -> str:
        """估计所需显存"""
        params = self.params
        if params == "1.8B":
            return "2-4 GB"
        elif params == "3B":
            return "4-6 GB"
        elif params == "7B":
            return "8-12 GB"
        elif params == "35B":
            return "20-24 GB"
        else:
            return "Unknown"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "size_mb": round(self.size_mb, 2),
            "size_gb": round(self.size_gb, 2),
            "params": self.params,
            "vram_required": self.vram_required,
        }


class ModelManager:
    def __init__(self, hermes_root: Path):
        self.hermes_root = hermes_root
        self.models_dir = hermes_root / "data" / "models"
        self.webui_db = hermes_root / "hermes" / "data" / "openwebui" / "webui.db"
        self.webui_url = "http://127.0.0.1:7870"
        self.llama_port = 8080
        self.models: Dict[str, ModelInfo] = {}

    def scan_models(self) -> List[ModelInfo]:
        """扫描所有 GGUF 模型"""
        self.models = {}
        models = []
        for gguf in self.models_dir.glob("*.gguf"):
            info = ModelInfo(gguf)
            self.models[info.name] = info
            models.append(info)
        return sorted(models, key=lambda m: m.size_mb)

    def get_gpu_info(self) -> dict:
        """获取 GPU 信息"""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                return {
                    "name": parts[0],
                    "total_mb": int(parts[1]),
                    "free_mb": int(parts[2]),
                    "total_gb": round(int(parts[1]) / 1024, 2),
                    "free_gb": round(int(parts[2]) / 1024, 2),
                }
        except Exception as e:
            pass
        return {"name": "Not detected", "total_mb": 0, "free_mb": 0, "total_gb": 0, "free_gb": 0}

    def calculate_ngl(self, model: ModelInfo, gpu: dict) -> int:
        """计算 NGL (GPU layers)"""
        if gpu["free_mb"] == 0:
            return 0

        model_mb = model.size_mb
        vram_free = gpu["free_mb"]

        # 智能计算策略
        if model_mb <= vram_free * 0.7:
            return 99  # 全部 GPU
        elif model_mb <= vram_free * 1.2:
            return 99  # 全部 GPU + KV cache
        else:
            # 部分卸载（即使模型很大，也尽量使用一些GPU层）
            vram_for_model = vram_free * 0.7
            avg_layer_mb = model_mb / 80  # 假设约80层
            ngl = int(vram_for_model / avg_layer_mb)
            # 确保至少使用一些GPU层
            return max(1, min(99, ngl))

    def stop_llama_server(self) -> bool:
        """停止 llama-server"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | Stop-Process -Force"],
                capture_output=True,
                timeout=10
            )
            time.sleep(2)
            return True
        except Exception as e:
            print(f"[WARN] 停止 llama-server 失败: {e}")
            return False

    def start_llama_server(self, model: ModelInfo, ngl: int) -> bool:
        """启动 llama-server"""
        try:
            script = self.hermes_root / "bin" / "start-llm-smart.bat"
            env = os.environ.copy()
            env["LLAMA_MODEL"] = str(model.path)
            env["LLAMA_NGL"] = str(ngl)

            subprocess.Popen(
                ["cmd", "/c", str(script)],
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )

            # 等待启动
            for i in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{self.llama_port}/health", timeout=2)
                    return True
                except:
                    time.sleep(2)
            return False
        except Exception as e:
            print(f"[ERROR] 启动 llama-server 失败: {e}")
            return False

    def clean_webui_models(self) -> bool:
        """清理 Open WebUI 中的无效模型"""
        if not self.webui_db.exists():
            return False

        try:
            conn = sqlite3.connect(self.webui_db)
            cursor = conn.cursor()

            # 获取数据库中的所有模型
            cursor.execute("SELECT id, name FROM model")
            db_models = cursor.fetchall()

            # 删除不在当前运行模型列表中的模型
            current_model_id = self._get_current_model_id()
            deleted = 0
            for model_id, model_name in db_models:
                if model_id != current_model_id:
                    cursor.execute("DELETE FROM model WHERE id = ?", (model_id,))
                    deleted += 1

            conn.commit()
            conn.close()
            return deleted > 0
        except Exception as e:
            print(f"[WARN] 清理模型列表失败: {e}")
            return False

    def _get_current_model_id(self) -> str:
        """获取当前运行的模型 ID"""
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{self.llama_port}/v1/models", timeout=5)
            data = json.loads(resp.read())
            if data.get("data"):
                return data["data"][0]["id"]
        except:
            pass
        return ""

    def add_model_to_webui(self, model: ModelInfo) -> bool:
        """添加模型到 Open WebUI"""
        try:
            # 获取 admin token
            token = self._get_webui_token()
            if not token:
                return False

            # 构造模型 ID
            model_id = model.name.replace(".", "_").replace("-", "_")
            model_name = f"{model.name.replace('.gguf', '')} (Local)"

            # 添加模型
            data = json.dumps({
                "id": model_id,
                "name": model_name,
                "base_model_id": model_id,
                "base_model_name": model_id,
                "meta": {"capabilities": {"vision": False, "usage": True}},
                "params": {},
            }).encode()

            req = urllib.request.Request(
                f"{self.webui_url}/api/v1/models/create",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                }
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[WARN] 添加模型到 Open WebUI 失败: {e}")
            return False

    def _get_webui_token(self) -> Optional[str]:
        """获取 WebUI token"""
        try:
            data = json.dumps({
                "email": "admin@hermes.local",
                "password": "hermes123",
            }).encode()

            req = urllib.request.Request(
                f"{self.webui_url}/api/v1/auths/signin",
                data=data,
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get("token")
        except:
            return None

    def switch_model(self, model_name: str) -> bool:
        """切换模型"""
        if model_name not in self.models:
            print(f"[ERROR] 模型不存在: {model_name}")
            return False

        model = self.models[model_name]
        gpu = self.get_gpu_info()
        ngl = self.calculate_ngl(model, gpu)

        print(f"\n切换到模型: {model.name}")
        print(f"  大小: {model.size_gb:.2f} GB")
        print(f"  参数: {model.params}")
        print(f"  推荐显存: {model.vram_required}")
        print(f"  GPU: {gpu['name']}")
        print(f"  可用显存: {gpu['free_gb']:.2f} GB")
        print(f"  NGL (GPU layers): {ngl}")
        print()

        # 停止旧服务
        print("[1/4] 停止 llama-server...")
        self.stop_llama_server()

        # 启动新服务
        print("[2/4] 启动 llama-server...")
        if not self.start_llama_server(model, ngl):
            print("[ERROR] 启动失败")
            return False

        # 清理旧模型
        print("[3/4] 清理 Open WebUI 模型列表...")
        self.clean_webui_models()

        # 添加新模型
        print("[4/4] 添加模型到 Open WebUI...")
        self.add_model_to_webui(model)

        print("\n✓ 模型切换完成！")
        print(f"  请刷新浏览器: http://localhost:7870")
        return True

    def list_models(self):
        """列出所有模型"""
        models = self.scan_models()
        gpu = self.get_gpu_info()

        print("\n" + "=" * 60)
        print("  Hermes Model List")
        print("=" * 60)
        print(f"\nGPU: {gpu['name']}")
        print(f"Free VRAM: {gpu['free_gb']:.2f} GB / {gpu['total_gb']:.2f} GB")
        print(f"\nModel directory: {self.models_dir}")
        print(f"Found {len(models)} models:\n")

        for i, model in enumerate(models, 1):
            ngl = self.calculate_ngl(model, gpu)
            status = "[GPU]" if ngl > 0 else "[CPU]"
            print(f"{status} [{i}] {model.name}")
            print(f"    Size: {model.size_gb:.2f} GB  |  Params: {model.params}  |  VRAM req: {model.vram_required}")
            print(f"    NGL: {ngl} ({'GPU accelerated' if ngl > 0 else 'CPU mode'})")
            print()

        print("=" * 60)


def main():
    # hermes_root should be the project root, not the hermes package
    hermes_root = Path(__file__).parent.parent.parent
    manager = ModelManager(hermes_root)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "list":
            manager.list_models()
        elif cmd == "switch" and len(sys.argv) > 2:
            model_name = sys.argv[2]
            manager.switch_model(model_name)
        elif cmd == "gpu":
            gpu = manager.get_gpu_info()
            print(json.dumps(gpu, indent=2, ensure_ascii=False))
        elif cmd == "clean":
            manager.clean_webui_models()
            print("✓ 模型列表已清理")
        else:
            print("用法:")
            print("  python model_manager.py list              # 列出所有模型")
            print("  python model_manager.py switch <model>     # 切换模型")
            print("  python model_manager.py gpu               # 显示 GPU 信息")
            print("  python model_manager.py clean             # 清理模型列表")
    else:
        manager.list_models()


if __name__ == "__main__":
    main()