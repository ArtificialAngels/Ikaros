#!/usr/bin/env python3
"""Hermes GPU 检测和配置工具

类似 ComfyUI-aki-v3 的 GPU 检测机制：
1. 自动检测 NVIDIA GPU
2. 检测 CUDA 版本
3. 选择最佳的 llama-server 二进制
4. 计算每个模型的 NGL (GPU layers)
"""

import subprocess
import json
import re
from pathlib import Path
from typing import Dict, Optional, List


class GPUDetector:
    def __init__(self):
        self.gpu_info = None
        self.cuda_version = None
        self.best_binary = None

    def detect_nvidia_gpu(self) -> Optional[Dict]:
        """检测 NVIDIA GPU"""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version,cuda_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                self.gpu_info = {
                    "name": parts[0],
                    "total_mb": int(parts[1]),
                    "total_gb": round(int(parts[1]) / 1024, 2),
                    "driver_version": parts[2],
                    "cuda_version": parts[3],
                }
                return self.gpu_info
        except Exception as e:
            pass
        return None

    def detect_cuda_version(self) -> Optional[str]:
        """检测 CUDA 版本"""
        if self.gpu_info:
            self.cuda_version = self.gpu_info.get("cuda_version", "")
            return self.cuda_version

        # 尝试从 nvcc 检测
        try:
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                match = re.search(r"release (\d+\.\d+)", result.stdout)
                if match:
                    self.cuda_version = match.group(1)
                    return self.cuda_version
        except:
            pass

        return None

    def select_best_binary(self, runtime_dir: Path) -> Optional[Path]:
        """选择最佳的 llama-server 二进制

        优先级：
        1. llama-server-cuda-12.4.exe (CUDA 12.4)
        2. llama-server-cuda-11.8.exe (CUDA 11.8)
        3. llama-server-cuda.exe (通用 CUDA)
        4. llama-server-vulkan.exe (Vulkan)
        5. llama-server.exe (CPU)
        """
        binaries = [
            ("llama-server-cuda-12.4.exe", "CUDA 12.4", True),
            ("llama-server-cuda-11.8.exe", "CUDA 11.8", True),
            ("llama-server-cuda.exe", "CUDA (通用)", True),
            ("llama-server-vulkan.exe", "Vulkan", True),
            ("llama-server.exe", "CPU", False),
        ]

        for name, desc, is_gpu in binaries:
            binary = runtime_dir / name
            if binary.exists():
                self.best_binary = {
                    "path": binary,
                    "name": name,
                    "description": desc,
                    "is_gpu": is_gpu,
                }
                return binary

        return None

    def calculate_ngl_for_model(self, model_path: Path, vram_free_mb: int) -> int:
        """为特定模型计算 NGL

        智能计算策略：
        - model < vram*0.7  -> 99 (全部 GPU)
        - model < vram*1.2  -> 99 (全部 GPU + KV cache)
        - model > vram*3    -> 部分卸载（尽可能使用GPU）
        - 其他             -> 部分卸载
        """
        model_mb = model_path.stat().st_size / (1024 * 1024)

        if vram_free_mb == 0:
            return 0

        # 全部 GPU
        if model_mb <= vram_free_mb * 0.7:
            return 99

        # 全部 GPU + KV cache
        if model_mb <= vram_free_mb * 1.2:
            return 99

        # 部分卸载（即使模型很大，也尽量使用一些GPU层）
        # 公式：可用显存的70%用于模型层，每层平均约占模型大小的1/80
        vram_for_model = vram_free_mb * 0.7
        avg_layer_mb = model_mb / 80  # 假设模型约80层
        ngl = int(vram_for_model / avg_layer_mb)

        # 确保至少使用一些GPU层（如果有GPU的话）
        ngl = max(1, min(99, ngl))

        return ngl

    def get_all_models_info(self, models_dir: Path) -> List[Dict]:
        """获取所有模型的信息和推荐配置"""
        models = []
        vram_free_mb = 0

        if self.gpu_info:
            # 获取可用显存
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    vram_free_mb = int(result.stdout.strip())
            except:
                pass

        for gguf in models_dir.glob("*.gguf"):
            model_mb = gguf.stat().st_size / (1024 * 1024)
            ngl = self.calculate_ngl_for_model(gguf, vram_free_mb)

            # 估计参数量
            name = gguf.name.lower()
            if "1.8b" in name or "1_8b" in name:
                params = "1.8B"
                vram_req = "2-4 GB"
            elif "3b" in name:
                params = "3B"
                vram_req = "4-6 GB"
            elif "7b" in name:
                params = "7B"
                vram_req = "8-12 GB"
            elif "35b" in name:
                params = "35B"
                vram_req = "20-24 GB"
            else:
                params = "Unknown"
                vram_req = "Unknown"

            models.append({
                "name": gguf.name,
                "path": str(gguf),
                "size_mb": round(model_mb, 2),
                "size_gb": round(model_mb / 1024, 2),
                "params": params,
                "vram_required": vram_req,
                "ngl": ngl,
                "mode": "GPU" if ngl > 0 else "CPU",
            })

        return sorted(models, key=lambda m: m["size_mb"])

    def print_status(self):
        """打印 GPU 状态"""
        print("\n" + "=" * 60)
        print("  Hermes GPU Detection")
        print("=" * 60)

        gpu = self.detect_nvidia_gpu()
        if gpu:
            print(f"\n[OK] NVIDIA GPU detected:")
            print(f"  Name: {gpu['name']}")
            print(f"  VRAM: {gpu['total_gb']:.2f} GB")
            print(f"  Driver: {gpu['driver_version']}")
            print(f"  CUDA: {gpu['cuda_version']}")
        else:
            print("\n[X] No NVIDIA GPU detected")
            print("  Will use CPU mode")

        cuda = self.detect_cuda_version()
        if cuda:
            print(f"\n[OK] CUDA version: {cuda}")

        if self.best_binary:
            print(f"\n[OK] Recommended binary: {self.best_binary['name']}")
            print(f"  Description: {self.best_binary['description']}")
            print(f"  Path: {self.best_binary['path']}")

        print("\n" + "=" * 60)

    def to_json(self) -> str:
        """输出 JSON 格式"""
        return json.dumps({
            "gpu": self.gpu_info,
            "cuda_version": self.cuda_version,
            "best_binary": {
                "name": self.best_binary["name"],
                "description": self.best_binary["description"],
                "is_gpu": self.best_binary["is_gpu"],
            } if self.best_binary else None,
        }, indent=2, ensure_ascii=False)


def main():
    import sys

    hermes_root = Path(__file__).parent.parent.parent
    runtime_dir = hermes_root / "runtime"
    models_dir = hermes_root / "data" / "models"

    detector = GPUDetector()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        detector.detect_nvidia_gpu()
        detector.detect_cuda_version()
        detector.select_best_binary(runtime_dir)
        detector.print_status()

    elif cmd == "json":
        detector.detect_nvidia_gpu()
        detector.detect_cuda_version()
        detector.select_best_binary(runtime_dir)
        print(detector.to_json())

    elif cmd == "models":
        detector.detect_nvidia_gpu()
        models = detector.get_all_models_info(models_dir)
        print(json.dumps(models, indent=2, ensure_ascii=False))

    elif cmd == "ngl" and len(sys.argv) > 2:
        model_path = Path(sys.argv[2])
        if not model_path.exists():
            model_path = models_dir / model_path
        if model_path.exists():
            detector.detect_nvidia_gpu()
            vram_free = 0
            if detector.gpu_info:
                try:
                    result = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        vram_free = int(result.stdout.strip())
                except:
                    pass
            ngl = detector.calculate_ngl_for_model(model_path, vram_free)
            print(ngl)
        else:
            print("0")

    else:
        print("用法:")
        print("  python gpu_detector.py status          # 显示 GPU 状态")
        print("  python gpu_detector.py json            # 输出 JSON 格式")
        print("  python gpu_detector.py models          # 显示所有模型的 NGL 配置")
        print("  python gpu_detector.py ngl <model.gguf> # 计算指定模型的 NGL")


if __name__ == "__main__":
    main()