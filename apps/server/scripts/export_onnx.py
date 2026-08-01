"""一次性 ONNX 模型导出脚本。

在启用 ONNX 引擎前运行一次即可。该脚本会：
1. 下载 PyTorch 模型（~500MB，首次）
2. 导出为 ONNX 格式（~120MB）
3. 保存到 config 中 onnxModelPath 指向的目录（默认 data/onnx_model/）

此后所有启动均走 export=False 快路径（零 multiprocessing、零 GIL 阻塞、零额外内存）。

用法：
    cd apps/server
    ..\..\.venv\Scripts\python.exe scripts\export_onnx.py
"""

import sys
import os
import time
from pathlib import Path

_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# 保存目录：项目根 / data / onnx_model
_EXPORT_DIR = Path(__file__).resolve().parents[3] / "data" / "onnx_model"


def main():
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("[ONNX Export] exporting model (first time downloads ~500MB)")
    print(f"[ONNX Export] model:  {_MODEL}")
    print(f"[ONNX Export] output: {_EXPORT_DIR}")
    print("=" * 60)

    t0 = time.time()

    # ---- Step 1: Export PyTorch -> ONNX ----
    print("\n[Step 1/3] PyTorch -> ONNX ...")
    from optimum.onnxruntime import ORTModelForFeatureExtraction

    model = ORTModelForFeatureExtraction.from_pretrained(
        _MODEL,
        export=True,
        provider="CPUExecutionProvider",
    )
    model.save_pretrained(str(_EXPORT_DIR))
    print(f"  [OK] exported + saved ({time.time() - t0:.0f}s)")

    # ---- Step 2: Tokenizer ----
    print("\n[Step 2/3] loading tokenizer ...")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(_MODEL)
    tokenizer.save_pretrained(str(_EXPORT_DIR))
    print("  [OK] tokenizer saved")

    # ---- Step 3: verify ----
    print("\n[Step 3/3] verifying inference ...")
    import numpy as np

    test = "test ONNX inference"
    inputs = tokenizer(test, return_tensors="np", padding=True, truncation=True, max_length=512)
    outputs = model(**inputs)
    vec = outputs.last_hidden_state.mean(axis=1)[0]
    print(f"  [OK] shape={vec.shape[0]}, elapsed={time.time() - t0:.0f}s")

    # ---- confirm model.onnx exists ----
    onnx_files = list(_EXPORT_DIR.rglob("*.onnx"))
    if onnx_files:
        for f in onnx_files:
            print(f"\n[ONNX Export] [OK] {f.name} ({f.stat().st_size / 1024**2:.0f} MB)")
    else:
        print("\n[ONNX Export] [WARN] no .onnx file found -- check export")

    print("\n" + "=" * 60)
    print(f"[ONNX Export] done! total: {time.time() - t0:.0f}s")
    print(f"[ONNX Export] model saved to: {_EXPORT_DIR}")
    print("[ONNX Export] you can now enable embeddingEngine: onnx")
    print("=" * 60)


if __name__ == "__main__":
    main()
