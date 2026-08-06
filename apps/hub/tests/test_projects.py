"""projects 读取测试：目录结构、摘要提取、空目录兜底。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hub.projects import list_projects


def _make_project(tmp_path: Path, name: str, content: str):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "CURRENT_STATE.md").write_text(content, encoding="utf-8")
    return d


def test_empty_dir(tmp_path):
    assert list_projects(tmp_path) == []


def test_single_project_summary(tmp_path):
    _make_project(
        tmp_path,
        "firefly-bot",
        "# CURRENT_STATE\n\n> 机器无关接管文件\n\n## 运行状态\n- 机器人在线\n- 服务 RUNNING\n\n## 能力\n- 长期记忆\n",
    )
    projs = list_projects(tmp_path)
    assert len(projs) == 1
    p = projs[0]
    assert p["name"] == "firefly-bot"
    assert "机器人在线" in p["summary"]
    assert "服务 RUNNING" in p["summary"]
    assert len(p["summary"]) < 300  # 只取头部摘要，不全量
    assert p["updated_at"]


def test_multiple_projects_order(tmp_path):
    _make_project(tmp_path, "b-proj", "# B\n- 状态1\n")
    _make_project(tmp_path, "a-proj", "# A\n- 状态2\n")
    names = [p["name"] for p in list_projects(tmp_path)]
    assert names == ["a-proj", "b-proj"]  # 排序稳定


def test_no_state_file_falls_back_readme(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "README.md").write_text("# 项目 X\n- 说明\n", encoding="utf-8")
    projs = list_projects(tmp_path)
    assert projs[0]["name"] == "x"
    assert "说明" in projs[0]["summary"]


def test_env_dir_override(tmp_path, monkeypatch):
    _make_project(tmp_path, "p", "# P\n- ok\n")
    monkeypatch.setenv("PCH_PROJECTS_DIR", str(tmp_path))
    assert list_projects()[0]["name"] == "p"
