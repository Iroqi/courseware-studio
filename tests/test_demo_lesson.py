"""demo_lesson.py：无 TTS key 构建样例课件的回归。

demo 脚本是"给开发/使用者本地看成品"的开发工具，不是课件自建验证器——
测试只守三条：它能产出结构完整、通过 check_gates 的成品；它拒绝把产物
写进本技能目录（与各写盘入口共用同一道守卫）；有 Chrome 时生产检查器的
浏览器冒烟与 `--require-browser`（CI 严格模式，SKILL.md §8）必须真跑。
"""
import json
import re
import shutil
import subprocess
import sys

import pytest

from conftest import REPO, SCRIPTS, TEMPLATE_NARRATION

# 与生产查找器（check_gates._find_chrome）同序：google-chrome 在前。
HAVE_CHROME = shutil.which("google-chrome") or shutil.which("chromium") \
    or shutil.which("chromium-browser") or shutil.which("chrome") \
    or shutil.which("microsoft-edge")
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


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
    # 有 Chrome 时必须真做了浏览器冒烟（CI 装了 Chrome，锁"生产检查器的
    # 浏览器路径真的执行过"，而不是静默退化成静态检查）。
    if HAVE_CHROME:
        assert "[browser] captions=" in r.stdout, \
            "浏览器冒烟未执行：CI 有 Chrome 时 demo 的检查必须走浏览器路径"
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
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_demo_export_flag(tmp_path):
    """--export 时产出 lesson.mp4，且成片真实含视频流、时长与时间轴一致。"""
    r = _run_demo(tmp_path / "out", "--export")
    assert r.returncode == 0, r.stderr + r.stdout
    mp4 = tmp_path / "out" / "lesson.mp4"
    assert mp4.is_file()
    assert "[demo] 完成" in r.stdout

    # 时长契约：成片时长 ≈ 时间轴 total_duration（±0.6s）。
    # 只断言"mp4 存在"拦不住"画面全空/只混了音轨"这类回归。
    timing = json.loads(
        (tmp_path / "out" / "lesson" / "audio" / "narration_timing.json")
        .read_text(encoding="utf-8"))
    ff = subprocess.run(["ffmpeg", "-i", str(mp4)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", ff.stderr)
    assert m, f"无法从 ffmpeg 解析 mp4 时长：{ff.stderr}"
    h, mi, s = (int(m.group(1)), int(m.group(2)), float(m.group(3)))
    got = h * 3600 + mi * 60 + s
    assert abs(got - timing["total_duration"]) <= 0.6, \
        f"成片时长 {got:.2f}s 与时间轴 {timing['total_duration']}s 不符"
    # 必须真有视频流：纯音频 mux 或截图全失败都会在这里现形。
    assert re.search(r"Stream #\d+:\d+.*Video:", ff.stderr), \
        "mp4 里没有视频流（成片缺帧）"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
def test_demo_check_gates_require_browser(tmp_path):
    """SKILL §8 的"CI 严格模式"契约：--require-browser 必须真实执行浏览器冒烟。

    demo_lesson 内部调 check_gates 时不带该开关（本地无 Chrome 也要能过），
    CI 严格模式因此从未被任何测试锁过——这里直接补上：有 Chrome 时，
    --require-browser 必须返回 0 且报告浏览器冒烟数据。
    """
    r = _run_demo(tmp_path / "out")
    assert r.returncode == 0, r.stderr + r.stdout
    page = tmp_path / "out" / "lesson"
    cg = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_gates.py"),
         str(page), "--require-browser"],
        capture_output=True, text=True, cwd=str(REPO), timeout=600)
    assert cg.returncode == 0, cg.stderr + cg.stdout
    assert "[browser] captions=" in cg.stdout, "浏览器冒烟未执行"
    assert "浏览器冒烟通过" in cg.stdout


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
