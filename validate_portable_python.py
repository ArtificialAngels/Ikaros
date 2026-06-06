#!/usr/bin/env python3
"""Portable Python 验证脚本"""

import sys
import os
import subprocess


def check_python_version():
    """检查 Python 版本"""
    print(f"Python 版本: {sys.version.split()[0]}")
    print(f"Python 可执行文件: {sys.executable}")
    return sys.version_info >= (3, 12)


def check_dependencies():
    """检查所有关键依赖"""
    dependencies = [
        # 核心依赖
        ("fastapi", "0.115.0"),
        ("uvicorn", "0.32.0"),
        ("pydantic", "2.9.2"),
        ("openai", "1.54.3"),
        ("requests", "2.32.3"),
        ("httpx", "0.27.2"),
        ("chromadb", "0.5.20"),
        
        # 工具类
        ("tenacity", None),
        ("rich", None),
        ("typer", None),
        ("structlog", None),
        ("loguru", None),
        ("yaml", "6.0.1"),  # pyyaml
        ("dotenv", "1.0.1"),  # python-dotenv
        ("bs4", None),  # beautifulsoup4
        ("lxml", None),
        
        # 安全
        ("cryptography", None),
        
        # WebSocket
        ("websockets", None),
        ("multipart", None),
        
        # 数据处理
        ("numpy", None),
        ("pandas", None),
        
        # 机器学习
        ("torch", None),
        ("cv2", None),
    ]
    
    print("\n依赖检查:")
    all_ok = True
    for pkg, version in dependencies:
        try:
            module = __import__(pkg)
            if version:
                installed = getattr(module, '__version__', 'unknown')
                if installed != version:
                    print(f"  WARNING: {pkg}: installed {installed} (expected {version})")
                else:
                    print(f"  OK: {pkg}: {installed}")
            else:
                print(f"  OK: {pkg}")
        except ImportError as e:
            print(f"  ERROR: {pkg}: not installed ({e})")
            all_ok = False
    
    return all_ok


def check_pip():
    """检查 pip"""
    print("\npip 检查:")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"  OK: pip: {result.stdout.strip()}")
            return True
        else:
            print(f"  ERROR: pip error: {result.stderr}")
            return False
    except Exception as e:
        print(f"  ERROR: pip execution failed: {e}")
        return False


def check_site_packages():
    """检查 site-packages"""
    print("\nsite-packages 检查:")
    import site
    site_packages = site.getsitepackages()
    print(f"  site-packages 路径:")
    for path in site_packages:
        if os.path.exists(path):
            pkg_count = len([f for f in os.listdir(path) if os.path.isdir(os.path.join(path, f))])
            print(f"    OK: {path} ({pkg_count} packages)")
        else:
            print(f"    ERROR: {path} (not found)")


def main():
    print("=" * 60)
    print("Portable Python 完整性验证")
    print("=" * 60)
    
    results = []
    
    # 检查 Python 版本
    print("\n1. Python 版本检查")
    results.append(("Python 版本", check_python_version()))
    
    # 检查依赖
    results.append(("依赖安装", check_dependencies()))
    
    # 检查 pip
    results.append(("pip 可用", check_pip()))
    
    # 检查 site-packages
    check_site_packages()
    
    # 总结
    print("\n" + "=" * 60)
    print("Validation Results:")
    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False
    
    if all_pass:
        print("\nSUCCESS: portable-python is complete and working!")
    else:
        print("\nWARNING: Some issues need to be fixed")


if __name__ == "__main__":
    main()