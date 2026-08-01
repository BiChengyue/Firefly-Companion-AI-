"""统一测试入口 — 运行全部记忆系统测试并生成覆盖率报告。

用法:
    python run_tests.py              # 运行全部（不含 onnx 标记）
    python run_tests.py --all        # 运行全部含 onnx 测试
    python run_tests.py --cov        # 运行并生成 HTML 覆盖率报告
"""
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS_DIR = HERE / "tests"


def main():
    args = sys.argv[1:]
    run_all = "--all" in args
    with_cov = "--cov" in args

    cmd = [
        sys.executable, "-m", "pytest",
        str(TESTS_DIR),
        "-v",
        "--tb=short",
        "-p", "no:warnings",
    ]

    if not run_all:
        cmd.extend(["-m", "not onnx"])

    if with_cov:
        cmd.extend([
            "--cov=app/core/memory",
            "--cov=app/config",
            "--cov-report=term",
            "--cov-report=html:htmlcov",
        ])

    print(f"[run_tests] {'ALL tests' if run_all else 'non-ONNX tests'} {'with coverage' if with_cov else ''}")
    print(f"[run_tests] {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=str(HERE))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
