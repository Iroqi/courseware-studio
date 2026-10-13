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
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import REPO, SCRIPTS, TEMPLATE_HTML

# 浏览器优先级与生产查找器（check_gates._find_chrome）一致：google-chrome 在前。
# Ubuntu 24.04 runner 的 /usr/bin/chromium 是 snap 过渡包，冷启动/稳定性不如
# google-chrome——此前把 chromium 排第一导致同一台机器上部分用例连不上 CDP。
HAVE_CHROME = shutil.which("google-chrome") or shutil.which("chromium") \
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


def _manifest_from(scenes_payload):
    """范本内联时间轴 → narration_timing.json 形状（页面骨架与音频时长同源）。"""
    scenes = []
    for sc in scenes_payload:
        r = sc["runtime"]
        scenes.append({
            "step_id": sc["step_id"],
            "title": (sc.get("content") or {}).get("title") or sc["step_id"],
            "tagline": (sc.get("content") or {}).get("tagline") or "",
            "start": r["start"], "duration": r["duration"], "end": r["end"],
            "sentences": [{"start": n["start"], "duration": n["duration"],
                           "text": n["text"]} for n in r["narration"]],
        })
    return {
        "schema_version": 1, "status": "ok", "title": "范本全量课",
        "voice_id": "冰糖", "audio": "combined.wav",
        "total_duration": round(scenes[-1]["end"], 3),
        "degraded": {"tts_silence_fallback_count": 0, "dropped_sentence_count": 0},
        "scenes": scenes,
    }


def _build_lesson(root, manifest):
    """manifest → 成品页（build_timeline + build_page，与真实交付同一条 CLI 链）。"""
    proj = root / "proj"
    (proj / "audio").mkdir(parents=True, exist_ok=True)
    timing_path = proj / "audio" / "narration_timing.json"
    timing_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={manifest['total_duration']}",
         "-ar", "24000", "-ac", "1", str(proj / "audio" / "combined.wav")],
        capture_output=True)
    assert r.returncode == 0, r.stderr

    def _run(args):
        return subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, cwd=str(root), timeout=600)

    r = _run([str(SCRIPTS / "build_timeline.py"), "--timing", str(timing_path),
              "-o", str(proj / "timeline.html")])
    assert r.returncode == 0, r.stderr
    draft = proj / "page-draft.html"
    draft.write_text(TEMPLATE_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    page_dir = root / "lesson"
    r = _run([str(SCRIPTS / "build_page.py"), "--template", str(draft),
              "--timeline", str(proj / "timeline.html"),
              "--audio", str(proj / "audio" / "combined.wav"),
              "--timing", str(timing_path), "--title", manifest["title"],
              "-o", str(page_dir / "index.html")])
    assert r.returncode == 0, r.stderr
    return page_dir


@pytest.fixture(scope="module")
def full_lesson(tmp_path_factory):
    """时间轴 = 范本自带的那一份（33 句）的成品页。

    speaker_lesson 每场只喂 3 句，够不到 0.13.0 的动效现场：焦点弱化挂在
    seg-2 第 5 句（换组）与 seg-3 第 4 句（聚焦翻车行），大幅运镜要 seg-1 s3 /
    seg-2 s4 这种"真的挪了镜头"的步骤。用范本内联时间轴建页，步数与页面骨架同源。
    """
    root = tmp_path_factory.mktemp("full-lesson")
    raw = TEMPLATE_HTML.read_text(encoding="utf-8")
    m = re.search(r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>(.*?)</script>',
                  raw, re.S)
    assert m, "范本缺少内联时间轴，无从派生全量页面"
    payload = json.loads(m.group(1).strip())
    return _build_lesson(root, _manifest_from(payload["scenes"]))


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
    chrome_err = page_dir / "_spk-stderr.log"
    chrome = subprocess.Popen(
        [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
         f"--remote-debugging-port={port}", "--user-data-dir=" + str(page_dir / "_spkprof"),
         (page_dir / "index.html").as_uri()],
        stdout=subprocess.DEVNULL, stderr=open(chrome_err, "wb"))
    try:
        ws = None
        die_reason = None
        for _ in range(60):
            if chrome.poll() is not None:
                die_reason = (chrome_err.read_text(encoding="utf-8", errors="replace")
                              if chrome_err.exists() else "") or "<无 stderr>"
                break
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2))
                # 优先选中目标页：headless 可能带出 about:blank 等附加 page tab，
                # 只认 URL 含 index.html 的那一个（首个 page tab 可能是空页）。
                pages = [t for t in tabs if t.get("type") == "page"]
                tab = next((t for t in pages if "index.html" in t.get("url", "")),
                           pages[0] if pages else None)
                ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
                break
            except Exception:
                time.sleep(0.4)
        if ws is None:
            detail = f"浏览器进程已退出：{die_reason}" if die_reason else \
                f"CDP 端口 {port} 一直无响应"
            raise AssertionError(f"无法连接 CDP（{HAVE_CHROME}）：{detail}")

        def ev(expr):
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    return m["result"].get("result", {}).get("value")

        # 页面就绪要轮询：CI 上页面解析可能比 CDP 连上慢一拍，单次断言会假失败。
        ready = None
        for _ in range(60):
            ready = ev("document.readyState === 'complete' && "
                       "document.querySelector('#lesson-timeline') !== null")
            if ready:
                break
            time.sleep(0.4)
        assert ready, "页面未就绪"
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
        # 翻句微动效（0.11.0）：字幕文本变化后 .capLift 在类表里、动画生效
        lift = json.loads(ev(
            "(function(){ var t = document.getElementById('cap-text');"
            " return JSON.stringify({cls: t.classList.contains('capLift'),"
            " anim: getComputedStyle(t).animationName}); })()"))
        assert lift["cls"] is True, "翻句后 #cap-text 应带 .capLift"
        assert lift["anim"] == "capLift", f"字幕翻句动画应生效，实际 {lift['anim']!r}"
    finally:
        # 清理不得掩盖原始失败：kill + 短等待，超时也静默（chromium 曾拒绝在
        # 10s 内退出，把"无法连接 CDP"这类真失败吞成 TimeoutExpired）。
        chrome.kill()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


# ── 4. 进度条键盘可达 + ended 重播（0.7.0）──────────────────────────

def test_progress_slider_and_replay_hooks_present():
    """模板结构契约：slider 钩子与重播路径必须长在范本里（无浏览器也可锁）。"""
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # slider：role/tabindex 写在标记里（a11y 树早可见），min/max/now 由 JS
    # 在 TOTAL 就绪后写真实值（占位 0 在标记里）。
    assert 'role="slider"' in html
    assert 'tabindex="0"' in html
    assert 'aria-valuemax="0"' in html
    assert 'barAria' in html
    assert "ArrowRight" in html and "ArrowLeft" in html
    # ended 重播：播放器行 ▶ 在 ended 态点按 = 从头重播，提示文案同步更新
    assert "点 ▶ 重播" in html
    assert "audio.ended" in html
    assert "resetGates(); lastKey = '';" in html


def test_mute_hooks_present():
    """模板结构契约：静音按钮与 M 键必须长在范本里（无浏览器也可锁）。"""
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    assert 'id="mute"' in html
    assert 'aria-label="静音"' in html
    assert 'aria-pressed' in html
    assert 'body[data-muted="1"]' in html          # 图标切换状态钩子
    assert 'function setMuted' in html
    assert "e.key === 'm'" in html                 # M 键路径


# ── 5. 动效设计令牌 + 错峰入场 + reduced-motion 兜底（0.10.0）──────────

def test_motion_tokens_and_stagger_present():
    """模板结构契约：动效令牌、.in 错峰原语与 reduced-motion 兜底必须长在范本里。"""
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 设计令牌：缓动与时长集中在 :root，词汇统一引用
    assert "--ease-out:cubic-bezier(.22,1,.36,1)" in html
    assert "--ease-in-out:cubic-bezier(.65,0,.35,1)" in html
    assert "--dur-in:.42s" in html and "--dur-move:.5s" in html
    assert "--dur-gate:.3s" in html and "--dur-breathe:2.8s" in html
    assert "var(--ease-out)" in html and "var(--dur-in)" in html
    assert "var(--dur-gate)" in html and "var(--dur-breathe)" in html
    # 错峰入场：.in 支持 --delay，.st1/.st2/.st3 是现成档位
    assert "animation-delay:var(--delay,0s)" in html
    assert ".st1{--delay:.06s}.st2{--delay:.12s}.st3{--delay:.18s}" in html
    # reduced-motion 兜底：入场/补间/呼吸全部关闭，循环动画停掉、终态保留
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert ".stage[data-state=\"idle\"] .veilplay{animation:none}" in html
    assert "#cam,.nt,.bar-fill{transition:none}" in html
    assert ".in,.pop,.draw,.pulse,.gate,.gate.leaving,.gate .g-card>*{animation:none}" in html


# ── 6. 微动效：进度条补间 / 字幕翻句 / 门禁反馈（0.11.0）────────────

def test_micro_motion_present():
    """模板结构契约：三处微动效与对应令牌必须长在范本里（无浏览器也可锁）。"""
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 新令牌
    assert "--dur-fill:.25s" in html and "--dur-cap:.22s" in html and "--dur-fb:.32s" in html
    # 进度条平滑补间（不再每拍跳变）
    assert "transition:width var(--dur-fill) linear" in html
    # 字幕翻句微动效：class 重触发 + 纯 transform（不碰 opacity/visibility 链）
    assert "#cap-text.capLift{animation:capLift var(--dur-cap) var(--ease-out) backwards}" in html
    assert "@keyframes capLift{from{transform:translateY(6px)}}" in html
    assert ("t.classList.remove('capLift'); void t.getBoundingClientRect();"
            " t.classList.add('capLift');") in html
    # 门禁反馈微动效：答对轻弹（弹簧缓动，见 0.12.0）、答错抖动
    assert "animation:fbOk var(--dur-fb) var(--ease-spring) backwards" in html
    assert "animation:fbBad var(--dur-fb) var(--ease-in-out)" in html
    assert "translateX(-4px)" in html
    # reduced-motion 覆盖新增动效
    assert "#cap-text.capLift," in html
    assert ".interaction-feedback.is-correct,.interaction-feedback.is-wrong{animation:none}" in html


# ── 7. 播放器动效体验：拖动跟手 / 章节刻度点亮 / 弹簧缓动（0.12.0）────

def test_player_motion_present():
    """模板结构契约：拖动跟手模式、当前章节 tick 点亮与 --ease-spring 令牌。"""
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 弹簧缓动令牌：linear() 逐点近似，作者可直接引用
    assert ("--ease-spring:linear(0,.05,.2 10%,.5 25%,.85 45%,1.08 60%,1.15 70%,"
            "1.05 82%,.98 92%,1);" in html)
    # 拖动跟手：pointerdown 打 data-drag、过渡缩到 50ms，松手删除恢复 --dur-fill
    assert ".bar[data-drag=\"1\"] .bar-fill{transition-duration:.05s}" in html
    assert "barEl.dataset.drag = '1';" in html and "delete barEl.dataset.drag;" in html
    # 当前章节刻度点亮：tick 记 data-i，tick() 逐拍维护 .on（与 tick/场景头同源）
    assert ".bar .tick.on{background:var(--key);box-shadow:0 0 5px rgba(245,166,35,.55)}" in html
    assert "TICKS.push(tk);" in html
    assert ("for (var q=0;q<TICKS.length;q++)"
            " TICKS[q].classList.toggle('on', TICKS[q].dataset.i == si);") in html


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_progress_slider_keyboard_and_replay(speaker_lesson):
    """真实浏览器：焦点在进度条上 ←/→ 微调 1s（不叠加全局 ±8s）、
    Home/End 到头/尾；ended 后点 ▶ 从头重播。"""
    import websocket

    page_dir = speaker_lesson
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    chrome_err = page_dir / "_sl-stderr.log"
    chrome = subprocess.Popen(
        [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
         f"--remote-debugging-port={port}", "--user-data-dir=" + str(page_dir / "_slprof"),
         (page_dir / "index.html").as_uri()],
        stdout=subprocess.DEVNULL, stderr=open(chrome_err, "wb"))
    try:
        ws = None
        for _ in range(60):
            if chrome.poll() is not None:
                break
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2))
                pages = [t for t in tabs if t.get("type") == "page"]
                tab = next((t for t in pages if "index.html" in t.get("url", "")),
                           pages[0] if pages else None)
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

        ready = None
        for _ in range(60):
            ready = ev("document.readyState === 'complete' && "
                       "document.querySelector('#lesson-timeline') !== null")
            if ready:
                break
            time.sleep(0.4)
        assert ready, "页面未就绪"
        # 探针：桩住 currentTime 与 ended（t >= TOTAL 即 ended），不真播音频
        probe = r"""
(function(){
  var t = 0, TOTAL = (function(){
    var tl = JSON.parse(document.getElementById('lesson-timeline').textContent);
    return tl.scenes[tl.scenes.length-1].runtime.end;
  })();
  Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
    configurable: true, get(){ return t; }, set(v){ t = Number(v) || 0; }
  });
  Object.defineProperty(HTMLMediaElement.prototype, 'ended', {
    configurable: true, get(){ return t >= TOTAL - 0.01; }
  });
  HTMLMediaElement.prototype.play = function(){ return Promise.resolve(); };
  HTMLMediaElement.prototype.pause = function(){};
  window.__t = function(){ return t; };
  window.__set = function(v){ t = v; };
  window.__TOTAL = TOTAL;
  return 'stubbed';
})()
"""
        assert ev(probe) == "stubbed"

        # 进度条键盘：焦点在 bar 上按 →，跳 1s（不是 document 层的 8s）
        rep = json.loads(ev(
            "(function(){ var bar = document.getElementById('bar'); bar.focus();"
            " bar.dispatchEvent(new KeyboardEvent('keydown',"
            " {key:'ArrowRight', code:'ArrowRight', bubbles:true, cancelable:true}));"
            " return JSON.stringify({t: window.__t(),"
            " focused: document.activeElement === bar}); })()"))
        assert rep["focused"], "进度条未能获得焦点"
        assert abs(rep["t"] - 1.0) < 0.05, f"→ 应跳 1s，实际 {rep['t']}"
        # 再按 ←，回到 0
        t = ev("(function(){ document.getElementById('bar').dispatchEvent("
               "new KeyboardEvent('keydown', {key:'ArrowLeft', code:'ArrowLeft',"
               " bubbles:true, cancelable:true})); return window.__t(); })()")
        assert abs(t - 0.0) < 0.05, f"← 应回到 0，实际 {t}"
        # Home / End 到头/尾
        ev("(function(){ window.__set(20); document.getElementById('bar').dispatchEvent("
           "new KeyboardEvent('keydown', {key:'Home', code:'Home',"
           " bubbles:true, cancelable:true})); })()")
        assert abs(ev("window.__t()") - 0.0) < 0.05, "Home 应回到 0"
        ev("(function(){ document.getElementById('bar').dispatchEvent("
           "new KeyboardEvent('keydown', {key:'End', code:'End',"
           " bubbles:true, cancelable:true})); })()")
        assert abs(ev("window.__t()") - ev("window.__TOTAL")) < 0.05, "End 应到尾"

        # ended 重播：走到课程结束，点 ▶ 应重置到 0 并重播
        rep = json.loads(ev(
            "(function(){ window.__set(window.__TOTAL);"
            " document.getElementById('main-audio').dispatchEvent(new Event('ended'));"
            " document.getElementById('pp').click();"
            " return JSON.stringify({t: window.__t(),"
            " step: document.getElementById('step').textContent}); })()"))
        assert rep["t"] == 0, f"重播应回到 0，实际 {rep['t']}"
        assert "重播" in rep["step"], f"ended 提示未更新：{rep['step']!r}"

        # 静音（M 键 + 按钮）：muted 态写 body[data-muted] 与 aria-pressed
        ev("(function(){ document.dispatchEvent(new KeyboardEvent('keydown',"
           " {key:'m', code:'KeyM', bubbles:true, cancelable:true})); })()")
        assert ev("document.getElementById('main-audio').muted") is True
        assert ev("document.body.dataset.muted") == "1"
        assert ev("document.getElementById('mute').getAttribute('aria-pressed')") == "true"
        ev("document.getElementById('mute').click()")
        assert ev("document.getElementById('main-audio').muted") is False
        assert ev("document.getElementById('mute').getAttribute('aria-pressed')") == "false"

        # 动效可及性（0.10.0）：模拟 prefers-reduced-motion 后，舞台入场动画
        # 被关闭（终态仍由属性决定，画面不受影响）
        ws.send(json.dumps({"id": 99, "method": "Emulation.setEmulatedMedia",
                            "params": {"features": [
                                {"name": "prefers-reduced-motion", "value": "reduce"}]}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 99:
                break
        anim = ev("(function(){ var el = document.querySelector('#stage .in');"
                  " var cap = document.getElementById('cap-text');"
                  " return JSON.stringify({in_: el ? getComputedStyle(el).animationName : 'no-el',"
                  " cap: getComputedStyle(cap).animationName}); })()")
        motion = json.loads(anim)
        assert motion["in_"] == "none" and motion["cap"] == "none", \
            f"reduced-motion 下动画应全部关闭：{motion!r}"
    finally:
        chrome.kill()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_bar_drag_follow_tick_highlight(speaker_lesson):
    """真实浏览器：按下进度条进入跟手模式（填充过渡缩到 50ms、tip 跟随报时间），
    松手恢复 250ms 滑行；当前章节刻度点亮（.tick.on 与场景索引一致）。"""
    import websocket

    page_dir = speaker_lesson
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    chrome_err = page_dir / "_drag-stderr.log"
    chrome = subprocess.Popen(
        [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
         f"--remote-debugging-port={port}", "--user-data-dir=" + str(page_dir / "_dragprof"),
         (page_dir / "index.html").as_uri()],
        stdout=subprocess.DEVNULL, stderr=open(chrome_err, "wb"))
    try:
        ws = None
        for _ in range(60):
            if chrome.poll() is not None:
                break
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2))
                pages = [t for t in tabs if t.get("type") == "page"]
                tab = next((t for t in pages if "index.html" in t.get("url", "")),
                           pages[0] if pages else None)
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

        ready = None
        for _ in range(60):
            ready = ev("document.readyState === 'complete' && "
                       "document.querySelector('#lesson-timeline') !== null")
            if ready:
                break
            time.sleep(0.4)
        assert ready, "页面未就绪"

        # 桩住 currentTime（与其它 CDP 用例同手法），暴露 __t/__set/__TOTAL
        assert ev("""(function(){
          var t = 0, TOTAL = 0;
          var tl = JSON.parse(document.getElementById('lesson-timeline').textContent);
          TOTAL = tl.scenes[tl.scenes.length-1].runtime.end;
          Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
            configurable: true, get(){ return t; }, set(v){ t = Number(v) || 0; }
          });
          HTMLMediaElement.prototype.play = function(){ return Promise.resolve(); };
          HTMLMediaElement.prototype.pause = function(){};
          window.__t = function(){ return t; };
          window.__set = function(v){ t = v; };
          window.__TOTAL = TOTAL;
          return 'stubbed';
        })()""") == "stubbed"

        # 拿到进度条在视口里的几何位置（headless 默认 800×600，先把条滚进视口）
        ev("document.getElementById('bar').scrollIntoView({block:'center'})")
        rect = json.loads(ev(
            "(function(){ var r = document.getElementById('bar').getBoundingClientRect();"
            " return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2,"
            " w: r.width, h: r.height}); })()"))
        x0, y0 = rect["x"], rect["y"]

        def mouse(ev_id, mtype, x, y, button="none", buttons=0):
            ws.send(json.dumps({"id": ev_id, "method": "Input.dispatchMouseEvent",
                                "params": {"type": mtype, "x": x, "y": y,
                                           "button": button, "buttons": buttons,
                                           "clickCount": 1}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == ev_id:
                    return

        # 按下：进入跟手模式——data-drag=1、填充过渡 50ms、t 已 seek
        mouse(101, "mousePressed", x0, y0, "left", 1)
        st = json.loads(ev(
            "(function(){ var b = document.getElementById('bar');"
            " var f = document.getElementById('fill');"
            " return JSON.stringify({drag: b.dataset.drag || '',"
            " dur: getComputedStyle(f).transitionDuration,"
            " t: window.__t()}); })()"))
        assert st["drag"] == "1", "按下后应进入跟手模式（data-drag=1）"
        assert st["dur"] == "0.05s", f"拖动中填充过渡应 50ms，实际 {st['dur']!r}"
        assert st["t"] > 0, "按下即 seek，t 应 > 0"

        # 拖动：tip 跟随显示（章节 · 标题 · 时间），seek 继续走
        mouse(102, "mouseMoved", x0 + rect["w"] * 0.2, y0, "left", 1)
        st2 = json.loads(ev(
            "(function(){ var tip = document.getElementById('bar-tip');"
            " return JSON.stringify({on: tip.dataset.on || '',"
            " txt: tip.textContent, t: window.__t()}); })()"))
        assert st2["on"] == "1", "拖动中 tip 应点亮（data-on=1）"
        assert "·" in st2["txt"] and ":" in st2["txt"], f"tip 应报章节与时间：{st2['txt']!r}"
        assert abs(st2["t"] - st["t"]) > 0.01, "拖动应继续 seek"

        # 松手：退出跟手模式，恢复 --dur-fill 滑行
        mouse(103, "mouseReleased", x0 + rect["w"] * 0.2, y0, "left", 0)
        st3 = json.loads(ev(
            "(function(){ var b = document.getElementById('bar');"
            " return JSON.stringify({drag: b.dataset.drag || '',"
            " dur: getComputedStyle(document.getElementById('fill')).transitionDuration}); })()"))
        assert st3["drag"] == "", "松手后应退出跟手模式"
        assert st3["dur"] == "0.25s", f"松手应恢复 250ms 滑行，实际 {st3['dur']!r}"

        # 当前章节刻度点亮：seek 进 seg-1（scene 1）后 .tick.on 的 data-i 应 = 1
        rep = json.loads(ev("""(function(){
          var tl = JSON.parse(document.getElementById('lesson-timeline').textContent);
          var s1 = tl.scenes[1];
          window.__set(s1.runtime.narration[0].start);
          document.getElementById('main-audio').dispatchEvent(new Event('timeupdate'));
          var on = document.querySelectorAll('#bar .tick.on');
          return JSON.stringify({n: on.length,
            i: on.length ? on[0].dataset.i : null});
        })()"""))
        assert rep["i"] == "1" and rep["n"] == 1, \
            f"seg-1 播放中应恰好点亮第 1 根刻度，实际 {rep!r}"
    finally:
        chrome.kill()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

# ── 8. 焦点弱化 / 按压手感 / 运镜模糊 / 章节翻场（0.13.0）────────────

def test_focus_dim_press_feel_and_camera_blur_present():
    """模板结构契约：0.13.0 四项动效的原语必须长在范本里（无浏览器也可锁）。

    锁的是"口径"而不是数字：注册变量 --dimmer 必须注册（未注册的自定义属性不做
    补间），且**不能占用调色板的 --dim 色值**（注册是有全局副作用的，见范本注释）、
    弱化只碰 filter（opacity 归 renderer 的逐句档位）、模糊只在大幅运镜时触发
    且导出模式抑制、翻场只对"换节"触发。这些漂回去以后页面会悄悄退化成
    "每句都闪 / 每步都糊"，在成品里反而最难当场发现。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 新令牌：悬浮 / 运镜 / 幕布 / 元素 / 翻场 / 模糊时长 + 弱化强度
    assert "--dur-hover:.16s; --dur-cam:.6s;   --dur-veil:.32s; --dur-el:.38s;" in html
    assert "--dur-ch:.22s; --dur-blur:.42s;" in html
    assert "--dim-focus:.62; --dur-dim:.45s;" in html
    # 焦点弱化：注册变量（名字避开调色板的 --dim 色值）+ 纯 filter + 走 .nt 的 transition
    assert "@property --dimmer{syntax:'<number>';inherits:true;initial-value:0}" in html
    # 调色板里有 --dim（灰字色），@property 是整页生效的：注册同名会把十几处
    # color:var(--dim) 判成非法值、整条作废。注册名必须与色值令牌错开。
    assert "--dim:#8d95a8" in html
    assert "@property --dim{" not in html and "@property --dim " not in html
    assert '[data-dim="1"]{--dimmer:var(--dim-focus);' in html
    assert ("filter:saturate(calc(1 - var(--dimmer) * .72)) "
            "brightness(calc(1 - var(--dimmer) * .26))}") in html
    assert ("--dimmer var(--dur-dim) var(--ease-out),"
            "filter var(--dur-dim) var(--ease-out)}") in html
    # 两处原语消费者：换组的上一行、被聚焦行挤到背景的对照组
    assert "{'data-dim': offRow ? 1 : 0, opacity: offRow ? .45 : 1});" in html
    assert "'data-dim': dim ? 1 : 0, opacity: dim ? .45 : 1});" in html
    # 按压手感：独立 scale 属性（不写进 transform，免得和既有位移互相缩放）
    assert ".opt:active:not(:disabled){scale:.94}" in html
    assert (".gate-next .go:active:not(:disabled),"
            ".act-row .go:active:not(:disabled){scale:.94}") in html
    assert ".rack button:active{scale:.9}" in html
    assert ".hotspot-stage .hs:active{scale:.9}" in html
    # .hs 靠 translate 属性居中：transform:translate(-50%,-50%) 会被独立 scale
    # 连位移一起缩放（实测按下去漂 2.2px），translate 属性不参与缩放。
    assert ".hotspot-stage .hs{position:absolute;translate:-50% -50%;" in html
    # 运镜模糊：0% = blur(0)（导出切掉 animation 落在定格），阈值 + 三种抑制
    assert ("@keyframes camBlur{0%{filter:blur(0)}38%{filter:blur(2.2px)}"
            "100%{filter:blur(0)}}") in html
    assert "#cam.blurpass{animation:camBlur var(--dur-blur) var(--ease-in-out)}" in html
    assert "#cam{transition:transform var(--dur-cam) var(--ease-out)}" in html
    assert "var camAt = null;" in html and "function camBlurOK(prev, now){" in html
    assert ("return Math.sqrt(dx * dx + dy * dy) > 180 "
            "|| Math.abs(now.k / prev.k - 1) > 0.22;") in html
    assert ("if (camBlurOK(camAt, now) && !scrubbing "
            "&& !window.__coursewareShotMode){") in html
    # 不达标的这一步必须主动摘 class，否则上一次的 blurpass 还挂着
    assert "} else cam.classList.remove('blurpass');" in html
    # 章节头翻场：换节才触发，重触发口径与字幕翻句同款
    assert (".chapter.chFlip{animation:chFlip var(--dur-ch) var(--ease-out)"
            " backwards}") in html
    assert "@keyframes chFlip{from{opacity:0;transform:translateY(6px)}}" in html
    assert "var lastSceneIdx = -1;" in html
    assert "if (lastSceneIdx !== idx){" in html
    # 重触发三段式（remove → 读一次几何 → add），跨行写就按空白折叠匹配
    assert re.search(r"ch\.classList\.remove\('chFlip'\);\s*void ch\.getBoundingClientRect"
                     r"\(\);\s*ch\.classList\.add\('chFlip'\);", html), \
        "章节翻场必须按 remove → 读几何 → add 的口径重触发"
    # reduced-motion 兜底覆盖新增的两条动画
    assert "#cam.blurpass{animation:none}" in html
    assert ".chapter.chFlip{animation:none}" in html


def _cdp(page_dir, tag, extra_flags=()):
    """起一个 headless Chrome 连到页面 target，返回 (chrome, ev, send)。

    手法与上面几个 CDP 用例一致（独立 profile、--remote-allow-origins=*、连
    page target 而非 browser），只是把新用例共用的连接与 currentTime 桩收在一处。
    --window-size 不能省：视口 ≤560 会让页面进 compact 档，camSet 改走"按内容
    包围盒兜底取景"那条分支，测不到范本 CAM 表声明的运镜。
    """
    import websocket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    chrome = subprocess.Popen(
        [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
         "--window-size=1280,900", *extra_flags,
         f"--remote-debugging-port={port}",
         "--user-data-dir=" + str(page_dir / f"_prof-{tag}"),
         (page_dir / "index.html").as_uri()],
        stdout=subprocess.DEVNULL,
        stderr=open(page_dir / f"_{tag}-stderr.log", "wb"))
    ws = None
    for _ in range(60):
        if chrome.poll() is not None:
            break
        try:
            tabs = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=2))
            pages = [t for t in tabs if t.get("type") == "page"]
            tab = next((t for t in pages if "index.html" in t.get("url", "")),
                       pages[0] if pages else None)
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
            break
        except Exception:
            time.sleep(0.4)
    assert ws is not None, "无法连接 CDP"

    mid = [0]

    def _await(i):
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == i:
                return m

    def send(method, params=None):
        mid[0] += 1
        i = mid[0]
        ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        m = _await(i)
        assert "error" not in m, f"{method} 失败：{m.get('error')}"
        return m["result"]

    def ev(expr, await_promise=False):
        mid[0] += 1
        i = mid[0]
        ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True,
                                       "awaitPromise": await_promise}}))
        r = _await(i)["result"]
        # 表达式抛错时 CDP 回的是 exceptionDetails 而不是 error：不读它就会拿到
        # None，下一句 json.loads(None) 把真因盖成 "must be str, not NoneType"。
        assert not r.get("exceptionDetails"), \
            f"页面 JS 抛错：{r.get('exceptionDetails')}\n表达式：{expr[:200]}"
        # setter 一类的表达式没有返回值：CDP 连 value 键都不给，取 None 而非 KeyError
        return r["result"].get("value")

    for _ in range(60):
        if ev("document.readyState === 'complete' && "
              "document.getElementById('lesson-timeline') !== null"):
            break
        time.sleep(0.4)
    else:
        raise AssertionError("页面未就绪")

    # 桩住 currentTime：seek = 写一个数 + 发一次 timeupdate（同既有 CDP 用例）
    ev("""(function(){
      window.__t = 0;
      Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
        configurable: true, get(){ return window.__t; },
        set(v){ window.__t = Number(v) || 0; } });
      HTMLMediaElement.prototype.play = function(){ return Promise.resolve(); };
      HTMLMediaElement.prototype.pause = function(){};
      return 1;
    })()""")
    return chrome, ev, send


_SEEK = """(function(){
  window.__tl = JSON.parse(document.getElementById('lesson-timeline').textContent);
  window.__jump = function(sid, k){          // k<0 → 场景起点，否则第 k 句起点
    for (var i = 0; i < window.__tl.scenes.length; i++){
      var sc = window.__tl.scenes[i];
      if (sc.step_id !== sid) continue;
      var r = sc.runtime;
      window.__t = k < 0 ? r.start : r.narration[k].start;
      document.getElementById('main-audio').dispatchEvent(new Event('timeupdate'));
      return window.__t;
    }
    throw new Error('时间轴里没有这一场：' + sid);
  };
  return 1;
})()"""

# 取元素上正在跑的动画名（CSS 动画名是断言的主体：跑过 = 这一拍真的动了）
_ANIMS = ("(function(){ var n = document.querySelector('%s');"
          " return JSON.stringify((n.getAnimations() || []).map("
          "function(a){ return a.animationName; })); })()")


def _anims(ev, selector):
    return json.loads(ev(_ANIMS % selector))


def _wait_idle(ev, selector, timeout=2.5):
    """等元素上的动画跑完，返回最后一次读到的动画名列表（应为空）。"""
    deadline = time.time() + timeout
    while True:
        got = _anims(ev, selector)
        if not got or time.time() >= deadline:
            return got
        time.sleep(0.05)


def _kill(chrome):
    chrome.kill()
    try:
        chrome.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_chapter_flip_only_on_scene_change(full_lesson):
    """真实浏览器：章节头只在**换节**那一刻翻场，同节内换句不重放。

    挂在"换句"分支上（最初写错的那版）每一句都会抖一下——那是闪烁，不是位置 cue。
    class 翻完之后仍挂在元素上（下一次翻场前才摘），所以判据是 getAnimations()
    里有没有 chFlip，不是 classList。
    """
    chrome, ev, _send = _cdp(full_lesson, "flip")
    try:
        ev(_SEEK)
        # 首帧（t=0 落在 opening 第 1 句）就该翻一次；等连上 CDP 时动画早跑完了
        assert ev("document.getElementById('chapter')"
                  ".classList.contains('chFlip')"), "首帧应已翻过一次场"
        assert not _wait_idle(ev, "#chapter"), "翻场动画应在 .22s 内跑完"

        # 同节内换句：不重放（seg-1 有 4 句，够走三步）
        ev("window.__jump('seg-1', 0)")
        assert "chFlip" in _anims(ev, "#chapter"), "换到 seg-1 应重新翻场"
        assert not _wait_idle(ev, "#chapter"), "翻场动画应在 .22s 内跑完"
        for k in (1, 2, 3):
            ev(f"window.__jump('seg-1', {k})")
            time.sleep(0.08)
            got = _anims(ev, "#chapter")
            assert "chFlip" not in got, f"seg-1 内部换到第 {k + 1} 句不该翻场：{got!r}"
        ev("window.__jump('seg-2', 0)")
        assert "chFlip" in _anims(ev, "#chapter"), "换到 seg-2 应再翻一次"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_focus_dim_tweens_filter_not_opacity(full_lesson):
    """真实浏览器：焦点弱化是 --dimmer 的**逐帧补间**（材质变化），终态仍是定格。

    一次锁三件事：① @property 生效（喂非法值落回 initial 0，而不是留住字符串）；
    ② 档位切换时 --dimmer 走过中间值（未注册的自定义属性只会整档跳变）；
    ③ 弱化只动 filter，renderer 写的 opacity .45 不被 CSS 盖掉。
    """
    chrome, ev, _send = _cdp(full_lesson, "dim")
    try:
        ev(_SEEK)
        # 调色板的 --dim 色值不能被注册吃掉：注册是整页生效的，同名会把
        # `color:var(--dim)` 的灰字判成非法值、整条作废回落到继承色（实测踩过）。
        grey = ev("(function(){ var n = document.querySelector('.step');"
                  " return n ? getComputedStyle(n).color : ''; })()")
        assert grey == "rgb(141, 149, 168)", \
            f"--dim 灰字被注册属性顶掉了（#8d95a8 → {grey!r}），改名 --dimmer 的理由就在这"

        ev("document.documentElement.style.setProperty('--dimmer','xyz')")
        got = ev("getComputedStyle(document.documentElement).getPropertyValue('--dimmer')")
        ev("document.documentElement.style.removeProperty('--dimmer')")
        assert got.strip() == "0", f"--dimmer 未注册（非法值应落回 0，实得 {got!r}）"

        probe = ("(function(){ var r = document.getElementById('b-row0');"
                 " var cs = getComputedStyle(r);"
                 " return JSON.stringify({attr: r.getAttribute('data-dim'),"
                 " dim: cs.getPropertyValue('--dimmer').trim(), f: cs.filter,"
                 " o: cs.opacity}); })()")

        # seg-2 s1：第一位那一行是话头，不该带弱化
        ev("window.__jump('seg-2', 1)")
        time.sleep(0.7)
        st = json.loads(ev(probe))
        assert st["attr"] == "0" and st["dim"] == "0" and st["f"] == "none", \
            f"聚焦行不该带弱化：{st!r}"

        # s4 换组：--dimmer 从 0 补间到 .62——过渡中必须抓到中间值
        ev("window.__jump('seg-2', 4)")
        seen = []
        for _ in range(12):
            seen.append(float(json.loads(ev(probe))["dim"]))
            time.sleep(0.03)
        assert len(set(round(v, 4) for v in seen)) >= 2, \
            f"--dimmer 没有逐帧补间（采样全为同值）：{seen!r}"
        assert any(0.0 < v < 0.62 for v in seen), \
            f"--dimmer 应走过 0→.62 的中间值：{seen!r}"
        for _ in range(30):
            st = json.loads(ev(probe))
            if abs(float(st["dim"]) - 0.62) < 1e-9:
                break
            time.sleep(0.05)
        assert st["attr"] == "1" and abs(float(st["dim"]) - 0.62) < 1e-6, st
        assert st["f"].startswith("saturate(") and "brightness(" in st["f"], st
        assert st["o"] == "0.45", f"弱化不该接管 opacity：{st!r}"

        # 回到聚焦：材质与档位一起退干净（导出定格看的就是这个终态）
        ev("window.__jump('seg-2', 1)")
        time.sleep(0.7)
        st = json.loads(ev(probe))
        assert st["attr"] == "0" and st["f"] == "none" and st["o"] == "1", st

        # 第二处消费者：seg-3 s3 聚焦翻车那行，对照组同样弱化
        ev("window.__jump('seg-3', 3)")
        time.sleep(0.7)
        g = json.loads(ev("(function(){ var r = document.getElementById('g-same');"
                          " var cs = getComputedStyle(r);"
                          " return JSON.stringify({attr: r.getAttribute('data-dim'),"
                          " f: cs.filter, o: cs.opacity}); })()"))
        assert g["attr"] == "1" and g["f"].startswith("saturate(") and g["o"] == "0.45", g
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_camera_blur_threshold_and_shot_mode(full_lesson):
    """真实浏览器：运动模糊只在"真的挪了一次镜头"时跑，导出截图模式一律不糊。

    seg-1 的取景窗（范本 CAM 表）刚好给出两种步长：s1→s2 中心只挪 24、倍率差
    .2，双双不过阈值 → 不该糊；s2→s3 倍率 1.28→1.8 → 该糊一下，落定回到 blur(0)。
    """
    chrome, ev, _send = _cdp(full_lesson, "cam")
    try:
        ev(_SEEK)
        cam = ("(function(){ var c = document.getElementById('cam');"
               " return JSON.stringify({cls: c.classList.contains('blurpass'),"
               " anim: (c.getAnimations() || []).map("
               "function(a){ return a.animationName; })}); })()")

        # 冷启动第一步没有"上一状态"，不该糊
        ev("window.__jump('seg-1', 0)")
        time.sleep(0.06)
        assert "camBlur" not in json.loads(ev(cam))["anim"], "冷启动第一步不该糊"

        # s0→s1：倍率 1 → 1.48，过阈值 → 糊
        ev("window.__jump('seg-1', 1)")
        time.sleep(0.06)
        assert json.loads(ev(cam))["cls"], "大幅推近应挂上运动模糊"
        assert not _wait_idle(ev, "#cam"), "模糊应在 .42s 内跑完"

        # s1→s2 小步：不糊，而且 class 必须被主动摘掉（残留会让下一步白糊一次）
        time.sleep(0.5)
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.06)
        st = json.loads(ev(cam))
        assert not st["cls"] and "camBlur" not in st["anim"], \
            f"小步运镜不该糊：{st!r}"

        # s2→s3：倍率 1.28 → 1.8，再糊一下，然后落定即清晰
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.06)
        st = json.loads(ev(cam))
        assert st["cls"] and "camBlur" in st["anim"], f"推近应挂上运动模糊：{st!r}"
        f = "unset"
        for _ in range(40):
            f = ev("getComputedStyle(document.getElementById('cam')).filter")
            if f == "none":
                break
            time.sleep(0.05)
        assert f == "none", f"运镜落定后不该残留模糊，实得 {f!r}"

        # 导出截图模式：同样的大幅运镜，一律不糊（定格必须清晰）
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-1', 1)")
        time.sleep(0.7)
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.06)
        st = json.loads(ev(cam))
        assert not st["cls"] and "camBlur" not in st["anim"], \
            f"截图模式不该糊：{st!r}"
        ev("delete window.__coursewareShotMode")
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_press_feedback_and_reduced_motion_fallback(full_lesson):
    """真实浏览器：按住播放器按钮有下沉（独立 scale）；关掉动效后新动画全停、状态照落。

    :active 不能靠 JS 假装，必须用 CDP 发真鼠标事件；reduced-motion 用
    Emulation.setEmulatedMedia 模拟系统开关——验的是"内容不依赖动效"：翻场与
    模糊不再跑，但弱化档位与材质仍在（终态就是定格）。
    """
    chrome, ev, send = _cdp(full_lesson, "press")
    try:
        ev(_SEEK)
        ev("window.__jump('opening', 0)")
        ev("document.getElementById('rack').scrollIntoView({block:'center'})")
        rect = json.loads(ev("(function(){ var r = document.getElementById('pp')"
                             ".getBoundingClientRect();"
                             " return JSON.stringify({x: r.left + r.width / 2,"
                             " y: r.top + r.height / 2}); })()"))
        scale = "getComputedStyle(document.getElementById('pp')).scale"

        def mouse(mtype, button="none", buttons=0):
            send("Input.dispatchMouseEvent",
                 {"type": mtype, "x": rect["x"], "y": rect["y"],
                  "button": button, "buttons": buttons, "clickCount": 1})

        assert ev(scale) == "none", f"静息不该缩放，实得 {ev(scale)!r}"
        mouse("mousePressed", "left", 1)
        time.sleep(0.15)
        assert ev(scale) == "0.9", f"按下去应缩到 .9，实得 {ev(scale)!r}"
        mouse("mouseReleased", "left", 0)
        time.sleep(0.15)
        assert ev(scale) == "none", f"松手应回到原样，实得 {ev(scale)!r}"

        # 关掉动效：翻场与模糊都不再跑，但状态（弱化档位 / 材质）照落
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")

        ev("window.__jump('seg-1', 0)")
        time.sleep(0.1)
        assert _anims(ev, "#chapter") == [], "reduced-motion 下章节头不该再翻场"
        ev("window.__jump('seg-2', 1)")
        time.sleep(0.1)
        ev("window.__jump('seg-2', 4)")
        time.sleep(0.2)
        assert _anims(ev, "#cam") == [], "reduced-motion 下运镜不该糊"
        assert ev("getComputedStyle(document.getElementById('cam')).filter") == "none"
        st = json.loads(ev("(function(){ var r = document.getElementById('b-row0');"
                           " var cs = getComputedStyle(r);"
                           " return JSON.stringify({dim: cs.getPropertyValue('--dimmer').trim(),"
                           " f: cs.filter, o: cs.opacity}); })()"))
        # 弱化是"状态"不是"动效"：过渡被收掉后直接落档，定格与播放终态一致
        assert abs(float(st["dim"]) - 0.62) < 1e-6, st
        assert st["f"].startswith("saturate(") and st["o"] == "0.45", st
    finally:
        _kill(chrome)
