@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set ENGINE_DIR=%~dp0
set ENV_DIR=%ENGINE_DIR%env

echo.
echo ================================================
echo    Firefly GPT-SoVITS 语音引擎一键安装
echo ================================================
echo.
echo 本脚本将在 engine\env\ 下创建 Python 虚拟环境
echo 并安装 GPU 推理所需的所有依赖（约 3-5 GB）
echo 请确保网络畅通，预计耗时 10-20 分钟
echo.

:: 修复 Windows GBK 编码下读取 UTF-8 文件导致 pip install -r 失败的问题
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

:: ── 1. 检查 Python ──
echo [1/4] 检查系统 Python ...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 未检测到 Python，请先安装 Python 3.10 或 3.11
    echo         下载地址: https://www.python.org/downloads/
    echo         安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

for /f "tokens=*" %%a in ('python --version 2^>^&1') do set PY_VER=%%a
echo   • 检测到: %PY_VER%

:: 检查版本
python -c "import sys; sys.exit(0 if sys.version_info[:2] in [(3,10),(3,11)] else 1)" 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] 建议使用 Python 3.10 或 3.11，当前版本可能不兼容
    echo          是否继续？按任意键继续，Ctrl+C 取消...
    pause >nul
)

:: ── 2. 创建 venv ──
echo.
echo [2/4] 创建虚拟环境: %ENV_DIR%
if exist "%ENV_DIR%" (
    echo   • 检测到已有 env 目录，跳过创建
) else (
    python -m venv "%ENV_DIR%"
    if %errorlevel% neq 0 (
        echo [ERROR] 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo   • 虚拟环境创建成功
)

call "%ENV_DIR%\Scripts\activate.bat"
pip install --upgrade pip -q
echo   • pip 已升级

:: ── 3. 安装 PyTorch CUDA ──
echo.
echo [3/4] 安装 PyTorch (CUDA 12.4) ...
echo   • 下载中，文件较大（约 2.5 GB）请耐心等待...
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
if %errorlevel% neq 0 (
    echo [WARNING] PyTorch CUDA 安装失败，尝试 CPU 版本...
    pip install torch torchaudio
)

:: 验证 CUDA
python -c "import torch; print('   CUDA 可用:', torch.cuda.is_available()); print('   GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

:: ── 4. 安装推理依赖 ──
echo.
echo [4/4] 安装推理依赖（约 30 个包）...
pip install -r "%ENGINE_DIR%requirements_infer.txt"
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] 部分依赖安装失败，可能是网络问题
    echo          请手动运行: %ENV_DIR%\Scripts\pip.exe install -r "%ENGINE_DIR%requirements_infer.txt"
)

:: ── 完成 ──
echo.
echo ================================================
echo              !  安装完成！
echo ================================================
echo   环境路径: %ENV_DIR%
echo   Python  : %ENV_DIR%\Scripts\python.exe
echo.
echo   重启应用后自动检测，无需手动配置
echo ================================================
echo.
pause
