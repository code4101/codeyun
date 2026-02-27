
import os
import subprocess
import shutil
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

"""
================================================================================
脚本名称: build_dsp_static.py
功能描述: 
    自动化构建并部署“戴森球计划量化计算器”前端静态资源。
    该脚本会将独立的 React 项目 (dsp-calc) 编译为静态文件，
    并复制到 CodeYun 项目的 public 目录下，以便通过 Iframe 集成。

使用场景:
    当 dsp-calc 源码更新后，运行此脚本将最新版本同步到 CodeYun。

工作流程:
    1. 检查并安装 dsp-calc 的依赖 (npm install)。
    2. 使用 Vite 构建 dsp-calc，指定 base 路径为 /dsp-calc/ (npm run build)。
    3. 清理 CodeYun 前端 public/dsp-calc 旧目录。
    4. 将构建产物 (dist) 复制到 public/dsp-calc。

依赖环境:
    - Python 3
    - Node.js & npm (需在系统环境变量中)
================================================================================
"""

# 配置路径
# DSP 计算器源码目录 (独立仓库路径)
# 优先从环境变量读取，否则使用默认值（仅供参考，建议在 .env 中配置）
DEFAULT_DSP_DIR = r"D:\home\chenkunze\slns+\dsp-calc"
DSP_SOURCE_DIR = os.getenv("DSP_SOURCE_DIR", DEFAULT_DSP_DIR)

if not os.path.exists(DSP_SOURCE_DIR):
    print(f"警告: DSP 源码目录不存在: {DSP_SOURCE_DIR}")
    print("请在 .env 文件中配置 DSP_SOURCE_DIR 或检查路径。")
    # 不立即退出，让后续检查决定

# CodeYun 前端静态资源目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODEYUN_PUBLIC_DIR = os.path.join(PROJECT_ROOT, "frontend", "public")
# 目标子目录名称 (对应 URL 路径 /dsp-calc/)
TARGET_DIR_NAME = "dsp-calc"
# 最终部署路径
TARGET_DIR = os.path.join(CODEYUN_PUBLIC_DIR, TARGET_DIR_NAME)

def run_command(command, cwd=None):
    """在指定目录执行 Shell 命令"""
    print(f"正在执行: {command} (目录: {cwd})")
    try:
        # Windows 下需设置 shell=True 以正确处理 npm/vite 命令
        subprocess.check_call(command, cwd=cwd, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"命令执行失败: {e}")
        sys.exit(1)

def main():
    print("=== 开始构建戴森球计划计算器静态资源 ===")
    
    # 1. 检查依赖安装
    node_modules_path = os.path.join(DSP_SOURCE_DIR, "node_modules")
    if not os.path.exists(node_modules_path):
        print("检测到未安装依赖，正在执行 npm install...")
        run_command("npm install", cwd=DSP_SOURCE_DIR)
    
    # 2. 构建项目
    # 使用 --base 参数指定部署子路径，确保资源引用正确
    print(f"正在构建项目 (Base URL: /{TARGET_DIR_NAME}/)...")
    run_command(f"npm run build -- --base=/{TARGET_DIR_NAME}/", cwd=DSP_SOURCE_DIR)
    
    # 3. 清理旧文件
    if os.path.exists(TARGET_DIR):
        print(f"正在清理目标目录: {TARGET_DIR}")
        shutil.rmtree(TARGET_DIR)
    
    # 4. 复制构建产物
    dist_dir = os.path.join(DSP_SOURCE_DIR, "dist")
    if not os.path.exists(dist_dir):
        print(f"错误: 构建目录 {dist_dir} 不存在，请检查构建过程是否出错。")
        sys.exit(1)
        
    print(f"正在将构建产物从 {dist_dir} 复制到 {TARGET_DIR}...")
    shutil.copytree(dist_dir, TARGET_DIR)
    
    print("=== 构建与部署完成 ===")
    print(f"访问路径: /{TARGET_DIR_NAME}/index.html")

if __name__ == "__main__":
    main()
