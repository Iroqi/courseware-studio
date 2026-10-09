"""demo_lesson.py：无 TTS key 构建样例课件的回归。

demo 脚本是"给开发/使用者本地看成品"的开发工具，不是课件自建验证器——
测试只守两条：它能产出结构完整、通过 check_gates 的成品；它拒绝把产物
写进本技能目录（与各写盘入口共用同一道守卫）。
"""
import json
import shutil
import subprocess
import sys

import pytest

from conftest import REPO, SCRIPTS, TEMPLATE_NARRATION

HAVE_CHROME = shutil.which("chromium") or shutil.which("google-chrome") \
    or shutil.which("chromium-browser") or shutil.which("chrome") \
    or shutil.which("microsoft-edge")


def _run_demo(out_dir, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "demo_lesson.py"),
         "--out", str(out_dir), *extra],
        capture_output=True, text=True, cwd=str(REPO), timeout=900)


def test_demo_builds_lesson(tmp_path):
    r = _run_demo(tmp_path / "out")
    assert r.returncode == 0, r.stderr + r.stdout
    page = tmp_path / "out" / "lesson"
    assert (page / "index.html").is_file()
    assert (page / "interactive_runtime.js").is_file()
    assert (page / "audio" / "combined.wav").is_file()
    assert (page / "audio" / "narration_timing.json").is_file()
    # 交付检查真的跑了：静态 + 浏览器冒烟都要过（无 Chrome 时静态也必须过）
    assert "[static] scenes=8" in r.stdout
    assert "完成" in r.stdout


def test_demo_timing_matches_template_narration(tmp_path):
    """占位时间轴的句子必须来自范本讲稿的分句结果，且场景与范本页面一致。"""
    sys.path.insert(0, str(SCRIPTS))
    from _script_utils import split_sentences

    source = json.loads(TEMPLATE_NARRATION.read_text(encoding="utf-8"))
    r = _run_demo(tmp_path / "out")
    assert r.returncode == 0, r.stderr + r.stdout
    timing = json.loads(
        (tmp_path / "out" / "lesson" / "audio" / "narration_timing.json")
        .read_text(encoding="utf-8"))
    ids = [sc["step_id"] for sc in timing["scenes"]]
    assert ids == ["opening", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5",
                   "seg-6", "closing"]
    joined = "".join(s["text"] for sc in timing["scenes"]
                     for s in sc["sentences"])
    # 范本正文按同一套分句逻辑切句，一条不丢（分句只切不丢）
    for seg in source["segments"]:
        for s in split_sentences(seg["text"]):
            assert s in joined, f"范本句子未进入字幕：{s}"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
def test_demo_export_flag(tmp_path):
    """--export 时额外产出 lesson.mp4（依赖本机 Chrome + ffmpeg）。"""
    r = _run_demo(tmp_path / "out", "--export")
    assert r.returncode == 0, r.stderr + r.stdout
    assert (tmp_path / "out" / "lesson.mp4").is_file()
    assert "[demo] 完成" in r.stdout


def test_demo_rejects_skill_dir():
    """产物守卫：--out 落在本技能目录（仓库根）内必须 fail-fast。"""
    r = _run_demo(REPO / "some-inner-dir")
    assert r.returncode != 0
    assert "[guard]" in (r.stderr + r.stdout)


def test_demo_page_uses_template_contract(tmp_path):
    """成品页必须保留 QA 契约钩子（范本结构），否则 check_gates 的通过就是假绿。"""
    r = _run_demo(tmp_path / "out")
    assert r.returncode == 0, r.stderr + r.stdout
    html = (tmp_path / "out" / "lesson" / "index.html").read_text(encoding="utf-8")
    for hook in ('id="lesson-timeline"', 'id="cap-text"', 'id="cap-speaker"',
                 'id="main-audio"', 'id="gate"', 'id="gate-host"', 'id="gate-next"',
                 'id="gate-go"', 'id="pregate"', 'id="stage"',
                 "window.__coursewareRenderTrace"):
        assert hook in html, f"成品页缺少 QA 钩子：{hook}"
