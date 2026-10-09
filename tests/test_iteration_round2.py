"""第二轮迭代的回归：--list-voices、范本检查契约锁定、speaker 字幕标签。

守住的都是本轮新增/变更的行为：

  · narration.py --list-voices：无 key、无 --source 即可列出内置音色（CLI 承诺）；
  · check_gates.py references/template.html --no-browser 的文档化输出
    （恰好 3 条 error + 超 3 道门禁 warning）——范本不是可交付页，这条锁定
    "契约演示物恒报这些错"的行为，防止检查器漂移把范本误判成可交付；
  · 范本字幕带的 #cap-speaker：对话课的说话人标签真实渲染（浏览器驱动，
    无 Chrome / websocket-client 时跳过），且 #cap-text 保持纯净原文
    （字幕唯一来源契约不破，stage.md §4）。
"""
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import REPO, SCRIPTS, TEMPLATE_HTML

HAVE_CHROME = shutil.which("chromium") or shutil.which("google-chrome") \
    or shutil.which("chromium-browser") or shutil.which("chrome") \
    or shutil.which("microsoft-edge")
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
try:
    import websocket  # noqa: F401
    HAVE_WEBSOCKET = True
except ImportError:
    HAVE_WEBSOCKET = False

_IDS = ["opening", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5", "seg-6",
        "closing"]


def _aligned_manifest(speaker=None):
    """与范本页面场景对齐的 8 场景 manifest；speaker 落在 seg-1 第一句上。"""
    scenes = []
    t = 0.0
    for i, sid in enumerate(_IDS):
        sentences = []
        for k in range(1, 4):
            s = {"start": round(t, 3), "duration": 1.0,
                 "text": f"{sid}第{k}句。"}
            if speaker and sid == "seg-1" and k == 1:
                s["speaker"] = speaker
            sentences.append(s)
            t = round(t + 1.4, 3)
        end = round(sentences[-1]["start"] + 1.0, 3)
        scenes.append({
            "step_id": sid,
            "title": "开场" if sid == "opening" else ("小结" if sid == "closing"
                                                      else f"第{i}节"),
            "tagline": "",
            "start": sentences[0]["start"],
            "duration": round(end - sentences[0]["start"], 3),
            "end": end,
            "sentences": sentences,
        })
        t = round(end + 0.5, 3)
    return {
        "schema_version": 1, "status": "ok", "title": "说话人测试课",
        "voice_id": "冰糖", "audio": "combined.wav",
        "total_duration": round(scenes[-1]["end"], 3),
        "degraded": {"tts_silence_fallback_count": 0, "dropped_sentence_count": 0},
        "scenes": scenes,
    }


@pytest.fixture(scope="module")
def speaker_lesson(tmp_path_factory):
    """跑 build_timeline → build_page 出一份含 speaker 句子的成品页。"""
    root = tmp_path_factory.mktemp("speaker-lesson")
    proj = root / "proj"
    (proj / "audio").mkdir(parents=True)
    manifest = _aligned_manifest(speaker="小明")
    (proj / "audio" / "narration_timing.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={manifest['total_duration']}",
         "-ar", "24000", "-ac", "1", str(proj / "audio" / "combined.wav")],
        capture_output=True)
    assert r.returncode == 0

    def _run(args):
        return subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, cwd=str(root), timeout=300)

    r = _run([str(SCRIPTS / "build_timeline.py"),
              "--timing", str(proj / "audio" / "narration_timing.json"),
              "-o", str(proj / "timeline.html")])
    assert r.returncode == 0, r.stderr
    draft = proj / "page-draft.html"
    draft.write_text(TEMPLATE_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    page_dir = root / "lesson"
    r = _run([str(SCRIPTS / "build_page.py"),
              "--template", str(draft),
              "--timeline", str(proj / "timeline.html"),
              "--audio", str(proj / "audio" / "combined.wav"),
              "--timing", str(proj / "audio" / "narration_timing.json"),
              "-o", str(page_dir / "index.html")])
    assert r.returncode == 0, r.stderr
    return page_dir


# ── 1. narration.py --list-voices ──────────────────────────────────

def test_narration_list_voices():
    """无 key、无 --source 就能列出内置音色；冰糖是默认。"""
    r = subprocess.run([sys.executable, str(SCRIPTS / "narration.py"),
                        "--list-voices"], capture_output=True, text=True,
                       cwd=str(REPO), timeout=60)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for v in ["冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]:
        assert v in out, f"音色表缺少 {v}"
    assert "冰糖  ← 默认" in out


def test_narration_list_voices_ignores_other_flags():
    """--list-voices 不需要 -o/--output，也不触发 API key 检查。"""
    r = subprocess.run([sys.executable, str(SCRIPTS / "narration.py"),
                        "--list-voices", "--dry-run"],
                       capture_output=True, text=True, cwd=str(REPO), timeout=60)
    assert r.returncode == 0, r.stderr
    assert "API key" not in r.stdout + r.stderr


# ── 2. 范本检查契约锁定 ───────────────────────────────────────────

def test_check_gates_template_contract():
    """范本恒报文档化的 3 条 error + 超 3 道门禁 warning，一条不多一条不少。"""
    r = subprocess.run([sys.executable, str(SCRIPTS / "check_gates.py"),
                        str(REPO / "references" / "template.html"),
                        "--no-browser"], capture_output=True, text=True,
                       cwd=str(REPO), timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "范本不可交付，check_gates 必须失败"
    assert "[warn] 页面配了 5 道门禁" in out
    errs = [ln for ln in out.splitlines() if "[error]" in ln]
    assert len(errs) == 3, f"范本应恰好报 3 条 error，实际：\n{out}"
    for key in ("主音频文件不存在", "缺少 narration_timing.json",
                "runtime 不存在"):
        assert any(key in ln for ln in errs), f"缺少文档化 error：{key}"


# ── 3. speaker 字幕标签 ───────────────────────────────────────────

def test_speaker_hook_present(speaker_lesson):
    """成品页保留 #cap-speaker 钩子（模板结构契约）。"""
    html = (speaker_lesson / "index.html").read_text(encoding="utf-8")
    assert 'id="cap-speaker"' in html
    assert 'id="cap-text"' in html


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_speaker_label_renders(speaker_lesson):
    """驱动真实浏览器：speaker 句显示说话人标签，且 #cap-text 仍是纯净原文。"""
    import websocket

    page_dir = speaker_lesson
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    chrome = subprocess.Popen(
        [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
         f"--remote-debugging-port={port}", "--user-data-dir=" + str(page_dir / "_spkprof"),
         (page_dir / "index.html").as_uri()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2))
                tab = [t for t in tabs if t.get("type") == "page"][0]
                ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
                break
            except Exception:
                time.sleep(0.4)
        assert ws is not None, "无法连接 CDP"

        def ev(expr):
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    return m["result"].get("result", {}).get("value")

        assert ev("document.querySelector('#lesson-timeline') !== null"), \
            "页面未就绪"
        # 探针：桩住 currentTime（与 check_gates 同一手法），seek 到 seg-1 第一句
        probe = r"""
(function(){
  var t = 0;
  Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
    configurable: true, get(){ return t; }, set(v){ t = Number(v) || 0; }
  });
  HTMLMediaElement.prototype.play = function(){ return Promise.resolve(); };
  HTMLMediaElement.prototype.pause = function(){};
  var tl = JSON.parse(document.getElementById('lesson-timeline').textContent);
  var s1 = tl.scenes[1];                       // seg-1
  var a = document.getElementById('main-audio');
  a.currentTime = s1.runtime.narration[0].start;  // seg-1 第一句（带 speaker）
  a.dispatchEvent(new Event('timeupdate'));
  return JSON.stringify({
    cap: document.getElementById('cap-text').textContent,
    spk: document.getElementById('cap-speaker').textContent,
    want: s1.runtime.narration[0].text
  });
})()
"""
        rep = json.loads(ev(probe))
        assert rep["cap"] == rep["want"], f"字幕被改写：{rep['cap']!r}"
        assert rep["cap"] == "seg-1第1句。", f"未命中 speaker 句：{rep['cap']!r}"
        assert rep["spk"] == "小明", f"说话人标签未渲染：{rep['spk']!r}"
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)
