#!/usr/bin/env python3
"""Hermes Model Launcher - 图形化模型选择器"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path


class ModelLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("Hermes Model Launcher")
        self.root.geometry("500x400")
        self.root.resizable(False, False)

        # 设置图标（可选）
        try:
            self.root.iconbitmap("hermes.ico")
        except:
            pass

        # 获取模型列表
        self.models = self._scan_models()

        # 创建界面
        self._create_widgets()

    def _scan_models(self):
        """扫描模型目录"""
        models_dir = Path(__file__).parent.parent.parent / "data" / "models"
        models = []
        for gguf in models_dir.glob("*.gguf"):
            size_gb = gguf.stat().st_size / (1024 * 1024 * 1024)
            models.append({
                "name": gguf.name,
                "path": str(gguf),
                "size_gb": round(size_gb, 2),
            })
        return sorted(models, key=lambda m: m["size_gb"])

    def _create_widgets(self):
        # 标题
        title_label = ttk.Label(
            self.root,
            text="选择模型启动",
            font=("Arial", 14, "bold")
        )
        title_label.pack(pady=20)

        # 模型列表
        self.model_var = tk.StringVar()
        self.model_combobox = ttk.Combobox(
            self.root,
            textvariable=self.model_var,
            values=[m["name"] for m in self.models],
            state="readonly",
            width=50
        )
        if self.models:
            self.model_combobox.current(0)
        self.model_combobox.pack(pady=10)

        # 模型信息
        self.info_frame = ttk.LabelFrame(self.root, text="模型信息")
        self.info_frame.pack(pady=10, padx=20, fill="x")

        self.info_text = tk.Text(self.info_frame, height=3, state="disabled")
        self.info_text.pack(pady=5, padx=10, fill="x")

        # 更新信息
        if self.models:
            self._update_model_info(self.models[0])

        self.model_combobox.bind("<<ComboboxSelected>>", self._on_model_select)

        # 启动按钮
        self.launch_btn = ttk.Button(
            self.root,
            text="启动 Hermes",
            command=self._launch,
            width=20
        )
        self.launch_btn.pack(pady=20)

        # 停止按钮
        self.stop_btn = ttk.Button(
            self.root,
            text="停止服务",
            command=self._stop,
            width=20
        )
        self.stop_btn.pack(pady=5)

        # 状态标签
        self.status_label = ttk.Label(self.root, text="")
        self.status_label.pack(pady=10)

    def _on_model_select(self, event):
        """选择模型时更新信息"""
        selected = self.model_var.get()
        for model in self.models:
            if model["name"] == selected:
                self._update_model_info(model)
                break

    def _update_model_info(self, model):
        """更新模型信息显示"""
        self.info_text.config(state="normal")
        self.info_text.delete(1.0, tk.END)
        info = f"名称: {model['name']}\n"
        info += f"大小: {model['size_gb']} GB\n"
        info += f"路径: {model['path']}"
        self.info_text.insert(1.0, info)
        self.info_text.config(state="disabled")

    def _launch(self):
        """启动 Hermes"""
        selected = self.model_var.get()
        if not selected:
            messagebox.showwarning("警告", "请选择一个模型")
            return

        # 找到选中的模型
        model_path = None
        for model in self.models:
            if model["name"] == selected:
                model_path = model["path"]
                break

        if not model_path:
            messagebox.showerror("错误", "未找到模型")
            return

        self.status_label.config(text="正在启动...")
        self.root.update()

        try:
            # 设置环境变量并启动
            env = os.environ.copy()
            env["MODEL"] = model_path

            # 启动 hermes-all.bat
            subprocess.Popen(
                ["cmd", "/c", "bin\\hermes-all.bat"],
                env=env,
                cwd=str(Path(__file__).parent.parent.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )

            self.status_label.config(text=f"已启动: {selected}")
            messagebox.showinfo("成功", f"正在启动 {selected}")

        except Exception as e:
            self.status_label.config(text="启动失败")
            messagebox.showerror("错误", f"启动失败: {str(e)}")

    def _stop(self):
        """停止服务"""
        self.status_label.config(text="正在停止...")
        self.root.update()

        try:
            subprocess.run(
                ["bin\\hermes-stop.bat"],
                cwd=str(Path(__file__).parent.parent.parent),
                check=True
            )
            self.status_label.config(text="已停止")
            messagebox.showinfo("成功", "服务已停止")
        except Exception as e:
            self.status_label.config(text="停止失败")
            messagebox.showerror("错误", f"停止失败: {str(e)}")


def main():
    root = tk.Tk()
    app = ModelLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()