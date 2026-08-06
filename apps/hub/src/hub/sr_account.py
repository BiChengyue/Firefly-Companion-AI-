"""SR（星穹铁道）账号数据读取。

数据由笔记本同步脚本（sr_sync.py）定时写入，本模块只读并标注新鲜度。
"""

import json
import os
import time

DATA_PATH = os.environ.get("SR_ACCOUNT_FILE", r"C:\ProgramData\firefly-bot\data\sr_account.json")
MAX_AGE = 2 * 3600  # 2 小时内的数据视为新鲜（同步每小时跑；停摆 >2h 即标陈旧）


def get_sr_account() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"数据未就绪（{type(e).__name__}）", "at": None}
    age = time.time() - data.get("synced_at", 0)
    return {"ok": True, "fresh": age <= MAX_AGE, "age_seconds": int(age), "data": data}
