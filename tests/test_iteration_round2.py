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
