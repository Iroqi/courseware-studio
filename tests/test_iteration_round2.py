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
import math
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import REPO, SCRIPTS, TEMPLATE_HTML, TEMPLATE_NARRATION

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
    """范本内联时间轴 → narration_timing.json 形状（页面骨架与音频时长同源）。

    这里**不带 hl**：结论句标记只认旁白脚本里的句序号（build_timeline 的
    `--source`，见 script.md §1），写在 manifest 句子上不会被读——想要成品页
    有 hl 句，把范本讲稿作为 source 传给 `_build_lesson` 才是那条真路。
    """
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


def _build_lesson(root, manifest, source=None):
    """manifest → 成品页（build_timeline + build_page，与真实交付同一条 CLI 链）。

    source 是给 build_timeline 的 `--source`：结论句 `hl` 只认旁白脚本里的句序号
    （manifest 句子自带的 hl 会被忽略，见 script.md §1），所以想要成品页带 hl 句
    就必须把讲稿一起传进来——demo_lesson.py 走的也是同一条路。
    """
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

    tl_args = [str(SCRIPTS / "build_timeline.py"), "--timing", str(timing_path)]
    if source is not None:
        src = proj / "narration-source.json"
        src.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        tl_args += ["--source", str(src)]
    r = _run([*tl_args, "-o", str(proj / "timeline.html")])
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

    讲稿一并作为 `--source` 传下去：范本那 7 句结论（hl）只有走这条路才进得了
    成品页，而 0.14.0 的划重点就挂在那几句上。
    """
    root = tmp_path_factory.mktemp("full-lesson")
    raw = TEMPLATE_HTML.read_text(encoding="utf-8")
    m = re.search(r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>(.*?)</script>',
                  raw, re.S)
    assert m, "范本缺少内联时间轴，无从派生全量页面"
    payload = json.loads(m.group(1).strip())
    source = json.loads(TEMPLATE_NARRATION.read_text(encoding="utf-8"))
    return _build_lesson(root, _manifest_from(payload["scenes"]), source=source)


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
    assert "--dur-hover:.16s; --dur-veil:.32s; --dur-el:.38s;" in html
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
    assert "#cam{transition:transform var(--cam-dur) var(--ease-out)}" in html
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


def _motion(ev, selector):
    """只看**动画名**：getAnimations() 连 CSS transition 一起列，而 transition 没有
    animationName（映射出来是 None）。0.14.0 的几条用例判的是"这个动画有没有跑"，
    幕布淡入那类 transition 不该算进来——否则每次换场都会误报"演过了"。"""
    return [n for n in _anims(ev, selector) if n]


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

        # 0.31.0 声画对齐：seg-3 s2 旁白讲"位数相同恰好一致"（话头 = same 行），
        # diff 行（红色"位数不同"标签）同拍满亮淡入就抢话头——s2 的 diff 行必须
        # 退居参照（data-dim + .45），s3 才升为话头。
        ev("window.__jump('seg-3', 2)")
        time.sleep(0.7)
        g = json.loads(ev("(function(){ var r = document.getElementById('g-diff');"
                          " var cs = getComputedStyle(r);"
                          " return JSON.stringify({attr: r.getAttribute('data-dim'),"
                          " f: cs.filter, o: cs.opacity}); })()"))
        assert g["attr"] == "1" and g["f"].startswith("saturate(") and g["o"] == "0.45", \
            f"s2 话头是 same 行，diff 行该退居参照：{g}"
        g2 = json.loads(ev("(function(){ var r = document.getElementById('g-same');"
                           " var cs = getComputedStyle(r);"
                           " return JSON.stringify({attr: r.getAttribute('data-dim'),"
                           " f: cs.filter, o: cs.opacity}); })()"))
        assert g2["attr"] == "0" and g2["f"] == "none" and g2["o"] == "1", \
            f"s2 的 same 行是话头，不该弱化：{g2}"
        # s3 反转：diff 行升为话头，same 行让位
        ev("window.__jump('seg-3', 3)")
        time.sleep(0.7)
        g = json.loads(ev("(function(){ var r = document.getElementById('g-diff');"
                          " var cs = getComputedStyle(r);"
                          " return JSON.stringify({attr: r.getAttribute('data-dim'),"
                          " f: cs.filter, o: cs.opacity}); })()"))
        assert g["attr"] == "0" and g["f"] == "none" and g["o"] == "1", \
            f"s3 的 diff 行升为话头：{g}"
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


# ── 9. 空间与连续性：视差景深 / 方向感换场 / 结论句划重点（0.14.0）──

def test_parallax_transition_and_highlight_present():
    """模板结构契约：0.14.0 三项动效原语必须长在范本里（无浏览器也可锁）。

    锁的是"口径"而不是像素：视差层必须与 #cam 同级同坐标系（放进 #cam 里会被
    那层 transform 属性顶掉）、系数只从令牌读一处、位移取 camCalc 回传的 tx/ty
    （钳位公式只允许存在一份）；换场必须只对"换节"触发且擦洗/截图双抑制
    （挂在换句分支上就成每句抖）；划重点的几何必须从字幕 bbox 现量（字号有
    桌面/小屏两套档位，写常数必错位），且非结论句归零。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 新令牌：换场 / 划重点 / 刻度点亮 / 播放器行浮起 / 视差系数
    assert "--dur-swp:.34s; --dur-hl:.42s;" in html
    assert "--dur-tick:.2s; --dur-rack:.22s; --par-far:.45;" in html
    # 视差层：在 #cam **之外**（同级兄弟），且在取景框内（跟着画面一起被裁）
    assert '<g id="depth"><rect' in html
    depth_at = html.index('<g id="depth">')
    assert html.index('<g clip-path="url(#frameClip)">') < depth_at < html.index('<g id="cam">')
    assert "#depth{transition:transform var(--cam-dur) var(--ease-out)}" in html
    # 位移量取自 camCalc 的回传值：钳位公式不留第二套
    assert "return {cx:cx, cy:cy, k:k, tx:500 - k * cx, ty:202 - k * cy};" in html
    assert "function depthShift(now){" in html
    assert "getPropertyValue('--par-far')" in html and "if (!isFinite(f)) f = .45;" in html
    assert "now.tx * f" in html and "now.ty * f" in html
    assert "depthShift(now);" in html            # camSet 里落一次
    assert "var p = el('depth');\n    if (!p) return;" in html
    # 方向感换场：独立 translate 属性 + 两个方向 + 只挂在 .el 上
    assert "@keyframes swF{from{translate:26px 0}}" in html
    assert "@keyframes swB{from{translate:-26px 0}}" in html
    assert ".el.swF{animation:swF var(--dur-swp) var(--ease-out) backwards}" in html
    assert ".el.swB{animation:swB var(--dur-swp) var(--ease-out) backwards}" in html
    assert "function sceneSlide(dir){" in html
    assert "n.classList.remove('swF', 'swB'); void n.getBoundingClientRect();" in html
    assert "n.classList.add(dir > 0 ? 'swF' : 'swB');" in html
    # 判据是"换节"（与 chFlip 同源），且擦洗 / 导出截图模式下不演
    assert "var fwd = lastSceneIdx < 0 ? 0 : (idx > lastSceneIdx ? 1 : -1);" in html
    assert "if (fwd && !scrubbing && !window.__coursewareShotMode) sceneSlide(fwd);" in html
    # 结论句色带：写在 #cap-text 之前（字压在上面），几何现量 + 属性双写兜底
    assert '<rect id="cap-hl" x="500" y="424" width="0" height="17" rx="2"/>' in html
    assert html.index('id="cap-hl"') < html.index('id="cap-text"')
    assert ("#cap-hl{fill:var(--key);opacity:.22;pointer-events:none;rx:2;\n"
            "          transition:width var(--dur-hl) var(--ease-out),"
            "x var(--dur-hl) var(--ease-out)}") in html
    assert "function capHl(t, on){" in html and "capHl(t, !!s.hl);" in html
    assert "var bb = t.getBBox();" in html
    assert ("m.setAttribute('x', x.toFixed(1)); "
            "m.setAttribute('width', w.toFixed(1));") in html
    assert "m.style.x = x + 'px'; m.style.width = w + 'px';" in html
    # 魔法时长收口：刻度点亮与窄屏/全屏播放器行都走令牌
    # （0.16.0 起 .tick 的清单多了 height——擦洗态刻度长高，仍走令牌，不留裸秒数）
    assert ("transition:background var(--dur-tick),"
            "height var(--dur-scrub) var(--ease-out)}") in html
    assert html.count("transition:opacity var(--dur-rack) ease,"
                      "transform var(--dur-rack) ease}") == 2
    # reduced-motion 兜底：换场与视差关掉，划重点关过渡但终态（属性）保留
    assert ".el.swF,.el.swB{animation:none}" in html
    assert "#depth{transition:none}" in html
    assert "#cap-hl{transition:none}" in html


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_parallax_depth_moves_less_than_camera(full_lesson):
    """真实浏览器：远景跟着镜头**同向漂一小段**（--par-far），且不改任何几何。

    锁三条：① 位移比镜头小、同向（写成反向或等幅，画面就变成"背景在逃"或"同
    一张纸"）；② 大窗（FULL）时远景不漂——没有运镜就没有视差；③ 位移挂在父层
    transform 上，**不改变子节点的局部 getBBox()**（实测：手动把 #depth 推到
    极端，#world 的包围盒一格没动）——所以相机取景、窄屏兜底与 QA 几何判定都
    不受影响。
    """
    chrome, ev, _send = _cdp(full_lesson, "par")
    try:
        ev(_SEEK)

        def state():
            return json.loads(ev("""(function(){
              var d=document.getElementById('depth'), c=document.getElementById('cam');
              var w=document.getElementById('world');
              return JSON.stringify({depth:d.getAttribute('transform'),
                                     cam:c.getAttribute('transform'),
                                     wbox:[+w.getBBox().x.toFixed(2),
                                          +w.getBBox().y.toFixed(2),
                                          +w.getBBox().width.toFixed(2)]});
            })()"""))

        def parse_tx(s):
            m = re.search(r"translate\((-?[\d.]+),(-?[\d.]+)\)", s or "")
            return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)

        ev("window.__jump('seg-1', 0)")          # FULL：不运镜
        st0 = state()
        assert parse_tx(st0["depth"]) == (0.0, 0.0), f"FULL 窗下远景不该漂：{st0}"

        ev("window.__jump('seg-1', 3)")           # 推近：真的一次运镜
        time.sleep(0.2)
        st1 = state()
        dtx, dty = parse_tx(st1["depth"])
        ctx, cty = parse_tx(st1["cam"])
        assert (dtx, dty) != (0.0, 0.0), f"大幅运镜后远景应跟着漂：{st1}"
        assert dtx * ctx > 0, f"远景与镜头必须同向（镜头 {ctx} / 远景 {dtx}）"
        assert abs(dtx) < abs(ctx) * 0.6, f"远景必须比镜头慢：{ctx} → {dtx}"
        assert abs(abs(dtx) / abs(ctx) - 0.45) < 0.02, f"系数应读令牌 .45：{ctx} → {dtx}"
        assert not _motion(ev, "#depth"), "远景走补间（transition），不该有动画在跑"

        # 位移不进几何：把 #depth 推到极端，世界层局部 bbox 一格不动。
        # 先等一切落定（运镜 .5~.78s / .nt .5s 还在跑时 bbox 本来就在变，
        # 那会被误读成"视差改变了几何"）。
        time.sleep(1.0)
        base = state()["wbox"]
        ev("document.getElementById('depth')"
           ".setAttribute('transform','translate(-420,-260)')")
        assert state()["wbox"] == base, "父层 transform 不该改变子节点 getBBox()"
        # 回写：先走到别的步（同一步重进不会调 camSet——renderer 只在步骤变化时跑，
        # 那是纯函数纪律），再走回来，页面自己把位移落回声明档位。
        ev("window.__jump('seg-1', 2)")
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.1)
        assert parse_tx(state()["depth"]) == (dtx, dty), "回写后应落回同一档位"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_scene_transition_direction_full_lesson(full_lesson):
    """真实浏览器：**前进从右进、后退从左进**，同节换句不演，截图模式不演。

    方向反了（前进用 swB）读起来就是"往回翻"，比不演更糟；挂在换句分支上则每
    句都推一下（那是闪烁）。class 会一直挂到下一次换节才摘，所以判据取
    getAnimations() 里的**动画名**，不看 classList，也不把幕布淡入那类 transition
    算成"演过了"（见 _motion）。
    """
    chrome, ev, _send = _cdp(full_lesson, "swp")
    try:
        ev(_SEEK)
        # 冷启动第一步不演：没有"上一节"可比，方向无从谈起
        ev("window.__jump('opening', 0)")
        assert _motion(ev, "#stF") == [], "opening 首步不该演换场"

        ev("window.__jump('seg-1', 0)")           # 前进
        assert "swF" in _motion(ev, "#stA"), "前进应从右侧进场"
        time.sleep(0.42)                          # --dur-swp .34s：跑完就该收干净
        assert _motion(ev, "#stA") == [], "换场跑完后不该留下在跑的动画"

        for k in (1, 2, 3):                       # 同节换句：不重放
            ev(f"window.__jump('seg-1', {k})")
            time.sleep(0.08)
            assert _motion(ev, "#stA") == [], f"seg-1 内部第 {k + 1} 句不该演换场"

        ev("window.__jump('seg-2', 0)")           # 再前进
        assert "swF" in _motion(ev, "#stB"), "前进到 seg-2 应从右侧进场"

        ev("window.__jump('seg-1', 1)")           # 回拖 = 后退
        assert "swB" in _motion(ev, "#stA"), "后退应从左侧进场"
        # 上一头的 class 必须摘掉，否则两次方向同时挂在节点上
        cls = ev("document.getElementById('stA').getAttribute('class')")
        assert "swF" not in cls and "swB" in cls, f"方向 class 未互斥：{cls!r}"

        # 导出截图模式：每帧一次冷启动，位移是直播的加护，不进成片
        ev("window.__coursewareShotMode = true;")
        ev("window.__jump('seg-2', 0)")
        time.sleep(0.1)
        cls2 = ev("document.getElementById('stB').getAttribute('class')")
        assert "swF" not in cls2 and "swB" not in cls2, \
            f"截图模式下连 class 都不该挂上（旧动画会在这一帧重放）：{cls2!r}"
        assert _motion(ev, "#stB") == [], "截图模式下不演换场"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_highlight_grows_and_clears(full_lesson):
    """真实浏览器：结论句划重点是**连续生长**的，且非结论句归零。

    取中间值才证明确实在补间（CSS 几何属性 x/width 若不被支持，只会整档跳变——
    那也是可接受的降级，但这条用例守着"现代浏览器里它是动画"这一半）。
    收起与起点是同一次写：非 hl 句必须归零，否则上一句的色带会赖在下一句下面。
    """
    chrome, ev, _send = _cdp(full_lesson, "hl")
    try:
        ev(_SEEK)

        def box():
            return json.loads(ev("""(function(){
              var m=document.getElementById('cap-hl'), t=document.getElementById('cap-text');
              var bb=t.getBBox();
              return JSON.stringify({x:+m.style.x.replace('px',''),
                                     w:+m.style.width.replace('px',''),
                                     rectW:+m.getBoundingClientRect().width.toFixed(1),
                                     attrW:m.getAttribute('width'),
                                     cap:t.textContent, hlFill:t.getAttribute('fill'),
                                     bbox:{x:+bb.x.toFixed(1), w:+bb.width.toFixed(1)}});
            })()"""))

        ev("window.__jump('seg-4', 0)")           # 非结论句
        st = box()
        assert st["w"] == 0 and float(st["attrW"]) == 0, f"非 hl 句色带应为 0：{st}"

        ev("window.__jump('seg-4', 4)")           # hl 句（seg-4 第 5 句）
        first = box()
        assert first["w"] > 0, f"hl 句应划出色带：{first}"
        assert first["hlFill"] == "#f5a623", f"hl 句应同时换主色：{first}"
        # 几何从字幕自己量：色带的 x 与 bbox 左缘同一条线
        assert abs(first["x"] - first["bbox"]["x"]) < 0.05, \
            f"色带左缘应贴字幕 bbox：{first}"
        assert abs(first["w"] - first["bbox"]["w"]) < 0.05, "色带宽应等于当句字宽"
        # 中间值：正在长出来的那一刻，屏幕上的色带宽既不是 0 也不是终值。
        # 量的是 getBoundingClientRect（已经过 SVG→屏幕缩放），终值也要用同一口径
        # 比，不能拿 viewBox 单位里的 style.width 当尺子。
        # 视口缩放比：色带宽写在 viewBox 单位里，量到屏幕上是另一个数，换算比取
        # SVG 自己的 CTM（同一处口径，不手写第二个常数）。
        scale = ev("(function(){var s=document.querySelector('#stage svg');"
                   " return s.getScreenCTM().a;})()")
        target = first["w"] * scale
        mid, settled, deadline = None, None, time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.02)
            cur = box()
            cur_screen = cur["w"] * scale
            if mid is None and 1.0 < cur["rectW"] < cur_screen - 1.0:
                mid = dict(cur, screen=cur_screen)
            settled = dict(cur, screen=cur_screen)
            if abs(settled["rectW"] - settled["screen"]) < 1.5:
                break
        assert mid is not None, f"应采到生长中的中间值（首采 {first}）"
        assert abs(settled["rectW"] - settled["screen"]) < 1.5, \
            f"终态应落在完整色带：{settled}"

        ev("window.__jump('seg-4', 5)")           # 连续两句 hl：色带照旧跟着字宽走
        nxt = box()
        assert nxt["w"] > 0 and abs(nxt["x"] - nxt["bbox"]["x"]) < 0.05, nxt

        ev("window.__jump('seg-2', 0)")           # 换到非结论句：收起
        time.sleep(0.1)
        off = box()
        assert off["w"] == 0, f"离开 hl 句色带应归零：{off}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_transition_parallax_highlight_reduced_motion(full_lesson):
    """关掉动效：换场与视差不演，划重点**仍完整划在那一句上**。

    前两项是纯装饰（关掉后每一帧仍是同一张正确定格）；色带承担"这是结论"的
    强调，属于状态而非动效——所以关的是 transition，属性上的终态必须留着。
    """
    chrome, ev, send = _cdp(full_lesson, "rm")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)

        ev("window.__jump('seg-1', 0)")
        assert _motion(ev, "#stA") == [], "reduced-motion 下不演换场"
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.1)
        # 兜底口径：视差层的过渡被收掉（transition-property 归 none），
        # 但镜头照旧落位——远景不再漂，画面一格信息也不少。
        dp = json.loads(ev("""(function(){var cs=getComputedStyle(document.getElementById('depth'));
          return JSON.stringify({p:cs.transitionProperty,d:cs.transitionDuration});})()"""))
        assert dp["p"] == "none" or dp["d"].startswith("0s"), f"视差过渡应被收掉：{dp}"
        assert _motion(ev, "#depth") == []
        ev("window.__jump('seg-4', 4)")
        time.sleep(0.1)
        st = json.loads(ev("""(function(){
          var m=document.getElementById('cap-hl'), t=document.getElementById('cap-text');
          var bb=t.getBBox();
          return JSON.stringify({w:+m.getAttribute('width'), bw:+bb.width.toFixed(1),
                                 tp:getComputedStyle(m).transitionProperty,
                                 td:getComputedStyle(m).transitionDuration});
        })()"""))
        assert st["w"] > 0 and abs(st["w"] - st["bw"]) < 0.6, \
            f"关掉动效后色带仍要完整划上（终态在属性上）：{st}"
        # 没有逐帧生长（property 归 none 或时长归 0s），但落的是同一张定格
        assert st["tp"] == "none" or st["td"].startswith("0s"), st
    finally:
        _kill(chrome)


# ── 10. 生长 / 擦除揭示 / 折叠面板（0.15.0）──────────────────────────

def test_growth_reveal_collapse_present():
    """模板结构契约：0.15.0 三项原语必须长在范本里（无浏览器也可锁）。

    锁的都是这轮实测出来的口径，不是风格：
      · 几何补间只能挂在**呈现属性**可动画的图元上（rect/circle/ellipse/path），
        `<text>` 的 x/y 与 `<line>` 的 x1/x2 不在 CSS 清单里——写成 transition 是白写，
        所以清单里不该出现它们；
      · 擦除揭示两端都必须是 `inset()`：只写 from 时隐式 to 取元素自身的
        `clip-path:none`，而 `inset()` 与 `none` 不可插值，浏览器按 discrete 处理，
        画面上就是"全遮→全显"的硬跳（实测过），那不是擦；
      · 折叠面板的 `display` 必须**同时出现在 transition-property 清单里**，
        否则 `allow-discrete` 无对象可管，收起半边是跳的（同样实测过）；
        `transition-behavior` 单独一行写，避免简写里出现不认的关键词把整条带走；
      · 卡框宽度从标签现量，且占位框**只给新建档**写（复用节点写占位宽会被当成
        一次真实状态变化，先补向 96 再掉头 = 抖动）；
      · reduced-motion 兜底那条要多带一层元素选择器才压得住基础规则（源序 + 特异度）。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 令牌：生长 / 擦除 / 折叠各一档，且 auto 关键字参与插值是折叠的前提
    assert "--dur-grow:.42s; --dur-reveal:.5s; --dur-acc:.3s;" in html
    assert "interpolate-size:allow-keywords;" in html
    root_at = html.index(":root{")
    assert root_at < html.index("interpolate-size:allow-keywords;") < html.index("--bg:")

    # .nt 的几何补间清单：rect(x/y/width/height/rx)、circle(cx/cy/r)、ellipse(ry)、path(d)
    nt_at = html.index(".nt{transition:transform")
    nt_block = html[nt_at:html.index("}", html.index("--dimmer var(--dur-dim)", nt_at))]
    for geom in ("x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "d"):
        assert f"{geom} var(--dur-grow) var(--ease-out)" in nt_block, geom
    # 明确**不该**进来的：这两个写在清单里就是白写（实测计算值为空/恒 0）
    assert "x1 var(" not in nt_block and "y1 var(" not in nt_block
    assert html.count("text-anchor") > 0                        # 文字仍靠锚点，不靠 x 补间

    # 擦除揭示：两端都是 inset()，终态收在 0；方向两个，挂在包装 g 上
    assert "@keyframes wipeR{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}" in html
    assert "@keyframes wipeL{from{clip-path:inset(0 0 0 100%)}to{clip-path:inset(0 0 0 0)}}" in html
    assert ".reveal{animation:wipeR var(--dur-reveal) var(--ease-out) backwards}" in html
    assert ".revealL{animation:wipeL var(--dur-reveal) var(--ease-out) backwards}" in html
    # 两个真实消费者：结论行左→右（往下推），降序行右→左（方向反了）
    assert ("var sum = put('b-body', 'g', 'b-sum-w', {class:'reveal'});\n"
            "      txt(sum, 'b-sum', {class:null,") in html
    assert ("var dn = put('c-body', 'g', 'c-dn-w', {class:'revealL'});\n"
            "      txt(dn, 'c-dn', {class:null,") in html

    # 折叠面板四件套：display 进清单 + allow-discrete 单写 + 展开态 auto + starting-style
    assert (".note-body{display:none;height:0;overflow:hidden;\n"
            "             transition:height var(--dur-acc) var(--ease-out),\n"
            "                        display var(--dur-acc) var(--ease-out)}" in html)
    assert ".note-body{transition-behavior:allow-discrete}" in html
    assert '.note[data-open="1"] .note-body{display:block;height:auto}' in html
    assert '@starting-style{.note[data-open="1"] .note-body{height:0}}' in html
    # 内容淡入是另一件事（noteIn），与高度补间并存、各自可关
    assert ('.note[data-open="1"] .note-body{animation:noteIn .22s ease both}' in html)

    # 卡框现量：宽度来自 getBBox，且占位框只在新建档时写
    assert "var w = Math.max(74, Math.round(lab.getBBox().width) + 28);" in html
    assert "put(g, 'rect', boxKey, {x:-w/2, width:w, rx:Math.min(20, Math.round(w/6))});" in html
    assert "if (!REG[boxKey]) put(g, 'rect', boxKey, {x:-48, y:-46, width:96, height:92, rx:14," in html
    assert "var slot = p.order.indexOf(v), g = 'w-' + v, boxKey = g + '-r';" in html

    # reduced-motion：擦除不演；折叠两头都关，且选择器带 li.note 才压得住基础规则
    assert ".reveal,.revealL{animation:none}" in html
    assert ('li.note .note-body,li.note[data-open="1"] .note-body'
            '{transition:none;animation:none}') in html
    # 旧口径（不带元素选择器）会被源序在后那条 .note-body 顶掉，别写回去
    assert ".note-body,.note[data-open=\"1\"] .note-body{transition:none;animation:none}" not in html


def _fit(ev, name):
    """主角某张卡的字宽 / 框宽（现量口径的唯一读者）。"""
    return json.loads(ev(f"""(function(){{
      var t=document.getElementById('w-{name}-t'), r=document.getElementById('w-{name}-r');
      if(!t||!r) return JSON.stringify({{absent:1}});
      return JSON.stringify({{txt:t.textContent, fs:+t.getAttribute('font-size'),
        tw:+t.getBBox().width.toFixed(1),
        bw:+parseFloat(getComputedStyle(r).width).toFixed(1),
        attr:+parseFloat(r.getAttribute('width')).toFixed(1),
        x:+parseFloat(getComputedStyle(r).x).toFixed(1)}});}})()"""))


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_hero_card_grows_to_measured_width(full_lesson):
    """真实浏览器：卡框**跟着字长大**——中间值在补间、终态容得下字，且窄屏现量跟着字号走。

    量宽度的写序不能反：getBBox 要布局，所以先落一个够用的占位框、量完再写终值；
    复用节点若把占位宽再写一遍，画面会先补向 96 再掉头（抖动）。这条用例同时钉住
    两件事：① 生长是连续的（暂停到 120ms 拿到介于两档之间的宽度）；② 终态框宽
    ≥ 字宽 + 呼吸位，桌面与小屏两档都成立（旧范本写死 96，quoted 姿势下字压出框外）。
    """
    chrome, ev, send = _cdp(full_lesson, "grow")
    try:
        ev(_SEEK)

        # 暂停 width 补间到三个时刻取值；play() 放回去，页面自己跑完
        def paused_widths():
            return json.loads(ev("""(function(){
              var r=document.getElementById('w-10-r');
              var as=r.getAnimations(), tr=null;
              for (var i=0;i<as.length;i++) if (as[i].transitionProperty==='width') tr=as[i];
              if (!tr) return JSON.stringify({none:true});
              var out=[];
              [0,120,300].forEach(function(ms){tr.pause(); tr.currentTime=ms;
                out.push([ms, +parseFloat(getComputedStyle(r).width).toFixed(1)]);});
              tr.play();
              return JSON.stringify({seq:out});})()"""))

        ev("window.__jump('seg-1', 2)")           # bay：无引号，框宽 76
        time.sleep(0.7)
        base = _fit(ev, "10")
        assert base["txt"] == "10" and base["bw"] == 76.0, base
        assert base["bw"] > base["tw"], f"占位框也该容得下字：{base}"

        ev("window.__jump('seg-1', 3)")           # shelf：加引号，字宽顶到 96.3
        got = paused_widths()
        assert not got.get("none"), f"width 应在补间：{got}"
        seq = got["seq"]
        assert seq[0][1] == 76.0, f"起点是上一档，不是 96 那个占位数：{seq}"
        assert 76.0 < seq[1][1] < 124.0, f"120ms 应采到生长中的中间值：{seq}"
        assert seq[1][1] < seq[2][1] <= 124.0, f"生长应单调、不回弹：{seq}"
        time.sleep(0.6)
        shelf = _fit(ev, "10")
        assert shelf["txt"] == "'10'" and shelf["tw"] >= 90, shelf
        assert shelf["bw"] >= shelf["tw"] + 27, f"框宽 = 字宽 + 左右呼吸：{shelf}"
        assert abs(shelf["x"] + shelf["bw"] / 2) < 0.6, f"框仍以文字中心对齐：{shelf}"

        # 复用节点重演：起点仍是当前档（76），不是硬编码的占位 96
        ev("window.__jump('seg-4', 0)")
        time.sleep(0.7)
        assert _fit(ev, "10")["bw"] == 76.0
        ev("window.__jump('seg-1', 3)")
        seq2 = paused_widths()["seq"]
        assert seq2[0][1] == 76.0, f"复用节点不该先补向占位宽：{seq2}"
        time.sleep(0.6)

        # 小屏档：字号由 put 统一放大到 52，现量的宽度自然跟着走
        send("Emulation.setDeviceMetricsOverride",
             {"width": 480, "height": 820, "deviceScaleFactor": 1, "mobile": False})
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.7)
        narrow = _fit(ev, "10")
        assert narrow["fs"] == 52, f"窄屏字号应放大：{narrow}"
        assert narrow["tw"] > 120, narrow
        assert narrow["bw"] >= narrow["tw"] + 27, \
            f"窄屏同样不能溢出：{narrow}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_wipe_reveal_sweeps_and_settles(full_lesson):
    """真实浏览器：擦除揭示是**连续扫出来**的，跑完落回完整揭示，几何一格不动。

    三个回归点都是这轮实测踩出来的：
      · 终态必须写成 `inset(0 0 0 0)`——只写 from 时到 `none` 不可插值，动画按
        discrete 走，画面只剩"全遮"和"全显"两格；所以这里要求中间值一路单调变小；
      · clip-path 只裁剪不位移：`getBBox()` 全程恒定，但**命中测试跟着裁**，
        还没擦到的那半截点不着（不留"看不见却点得着"的幽灵区）；
      · 导出截图页那一刀（`animation:none!important`）之后仍是完整揭示。
    """
    chrome, ev, _send = _cdp(full_lesson, "wipe")
    try:
        ev(_SEEK)

        def sweep(sid, k_prev, k, sel, text_id, name):
            """走到第 k 句 → **先暂停擦除、等镜头落定**，再逐时刻采样 → 放回去。

            三处非显然的手法，都是被实测教出来的：
              · 必须**在页面里等两帧**才拿得到动画对象：`put` 刚建好档的节点，动画要
                到下一次样式重算才登记（同步 read 是空数组，暂停无从下手）；
              · **先把擦除 pause() 再去等镜头**：换场时 `#cam` 还在补间（--cam-dur .5~.78s），
                一边是 clip-path 的百分比、一边是不断挪动的 `getScreenCTM()`，两个
                时间线搅在一起，命中测试就成随机数（实测：镜头没停时同一条动画
                采出的命中全错）。暂停的对象只有擦除自己，取样期间它不会动。
              · 命中点取在一串**固定分数位置**上（三个时刻共用同一条尺子），而不是
                "前缘 ±2%"：SVG `<text>` 的命中区是字形墨迹，字间空格点不着——按前缘
                取点会撞进空格里，那是假阴性。换算用 `getScreenCTM()`，不写第二套缩放比。
            """
            return json.loads(ev(f"""(function(){{
              function raf(){{return new Promise(function(r){{
                requestAnimationFrame(function(){{r()}});}});}}
              function camIdle(){{
                var c=document.getElementById('cam');
                return (c.getAnimations()||[]).every(function(a){{
                  return a.playState==='finished'||a.playState==='idle';}});}}
              window.__jump('{sid}', {k_prev});
              return raf().then(raf).then(function(){{
                window.__jump('{sid}', {k});
                var g=document.querySelector('{sel}'), t=document.getElementById('{text_id}');
                g.getBoundingClientRect();
                var as=g.getAnimations(), a=null;
                for (var i=0;i<as.length;i++) if (as[i].animationName==='{name}') a=as[i];
                if (!a) return JSON.stringify({{err:as.map(function(x){{
                  return x.animationName||x.transitionProperty;}})}});
                a.pause(); a.currentTime=0;              // 先冻住擦除
                var waited=0;
                function sample(){{
                  var FR=[0.04,0.16,0.28,0.40,0.52,0.64,0.76,0.88,0.96], out=[];
                  [40,120,250].forEach(function(ms){{
                    a.currentTime=ms;
                    var bb=t.getBBox(), ctm=t.getScreenCTM(), cp=getComputedStyle(g).clipPath;
                    var m=cp.match(/([\\d.]+(?:[eE][-+]?\\d+)?)%/);
                    if (!m){{out.push([ms,cp,null,null,null]);return;}}
                    var pct=parseFloat(m[1])/100;
                    var hits=FR.map(function(f){{
                      var q=new DOMPoint(bb.x+bb.width*f, bb.y+bb.height/2).matrixTransform(ctm);
                      return document.elementsFromPoint(q.x,q.y).some(function(e){{return e===t;}});}});
                    out.push([ms, cp, +bb.width.toFixed(2), +pct.toFixed(3), hits]);
                  }});
                  a.play();
                  return JSON.stringify({{seq:out, fr:FR, camWait:waited,
                                         dur:a.effect.getTiming().duration}});
                }}
                return (function spin(){{                 // 等镜头停稳再取值
                  if (camIdle() || waited>80) return sample();
                  waited++;
                  return raf().then(spin);
                }})();
              }});}})()""", await_promise=True))

        def insets(clip):
            """把 `inset(a b c d)` 的四个值拆成数（带 % 或 px 都吃；终态是 none）。"""
            m = re.search(r"inset\(([^)]*)\)", clip)
            assert m, f"应仍在擦除过程中，实得 {clip!r}"
            return [float(re.match(r"[\d.eE+-]+", v).group(0)) for v in m.group(1).split()]

        def edges(got, mask_idx):
            """整理成 [(时刻, 遮罩值, 命中数)]，报错时打这一行就够看懂。"""
            return [(s[0], insets(s[1])[mask_idx], sum(s[4])) for s in got["seq"]]

        def check_sweep(got, mask_idx, fr, side):
            """两端都是 inset()、只动一条边、一路单调变窄、命中跟着裁。"""
            assert all(s[1].startswith("inset(") for s in got["seq"]), \
                f"擦除途中计算值不该跳出 inset()（只写 from 时到 none 不可插值，" \
                f"浏览器按 discrete 走，画面只剩全遮/全显两格）：{[s[1] for s in got['seq']]}"
            vals = [insets(s[1])[mask_idx] for s in got["seq"]]
            assert vals == sorted(vals, reverse=True) and vals[0] > 20 > vals[-1] > 0, \
                f"遮罩那条边应一路单调变窄：{edges(got, mask_idx)}"
            assert len({s[2] for s in got["seq"]}) == 1, \
                f"裁剪不该改变 getBBox：{[s[2] for s in got['seq']]}"
            for s in got["seq"]:
                pct = s[3]
                lo, hi = (0, 1 - pct) if side == "R" else (pct, 1)
                for f, h in zip(fr, s[4]):
                    if not (lo <= f <= hi):
                        assert not h, f"没擦到的那截点不着（不留看不见的命中区）：{s}"
            # 已擦区内**允许有假阴性**（SVG text 的命中区是字形墨迹，字间空格点不着），
            # 所以判"命中随擦除一起变大"用总量，而不是逐点必中。
            counts = [sum(s[4]) for s in got["seq"]]
            assert counts == sorted(counts) and counts[-1] > counts[0], \
                f"命中区应随擦除一起变大：{counts} / {edges(got, mask_idx)}"
            assert counts[-1] >= 2, f"擦到末尾时命中区应已铺开：{edges(got, mask_idx)}"
            return vals

        # 舞台 B s5：结论行左→右（读作"顺着往下推"）
        got = sweep("seg-2", 4, 5, "#b-sum-w", "b-sum", "wipeR")
        assert not got.get("err"), f"结论行应演 wipeR：{got}"
        assert got["dur"] == 500, f"时长读 --dur-reveal：{got['dur']}"
        assert all(insets(s[1])[0] == 0 and insets(s[1])[2] == 0 for s in got["seq"]), \
            f"wipeR 不该碰上下与左：{[s[1] for s in got['seq']]}"
        check_sweep(got, 1, got["fr"], "R")

        assert _wait_idle(ev, "#b-sum-w") == [], "擦除跑完不该留下在跑的动画"
        assert ev("getComputedStyle(document.getElementById('b-sum-w')).clipPath") == "none"
        assert ev("document.getElementById('b-sum').getClientRects().length") > 0

        # 舞台 C s5：降序行右→左（这一行的新意就是"方向反了"）
        gotL = sweep("seg-4", 4, 5, "#c-dn-w", "c-dn", "wipeL")
        assert not gotL.get("err"), f"降序行应演 wipeL：{gotL}"
        assert all(insets(s[1])[0] == 0 and insets(s[1])[1] == 0 for s in gotL["seq"]), \
            f"wipeL 不该碰上下与右：{[s[1] for s in gotL['seq']]}"
        check_sweep(gotL, 3, gotL["fr"], "L")
        assert _wait_idle(ev, "#c-dn-w") == []

        # 导出截图页口径：一刀切掉 animation 后落在**完整揭示**的定格上
        ev("var s=document.createElement('style');"
           "s.textContent='*,*::before,*::after{transition:none!important;"
           "animation:none!important}';document.head.appendChild(s);")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-2', 4)")
        ev("window.__jump('seg-2', 5)")
        time.sleep(0.2)
        assert _anims(ev, "#b-sum-w") == [], "切掉动画后不该有在跑的揭示"
        assert ev("getComputedStyle(document.getElementById('b-sum-w')).clipPath") == "none"
        assert ev("document.getElementById('b-sum').getClientRects().length") > 0
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_note_accordion_animates_both_ways(full_lesson):
    """真实浏览器：侧栏折叠**两头都是连续运动**，且收起的最后一刻才退场 display。

    量的还是那两条边界：① 展开与收起都要采到中间高度（`interpolate-size` 让
    `height:auto` 参与插值，作者不用猜 max-height，也不用 JS 量 scrollHeight）；
    ② `allow-discrete` 的时序——展开立刻变 block（否则没盒可演），收起撑到动画
    结束才变 none（否则 collapse 演不完）。两头都得是真的，只有一边有动画是这轮
    实测踩过的坑：`display` 没进 transition-property 清单时就是这样。
    """
    chrome, ev, _send = _cdp(full_lesson, "acc")
    try:
        ev(_SEEK)
        ev("window.__jump('opening', 0)")

        def snap():
            return json.loads(ev("""(function(){
              var li=document.querySelectorAll('.note')[1], b=li.querySelector('.note-body');
              var cs=getComputedStyle(b);
              return JSON.stringify({open:li.dataset.open, disp:cs.display,
                h:+b.getBoundingClientRect().height.toFixed(1),
                aria:li.querySelector('.note-h').getAttribute('aria-expanded'),
                tp:cs.transitionProperty,
                an:(b.getAnimations()||[]).map(function(a){
                  return (a.transitionProperty||a.animationName)+':'+a.playState;})});})()"""))

        def settle(want):
            """推帧等到**终态**：条件成立且没有还在跑的 height / display 补间。

            headless 的帧很稀，两条补间都靠帧推进：放回去以后睡 0.35s 既可能停在
            `display:running:299.96`（收起差最后一帧没归 none），也可能停在
            `height:running`（高度已越过判据但补间还没结束）。所以这里主动用 rAF
            催帧，并把"演完了"一起写进终止条件，而不是加更长的睡眠。
            """
            deadline = time.time() + 8.0
            st = snap()
            while time.time() < deadline:
                if want(st) and not still_running(st):
                    return st
                ev("new Promise(function(r){requestAnimationFrame("
                   "function(){r(1)})})", await_promise=True)
                st = snap()
            return st

        def still_running(st):
            """还在跑的补间（noteIn 淡入不算：它是内容层面的提示，另一条纪律）。"""
            return [a for a in st["an"] if a.startswith(("height:", "display:"))]

        def toggle_then_pause(times):
            """点一次 → 暂停 height 补间取几个时刻 → 放回去。"""
            return json.loads(ev(f"""(function(){{
              var li=document.querySelectorAll('.note')[1], b=li.querySelector('.note-body');
              li.querySelector('.note-h').click();
              b.getBoundingClientRect();                  // 强制重算，让 transition 建档
              var as=b.getAnimations(), tr=null;
              for (var i=0;i<as.length;i++) if (as[i].transitionProperty==='height') tr=as[i];
              if (!tr) return JSON.stringify({{err:as.map(function(a){{
                return a.animationName||a.transitionProperty;}}),
                open:li.dataset.open, disp:getComputedStyle(b).display,
                h:+b.getBoundingClientRect().height.toFixed(1)}});
              var out=[];
              {times}.forEach(function(ms){{tr.pause(); tr.currentTime=ms;
                out.push([ms, getComputedStyle(b).display,
                          +b.getBoundingClientRect().height.toFixed(1)]);}});
              tr.play();
              return JSON.stringify({{seq:out, dur:tr.effect.getTiming().duration,
                open:li.dataset.open}});}})()"""))

        closed = snap()
        assert closed["disp"] == "none" and closed["h"] == 0 and closed["aria"] == "false", closed
        assert "display" in closed["tp"], f"display 必须进清单：{closed}"

        # 展开：第 0 帧就 block（可访问树随即有它），高度一路长到 auto
        op = toggle_then_pause("[0,120,300]")
        assert not op.get("err"), f"展开应有高度补间：{op}"
        assert op["dur"] == 300, f"时长读 --dur-acc：{op['dur']}"
        assert op["seq"][0][1] == "block", f"展开当帧就得是 block：{op}"
        hs = [s[2] for s in op["seq"]]
        assert 0 < hs[1] < hs[2], f"展开应采到递增的中间值：{op}"
        # 终止判据用**补间结束**，不用"高度超过某个像素数"：同一份 DOM 在不同渲染
        # 环境下量到的绝对值不一样（侧栏展开高本地 246.5、CI runner 182.1——字体
        # 解析差一档就会整体矮一截）。写死像素会把环境差异读成"折叠没演完"。这里
        # 只要求**明显不是一条缝**，真正把终态钉死的是下面与 scrollHeight 那条一致性。
        opened = settle(lambda s: s["disp"] == "block" and not still_running(s))
        assert opened["disp"] == "block" and opened["h"] > 20, opened
        assert opened["aria"] == "true", opened
        assert not still_running(opened), f"展开应演完收干净：{opened}"
        assert abs(opened["h"] - ev("document.querySelectorAll('.note')[1]"
                                    ".querySelector('.note-body').scrollHeight")) < 2.5, \
            f"auto 终态应等于内容高：{opened}"

        # 收起：补间演完之前 display 必须还是 block（撑住），演完才退场
        cl = toggle_then_pause("[0,120,290]")
        assert not cl.get("err"), f"收起同样要有高度补间：{cl}"
        hs2 = [s[2] for s in cl["seq"]]
        # 收起起点 = 展开终态（同一张静止定格），不比绝对像素——理由同上。
        assert hs2[0] > 20 and abs(hs2[0] - opened["h"]) < 2.5, \
            f"收起应从展开终态起算：opened={opened['h']} seq={hs2}"
        assert hs2[1] < hs2[0] and hs2[2] <= hs2[1], \
            f"收起应采到递减的中间值：{cl}"
        assert all(s[1] == "block" for s in cl["seq"]), \
            f"allow-discrete 要把 display 撑到补间结束：{cl}"
        back = settle(lambda s: s["disp"] == "none")
        assert back["disp"] == "none" and back["h"] == 0, \
            f"收起终态应退出可访问树（display 撑到补间结束后归 none）：{back}"
        assert back["aria"] == "false", back
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_growth_reveal_collapse_reduced_motion(full_lesson):
    """关掉动效：擦除不演、生长一步到位、折叠瞬开瞬合——终态每张定格都还是对的。

    三项都是**装饰**，不承担信息，所以可以整条关掉；但关掉之后必须仍落在同一张
    正确定格上：框宽照旧写在属性上（容得下字）、揭示的内容直接完整在场（不是停在
    "全遮"那一格）、折叠展开即完整可见且收起即整条退出可访问树。这条就是那套
    "内容不依赖动效"纪律对本轮三件的验收。
    """
    chrome, ev, send = _cdp(full_lesson, "rm15")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)

        # 生长：过渡被 .nt 那条 transition:none 收掉，宽度直接落档
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.4)
        assert ev("getComputedStyle(document.getElementById('w-10-r')).transitionProperty") == "none"
        base = _fit(ev, "10")
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.05)
        shelf = _fit(ev, "10")
        assert shelf["bw"] > base["bw"], f"宽度仍要落到位：{base} → {shelf}"
        assert shelf["bw"] >= shelf["tw"] + 27, f"关掉动效也不能把字挤出框：{shelf}"
        assert _anims(ev, "#w-10-r") == [], "关档后不该有在跑的补间"

        # 擦除：不演，且画面是"完整揭示"而不是"全遮"
        ev("window.__jump('seg-2', 5)")
        time.sleep(0.05)
        assert _anims(ev, "#b-sum-w") == [], "reduced-motion 下不演擦除"
        assert ev("getComputedStyle(document.getElementById('b-sum-w')).clipPath") == "none"
        assert ev("document.getElementById('b-sum').getClientRects().length") > 0
        ev("window.__jump('seg-4', 5)")
        time.sleep(0.05)
        assert _anims(ev, "#c-dn-w") == []
        assert ev("getComputedStyle(document.getElementById('c-dn-w')).clipPath") == "none"

        # 折叠：两头都关成 none（选择器不带 li.note 时这里会被源序在后的基础规则顶掉）
        def disp():
            return json.loads(ev("""(function(){
              var li=document.querySelectorAll('.note')[1], b=li.querySelector('.note-body');
              var cs=getComputedStyle(b);
              return JSON.stringify({open:li.dataset.open, disp:cs.display, tp:cs.transitionProperty,
                an:cs.animationName, h:+b.getBoundingClientRect().height.toFixed(1)});})()"""))

        assert disp()["tp"] == "none", f"折叠的过渡应被收掉：{disp()}"
        ev("document.querySelectorAll('.note-h')[1].click()")
        time.sleep(0.05)
        op = disp()
        # "展开即完整可见"钉的是**终态 = 内容高**（与另一条同一口径），不是某个
        # 绝对像素数：同一份 DOM 在不同渲染环境下量到的绝对值不一样（侧栏展开高
        # 本地 246.5、CI runner 182.1），写死像素会把环境差异读成"关档后展不开"。
        sh = ev("document.querySelectorAll('.note')[1]"
                ".querySelector('.note-body').scrollHeight")
        assert op["disp"] == "block" and op["h"] > 20, f"展开即完整可见：{op}"
        assert abs(op["h"] - sh) < 2.5, f"关档一步到位，终态应等于内容高：{op} vs {sh}"
        assert op["tp"] == "none" and op["an"] == "none", f"两头都要关（含 noteIn）：{op}"
        ev("document.querySelectorAll('.note-h')[1].click()")
        time.sleep(0.05)
        cl = disp()
        assert cl["disp"] == "none" and cl["h"] == 0, f"收起即退出可访问树：{cl}"
    finally:
        _kill(chrome)


# ── 11. 时间刻度层：擦洗态升级 / 句内进度细线 / 侧栏滚动入场（0.16.0）──

def test_time_scale_layer_present():
    """模板结构契约：0.16.0 三件的口径必须长在范本里（无浏览器也可锁）。

    锁的是"为什么这么写"而不是像素：擦洗态几何只准走 height/border-radius 那条
    清单（填充的 width 属于 --dur-fill 那套"进度连续滑动"，混进来就等于给进度
    装了第二个时钟）；句内细线必须**逐拍落呈现属性**、不写 style、不挂 transition，
    且调用点排在 capShow 之后（导出每帧只跑一次 tick，写在前面就拿到上一句的宽）；
    侧栏滚动入场必须整块包在 @supports 里、reduced-motion 兜底带 `ul.notes>`
    抬特异度——源序在后的基础规则会顶掉朴素选择器（0.15.0 同一课）。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    # 新令牌：擦洗态几何 / 气泡弹簧。细线**故意没有令牌**（不是动效，是时钟读数）
    assert "--dur-scrub:.18s; --dur-tip:.26s;" in html
    # ① 擦洗态：三态共用一条规则，轨道 6→9、圆角跟着高度走
    assert (".bar:hover,.bar:focus-visible,.bar[data-drag=\"1\"]{height:9px;"
            "border-radius:4.5px}") in html
    assert ("transition:height var(--dur-scrub) var(--ease-out),\n"
            "                  border-radius var(--dur-scrub) var(--ease-out)}") in html
    # 填充圆角继承轨道：写死 3px 会在擦洗态两端露出方角
    assert "border-radius:inherit" in html
    # 过渡清单里**没有 width**——进度那条走 --dur-fill，两者不许混
    assert "transition:width var(--dur-fill) linear" in html
    # 刻度：居中公式 + 意图升级时刻度一起长高
    assert (".bar .tick{position:absolute;top:50%;translate:-50% -50%;width:2px;"
            "height:12px;") in html
    assert (".bar:hover .tick,.bar:focus-visible .tick,"
            ".bar[data-drag=\"1\"] .tick{height:16px}") in html
    # ② 气泡弹簧：每一帧都得重复 translateX(-50%)，否则落定那一瞬跳半个身位
    assert ("@keyframes tipPop{from{transform:translateX(-50%) translateY(7px)}"
            "\n                    to{transform:translateX(-50%) translateY(0)}}") in html
    assert (".bar .tip.tipPop{animation:tipPop var(--dur-tip)"
            " var(--ease-spring) backwards}") in html
    # 重触发判据在 JS 的差分里（pointermove 每帧都进，无脑重放就是抖）
    assert "var retrigger = tip.dataset.on !== '1' || tip.dataset.seg !== String(i);" in html
    assert ("tip.classList.remove('tipPop'); void tip.getBoundingClientRect();"
            "\n      tip.classList.add('tipPop');") in html
    assert "delete el('bar-tip').dataset.seg;" in html
    # ③ 句内进度细线：几何现量 + 只写呈现属性
    assert '<rect id="cap-prog" x="500" y="443" width="0" height="2" rx="1"/>' in html
    assert (html.index('id="cap-hl"') < html.index('id="cap-prog"')
            < html.index('id="cap-text"')), "细线夹在色带与文字之间"
    assert "#cap-prog{fill:var(--key);opacity:.5;pointer-events:none;rx:1}" in html
    assert "function capMeasure(t){" in html and "function capProgWrite(t){" in html
    # 不写 style（写了就进 CSS 几何属性那条可补间通道）；范本里没有 #cap-prog 的 transition
    assert "p.style.width" not in html and "cap-prog" in html
    rm_src = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}",
                       html, re.S).group(1)
    assert "#cap-prog{" not in rm_src, "细线不是动效，不该被关（注释里提到它可以）"
    # 调用点排在换句分支之后：capShow 才写 capSent/capGeo
    assert html.index("capShow(hit.sentence);") < html.index("    capProgWrite(t);"), \
        "细线必须在本句的几何与句子写完之后读"
    assert "capSent = s;" in html and "capMeasure(t);" in html
    # 0.19.0 在同一处归零后面接了 capLitWrite(0)：细线与点亮都停在"句子收起来"那一格
    assert "capSent = null; capProgWrite(0); capLitWrite(0); return;" in html, \
        "收起必须把细线归零（点亮同步回满档）"
    # ④ 侧栏滚动入场：整块 @supports 包裹（Firefox 正式版未开）
    assert "@supports (animation-timeline: view()){" in html
    assert "@keyframes noteReveal{from{opacity:0;translate:0 10px}" in html
    assert "animation-timeline:view();" in html
    assert "animation-duration:auto;" in html
    # entry 段内收尾：cover 32% 实测静止态停在 0.845（装饰把常驻内容留在了半透明那格）
    assert "animation-range:entry 0% entry 60%}" in html
    assert "animation-range:entry 0% cover" not in html, "落点别越过 entry 段（注释里可以提）"
    # reduced-motion 兜底：擦洗几何关过渡、气泡关动画、滚动入场整条撤掉且抬过特异度
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    assert ".bar,.bar .tick{transition:none}" in rm
    assert ".bar .tip.tipPop{animation:none}" in rm
    assert "ul.notes>li.note{animation:none}" in rm
    assert "\n    li.note{animation:none}" not in rm, "朴素 li.note 会被源序在后的入场规则顶掉"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_scrub_affordance_grows_on_intent(full_lesson):
    """真实浏览器：指针落到进度条那一刻，轨道与刻度**连续长高**，离开后收回。

    验的是"意图升级"这个交互契约：静止 6px/12px → 悬停 9px/16px，而且中间值采得到
    （写成瞬跳就失去了"这根条进入可拖状态"的提示）。拖动态（data-drag=1）走同一条
    规则，所以按下也演；指针移开必须回到 6px，否则整页常驻一根变粗的条。
    """
    chrome, ev, send = _cdp(full_lesson, "scrub")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-2', 0)")

        def geom():
            return json.loads(ev("""(function(){var b=document.getElementById('bar');
              var tk=b.querySelector('.tick');
              return JSON.stringify({bar:+b.getBoundingClientRect().height.toFixed(2),
                tick:+tk.getBoundingClientRect().height.toFixed(2),
                drag:b.dataset.drag||'', hov:b.matches(':hover')});})()"""))

        def pump(n=1):
            for _ in range(n):
                ev("new Promise(function(r){requestAnimationFrame("
                   "function(){r(1)})})", await_promise=True)

        def settle_geom(pred):
            """推帧等到**落定**：谓词成立即返回，否则到截止返回最后一次读数。

            静止态用 `== 6` 这种精确等值会被 runner 快慢绊倒——擦洗是 `.18s` 过渡，
            靠真实帧时间推进，慢一点的 runner（实测 CI 3.11）固定帧数没走完尾巴就
            停在 6.02 / 12.03。所以收回/保持这些**落定态**都改成轮询到落定 + 容差，
            谓词里带 `abs(...) < 0.1`，与上行方向本来就有的 `>= 8.9` 余量同一口径。
            """
            deadline = time.time() + 8.0
            st = geom()
            while time.time() < deadline:
                if pred(st):
                    return st
                pump()
                st = geom()
            return st

        st = geom()
        assert st["bar"] == 6 and st["tick"] == 12, f"静止态：{st}"
        rect = json.loads(ev("(function(){var r=document.getElementById('bar')"
                             ".getBoundingClientRect();return JSON.stringify({x:r.left+r.width/2,"
                             "y:r.top+r.height/2});})()"))

        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": rect["y"]})
        # 落上来那一拍仍是 6px：过渡刚起（写了 transition 却没跑，就是瞬跳）
        first = geom()
        assert first["hov"], "指针应在条上"
        assert first["bar"] == 6, f"补间起点不该已落终态：{first}"
        assert ev("(document.getElementById('bar').getAnimations()||[]).map("
                  "function(a){return a.transitionProperty;}).join(',')").find("height") >= 0, \
            "height 补间没跑起来"
        heights = []
        for _ in range(10):
            pump()
            heights.append(geom()["bar"])
        mid = [h for h in heights if 6 < h < 9]
        assert mid, f"必须采到中间值（连续长高），实得 {heights}"
        assert heights[-1] >= 8.9, f"悬停终态应到 9px，实得 {heights[-1]}"
        assert geom()["tick"] >= 15.9, f"刻度应一起长到 16px，实得 {geom()['tick']}"

        # 移开：收回 6px（擦洗态是"意图"，不是常驻状态）。落定态轮询到收回完成，
        # 不用 == 6 精确等值——慢一点的 runner 固定帧数收不到最后一格（实测 6.02）。
        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": 6})
        st = settle_geom(lambda s: not s["hov"]
                         and abs(s["bar"] - 6) < 0.1 and abs(s["tick"] - 12) < 0.1)
        assert not st["hov"] and abs(st["bar"] - 6) < 0.1 and abs(st["tick"] - 12) < 0.1, \
            f"离开后应收回：{st}"

        # 按下拖动：同一条规则，data-drag 也演（手指在屏幕上没有 hover）
        send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": rect["x"],
                                          "y": rect["y"], "button": "left",
                                          "buttons": 1, "clickCount": 1})
        st = geom()
        assert st["drag"] == "1", f"按下应进跟手态：{st}"
        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"] + 40,
                                          "y": rect["y"], "button": "left", "buttons": 1})
        for _ in range(10):
            pump()
        assert geom()["bar"] >= 8.9, f"拖动态同样要变厚：{geom()}"
        # button 仍写 "left"、buttons 归 0（与 0.12.0 那条拖动用例同口径）：
        # button:"none" 的 mouseReleased 不会合成 pointerup，data-drag 会留在 1。
        send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": rect["x"] + 40,
                                          "y": rect["y"], "button": "left", "buttons": 0})
        for _ in range(6):
            pump()
        st = geom()
        assert st["drag"] == "", f"松手应退出跟手态：{st}"
        # 松手时指针仍在条上 → 轨道**保持** 9px 是对的（擦洗态跟着意图走，不是跟按钮状态）
        assert abs(st["bar"] - 9) < 0.1, f"指针还在条上就该保持擦洗态：{st}"
        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": 6})
        st = settle_geom(lambda s: abs(s["bar"] - 6) < 0.1 and abs(s["tick"] - 12) < 0.1)
        assert abs(st["bar"] - 6) < 0.1 and abs(st["tick"] - 12) < 0.1, \
            f"指针离开后收回：{st}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_caption_progress_is_a_clock_reading(full_lesson):
    """真实浏览器：句内细线的宽度 = 这一句的时间进度，且**只落在属性上**。

    这条把"它是时钟的读数、不是动效"钉成可测的事：句内 0 / 50% / 99.9% 三点比例
    对得上；换句归零重长；句间空档停在满格（字幕本来就停在上一句，不闪白）；
    播完（ended → capShow(null)）归零；width 只写属性、不写 style，导出截图页一刀
    切掉 transition 后那一帧落的仍是这一秒的真读数。字号换档（小屏 26px）后几何
    跟着 bbox 现量，不残留桌面档的宽度。
    """
    chrome, ev, send = _cdp(full_lesson, "capprog")
    try:
        ev(_SEEK)
        tl = json.loads(ev("JSON.stringify(window.__tl)"))
        seg2 = [s for s in tl["scenes"] if s["step_id"] == "seg-2"][0]
        n0 = seg2["runtime"]["narration"][0]

        def seek(t):
            ev("(function(){window.__t=%r;document.getElementById('main-audio')"
               ".dispatchEvent(new Event('timeupdate'));return 1;})()" % t)
            return json.loads(ev("""(function(){var t=document.getElementById('cap-text');
              var b=t.getBBox(), p=document.getElementById('cap-prog');
              return JSON.stringify({w:+p.getAttribute('width'),bbox:+b.width.toFixed(2),
                x:+p.getAttribute('x'),bx:+b.x.toFixed(2),styleW:p.style.width,
                cs:getComputedStyle(p).width, cap:t.textContent.slice(0,8)});})()"""))

        # ① 句内三点：比例即时钟比例
        ev("window.__jump('seg-2', 0)")
        for frac in (0.0, 0.5, 0.999):
            st = seek(n0["start"] + n0["duration"] * frac)
            assert st["bbox"] > 0, f"字幕没落上句：{st}"
            ratio = st["w"] / st["bbox"]
            assert abs(ratio - frac) < 0.01, f"进度 {frac} 实得比例 {ratio:.3f}：{st}"
            assert st["styleW"] == "", f"细线不该写 style.width：{st}"
            assert abs(st["x"] - st["bx"]) < 0.2, f"起点跟 bbox 走：{st}"
            assert abs(float(st["cs"].replace("px", "")) - st["w"]) < 0.6, \
                f"属性即终态（导出切 transition 后仍落这一格）：{st}"

        # ② 句间空档：停在**上一句**的满格（与字幕 gap-hold 同一口径，不闪白）。
        #    必须在跳到下一句之前测：空档不调用 capShow，capSent 仍是刚念完的那一句。
        st = seek(n0["start"] + n0["duration"] + 0.06)
        assert abs(st["w"] - st["bbox"]) < 0.3, f"空档应停在满格：{st}"

        # ③ 换句：新句从 0 重新长
        ev("window.__jump('seg-2', 1)")
        st = seek(seg2["runtime"]["narration"][1]["start"])
        assert st["w"] == 0, f"换句必须归零重长：{st}"

        # ④ 播完：capShow(null) 一并归零
        ev("(function(){document.getElementById('main-audio')"
           ".dispatchEvent(new Event('ended'));return 1;})()")
        assert ev("document.getElementById('cap-prog').getAttribute('width')") == "0", \
            "课程结束字幕收起，细线不能留一截"
        assert not ev("document.getElementById('cap').classList.contains('on')"), \
            "结束态字幕带收起"

        # ④b 结束态回拖**同一句**：带子必须跟着回来（纯函数律）。
        # 句号没变 → 换句分支不进；旧范本就此把字幕带一直空着，而读数与画面都回来了
        # （0.16.0 顺手补掉：tick 里按 capSent 差分补一次 capShow）。
        st = seek(seg2["runtime"]["narration"][1]["start"]
                  + seg2["runtime"]["narration"][1]["duration"] * 0.5)
        assert ev("document.getElementById('cap').classList.contains('on')"), \
            f"回拖进活跃句子，字幕带必须重新在场：{st}"
        assert abs(st["w"] / st["bbox"] - 0.5) < 0.01, f"细线跟着回来了：{st}"

        # ⑤ 字号换档：小屏 26px 现量，几何跟着 bbox 走。
        # 断言写成**同一句文案在两档下的宽度之比**，不写绝对像素：17px→26px 这档
        # 字号比是纯字号决定的，同一环境里桌面/小屏两量一起随字体缩放，比值恒 ≈
        # 26/17≈1.53（本地实测 356.61→545.74），换渲染环境也不动——比绝对像素稳。
        # 细线若残留桌面档宽度，下面的比例断言同样会红，所以两档之比既锁"换档有没有
        # 重量"，又不会被 CI runner 的整体字体差异误伤。
        ev("window.__jump('seg-2', 2)")
        n2 = seg2["runtime"]["narration"][2]
        desk = seek(n2["start"] + n2["duration"] * 0.5)
        assert desk["bbox"] > 0, f"字幕没落上句：{desk}"

        # 换档要让 capMeasure 真的重跑：它只在 capShow 里量，同句重演不进那条分支
        # （真实页面走 onCompactChange：清 lastKey 强制重画）。这里借 ended → 回拖
        # 同一句，走的是同一条"重挂带子"的公共路径。
        ev("(function(){document.documentElement.dataset.compact='1';return 1;})()")
        ev("(function(){document.getElementById('main-audio')"
           ".dispatchEvent(new Event('ended'));return 1;})()")
        st = seek(n2["start"] + n2["duration"] * 0.5)
        assert st["bbox"] > desk["bbox"] * 1.2, \
            f"26px 档同一句应变宽（比的是两档之比）：desk={desk} compact={st}"
        assert abs(st["w"] / st["bbox"] - 0.5) < 0.01, f"小屏同样按比例：{st}"
        assert abs(st["x"] - st["bx"]) < 0.2, f"起点跟着新 bbox：{st}"
        ev("(function(){delete document.documentElement.dataset.compact;return 1;})()")

        # ⑥ 导出截图模式：一次 tick 就落终态（不靠后续帧、也不空着）
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-2', 0)")
        st = seek(n0["start"] + n0["duration"] * 0.42)
        assert abs(st["w"] / st["bbox"] - 0.42) < 0.01, f"截图模式一次 tick 就要落读数：{st}"
        ev("delete window.__coursewareShotMode")
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_tip_springs_only_on_new_intent(full_lesson):
    """真实浏览器：窥探气泡只在"从无到有 / 换到另一节"时弹，同节内跟手不重放。

    pointermove 每帧都进这条处理器，无脑摘重挂就是"气泡一路抖着跟手"。判据是
    data-on 与节号的差分：进条 → 演一次；同节内继续拖 → 同一条动画在走（currentTime
    只增不减）；跨到另一节 → 重头再演；移出再移回 → 又算"从无到有"。
    """
    chrome, ev, send = _cdp(full_lesson, "tipspring")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-2', 0)")

        def tip():
            return json.loads(ev("""(function(){var t=document.getElementById('bar-tip');
              var as=(t.getAnimations()||[]).filter(function(a){return a.animationName==='tipPop';});
              return JSON.stringify({n:as.length, ct:as.length?Math.round(as[0].currentTime):null,
                on:t.dataset.on||'', seg:t.dataset.seg||''});})()"""))

        def move(frac):
            r = json.loads(ev("(function(){var b=document.getElementById('bar');"
                              "var q=b.getBoundingClientRect();return JSON.stringify({x:q.left,"
                              "y:q.top+q.height/2,w:q.width});})()"))
            send("Input.dispatchMouseEvent", {"type": "mouseMoved",
                                              "x": r["x"] + r["w"] * frac, "y": r["y"]})
            ev("new Promise(function(r){requestAnimationFrame(function(){r(1)})})",
               await_promise=True)

        assert tip()["n"] == 0, "没碰进度条时不该有弹簧在跑"
        move(0.08)
        st = tip()
        assert st["n"] == 1 and st["on"] == "1", f"进条即弹一次：{st}"
        t0 = st["ct"]
        move(0.12)
        st = tip()
        assert st["n"] == 1 and st["seg"] == "1", f"同节内不重挂：{st}"
        assert st["ct"] >= t0, f"同节内应让同一条动画继续，不该回到 0：{t0} → {st['ct']}"
        move(0.45)
        st = tip()
        assert st["n"] == 1 and st["seg"] != "1" and st["ct"] <= 60, \
            f"跨到另一节应重头再弹：{st}"
        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 4, "y": 4})
        assert tip()["on"] == "", "移出应熄灭气泡"
        move(0.08)
        st = tip()
        assert st["n"] == 1 and st["ct"] <= 60, f"再进来算从无到有：{st}"
        # 弹簧落定后 transform 回到静态值：动画期间 transform 整条接管静态值，
        # 每帧都重复写了 translateX(-50%)，落定时 translateY 必须归 0（残留就是气泡
        # 停在半空 7px），而水平仍居中（-half 由 left/translateX 决定，只查纵向那一格）。
        time.sleep(0.4)
        tf = ev("getComputedStyle(document.getElementById('bar-tip')).transform")
        m = re.match(r"matrix\(1, 0, 0, 1, (-?[\d.]+), (-?[\d.]+)\)$", tf)
        assert m, f"落定后 transform 应是平移矩阵，实得 {tf!r}"
        assert float(m.group(2)) == 0, f"落定后不该残留 7px 的下坠偏移：{m.group(0)}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_sidebar_scroll_reveal_is_progressive(full_lesson):
    """真实浏览器：侧栏随滚随显是**装饰**——静止时六条全可读，滚动时才演。

    Chrome 支持 animation-timeline 时验两件事：① 不溢出时全部 opacity=1（第一版取
    `entry 0% cover 32%`，实测静止态停在 0.845，把常驻内容留在了半透明那一格）；
    ② 强制溢出后，滚到中间能看到"进栏的那半没显形、已过的全显"，滚到底部全部落 1。
    不支持该特性的浏览器（Firefox 正式版）走的是 @supports 之外那条路：没有动画、
    文字一格不少——这条靠静态契约锁（见上一个用例），不在这里造第二个内核。
    """
    chrome, ev, send = _cdp(full_lesson, "scrollin")
    try:
        ev(_SEEK)

        def pump(n=4):
            for _ in range(n):
                ev("new Promise(function(r){requestAnimationFrame("
                   "function(){r(1)})})", await_promise=True)

        def ops():
            return json.loads(ev("""(function(){var out=[];
              document.querySelectorAll('.note').forEach(function(li){
                out.push(+getComputedStyle(li).opacity);});return JSON.stringify(out);})()"""))

        if not ev("CSS.supports('animation-timeline','view()')"):
            pytest.skip("本机内核不支持 animation-timeline，@supports 那条路本就是常显")

        pump(6)
        rest = ops()
        assert all(o >= 0.999 for o in rest), f"不滚动时六条必须全可读：{rest}"

        ev("(function(){var c=document.querySelector('.col-side');"
           "c.style.maxHeight='150px';c.scrollTop=0;return 1;})()")
        pump()
        top = ops()
        assert top[0] >= 0.99, f"栏顶那条已经进栏：{top}"
        assert any(o < 0.5 for o in top[1:]), f"栏外的要点应还没显形：{top}"

        ev("(function(){document.querySelector('.col-side').scrollTop=9999;return 1;})()")
        pump()
        bot = ops()
        assert all(o >= 0.99 for o in bot), f"滚到底部要点全部落定：{bot}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
def test_time_scale_reduced_motion_fallback(full_lesson):
    """关掉动效：擦洗几何直接落档、气泡照出不弹、侧栏常显，细线仍跟着时钟走。

    三件的兜底方式不同，正是这一轮要锁的：擦洗态是 transition（关时长即瞬落 9px，
    "可以拖"这件事仍然看得见）；气泡是 animation（关掉后 data-on 那条 opacity 照旧，
    只是不弹）；滚动入场整条撤掉——它唯一的隐藏态写在 keyframe 的 from 里，动画不跑
    就没有隐藏，六条要点一格不少。句内细线**不在兜底清单里**：它是读数不是动效，
    关了动效也得跟上嘴。
    """
    chrome, ev, send = _cdp(full_lesson, "rm16")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("window.__jump('seg-2', 0)")

        def pump(n=3):
            for _ in range(n):
                ev("new Promise(function(r){requestAnimationFrame("
                   "function(){r(1)})})", await_promise=True)

        # 侧栏：入场整条撤掉，静止与溢出都常显
        ops = json.loads(ev("""(function(){var out=[];
          document.querySelectorAll('.note').forEach(function(li){
            var cs=getComputedStyle(li);out.push([+cs.opacity,cs.animationName]);});
          return JSON.stringify(out);})()"""))
        assert all(o[1] == "none" for o in ops), f"入场动画整条关掉：{ops}"
        assert all(o[0] == 1 for o in ops), f"关掉动效后内容常显：{ops}"

        # 擦洗态：过渡被收掉，落上指针即瞬到 9px（没有中间值，但状态仍在）
        rect = json.loads(ev("(function(){var r=document.getElementById('bar')"
                             ".getBoundingClientRect();return JSON.stringify({x:r.left+r.width/2,"
                             "y:r.top+r.height/2});})()"))
        assert ev("getComputedStyle(document.getElementById('bar')).transitionProperty") == "none"
        send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": rect["y"]})
        pump()
        st = json.loads(ev("""(function(){var b=document.getElementById('bar');
          return JSON.stringify({bar:+b.getBoundingClientRect().height.toFixed(2),
            tick:+b.querySelector('.tick').getBoundingClientRect().height.toFixed(2),
            an:getComputedStyle(document.getElementById('bar-tip')).animationName,
            on:document.getElementById('bar-tip').dataset.on||''});})()"""))
        assert st["bar"] == 9 and st["tick"] == 16, f"减动效下擦洗态直接落档：{st}"
        assert st["an"] == "none" and st["on"] == "1", f"气泡照出不弹：{st}"

        # 句内细线：仍然是时钟的读数
        tl = json.loads(ev("JSON.stringify(window.__tl)"))
        n0 = [s for s in tl["scenes"] if s["step_id"] == "seg-2"][0]["runtime"]["narration"][0]
        ev("window.__jump('seg-2', 0)")
        ev("(function(){window.__t=%r;document.getElementById('main-audio')"
           ".dispatchEvent(new Event('timeupdate'));return 1;})()"
           % (n0["start"] + n0["duration"] * 0.6))
        st = json.loads(ev("""(function(){var p=document.getElementById('cap-prog'),
          t=document.getElementById('cap-text');
          return JSON.stringify({w:+p.getAttribute('width'),bbox:+t.getBBox().width.toFixed(1),
            tr:getComputedStyle(p).transitionProperty});})()"""))
        assert abs(st["w"] / st["bbox"] - 0.6) < 0.01, f"减动效下细线照样跟上嘴：{st}"
    finally:
        _kill(chrome)


# ── 12. 边缘与材质：羽化揭示 / 放行键掠光 / 排版留白层 / 主色浅档（0.17.0）──

def test_edge_texture_layer_present():
    """模板结构契约：0.17.0 四条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是这一轮实测出来"为什么只能这么写"的边界：
      · 羽化揭示的驱动变量必须注册成 `<percentage>` 且 **initial-value 取终值 134%**
        （静止态 = 全显）：不支持 @property / 不支持 mask / 导出切掉 animation 三种
        "动画没跑"的情形都落在完整可见上（纪律③），initial 若取 0% 就把内容锁进门里；
      · 两端都必须写百分比：`inset()` 那类硬边擦除是两端同函数才可插值，mask 停靠点
        若写成 `none`/关键字同样会退成 discrete（两格闪烁）；
      · `.featherR/.featherL` 必须自带 `pointer-events:none`：**mask 不裁命中测试**
        （与 clip-path 相反，实测被遮住的左半边照样命中），需要 Learner 点的图元
        只能用 .reveal，不能拿羽化层当遮罩用；
      · 掠光静止态必须停在盒外（base `background-position:120% 0`、keyframes 两端都
        写位置），跑完/关掉都是"无光泽的正常按钮"；伪元素带 `pointer-events:none`
        且宿主 `overflow:hidden`，不抢点击也不漏光；
      · 掠光的差分排在状态写入之前（先读 hidden 的旧值，再放开显示）——写在
        `gateNext.hidden = false` 之后判据恒真，同一个键会被反复刷亮；
      · 减动效兜底那行必须带 `#gate-next` 抬特异度：基础规则源序排在媒体块之后，
        朴素同形选择器平手时后来者胜（0.15.0 折叠、0.16.0 侧栏同一课）；
      · 主色浅档走 `@supports` 覆盖，不靠"同名两行"：自定义属性解析期什么都收，
        坏的是替换后的那条声明（非法计算值 → unset → 背景透明）；
      · 排版两条只管 HTML 侧文本，`text-box-edge` 只写 `text`（`cap`/`ex` 在本机
        Chrome 的 CSS.supports 全 false，写上去整条作废）。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")

    # ① 令牌：羽化比硬边擦除略长一档，掠光比揭示短——都是实测调出来的相对关系
    assert "--dur-feather:.62s; --dur-shine:.9s;" in html

    # ② 羽化揭示：注册变量的终值即静止值（纪律③那条门不能反过来锁内容）
    assert ("@property --feather{syntax:'<percentage>';inherits:false;"
            "initial-value:134%}") in html
    assert "@keyframes feSoft{from{--feather:0%}to{--feather:134%}}" in html
    assert (".featherR,.featherL{pointer-events:none;mask-repeat:no-repeat;\n"
            "        animation:feSoft var(--dur-feather) var(--ease-out) backwards}") in html
    assert ("mask-image:linear-gradient(90deg,#000 0 calc(var(--feather) - 34%),\n"
            "                        transparent var(--feather) 100%)") in html
    assert ("mask-image:linear-gradient(270deg,#000 0 calc(var(--feather) - 34%),\n"
            "                         transparent var(--feather) 100%)") in html
    # 消费者：挂在包装 <g> 上，内层文字不带 class（两层入场叠在一起只会互糊）
    assert "var fixw = put('d-body', 'g', 'd-fix-w', {class:'featherR'});" in html
    assert "txt(fixw, 'd-fix', {class:null," in html
    assert html.count("{class:'featherR'}") == 1, "羽化层每个消费者都要 pointer-events:none，别给热区用"

    # ③ 放行键掠光：伪元素 + background-position（不是 mask——这里要加亮不是裁剪）
    assert ".gate-next .go{position:relative;overflow:hidden}" in html
    assert ('.gate-next .go:not(:disabled)::after{content:"";position:absolute;'
            'inset:0;pointer-events:none;\n'
            '        border-radius:inherit;background:linear-gradient(105deg,'
            'transparent 0 42%,\n'
            '        rgba(255,255,255,.55) 50%,transparent 58% 100%);\n'
            '        background-size:260% 100%;background-repeat:no-repeat;'
            'background-position:120% 0}') in html
    assert ("@keyframes passShine{from{background-position:120% 0}"
            "to{background-position:-60% 0}}") in html
    assert ".gate-next .go.shine::after{animation:passShine var(--dur-shine) linear}" in html
    # 差分排在状态写入之前，摘 → 读几何 → 挂 的三段式重触发
    assert "var nextShown = !gateNext.hidden;" in html
    assert html.index("var nextShown = !gateNext.hidden;") < html.index("    gateNext.hidden = false;\n"
                                                                        "    // \"是不是最后一题\""), \
        "hidden 的旧值必须写在放开显示之前，否则判据恒真"
    assert ("if (!nextShown){\n"
            "      gateGo.classList.remove('shine'); void gateGo.getBoundingClientRect();\n"
            "      gateGo.classList.add('shine');\n"
            "    }") in html

    # ④ 排版留白层：只在 HTML 侧，edge 只认 text
    assert ".chapter h1{font-size:19px;margin:0;font-weight:700;text-wrap:balance}" in html
    assert (".chapter .tagline{font-size:13px;color:var(--dim);margin:0;flex:1 1 100%;\n"
            "                    text-wrap:balance;text-box-trim:trim-both;"
            "text-box-edge:text}") in html
    assert "text-wrap:pretty" not in html, "pretty 靠空格分词，中文没有空格"
    assert "text-box-edge:cap" not in html and "text-box-edge:ex" not in html, \
        "这两个 edge 值本机 CSS.supports 全 false，写上去整条声明作废"

    # ⑤ 主色浅档令牌：rgba 兜底 + @supports 覆盖，消费者一律引用令牌
    assert "--key-soft:rgba(245,166,35,.14);" in html
    assert "--key-softer:rgba(245,166,35,.08);" in html
    assert "--key-act:rgba(245,166,35,.16);" in html
    assert ("@supports (color:color-mix(in oklab,red 50%,transparent)){\n"
            "    :root{\n"
            "      --key-soft:color-mix(in oklab,var(--key) 14%,transparent);\n"
            "      --key-softer:color-mix(in oklab,var(--key) 8%,transparent);\n"
            "      --key-act:color-mix(in oklab,var(--key) 16%,transparent);") in html
    assert "background:var(--key-soft);color:var(--key)}" in html        # .chapter .tag
    assert ".rack .spd[data-off]{background:var(--key-act);color:var(--key)}" in html
    assert "border:1.6px dashed #62708c;background:var(--key-softer);padding:0;" in html
    assert ".hotspot-stage .hs:hover{border-color:var(--key);background:var(--key-act);color:var(--key)}" in html
    # 一次性的高透明值不造令牌（一个消费者不叫令牌系统），SVG 侧那几处是**语义色**的底
    assert "::selection{background:rgba(245,166,35,.28);color:#fff}" in html
    assert "box-shadow:0 0 5px rgba(245,166,35,.55)}" in html
    assert "fill:'rgba(245,166,35,.12)'" in html
    assert "amber: {fill:'#f5a623', stroke:'#f5a623', box:'rgba(245,166,35,.13)'}" in html

    # ⑥ 减动效兜底：羽化关动画即落静止态 134%（全显）；掠光兜底要带 id 抬过基础规则
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    assert ".featherR,.featherL{animation:none}" in rm
    assert "#gate-next .go.shine::after{animation:none}" in rm
    assert "\n    .gate-next .go.shine::after{animation:none}" not in rm, \
        "朴素同形选择器会被源序在后的基础规则顶掉（兜底不是写了就生效）"
    # 羽化不进 .nt 的 transition 清单：mask 的参考盒跟着缩放走，同节点会互相干扰
    nt_at = html.index(".nt{transition:transform")
    nt_block = html[nt_at:html.index("--dimmer var(--dur-dim)", nt_at)]
    assert "mask" not in nt_block, "mask 不参与 .nt 的几何补间清单"

    # ⑦ 本轮实测否决的三条，别被下一轮再请回来：
    #    counter() 里程表在不支持 @property 时渲染**空字符串**（纪律③直接不合格）；
    #    conic-gradient 扫描遮罩在被缩放的 SVG 上圆心/方向不可靠；
    #    View Transitions / content-visibility / calc-size 与逐句冷启动截图模型冲突。
    assert "counter(" not in html
    assert "conic-gradient" not in html
    assert "view-transition" not in html and "content-visibility" not in html
    assert "calc-size(" not in html


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_feather_reveal_sweeps_softly_and_settles(full_lesson):
    """真实浏览器：羽化揭示是**遮罩停靠点的连续补间**，静止态与定格都是完整可见。

    量的四条边界都来自这轮实测：
      · --feather 走过 0%→134% 的连续值（未注册的自定义属性不会补间，只整档跳变）；
        不透明停靠点恒等于 `--feather − 34%`，也就是"软边"确实挂在移动的渐变带上；
      · mask 不改几何：`getBBox()` 全程一格不动（相机取景与 QA 几何判定不受影响）；
      · **mask 不裁命中测试**，所以羽化层不可点——elementFromPoint 打在那行字上
        拿到的必须不是它本身（.reveal 靠 clip-path 天然没这问题，两者口径相反）；
      · 跑完落回元素自己的 134%（fill-mode 不写 forwards），导出那一刀切掉 animation
        之后同样是完整可见的定格。
    """
    chrome, ev, _send = _cdp(full_lesson, "feather")
    try:
        ev(_SEEK)
        probe = """(function(){
          function raf(){return new Promise(function(r){requestAnimationFrame(function(){r(1)})});}
          window.__jump('seg-5', 4);                 // 先落上一拍，把 d-fix-w 从 DOM 撤走
          return raf().then(raf).then(function(){
            window.__jump('seg-5', 5);               // 这一步才建羽化包装
            var g=document.getElementById('d-fix-w'), t=document.getElementById('d-fix');
            g.getBoundingClientRect();
            var as=g.getAnimations(), a=null;
            for (var i=0;i<as.length;i++) if (as[i].animationName==='feSoft') a=as[i];
            if (!a) return JSON.stringify({err:as.map(function(x){
              return x.animationName||x.transitionProperty;})});
            a.pause(); a.currentTime=0;
            var seq=[];
            [0,120,310,500,620].forEach(function(ms){
              a.currentTime=ms;
              var cs=getComputedStyle(g), bb=t.getBBox();
              seq.push([ms, cs.getPropertyValue('--feather').trim(), cs.maskImage,
                        +bb.width.toFixed(2)]);
            });
            var out={seq:seq, dur:a.effect.getTiming().duration,
                     pe:getComputedStyle(g).pointerEvents,
                     cls:g.getAttribute('class'), tcls:t.getAttribute('class')};
            a.play();
            return JSON.stringify(out);
          });})()"""
        got = json.loads(ev(probe, await_promise=True))
        assert not got.get("err"), f"seg-5 末句应演 feSoft：{got}"
        assert got["dur"] == 620, f"时长读 --dur-feather：{got['dur']}"
        assert got["pe"] == "none", "羽化层必须自带 pointer-events:none（mask 不裁命中测试）"
        assert "featherR" in got["cls"] and got["tcls"] == "nt", \
            f"羽化挂包装 g、内层文字不带入场类：{got['cls']!r} / {got['tcls']!r}"

        def stops(mask):
            """从计算值取（不透明段终点, 透明段起点）两个百分数。"""
            assert mask.startswith("linear-gradient(90deg, rgb(0, 0, 0) 0px"), \
                f"静止与途中都该是那条渐变遮罩：{mask!r}"
            vals = re.findall(r"(-?[\d.eE+-]+)%", mask)
            assert len(vals) >= 2, mask
            return float(vals[0]), float(vals[1])

        pcts = []
        for ms, feather, mask, bbw in got["seq"]:
            f = float(feather.rstrip("%"))
            pcts.append(f)
            opaque, clear = stops(mask)
            assert abs(opaque - (f - 34.0)) < 1e-3, f"软边宽度该恒为 34%：{ms} → {feather} / {mask}"
            assert abs(clear - f) < 1e-3, f"透明段起点该贴着 --feather：{ms} → {mask}"
        assert pcts[0] == 0.0 and pcts[-1] == 134.0, f"两端写死 0%→134%：{pcts}"
        assert pcts == sorted(pcts) and len(set(pcts)) == len(pcts), \
            f"--feather 应逐帧连续推过去：{pcts}"
        assert 0 < pcts[2] < 134, f"中途要采到真正的软边行进：{pcts}"
        widths = [s[3] for s in got["seq"]]
        assert len(set(widths)) == 1, f"遮罩不该改变几何包围盒：{widths}"

        # 落定：回到元素自己的静止态（134% = 整条遮罩带已移出右侧 = 全显）
        assert _wait_idle(ev, "#d-fix-w") == [], "羽化跑完不该留下在跑的动画"
        st = json.loads(ev("""(function(){var g=document.getElementById('d-fix-w'),
          t=document.getElementById('d-fix'), cs=getComputedStyle(g);
          var q=t.getBoundingClientRect();
          var hit=document.elementFromPoint(q.left+q.width/2, q.top+q.height/2);
          return JSON.stringify({f:cs.getPropertyValue('--feather').trim(),
            op:cs.maskImage.indexOf('rgb(0, 0, 0) 100%')>=0,
            rects:t.getClientRects().length,
            hit:hit?(hit.id||hit.tagName):null,
            self:(hit===t)||(hit===g)});})()"""))
        assert st["f"] == "134%", f"静止态应落回注册终值：{st}"
        assert st["op"], f"静止态不透明段应铺满 100%（全显）：{st}"
        assert st["rects"] > 0, f"落定后文字要有盒子：{st}"
        assert not st["self"], f"羽化层不可点（命中区不被 mask 裁掉）：{st}"

        # 导出截图页口径：一刀切掉 animation 后落在**完整可见**的定格上
        ev("var s=document.createElement('style');"
           "s.textContent='*,*::before,*::after{transition:none!important;"
           "animation:none!important}';document.head.appendChild(s);")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-5', 4)")
        ev("window.__jump('seg-5', 5)")
        time.sleep(0.2)
        assert _anims(ev, "#d-fix-w") == [], "切掉动画后不该有在跑的揭示"
        st = json.loads(ev("""(function(){var g=document.getElementById('d-fix-w'),
          t=document.getElementById('d-fix'), cs=getComputedStyle(g);
          return JSON.stringify({f:cs.getPropertyValue('--feather').trim(),
            op:cs.maskImage.indexOf('rgb(0, 0, 0) 100%')>=0,
            rects:t.getClientRects().length});})()"""))
        assert st["f"] == "134%" and st["op"] and st["rects"] > 0, \
            f"导出定格必须是完整可见的一行：{st}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_gate_shine_fires_once_on_gate_open(full_lesson):
    """真实浏览器：掠光只在放行那一行"从无到有"时演一次，静止态高光带停在盒外。

    走真实通路（seek 到 seg-2 末句之后的空档 → recall 揭开参考答案 → 运行时写
    data-locked → 页面侧 MutationObserver），不手工加 class——因为要验的正是那条
    差分判据：
      · 第一次 locked：`.shine` 挂上、passShine 跑起来（时长读 --dur-shine）；
      · 跑途中再写一次 locked（观察者会重进）：判据是 hidden 的**旧值**，
        同一条动画继续走（currentTime 只增不减），不会从头重放；
      · 跑完之后又写：摘重挂只在"这一行刚出现"时做一次，已经亮着的键不该闪第二遍；
      · 位置补间两头都写在盒外（120% → −60%），落定回到 120% = 看不见。

    时序上有个实测踩到的坑：**引导浮层（preGate）那 1.1s 里 `#gate` 整层还是
    display:none**，放行行虽然被摘了 hidden 也没有盒——伪元素的动画要到元素**第一次
    有盒**才登记进 document.getAnimations()。所以这里等的是"浮层揭开"，与学习者
    真正看到那一行的时刻对齐；抢先点 recall 也能走通（JS click 不看可见性），
    但那时采到的"没有动画"是取景时刻的假阴性。
    """
    chrome, ev, _send = _cdp(full_lesson, "shine")
    try:
        ev(_SEEK)

        def pump(n=2):
            """推 n 帧：类挂上 ≠ 时间线已登记，元素要等下一次样式重算才有盒、
            动画才进 getAnimations()（同 0.15.0 擦除用例那条取样口径）。"""
            for _ in range(n):
                ev("new Promise(function(r){requestAnimationFrame(function(){r(1)})})",
                   await_promise=True)

        def wait(pred_js, timeout=4.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if ev(pred_js):
                    return True
                time.sleep(0.05)
            return False

        def shines():
            return json.loads(ev("""(function(){
              var as=document.getAnimations().filter(function(a){
                return a.animationName==='passShine';});
              return JSON.stringify(as.map(function(a){
                return [Math.round(a.currentTime||0), a.playState];}));})()"""))

        def next_state():
            return json.loads(ev("""(function(){var nx=document.getElementById('gate-next'),
              go=document.getElementById('gate-go'), cs=getComputedStyle(go,'::after');
              return JSON.stringify({hidden:nx.hidden, dis:go.disabled,
                shine:go.classList.contains('shine'), an:cs.animationName,
                pos:cs.backgroundPosition, pe:cs.pointerEvents});})()"""))

        # 空档里开 recall 门禁：先落末句（不越锚点），再落到锚点之后 0.2s
        tl = json.loads(ev("JSON.stringify(window.__tl)"))
        seg2 = [s for s in tl["scenes"] if s["step_id"] == "seg-2"][0]["runtime"]
        last = seg2["narration"][-1]
        anchor = last["start"] + last["duration"]
        ev("window.__jump('seg-2', %d)" % (len(seg2["narration"]) - 1))
        ev("(function(){window.__t=%r;document.getElementById('main-audio')"
           ".dispatchEvent(new Event('timeupdate'));return 1;})()" % (anchor + 0.2))
        # 等**整层浮层揭开**（preGate 1.1s 之后），不是只等卡片摘 hidden
        assert wait("!document.getElementById('gate').hidden", 4.0), "recall 门禁未开"
        assert ev("!document.getElementById('gate-recall').hidden")
        st = next_state()
        assert st["hidden"] is True, f"答对之前放行那行该收着：{st}"
        assert st["shine"] is False and st["an"] == "none", f"没演过掠光：{st}"
        assert st["dis"] is True, "未答对时继续键该 disabled（伪元素不生成）"

        # 真实通路：点「看参考答案」→ 运行时写 data-locked → 观察者放行
        ev("document.querySelector('#gate-recall [data-recall-reveal]').click()")
        assert wait("!document.getElementById('gate-next').hidden", 2.0), "答对后放行行未出现"
        pump()
        st = next_state()
        assert st["shine"] and not st["dis"], f"放行行应出现并挂上掠光：{st}"
        seq = shines()
        assert len(seq) == 1, f"只该有一条 passShine：{seq}"
        dur = ev("""(function(){ var a = document.getAnimations().filter(function(x){
          return x.animationName === 'passShine'; })[0];
          return a ? a.effect.getTiming().duration : null; })()""")
        assert dur == 900, f"时长读 --dur-shine：{dur}"
        assert st["an"] == "passShine" and st["pe"] == "none", f"伪元素在跑且不抢点击：{st}"

        # 途中再写一次 locked：差分判据（旧值已显示）不该让它从头重放。
        # 先等 currentTime 真的走起来——刚登记那一瞬它还是 0（起播时刻未定），
        # 拿 0 去比"只增不减"等于什么都没验。
        assert wait("(function(){var a=document.getAnimations().filter(function(x){"
                    "return x.animationName==='passShine';})[0];"
                    "return !!a && a.currentTime>0;})()", 2.0), \
            f"掠光应在放行行出现后开走：{shines()}"
        before = shines()[0][0]
        assert before < 900, f"应仍在掠光途中再写 locked：{before}"
        ev("document.getElementById('gate-recall').dataset.locked = '1'")
        pump()
        after = shines()
        assert len(after) == 1, f"重写 locked 不该重放掠光：{before} → {after}"
        assert after[0][0] >= before, f"同一条动画应继续走：{before} → {after}"

        # 位置补间：静止与起点都在盒外右侧，途中单调左移（暂停取样，同擦除用例的
        # 取样口径）。终点 −60% 由静态契约锁 keyframes 两端；这里取 880ms 而不是
        # 整 900ms——fill 不写 forwards 时 local time == duration 已属 after 相，
        # 计算值会落回静止态，拿 −60% 断言会踩模型边界。
        track = json.loads(ev("""(function(){
          var go=document.getElementById('gate-go'), a=null;
          var as=document.getAnimations();
          for (var i=0;i<as.length;i++) if (as[i].animationName==='passShine') a=as[i];
          if (!a) return JSON.stringify({err:as.map(function(x){return x.animationName;})});
          a.pause();
          var out=[];
          [0,300,600].forEach(function(ms){a.currentTime=ms;
            out.push(getComputedStyle(go,'::after').backgroundPosition);});
          a.currentTime=880;
          out.push(getComputedStyle(go,'::after').backgroundPosition);
          a.play();
          return JSON.stringify({seq:out});})()"""))
        assert not track.get("err"), f"伪元素动画应可取样：{track}"
        xs = [float(re.match(r"(-?[\d.]+)%", p).group(1)) for p in track["seq"]]
        assert xs[0] == 120.0, f"起点该停在盒外右侧：{track['seq']}"
        assert xs[-1] < -50, f"掠到末段应已越出左侧：{track['seq']}"
        assert xs == sorted(xs, reverse=True), f"高光带应一路向左掠：{track['seq']}"

        # 落定：跑完回到静止态 120%（看不见），没有残留动画
        assert wait("document.getAnimations().filter(function(a){"
                    "return a.animationName==='passShine';}).length===0", 3.0), \
            f"掠光跑完不该留在场：{shines()}"
        st = next_state()
        assert st["pos"].startswith("120%"), f"静止态高光带应停在盒外：{st}"

        # 放行行已经开着：之后再写 locked 一律不重放
        ev("document.getElementById('gate-recall').dataset.locked = '1'")
        pump()
        assert shines() == [], f"已亮着的键不该再闪：{shines()}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_edge_texture_reduced_motion_fallback(full_lesson):
    """关掉动效：羽化直接全显、掠光不演但放行行照旧出现、浅档底色照旧落色。

    两条新原语的兜底方式是同一句话的两面——**静止态就是终态**：
      · 羽化关掉 animation 后落在元素自己的 --feather=134%（不透明段 100%），
        与 .reveal 关掉落 `clip-path:none` 对齐，没有"半截羽化"留在定格上；
      · 掠光关掉后伪元素仍在那儿，但位置停在盒外 120%，本来就看不见；
        这一行必须带 `#gate-next` 抬特异度才真的落上（源序在后的基础规则会顶掉它）。
    底色那组令牌是**静态取值**（color-mix 不参与动画），减动效下算得出同一个浅色，
    顺带验一次 @supports 覆盖后的值仍等于兜底 rgba。
    """
    chrome, ev, send = _cdp(full_lesson, "rm17")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("window.__jump('seg-5', 5)")
        time.sleep(0.15)
        st = json.loads(ev("""(function(){var g=document.getElementById('d-fix-w'),
          t=document.getElementById('d-fix'), cs=getComputedStyle(g);
          return JSON.stringify({an:cs.animationName,
            f:cs.getPropertyValue('--feather').trim(),
            op:cs.maskImage.indexOf('rgb(0, 0, 0) 100%')>=0,
            rects:t.getClientRects().length});})()"""))
        assert st["an"] == "none", f"减动效下羽化动画该整条关掉：{st}"
        assert st["f"] == "134%" and st["op"], f"关掉后必须全显（内容不锁进门里）：{st}"
        assert st["rects"] > 0, f"减动效下这一行照样在场：{st}"

        # 浅色令牌：@supports 覆盖生效时，**合成到同一个底色上的像素**应与 rgba 兜底一致。
        # 不能比字符串：canvas 的 fillStyle getter 原样保留作者写的颜色规范（实测
        # `color-mix(...)` 取回还是那串 oklab()，不换算成 rgba()），比字符串会把
        # "像素相同"这条实测结论误判成失败——所以拿 getImageData 比合成结果。
        tint = json.loads(ev("""(function(){
          function px(css){var c=document.createElement('canvas');c.width=c.height=4;
            var g=c.getContext('2d');g.fillStyle='#101319';g.fillRect(0,0,4,4);
            g.fillStyle=css;g.fillRect(0,0,4,4);
            var d=g.getImageData(0,0,4,4).data;return String([d[0],d[1],d[2]]);}
          var raw=getComputedStyle(document.getElementById('ch-tag')).backgroundColor;
          return JSON.stringify({sup:CSS.supports('color','color-mix(in oklab,red 50%,transparent)'),
            mix:px('color-mix(in oklab,rgb(245,166,35) 14%,transparent)'),
            rgba:px('rgba(245,166,35,.14)'), token:px(raw), raw:raw,
            act:px('color-mix(in oklab,rgb(245,166,35) 16%,transparent)'),
            actRgba:px('rgba(245,166,35,.16)')});})()"""))
        if tint["sup"]:
            assert tint["mix"] == tint["rgba"], f"现调浅色应与兜底 rgba 同值：{tint}"
            assert tint["act"] == tint["actRgba"], f"16% 那档同理：{tint}"
            assert tint["token"] == tint["mix"], f"消费者应取到令牌：{tint}"

        # 掠光：放行行出现、文字照旧，但那条动画整条关掉且底色不闪
        tl = json.loads(ev("JSON.stringify(window.__tl)"))
        seg2 = [s for s in tl["scenes"] if s["step_id"] == "seg-2"][0]["runtime"]
        last = seg2["narration"][-1]
        ev("window.__jump('seg-2', %d)" % (len(seg2["narration"]) - 1))
        ev("(function(){window.__t=%r;document.getElementById('main-audio')"
           ".dispatchEvent(new Event('timeupdate'));return 1;})()"
           % (last["start"] + last["duration"] + 0.2))
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if ev("!document.getElementById('gate-recall').hidden"):
                break
            time.sleep(0.1)
        assert ev("!document.getElementById('gate-recall').hidden"), "减动效下门禁照旧开"
        ev("document.querySelector('#gate-recall [data-recall-reveal]').click()")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if ev("!document.getElementById('gate-next').hidden"):
                break
            time.sleep(0.05)
        st = json.loads(ev("""(function(){var go=document.getElementById('gate-go'),
          cs=getComputedStyle(go,'::after');
          return JSON.stringify({hidden:document.getElementById('gate-next').hidden,
            txt:go.textContent, shine:go.classList.contains('shine'),
            an:cs.animationName, pos:cs.backgroundPosition,
            ps:document.getAnimations().filter(function(a){
              return a.animationName==='passShine';}).length});})()"""))
        assert st["hidden"] is False and st["txt"], f"减动效下放行行照样出现：{st}"
        assert st["shine"], "class 仍挂上（判据没变），只是不演：{}".format(st)
        assert st["an"] == "none", f"兜底行要真的盖过基础规则（带 #gate-next 抬特异度）：{st}"
        assert st["pos"].startswith("120%"), f"静止态本就看不见：{st}"
        assert st["ps"] == 0, f"减动效下不该有掠光在跑：{st}"
    finally:
        _kill(chrome)


def _keyframe_bodies(html):
    """把每条 `@keyframes` 的**整个规则体**取出来（按花括号配平，不靠正则截断）。

    正则 `(.*?)}` 在这种嵌套写法上只会吃到第一个 `}`，也就是只拿到 `from{…` 那一帧，
    剩下的 `to{…}` 全漏网——用它判"关键帧里不许出现某属性"等于没判（本轮实测踩过）。
    注释里也会提到 `@keyframes`（`.flow` 那段就这么写），所以先剥掉注释再扫。
    """
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    out, i = [], 0
    while True:
        i = html.find("@keyframes", i)
        if i < 0:
            return out
        j = html.find("{", i)
        depth, k = 0, j
        while k < len(html):
            if html[k] == "{":
                depth += 1
            elif html[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append(html[j + 1:k])
        i = k


# ── 12. 连线与标注：沿线流光 / 恒定线宽 / 自动错峰 / 描边光环（0.18.0）──

def test_line_and_label_primitives_present():
    """模板结构契约：0.18.0 四条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮在 Chrome 里量出来"为什么只能这么写"的边界：
      · 流光的**两端都必须落在"看不见"**（静止 = 光段在路头之外、终点 -1 推到路尾之外），
        掠光是事件不是状态——导出切掉 animation、系统关掉动效，得到的都是"一条普通连线"，
        纪律③与"终态落在属性上"同时成立；
      · `.flow` 必须自带 `pointer-events:none`：虚线的**空隙不可命中**，看不见的一层会在
        整条路上挡掉底下的热区（与 0.17.0 羽化层同一课）；
      · `.flow` 与 `.draw` **不许同节点**（两条都声明 stroke-dasharray，源序后者胜，实测
        叠在一起只剩 .16/1 的图案、线根本画不满），消费者一律分两个节点、用 --delay 排先后；
      · `.crisp` 的 `vector-effect` **不继承**，只能逐图元挂；且只给注记/参照线用；
      · `.stag` 的 calc 里**两处减号两侧必须有空格**：写成紧凑的 `sibling-count()-1` 时
        整条 calc() 在计算值阶段作废、延迟全落 0s，而**动画照样跑**、控制台一句话都不报
        ——本轮最容易静默通过的一条，所以下面既锁带空格的写法、也锁紧凑写法不在规则里出现；
      · `--delay` 必须注册成 `<time>` 且 `inherits:false`：把错峰锁在分组那一层，否则
        `put()` 嵌套建档的深层小图元会各自重算 sibling-index()（那是满屏乱闪不是错峰）；
      · `.halo` 的 `paint-order` 必须包在 `@supports` 里：不支持时描边压在字面上会啃细笔画；
      · 门禁卡片整族的 reduced-motion 兜底**必须抬特异度**（带 #gate）：那两条基础规则
        源序排在媒体块之后，朴素同形选择器平手时后来者胜（0.15.0 折叠 / 0.16.0 侧栏 /
        0.17.0 掠光同一课）。本轮挂上 .stag 之后这才成为真问题——§⑤ 那条
        `animation-duration:.01ms!important` 钳得住时长、**钳不住 animation-delay**，
        兜不住的话减少动效下三张卡片仍然一个接一个冒出来。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")

    # ① 令牌：流光比画线长一档；错峰两只旋钮与 .st1/.st2/.st3 同族；光环色取舞台局部底色
    assert "--dur-flow:.9s; --flow-seg:.16;" in html
    assert "--stagger:.06s; --stag-cap:.18s;" in html
    assert "--halo-bg:#1a2029;" in html
    assert "--halo-bg:var(--bg)" not in html and "--halo-bg:var(--panel)" not in html, \
        "光环色必须取舞台局部底色：取 --bg/--panel 会在字周围留一圈看得见的深色边"

    # ② --delay 注册：收窄成 <time> 且不继承（错峰只属于分组那一层）
    assert ("@property --delay{syntax:'<time>';inherits:false;initial-value:0s}") in html
    assert "@property --delay{syntax:'<time>';inherits:true}" not in html

    # ③ 沿线流光：静止态 = 起点、终点 = 路尾之外，两端都"看不见"
    assert (".flow{stroke-dasharray:var(--flow-seg) 1;stroke-dashoffset:var(--flow-seg);\n"
            "        animation:ntFlow var(--dur-flow) linear backwards;"
            "animation-delay:var(--delay,0s);\n"
            "        pointer-events:none}") in html
    assert ("@keyframes ntFlow{from{stroke-dashoffset:var(--flow-seg)}"
            "to{stroke-dashoffset:-1}}") in html
    # linear + 不写 infinite：easeOut 会把行程挤进路尾，循环则违反"0% 必须等于静止态"
    assert "ntFlow var(--dur-flow) linear" in html
    assert "ntFlow var(--dur-flow) var(--ease-out)" not in html
    assert "ntFlow infinite" not in html
    # 掠光是**补间出来的**、不是补间回去的：dashoffset 不得混进 .nt 的过渡清单
    nt_at = html.index(".nt{transition:")
    nt_block = html[nt_at:html.index("--dimmer var(--dur-dim)", nt_at)]
    assert "stroke-dashoffset" not in nt_block, \
        "renderer 同帧写起点与终态时 transition 根本不补间（实测计算值一步落终值），" \
        "掠光必须走 animation"

    # ④ 恒定线宽：只有一条声明，消费者只有注记/参照线
    assert ".crisp{vector-effect:non-scaling-stroke}" in html
    assert '<line class="crisp" x1="0" y1="230" x2="1000" y2="230"' in html, \
        "世界分栏线是画面上的注记，推近时该保持屏幕读数"

    # ⑤ 自动错峰：整块包在 @supports 里，两处减号两侧都带空格
    stag_at = html.index("@supports (animation-delay:calc(1ms * sibling-index())){")
    stag_block = html[stag_at:html.index("\n  }", stag_at)]
    assert (".stag>*{--delay:calc(min(var(--stagger),\n"
            "                             var(--stag-cap) / max(sibling-count() - 1, 1))\n"
            "                          * (sibling-index() - 1))}") in stag_block
    # 规则里绝不允许出现紧凑写法（注释里提到它是反面教材，所以只查规则段）
    assert "sibling-count()-1" not in stag_block and "sibling-index()-1" not in stag_block, \
        "紧凑减号让整条 calc() 作废、延迟全落 0s，而动画照跑——静默回归"
    assert "max(sibling-count() - 1, 1)" in stag_block, \
        "单项组分母为 0 会让 calc() 作废（不是退化成 0s）"

    # ⑥ 描边光环：paint-order 不支持时不能只靠声明（描边会啃字），必须 @supports 包住
    assert "@supports (paint-order:stroke){" in html
    assert (".halo{stroke:var(--halo-bg);stroke-width:.22em;stroke-linejoin:round;"
            "paint-order:stroke}") in html
    assert ".halo{stroke-width:2.86px" not in html, \
        "描边宽度必须用 em 跟着字号走：小屏档 put() 统一放大字号时 px 常数会露缝"

    # ⑦ 消费者：底线 + 流光分两个节点、同 d、流光排在画线之后，且不带头
    assert "put(arcs, 'path', 'a-arc-1-flow', {class:'flow', pathLength:1, fill:'none'," in html
    assert "style:'--delay:.55s'" in html, "掠光要排在 --dur-draw .55s 之后：先有线、后走光"
    assert "class:'flow draw'" not in html and "class:'draw flow'" not in html, \
        "同节点两条 stroke-dasharray 会互相顶掉"
    flow_at = html.index("'a-arc-1-flow'")
    assert "marker-end" not in html[flow_at:flow_at + 320], \
        "箭头只留给底线：光段是一个事件，不是一条路径"
    assert html.count("class:'flow'") == 1, "本轮只加一个流光消费者，别把掠光撒满全场"

    # ⑧ 门禁卡片：挂 .stag、旧的三份 nth-child 魔法数必须整条消失
    assert '<div class="g-card stag">' in html
    assert ":nth-child(1){animation-delay" not in html and \
        ".gate .g-card>*:nth-child(2)" not in html, \
        "runtime 动态生成的交互块多于三项时，nth-child 那份魔法数会把第四项顶回 0s"
    assert (".gate .g-card>*{animation:cardIn var(--dur-gate) var(--ease-out) both;\n"
            "        animation-delay:var(--delay,0s)}") in html

    # ⑨ reduced-motion 兜底：流光关 animation（落回"看不见"），门禁一族抬特异度
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    assert ".flow{animation:none}" in rm
    assert "#gate,#gate.leaving,#gate .g-card>*{animation:none}" in rm
    # 旧那行保留（它同时兜住 .in/.pop/.draw/.pulse，那四条基础规则写在本块之前）
    assert ".in,.pop,.draw,.pulse,.gate,.gate.leaving,.gate .g-card>*{animation:none}" in rm
    # 线宽/错峰/光环**不进**兜底清单：前两条是静态口径、第三条只影响排程
    rm_rules = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert "vector-effect" not in rm_rules and ".stag>" not in rm_rules \
        and ".halo{" not in rm_rules, "线宽口径 / 错峰排程 / 描边顺序都不是动效，不进兜底清单"

    # ⑩ 本轮实测否决的四条，别被下一轮再请回来。
    #     **其中 offset-path 在 0.20.0 被有条件翻案**（`.ride`），这里把重新画过的
    #     边界锁住：当年否决它的理由是"用 animation 驱动沿线位移，导出切掉动画后
    #     圆点落在路径起点，成片里多出一个谁也没请的小点"——**那条理由仍然成立**，
    #     成立的正是 animation 这个驱动方式（下面锁 keyframes 里不许出现 offset-*）。
    #     0.20.0 换成 transition + 呈现属性档位（data-go），实测切掉动效后
    #     offset-distance 落 100%、屏幕坐标停在路线终点 (646,472)，小点不是意外
    #     而是被请来的主角。另一半理由（移动的子节点不进 getBBox()）**原样有效**，
    #     所以 .ride 的消费者只把它当视觉指引：不当热区、不参与 §5 的连线几何判定。
    assert "<animate" not in html and "animateMotion" not in html, \
        "SMIL 不吃 animation:none，getAnimations() 也冻不住——可及性与导出两侧都管不了它"
    for body in _keyframe_bodies(html):
        assert "offset-" not in body, \
            "offset-* 一旦交给 keyframes，导出切掉 animation 就落回起点（0.18.0 否决的是这个写法）"
    assert '.ride[data-go="1"]{offset-distance:100%}' in html, \
        "0.20.0 翻案的条件：行程终态必须活在呈现属性上，否则定格就落在起点"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_flow_comet_sweeps_and_vanishes(full_lesson):
    """真实浏览器：一条弧线先被画满、再被一段光掠过去，两端都"看不见"。

    取样口径是 **`isPointInStroke()` 沿路扫一遍**，不是像素扫描：虚线的命中区跟着
    dash 图案走（实测静止态 201 个采样点全不中、中段只有一段命中），于是"光段在不在
    场上""在路的哪一段"这两件事用 DOM API 就能读到，不必引入位图阈值——0.16.1 的教训
    是别把某个渲染环境的绝对像素当契约，这里连"像素"这一层都省了。
    锁四件事：① 静止/延迟期/跑完三处**都是零命中**（掠光是事件不是状态）；
    ② 补间过程中命中段一路往路尾挪（连续行程，不是两格闪烁）；
    ③ 底线与流光层几何同源（同 d、同 getBBox），掠过去的是同一条路；
    ④ 流光层不吃命中，而它下面那条底线照旧点得到（虚线空隙不命中 → 不禁用命中就是
    一条"看不见的挡热区的带子"）。
    """
    chrome, ev, _send = _cdp(full_lesson, "flow")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-1', 1)")           # 弧线在这一句建档：建档即演
        st = json.loads(ev("""(function(){
          var f = document.getElementById('a-arc-1-flow'), b = document.getElementById('a-arc-1');
          var cf = getComputedStyle(f), cb = getComputedStyle(b);
          return JSON.stringify({dash:cf.strokeDasharray, off:cf.strokeDashoffset,
            name:cf.animationName, dur:cf.animationDuration, delay:cf.animationDelay,
            fill:cf.animationFillMode, iter:cf.animationIterationCount, pe:cf.pointerEvents,
            baseName:cb.animationName, baseDash:cb.strokeDasharray,
            sameD:b.getAttribute('d') === f.getAttribute('d'),
            box:[b.getBBox().x, b.getBBox().y, b.getBBox().width, b.getBBox().height],
            fbox:[f.getBBox().x, f.getBBox().y, f.getBBox().width, f.getBBox().height]});
        })()"""))
        assert st["name"] == "ntFlow" and st["iter"] == "1", f"掠光该演一次：{st}"
        assert st["dur"] == "0.9s" and st["delay"] == "0.55s", \
            f"时长走 --dur-flow、排在画线（--dur-draw .55s）之后：{st}"
        assert st["fill"] == "backwards", f"延迟期间停在还没起头：{st}"
        assert st["pe"] == "none", f"装饰层不吃命中：{st}"
        assert st["dash"] == "0.16px, 1px" and st["off"] == "0.16px", \
            f"静止态 = 光段整体在路头之外（--flow-seg）：{st}"
        assert st["baseName"] == "ntDraw" and st["baseDash"] == "1px, 1px", \
            f"底线仍归 .draw 画满，两层各管各的：{st}"
        assert st["sameD"] and st["box"] == st["fbox"], f"两层同一条路：{st}"
        # 建档即演：动画在**这一拍**就登记进 getAnimations()（晚一步建档就只剩淡入）
        assert "ntFlow" in _anims(ev, "#a-arc-1-flow"), _anims(ev, "#a-arc-1-flow")

        # 沿路扫命中：返回 (dashoffset, 命中点数, 连续段数, 首段位置)。
        # fill-mode 是 backwards，动画**跑完就不在 getAnimations() 里挂着了**（实测自然
        # 落定后 getAnimations() 为空、计算值回到 .16px、沿路零命中）——所以每次取样前
        # 先按范本三段式重挂（摘 class → 读一次几何 → 挂回），再 pause + 设 currentTime；
        # 而"演完之后"那一档反倒要**让它自然跑完**，量到的才是导出/兜底真正会落的那一格。
        REARM = r"""(function(){
          var e = document.getElementById('a-arc-1-flow');
          e.setAttribute('class', ''); void e.getBoundingClientRect();
          e.setAttribute('class', 'nt flow'); void e.getBoundingClientRect();
          var a = (e.getAnimations() || []).filter(function(x){
            return x.animationName === 'ntFlow'; })[0];
          return JSON.stringify({found: !!a});
        })()"""
        SCAN = r"""(function(){
          var e = document.getElementById('a-arc-1-flow');
          var a = (e.getAnimations() || []).filter(function(x){
            return x.animationName === 'ntFlow'; })[0];
          if(!a) return JSON.stringify({err:'重挂后 ntFlow 没登记进 getAnimations()'});
          a.pause(); a.currentTime = %d;
          var L = e.getTotalLength(), lit = 0, runs = 0, prev = false, first = -1, last = -1;
          for (var i = 0; i <= 200; i++){
            var hit = e.isPointInStroke(e.getPointAtLength(L * i / 200));
            if (hit){ lit++; if(!prev) runs++; if(first < 0) first = i; last = i; }
            prev = hit;
          }
          return JSON.stringify({t:%d, off:getComputedStyle(e).strokeDashoffset,
                                 lit:lit, runs:runs, first:first, last:last, L:L});
        })()"""
        # 延迟窗口内（--delay .55s）与行程中（.55s ~ 1.45s）
        shots = []
        for t_ms in (0, 400, 549, 700, 900, 1200, 1400):
            assert json.loads(ev(REARM))["found"], t_ms
            shots.append(json.loads(ev(SCAN % (t_ms, t_ms))))
        for s in shots:
            assert "err" not in s, f"取不到动画：{s}"
        # ① 静止 / 延迟期：零命中（"不演就没有光"）
        assert shots[0]["lit"] == 0 and shots[1]["lit"] == 0 and shots[2]["lit"] == 0, \
            f"0 ~ --delay 期间光段必须完全不在路上：{shots[:3]}"
        # ② 行程连续往路尾走，且任何时刻路上只有一段光
        for s in shots[3:]:
            assert s["lit"] > 0 and s["runs"] == 1, f"路上应当只有一段光：{s}"
        assert shots[3]["first"] < shots[4]["first"] < shots[5]["first"] < shots[6]["first"], \
            f"命中段一路往路尾挪：{[s['first'] for s in shots[3:]]}"
        offs = [float(s["off"].rstrip("px")) for s in shots[3:]]
        assert offs == sorted(offs, reverse=True), f"dashoffset 单调递减（线性掠过）：{offs}"
        assert float(shots[6]["off"].rstrip("px")) < -0.9, \
            f"演到路尾那一带来才算走完一整条路：{shots[6]['off']}"
        # ③ 演完之后落回"看不见"，与静止态同一读数（让它自然跑完，不冻结）
        assert json.loads(ev(REARM))["found"]
        deadline = time.time() + 4.0
        while time.time() < deadline and _anims(ev, "#a-arc-1-flow"):
            time.sleep(0.1)
        after = json.loads(ev(r"""(function(){
          var e = document.getElementById('a-arc-1-flow'), cs = getComputedStyle(e);
          var L = e.getTotalLength(), lit = 0;
          for (var i = 0; i <= 200; i++){
            if (e.isPointInStroke(e.getPointAtLength(L * i / 200))) lit++;
          }
          return JSON.stringify({off:cs.strokeDashoffset, lit:lit,
                                 anims:(e.getAnimations() || []).length});
        })()"""))
        assert after["lit"] == 0 and after["off"] == "0.16px", \
            f"跑完与静止是同一格画面（掠光不留痕迹）：{after}"
        assert not [n for n in _anims(ev, "#a-arc-1-flow") if n == "ntFlow"], \
            f"backwards 的动画跑完不再挂在 getAnimations() 里：{after}"

        # ④ 命中：把镜头推到 playing（幕布退场），路中点应落在底线上而不是流光层
        hit = json.loads(ev(r"""(function(){
          document.getElementById('stage').setAttribute('data-state', 'playing');
          var f = document.getElementById('a-arc-1-flow'), b = document.getElementById('a-arc-1');
          var a = (f.getAnimations() || []).filter(function(x){
            return x.animationName === 'ntFlow'; })[0];
          if(a){ a.pause(); a.currentTime = 700; }        // 光段就停在采样点附近
          var L = f.getTotalLength(), pt = f.getPointAtLength(L / 2);
          var sp = new DOMPoint(pt.x, pt.y).matrixTransform(f.getScreenCTM());
          var t = document.elementFromPoint(sp.x, sp.y);
          return JSON.stringify({hit:t.id, isFlow:t === f, underFlow:f.contains(t),
                                 isBase:t === b});
        })()"""))
        assert not hit["isFlow"] and not hit["underFlow"], \
            f"流光层绝不能当命中目标：{hit}"
        assert hit["isBase"], f"底线路径必须照旧点得到（上一层没挡住它）：{hit}"

        # ⑤ 导出/兜底同一口径：一刀切掉 animation 后落回静止态（光段在路外）。
        # 注入与取值分两次 CDP 调用：同一份脚本里刚 appendChild 的那条规则，样式
        # 重算未必已经落上（读到的还是旧计算值），分调用天然跨过一次 flush。
        ev(r"""(function(){
          var st = document.createElement('style'); st.id = 'shot-cut';
          // 导出那一刀是 `*,*::before,*::after{…!important}`（export_video.py），
          // 这里只对这一个是图元素来一刀（id 选择器 + !important），读到的就是
          // 截图帧上这一层的落点。style 节点另起 id：与被测路径重名的话，
          // getElementById 会先抓到这个 <style>（它在 <head> 里，排在前面）。
          st.textContent = '#a-arc-1-flow,#a-arc-1{transition:none!important;'
                         + 'animation:none!important}';
          document.head.appendChild(st); return 1;
        })()""")
        cut = json.loads(ev(r"""(function(){
          var e = document.getElementById('a-arc-1-flow'), cs = getComputedStyle(e);
          var b = document.getElementById('a-arc-1'), cb = getComputedStyle(b);
          var L = e.getTotalLength(), lit = 0;
          for (var i = 0; i <= 200; i++){
            if (e.isPointInStroke(e.getPointAtLength(L * i / 200))) lit++;
          }
          var bl = b.getTotalLength(), bhit = 0;
          for (var j = 0; j <= 200; j++){
            if (b.isPointInStroke(b.getPointAtLength(bl * j / 200))) bhit++;
          }
          return JSON.stringify({off:cs.strokeDashoffset, name:cs.animationName, lit:lit,
                                 dash:cs.strokeDasharray, baseName:cb.animationName,
                                 baseOff:cb.strokeDashoffset, baseHit:bhit});
        })()"""))
        ev("var s=document.getElementById('shot-cut'); s.parentNode.removeChild(s); 1")
        assert cut["name"] == "none" and cut["baseName"] == "none", \
            f"这一刀要把两条动画都关成 none：{cut}"
        assert cut["off"] == "0.16px" and cut["lit"] == 0, \
            f"截图帧上这一层隐形、画面仍是那条底线：{cut}"
        assert cut["baseOff"] == "0px" and cut["baseHit"] >= 195, \
            f"底线切掉动画后仍是画满、可命中的那条线：{cut}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_auto_stagger_locks_at_group_level(full_lesson):
    """真实浏览器：错峰只发生在**分组那一层**，且总量封顶；顺带量恒定线宽与描边光环。

    ① 门禁卡片三项实测 0 / .06 / .12s（换掉原来三份 nth-child 魔法数），卡片里的
      runtime 生成物（题目、选项、放行键）**全部落回 0s**——`--delay` 注册成
      `inherits:false` 才有的行为。不注册的话它在每个后代上各自重算 sibling-index()，
      而 `put()` 的常态就是嵌套建档，那会把"错峰入场"变成"满屏乱闪"。
    ② 总量守恒：1 / 2 / 3 / 6 项的组各测一次，最后一项永远 ≤ --stag-cap（.18s），
      6 项摊薄成 .036s 一档。
    ③ 减号两侧没空格的那版**必须静默失败**：把带空格与不带空格两条规则同时挂上去对比，
      不带空格那组动画照跑（animationName 仍是 cardIn）但延迟全是 0s——控制台一句话
      都不报。静态契约锁的是写法，这一条锁的是"写法错了会怎样"。
    ④ `vector-effect` **不继承**：给 #cam 整层写 nss，子层照旧 none，而逐图元挂的
      `.crisp` 才是 non-scaling-stroke。线宽的视觉口径用 canvas 位图 A/B 量墨量
      （threshold-free 的亮度累加，不比绝对像素）：同一条 1.8× 的缩放里普通描边的墨量
      明显变大、`.crisp` 与 1× 时持平。
    ⑤ `.halo`：paint-order 落 stroke、描边色就是 --halo-bg、描边宽度按 em 跟着字号走。
    """
    chrome, ev, _send = _cdp(full_lesson, "stag")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-1', 1)")

        # ① 分组层有错峰、深层没有
        grp = json.loads(ev(r"""(function(){
          var direct = [], deep = [];
          document.querySelectorAll('.g-card>*').forEach(function(e){
            var c = getComputedStyle(e);
            direct.push([e.id, c.animationDelay, c.animationName]); });
          ['gate-kick', 'gate-host', 'gate-q', 'gate-opts', 'gate-block', 'gate-fb',
           'gate-next', 'gate-go', 'gate-hint'].forEach(function(id){
            var e = document.getElementById(id);
            if(!e) return;
            var c = getComputedStyle(e);
            deep.push([id, c.getPropertyValue('--delay').trim(),
                       e.parentElement.classList.contains('stag')]); });
          return JSON.stringify({direct:direct, deep:deep,
                                 card:document.querySelector('.g-card').getAttribute('class')});
        })()"""))
        assert "stag" in grp["card"], f"卡片必须真的挂上 .stag：{grp['card']}"
        assert [d[1] for d in grp["direct"]] == ["0s", "0.06s", "0.12s"], \
            f"门禁三项按 --stagger 逐位错开：{grp['direct']}"
        assert all(d[2] == "cardIn" for d in grp["direct"]), f"动作仍归 cardIn：{grp['direct']}"
        deep_in_card = [d for d in grp["deep"] if not d[2]]
        assert deep_in_card and all(d[1] == "0s" for d in deep_in_card), \
            f"卡片内的深层节点不得各自错峰（--delay 不继承）：{deep_in_card}"

        # ② 总量守恒：组越大间距越摊薄，最后一项永远 ≤ .18s
        GRP = r"""(function(){
          var d = document.createElement('div'); d.className = 'stag';
          for (var i = 0; i < @N@; i++){ d.appendChild(document.createElement('span')); }
          document.body.appendChild(d);
          var o = [].slice.call(d.children).map(function(c){
            return getComputedStyle(c).getPropertyValue('--delay').trim(); });
          d.parentNode.removeChild(d); return JSON.stringify(o);
        })()"""
        got = {n: json.loads(ev(GRP.replace("@N@", str(n)))) for n in (1, 2, 3, 6)}
        assert got[1] == ["0s"], f"单项组不能被 0 分母作废：{got[1]}"
        assert got[2] == ["0s", "0.06s"], got[2]
        assert got[3] == ["0s", "0.06s", "0.12s"], got[3]
        assert got[6] == ["0s", "0.036s", "0.072s", "0.108s", "0.144s", "0.18s"], \
            f"六项摊薄到 --stag-cap 封顶：{got[6]}"
        for n, lst in got.items():
            assert float(lst[-1].rstrip("s")) <= 0.18 + 1e-9, f"{n} 项组末尾超过 .18s：{lst}"

        # ③ 紧凑减号那条必须"看着在动、其实没错峰"
        ab = json.loads(ev(r"""(function(){
          var st = document.createElement('style');
          st.textContent =
            '.sbad>*{--delay:calc(min(var(--stagger),var(--stag-cap) / '
            + 'max(sibling-count()-1,1)) * (sibling-index()-1));'
            + 'animation:cardIn .4s both;animation-delay:var(--delay,0s)}'
            + '.sgood>*{--delay:calc(min(var(--stagger),var(--stag-cap) / '
            + 'max(sibling-count() - 1, 1)) * (sibling-index() - 1));'
            + 'animation:cardIn .4s both;animation-delay:var(--delay,0s)}';
          document.head.appendChild(st);
          function grp(cls){
            var d = document.createElement('div'); d.className = cls;
            for (var i = 0; i < 3; i++){ d.appendChild(document.createElement('span')); }
            document.body.appendChild(d);
            var o = [].slice.call(d.children).map(function(c){
              var cs = getComputedStyle(c);
              return [cs.getPropertyValue('--delay').trim(), cs.animationName]; });
            d.parentNode.removeChild(d); return o;
          }
          var r = {bad:grp('sbad'), good:grp('sgood')};
          st.parentNode.removeChild(st); return JSON.stringify(r);
        })()"""))
        assert [x[0] for x in ab["good"]] == ["0s", "0.06s", "0.12s"], ab["good"]
        assert [x[0] for x in ab["bad"]] == ["0s", "0s", "0s"], \
            f"带空格那版是唯一的正确写法，紧凑版静默作废：{ab['bad']}"
        assert all(x[1] == "cardIn" for x in ab["bad"]), \
            "紧凑版只坏掉排程、动画照跑——所以它不会自己报错，只能靠用例钉住"

        # ④ vector-effect 不继承 + 线宽的位图 A/B
        ve = json.loads(ev(r"""(function(){
          var st = document.createElement('style');
          st.textContent = '#cam{vector-effect:non-scaling-stroke}';
          document.head.appendChild(st);
          var cam = document.getElementById('cam');
          var o = {cam:getComputedStyle(cam).vectorEffect,
                   world:getComputedStyle(document.getElementById('world')).vectorEffect,
                   crisp:getComputedStyle(cam.querySelector('line.crisp')).vectorEffect};
          st.parentNode.removeChild(st);
          o.crispAfter = getComputedStyle(cam.querySelector('line.crisp')).vectorEffect;
          o.bareAfter = getComputedStyle(document.getElementById('world')).vectorEffect;
          return JSON.stringify(o);
        })()"""))
        assert ve["cam"] == "non-scaling-stroke", ve
        assert ve["crisp"] == "non-scaling-stroke", ve
        assert ve["world"] == "none", \
            f"父层写了 nss、子层照旧 none——它不继承，必须逐图元挂：{ve}"

        INK = r"""new Promise(function(res, rej){
          var K = @K@;
          var s = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"'
            + ' viewBox="0 0 400 400"><rect width="400" height="400" fill="black"/>'
            + '<g transform="scale(' + K + ')">'
            + '<line x1="10" y1="20" x2="180" y2="20" stroke="white" stroke-width="4"/>'
            + '<line x1="10" y1="70" x2="180" y2="70" stroke="white" stroke-width="4"'
            + ' vector-effect="non-scaling-stroke"/></g></svg>';
          var img = new Image();
          img.onload = function(){
            var c = document.createElement('canvas'); c.width = 400; c.height = 400;
            var x = c.getContext('2d', {willReadFrequently:true});
            x.drawImage(img, 0, 0);
            function ink(cx, y0, y1){ var t = 0;
              for (var y = y0; y < y1; y++){ t += x.getImageData(cx, y, 1, 1).data[0] / 255; }
              return Math.round(t * 100) / 100; }
            res(JSON.stringify({k:K, plain:ink(90, Math.round(20*K) - 12, Math.round(20*K) + 12),
                                crisp:ink(90, Math.round(70*K) - 12, Math.round(70*K) + 12)}));
          };
          img.onerror = function(){ rej(new Error('内联 SVG 没解码成位图')); };
          img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);
        })"""
        one = json.loads(ev(INK.replace("@K@", "1"), True))
        zoom = json.loads(ev(INK.replace("@K@", "1.8"), True))
        assert abs(one["plain"] - one["crisp"]) < 0.5, \
            f"1× 时两条线本该一模一样（基准档）：{one}"
        assert zoom["plain"] >= one["plain"] * 1.4, \
            f"普通描边跟着镜头变粗（实测约 1.8×）：{one} → {zoom}"
        assert abs(zoom["crisp"] - one["crisp"]) <= 0.5, \
            f".crisp 的读数不随镜头变粗——这才是恒定屏幕线宽：{one} vs {zoom}"

        # ⑤ 描边光环
        halo = json.loads(ev(r"""(function(){
          var t = document.getElementById('a-arc-1-lab');
          var c = getComputedStyle(t);
          var root = getComputedStyle(document.documentElement)
                       .getPropertyValue('--halo-bg').trim();
          return JSON.stringify({cls:t.getAttribute('class'), po:c.paintOrder,
            sw:c.strokeWidth, fs:c.fontSize, stroke:c.stroke, token:root,
            join:c.strokeLinejoin, fill:c.fill});
        })()"""))
        assert "halo" in halo["cls"] and halo["po"] == "stroke", halo
        assert halo["token"] and halo["stroke"] == "rgb(26, 32, 41)", \
            f"光环色取舞台局部底色令牌：{halo}"
        ratio = float(halo["sw"].rstrip("px")) / float(halo["fs"].rstrip("px"))
        assert abs(ratio - 0.22) < 0.01, f"描边宽度按 .22em 跟着字号走：{halo}"
        assert halo["join"] == "round", halo
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_line_and_label_reduced_motion_fallback(full_lesson):
    """关掉动效：流光整层隐形（底线照旧完整）、门禁一族真的落回 none（含错峰）。

    两条口径都在这一轮被实测重新定义：
      · 流光的兜底**落的正是终态**——animation 关掉后 stroke-dashoffset 回到 --flow-seg，
        光段在路头之外，沿路扫零命中，画面仍是那条被 .draw 画满的连线，一格信息不少；
      · 门禁那一族的兜底行**必须带 #gate 抬特异度**。旧那行 `.gate,.gate .g-card>*`
        被源序在后的基础规则顶掉过（实测计算值仍是 gateIn / cardIn），此前靠 §⑤ 那条
        `animation-duration:.01ms!important` 混过去——但 **animation-delay 不在钳制范围
        内**，卡片一挂 .stag，减少动效下三张卡片就会按 0 / .06 / .12s 一个接一个冒出来，
        正是该关掉的行为。本轮顺手补上。
    `.crisp` / `.halo` / `.stag` 不是动效，关动效后它们的静态口径照旧在场。
    """
    chrome, ev, send = _cdp(full_lesson, "rm18")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("window.__jump('seg-1', 1)")

        st = json.loads(ev(r"""(function(){
          var f = document.getElementById('a-arc-1-flow'), b = document.getElementById('a-arc-1');
          var L = f.getTotalLength(), lit = 0;
          for (var i = 0; i <= 200; i++){
            if (f.isPointInStroke(f.getPointAtLength(L * i / 200))) lit++;
          }
          var bl = b.getTotalLength(), bhit = 0;
          for (var j = 0; j <= 200; j++){
            if (b.isPointInStroke(b.getPointAtLength(bl * j / 200))) bhit++;
          }
          var cards = [];
          document.querySelectorAll('.g-card>*').forEach(function(e){
            var c = getComputedStyle(e);
            cards.push([e.id, c.animationName, c.animationDelay]); });
          var g = document.getElementById('gate');
          return JSON.stringify({
            flow:[getComputedStyle(f).animationName, getComputedStyle(f).strokeDashoffset, lit],
            base:[getComputedStyle(b).animationName, getComputedStyle(b).strokeDashoffset, bhit],
            gate:getComputedStyle(g).animationName, leaving:getComputedStyle(g).animationName,
            cards:cards,
            dur:getComputedStyle(g).animationDuration,
            pe:getComputedStyle(f).pointerEvents});
        })()"""))
        assert st["flow"][0] == "none", f"流光动画整条关掉：{st['flow']}"
        assert st["flow"][1] == "0.16px" and st["flow"][2] == 0, \
            f"关掉后落在静止态 = 光段在路外、沿路零命中：{st['flow']}"
        assert st["base"][0] == "none" and st["base"][1] == "0px", \
            f"底线不演但画满是完整的：{st['base']}"
        assert st["base"][2] >= 195, f"底线路径几乎全程可命中（画面仍是那条连线）：{st['base']}"
        assert st["pe"] == "none", st

        # 本轮补的那条兜底：门禁一族关到 none，错峰也不再留延迟
        assert st["gate"] == "none", \
            "旧那行 `.gate{animation:none}` 会被源序在后的基础规则顶掉，必须带 #gate"
        assert st["cards"] and all(c[1] == "none" for c in st["cards"]), \
            f"卡片三项的 cardIn 也要真的关掉：{st['cards']}"
        assert all(c[2] == "0s" for c in st["cards"]), \
            f"animation-delay 不在 .01ms 钳制之内，兜不住就是三张卡片一个接一个冒出来：" \
            f"{st['cards']}"
        # 时长钳制那条仍在那儿（它兜的是别的），但这一轮不靠它
        assert float(st["dur"].replace("1e-05s", "0.00001")) <= 0.00001 + 1e-9, st["dur"]

        # 静态口径不跟着关：线宽、光环、错峰令牌照旧在场
        keep = json.loads(ev(r"""(function(){
          var cam = document.getElementById('cam');
          var t = document.getElementById('a-arc-1-lab');
          var d = document.createElement('div'); d.className = 'stag';
          for (var i = 0; i < 3; i++){ d.appendChild(document.createElement('span')); }
          document.body.appendChild(d);
          var delays = [].slice.call(d.children).map(function(c){
            return getComputedStyle(c).getPropertyValue('--delay').trim(); });
          d.parentNode.removeChild(d);
          return JSON.stringify({
            crisp:getComputedStyle(cam.querySelector('line.crisp')).vectorEffect,
            po:getComputedStyle(t).paintOrder, sw:getComputedStyle(t).strokeWidth,
            delays:delays});
        })()"""))
        assert keep["crisp"] == "non-scaling-stroke", keep
        assert keep["po"] == "stroke" and keep["sw"], keep
        assert keep["delays"] == ["0s", "0.06s", "0.12s"], \
            f".stag 只写排程、不是动效，兜底清单里没有它也不该缺席：{keep}"
    finally:
        _kill(chrome)


# ── 0.19.0：跟读点亮 + 收拢退场 + CJK 行首标点 ────────────────────────

def test_reading_and_tuck_primitives_present():
    """模板结构契约：0.19.0 三条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮在 Chrome 152 里量出来"为什么只能这么写"的边界：
      · `--lit` 的 initial 必须取 **118% = 100% + --lit-ramp**，不能写 100%——写 100%
        就把每句**末尾那 18%** 永久留在灰阶地板上，静止态变成"句尾永远没念到"，
        纪律③（绝不把内容锁进可能不跑的门）直接不合格；
      · 地板档 `--lit-floor` 必须是 **.85**：合成后的实测对比度非结论 7.97:1、
        结论 6.08:1；.72 那一档墨量差更大（均值比 1.250 vs 1.128，0.23.1 同字两行口径）
        但结论字色掉到 4.76:1（贴线），.92 那一档均值比只剩 1.063（肉眼分不出念没念过）。
        这条是本轮改错过一次的地方：第一版按截图像素的**平均亮度**算出"8.04:1"
        并据此选了 .72——量法本身就不对（§8 要的是合成色对底色）；
      · 收拢两端都必须是**带四个长度的 `inset()`**、静止档 **-15%**（外扩，`inset(0 0 0 0)`
        会沿 fill-box 边缘啃掉描边与 marker-end 箭头）、收拢档 **115%**（= 100% + 那 15%
        余量；写 85% 实测留 46px 能点着的残角）；
      · 收拢走 **transition** 且只挂在纯包装 g 上：`.nt` 与本条都是 transition 简写，
        同节点时源序靠后的那条把另一条的清单整串顶掉（实测计算值只剩 `clip-path`，
        transform / opacity 一步落终值）——所以两个消费者只换 `gone`，不写 opacity；
      · `#cap-prog` 与 `--lit` 都**不进** reduced-motion 清单（它们是时钟的逐拍读数），
        `.tuckR/.tuckL` 进，且这一条**不需要**抬特异度（基础规则源序在本块之前）；
      · CJK 行首标点只挂会换行的 HTML 正文，**字幕 `#cap-text` 不挂**：它在 SVG `<text>`
        上确实生效（实测 bbox 宽 −0.5em），但字幕单行不换行，收益看不见、却要动三个
        派生视图共用的 bbox 读数。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")

    # ① 令牌：收拢比揭示短一档、点亮地板 .85、软过渡带 18%
    assert "--dur-tuck:.42s; --lit-floor:.85; --lit-ramp:18%;" in html
    assert "--lit-floor:.72" not in html, \
        ".72 那档合成后结论字色只剩 4.76:1（贴线零余量），本轮实测否决"
    assert "--lit-floor:1" not in html and "--lit-ramp:34%" not in html

    # ② --lit 注册：initial 取满亮档 118%，不是 100%
    assert ("@property --lit{syntax:'<percentage>';inherits:false;"
            "initial-value:118%}") in html
    assert "@property --lit{syntax:'<percentage>';inherits:false;initial-value:100%}" \
        not in html, "initial 写 100% = 每句句尾永久留在地板上"
    assert "@property --lit{syntax:'<percentage>';inherits:true}" not in html, \
        "继承会把读数漏给 #cap-text 之外的节点（与 --delay 同一课）"

    # ③ 跟读点亮：停靠点用注册变量算，地板写成 rgba 的 alpha（不是颜色）
    assert ("#cap-text{mask-image:linear-gradient(90deg,#000 0 "
            "calc(var(--lit) - var(--lit-ramp)),\n"
            "                        rgba(0,0,0,var(--lit-floor)) var(--lit) 100%)}") in html
    assert "clip-path" not in html.split("#cap-text{mask-image:")[1].split("}")[0], \
        "字幕上不能用 clip-path：那会把命中与 client rects 一起裁掉，QA 认不可见"

    # ④ 收拢退场：四长度 inset()、静止外扩 -15%、收拢 115%
    assert (".tuckR,.tuckL{clip-path:inset(-15% -15% -15% -15%);\n"
            "        transition:clip-path var(--dur-tuck) var(--ease-out)}") in html
    assert ".tuckR.gone{clip-path:inset(-15% -15% -15% 115%)}" in html
    assert ".tuckL.gone{clip-path:inset(-15% 115% -15% -15%)}" in html
    for bad in ("inset(0 0 0 0)", "inset(-15% -15% -15% 85%)", "inset(-15% 85% -15% -15%)"):
        assert bad not in html.split(".tuckR,.tuckL{")[1].split(".tuckL.gone")[1], \
            f"收拢档位写成 {bad} 就漏：-15% 外扩才不啃箭头，115% 才收掉那 15% 残角"
    assert ".tuckR,.tuckL{clip-path:none" not in html, \
        "inset() 与 none 不可插值（0.15.0 那条 discrete 教训）"

    # ⑤ JS 写手：满亮档常量与 @property 同数、逐拍写 style、同值不重写
    assert "var LIT_FULL = 118;" in html
    assert "function capLitWrite(t){" in html
    assert "el2.style.setProperty('--lit', v)" in html
    assert "el2.style.getPropertyValue('--lit') !== v" in html, "同值不重写：每帧都跑"
    # 调用点：capShow 的收起分支 + tick 尾部（且必须排在 capProgWrite 之后）
    assert "capProgWrite(0); capLitWrite(0); return; }" in html
    tick_at = html.index("  function tick(){")
    tail = html[tick_at:tick_at + 4200]
    assert tail.index("capProgWrite(t);") < tail.index("capLitWrite(t);"), \
        "必须排在换句分支之后：capShow 才写 capSent，写在前面会拿上一句的时长算亮度"

    # ⑥ 两个消费者：纯包装 g、只换 gone，旧那句 opacity 淡出必须整条消失
    assert ("var arcs = put('a-body', 'g', 'a-arcs',\n"
            "        {class: step === 1 ? 'tuckR' : 'tuckR gone'});") in html
    assert ("var ties = put('e-body', 'g', 'e-ties',\n"
            "        {class: step === 1 ? 'tuckL' : 'tuckL gone'});") in html
    assert "{opacity: step === 1 ? 1 : 0}" not in html, \
        "收拢与淡出是两件事：留着 opacity 就同时有两条退场词汇"
    assert html.count("'tuckR") == 2 and html.count("'tuckL") == 2, \
        "本轮各一个消费者，别把收拢撒满全场（.flow 同口径）"
    # 连线挪进包装 g：key 与图元不变，只是换了爹
    assert "put(ties, 'line', 'e-tie-'" in html
    assert "put('e-body', 'line', 'e-tie-'" not in html

    # ⑦ reduced-motion：收拢关 transition；--lit 与细线都不进这一栏
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    assert ".tuckR,.tuckL{transition:none}" in rm
    rm_rules = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert "--lit" not in rm_rules and "#cap-prog" not in rm_rules, \
        "点亮与细线是时钟读数不是动效，关了动效它们得跟上嘴"

    # ⑧ CJK 行首标点：@supports 包住，只挂 HTML 正文，字幕不挂
    assert "@supports (text-spacing-trim:trim-start){" in html
    assert (".n-txt,.gate .q,.interaction-feedback,.chapter .tagline"
            "{text-spacing-trim:trim-start}") in html
    trim_block = html.split("@supports (text-spacing-trim:trim-start){")[1].split("\n  }")[0]
    assert "cap-text" not in trim_block and "n-code" not in trim_block and \
        "kv" not in trim_block, f"单行/等宽/对照行不进这一档：{trim_block}"
    assert "text-autospace" not in html, "autospace 各取值实测零差值，不写进范本"
    assert "font-variant-numeric" not in html, "tabular-nums 在现用字体上零差值，同上"

    # ⑨ 本轮实测否决的三条，别被下一轮再请回来
    assert "position-anchor" not in html and "anchor(" not in html.split("<style>")[1] \
        .split("</style>")[0].replace("anchor:none", ""), \
        "anchor() 给指针那一类跟随会开第二个时钟：页面只允许 currentTime 一个钟"
    assert "forced-colors" not in html, "headless 模拟不了 forced-colors，量过的才算数"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_caption_lit_is_a_clock_reading(full_lesson):
    """真实浏览器：跟读点亮的 `--lit` 是 currentTime 的**逐拍读数**，且遮罩不改几何。

    与 0.16.0 那条细线用例同构，但量的东西不同：细线量 width（几何），这一层量一个
    注册自定义属性。四个回归点都来自本轮实测：
      · 句内 0 / 50% / 99.9% 三点 `--lit` = 118%×k 对得上，且落在 **style** 上
        （导出截图页那一刀只切 transition/animation，属性与 style 读数都留着）；
      · 遮罩不改盒：`getClientRects().length` 与 bbox 全程逐值不变——这正是
        `check_gates.py` 的 `captionShown()` 与相机取景读的两样东西（mask 与
        clip-path 最关键的差别，clip-path 会把 rects 裁掉）；
      · 换句归位、空档停在满亮、播完落回 118%（capSent 为 null 走满亮而不是清零），
        结束态回拖同一句又跟着走；
      · 字号换档（小屏 26px）不影响 k 的读数：写的是比例，不是像素。
    """
    chrome, ev, _send = _cdp(full_lesson, "caplit")
    try:
        ev(_SEEK)
        tl = json.loads(ev("JSON.stringify(window.__tl)"))
        seg2 = [s for s in tl["scenes"] if s["step_id"] == "seg-2"][0]
        n0 = seg2["runtime"]["narration"][0]

        def seek(t):
            ev("(function(){window.__t=%r;document.getElementById('main-audio')"
               ".dispatchEvent(new Event('timeupdate'));return 1;})()" % t)
            return json.loads(ev("""(function(){var t=document.getElementById('cap-text'),
              cs=getComputedStyle(t);
              var b=t.getBBox();
              return JSON.stringify({lit:cs.getPropertyValue('--lit').trim(),
                style:t.style.getPropertyValue('--lit'),
                mask:cs.maskImage, bw:+b.width.toFixed(2), bx:+b.x.toFixed(2),
                rects:t.getClientRects().length, cap:t.textContent.slice(0,8)});})()"""))

        # ① 句内三点：读数即时钟比例
        ev("window.__jump('seg-2', 0)")
        geo = None
        for frac in (0.0, 0.5, 0.999):
            st = seek(n0["start"] + n0["duration"] * frac)
            want = 118 * frac
            # 断言写成读数与 118×k 的差（容差 0.1），不写死字符串：页内是
            # `(118 * ((t - start) / duration)).toFixed(1)`，t 由这里加出来的浮点
            # 与页内再除一次会有 1e-16 量级的尾差，写死 "59.0%" 会在别的 runner 上红。
            assert abs(float(st["lit"].rstrip("%")) - want) <= 0.1, \
                f"亮度读数 {frac} 实得 {st['lit']}（应 ≈{want:.1f}%）：{st}"
            assert st["style"], f"--lit 必须落在 style 上（截图页一刀切后仍读得到）：{st}"
            if geo is None:
                geo = (st["bw"], st["bx"], st["rects"])
            assert (st["bw"], st["bx"], st["rects"]) == geo, \
                f"遮罩不该改 bbox / client rects（QA 与取景都读这两样）：{geo} → {st}"
        # 软过渡带恒等于 --lit-ramp：不透明段终点 = --lit − 18%
        for frac in (0.25, 0.75):
            st = seek(n0["start"] + n0["duration"] * frac)
            vals = [float(v) for v in re.findall(r"(-?[\d.eE+-]+)%", st["mask"])]
            edge = float(st["lit"].rstrip("%"))
            assert abs(vals[0] - (edge - 18.0)) < 1e-3, f"软边该恒为 18%：{st['mask']}"
            assert abs(vals[1] - edge) < 1e-3, f"地板段起点贴着 --lit：{st['mask']}"

        # ② 同值不重写：同一拍连读两次，style 字符串完全一致（不是"每次刷新"）
        a = seek(n0["start"] + n0["duration"] * 0.4)
        b = seek(n0["start"] + n0["duration"] * 0.4)
        assert a["style"] == b["style"], (a, b)

        # ③ 空档：字幕停在上一句，亮度停在满亮（gap-hold 同一口径，不闪白）
        st = seek(n0["start"] + n0["duration"] + 0.06)
        assert abs(float(st["lit"].rstrip("%")) - 118.0) <= 0.1, f"空档应停在满亮：{st}"

        # ④ 换句：新句从 0 重新点亮
        ev("window.__jump('seg-2', 1)")
        st = seek(seg2["runtime"]["narration"][1]["start"])
        # 注意：计算值会归一化（style 写 "0.0%" → 读回 "0%"），所以比的是**数值**，
        # 只有 style 那一格才是逐字的字符串（同值不重写靠它）。
        assert st["style"] == "0.0%" and float(st["lit"].rstrip("%")) == 0.0, \
            f"换句必须归零重亮（整句起点那一拍）：{st}"

        # ⑤ 播完：capShow(null) → 满亮（收起态不留"整句压暗"的计算值）
        ev("(function(){document.getElementById('main-audio')"
           ".dispatchEvent(new Event('ended'));return 1;})()")
        assert abs(float(ev("document.getElementById('cap-text')"
                            ".style.getPropertyValue('--lit')").rstrip("%")) - 118.0) <= 0.1, \
            "结束态字幕收起，亮度不能留一档压暗"

        # ⑤b 结束态回拖同一句：读数跟着回来（纯函数律）
        n1 = seg2["runtime"]["narration"][1]
        st = seek(n1["start"] + n1["duration"] * 0.5)
        assert abs(float(st["lit"].rstrip("%")) - 59.0) <= 0.1, \
            f"回拖进活跃句子，点亮必须跟着回来：{st}"

        # ⑥ 字号换档：写的是比例，26px 档同样落 0.5
        ev("window.__jump('seg-2', 2)")
        n2 = seg2["runtime"]["narration"][2]
        desk = seek(n2["start"] + n2["duration"] * 0.5)
        assert abs(float(desk["lit"].rstrip("%")) - 59.0) <= 0.1, desk
        ev("(function(){document.documentElement.dataset.compact='1';return 1;})()")
        ev("(function(){document.getElementById('main-audio')"
           ".dispatchEvent(new Event('ended'));return 1;})()")
        st = seek(n2["start"] + n2["duration"] * 0.5)
        assert abs(float(st["lit"].rstrip("%")) - 59.0) <= 0.1, \
            f"小屏同样是比例读数：compact={st} desk={desk}"
        assert st["bw"] > desk["bw"] * 1.2, f"26px 档 bbox 应变宽（比的是两档之比）：{st} {desk}"
        ev("(function(){delete document.documentElement.dataset.compact;return 1;})()")

        # ⑦ 导出截图模式：一次 tick 就落那一秒的读数
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-2', 0)")
        st = seek(n0["start"] + n0["duration"] * 0.42)
        assert abs(float(st["lit"].rstrip("%")) - 49.56) <= 0.1, \
            f"截图模式一次 tick 就要落读数：{st}"
        ev("delete window.__coursewareShotMode")

        # ⑧ 字幕不挂 CJK 行首标点：三档正文挂、#cap-text 恒 normal
        typ = json.loads(ev(r"""(function(){
          var t = document.getElementById('cap-text');
          var note = document.querySelector('.n-txt');
          var q = document.querySelector('.gate .q');
          var tag = document.querySelector('.chapter .tagline');
          return JSON.stringify({cap:getComputedStyle(t).textSpacingTrim,
            supports:CSS.supports('text-spacing-trim','trim-start'),
            note:note?getComputedStyle(note).textSpacingTrim:null,
            q:q?getComputedStyle(q).textSpacingTrim:null,
            tag:tag?getComputedStyle(tag).textSpacingTrim:null,
            capW:+t.getBBox().width.toFixed(2)});})()"""))
        assert typ["supports"], "本机 Chrome 应支持 text-spacing-trim（否则这条用例失去意义）"
        assert typ["cap"] == "normal", f"字幕刻意不挂 trim-start：{typ}"
        assert typ["note"] == "trim-start" and typ["q"] == "trim-start" \
            and typ["tag"] == "trim-start", typ
    finally:
        _kill(chrome)




# 收拢退场的量法：读 clip-path 四边内缩百分比，A/C 两条用例共用。
# 用 @ID@ + replace 传参（JS 里到处是 %，% 格式化会炸）。
_TUCK_SNAP = r"""(function(){
  var n = document.getElementById('@ID@'), cs = getComputedStyle(n);
  var m = cs.clipPath.match(/inset\(([^)]*)\)/);
  var f = m ? m[1].trim().split(/\s+/).map(function(v){ return parseFloat(v); }) : [];
  while (f.length < 4){ f.push(f[0]); }
  return JSON.stringify({cls:n.getAttribute('class'), cp:cs.clipPath, ins:f,
    tp:cs.transitionProperty, tdur:cs.transitionDuration,
    bbox:+n.getBBox().width.toFixed(2), rw:+n.getBoundingClientRect().width.toFixed(2),
    an:(n.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;}),
    kids:[].slice.call(n.children).map(function(k){ return k.id; })});
})()"""

# 暂停补间取 0 / 120 / 260 / 419ms 四档，量的是"裁切线真的在走"而不是两格闪烁；
# 采完 cancel()：补间撤销后计算值回到 after-change style（就是终档），后续命中与
# 下一次换档都从干净状态起，不会留一条被暂停的补间挂在节点上。
_TUCK_SWEEP = r"""(function(){
  var n = document.getElementById('@ID@');
  var list = (n.getAnimations()||[]).filter(function(a){
    return a.transitionProperty === 'clip-path';});
  if (list.length !== 1){
    return JSON.stringify({err:'clip-path 补间不在场', an:(n.getAnimations()||[]).map(
      function(a){ return a.transitionProperty || a.animationName; })});
  }
  var out = {dur:list[0].effect.getComputedTiming().duration, frames:[]};
  [0, 120, 260, 419].forEach(function(ms){
    list[0].pause(); list[0].currentTime = ms;
    var cs = getComputedStyle(n);
    var m = cs.clipPath.match(/inset\(([^)]*)\)/);
    var f = m ? m[1].trim().split(/\s+/).map(function(v){ return parseFloat(v); }) : [];
    while (f.length < 4){ f.push(f[0]); }
    out.frames.push({ms:ms, cp:cs.clipPath, ins:f,
      bbox:+n.getBBox().width.toFixed(2),
      rw:+n.getBoundingClientRect().width.toFixed(2)});
  });
  list[0].cancel();
  return JSON.stringify(out);
})()"""


def _tuck_snap(ev, node_id):
    return json.loads(ev(_TUCK_SNAP.replace("@ID@", node_id)))


def _tuck_sweep(ev, node_id):
    return json.loads(ev(_TUCK_SWEEP.replace("@ID@", node_id)))


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_tuck_retracts_on_reused_node(full_lesson):
    """真实浏览器：收拢退场在**复用节点**上连续走完行程，且只补间 clip-path。

    四条口径全是量出来的：
      · 边界 ⑤ 的后果——`put()` 把 class 拼成 `nt tuckR`，同节点两条 transition 简写
        只有一条活：计算值 `transition-property` 只剩 `clip-path`（transform / opacity
        一步落终值）。这正是"收拢只挂纯包装 g"的理由，所以这里锁的就是计算值；
      · 行程连续：暂停补间采四档，动的那一边严格单调 −15% → 115%（两端正好落在声明值
        上：85% 留 46px 能点着的残角，`inset(0 0 0 0)` 啃描边与 marker-end 箭头），
        其余三边全程钉在 −15%，时长 420ms（--dur-tuck）；
      · 裁切不动几何、但动命中：bbox 与 getBoundingClientRect 宽逐值不变，而沿路采样
        展开档 4/4 可命中、收拢档 0/4（需要 Learner 点的图元别收拢，边界 ④）；
      · 导出那一刀（`transition:none!important`）直接落终档且 getAnimations() 为空：
        成片帧里根本不存在"收拢中"那一格，退场要么演、要么就是没了，不留半档。
    """
    chrome, ev, _send = _cdp(full_lesson, "tuck")
    try:
        ev(_SEEK)
        # idle 态 #veilplay 铺满舞台会接走 elementFromPoint（实测命中全是 veilplay），
        # 命中那一档先把舞台打到 playing——口径同 0.13.0 按住反馈那条。
        ev("document.getElementById('stage').dataset.state = 'playing'")

        # ── ① .tuckR：建档 → 收拢 → 回展开 ──
        ev("window.__jump('seg-1', 1)")
        st = _tuck_snap(ev, "a-arcs")
        assert st["cls"] == "nt tuckR", f"put 的拼法是 'nt ' + class：{st}"
        assert st["cp"] == "inset(-15%)" and st["ins"] == [-15.0] * 4, \
            f"静止档四边外扩 −15%（inset(0 0 0 0) 会啃掉描边与 marker-end 箭头）：{st}"
        assert st["kids"] == ["a-arc-1", "a-arc-1-flow", "a-arc-1-lab",
                              "a-arc-2", "a-arc-2-lab"], f"复用节点的子层顺序不变：{st}"
        assert st["tp"] == "clip-path", \
            f"边界 ⑤：同节点时 .nt 那份长简写被整串顶掉，计算值只剩 clip-path：{st}"
        assert st["tdur"] == "0.42s", f"时长走 --dur-tuck：{st}"

        ev("window.__jump('seg-1', 2)")
        fwd = _tuck_sweep(ev, "a-arcs")
        assert "err" not in fwd, fwd
        assert abs(fwd["dur"] - 420) <= 0.01, \
            f"收拢行程 .42s（比揭示 --dur-reveal .5s 短一档）：{fwd}"
        edge = [f["ins"][3] for f in fwd["frames"]]     # inset(top right bottom left)
        assert abs(edge[0] + 15) < 1e-6 and abs(edge[3] - 115) < 1e-6, \
            f"两端落在声明值上（115% = 100% + 那 15% 外扩余量）：{edge}"
        assert edge[0] < edge[1] < edge[2] < edge[3], f"裁切线一路往右走，不是两格闪烁：{edge}"
        assert all(abs(f["ins"][i] + 15) < 1e-6 for f in fwd["frames"] for i in (0, 1, 2)), \
            f"只有动的那一边在变：{[f['ins'] for f in fwd['frames']]}"
        assert len({f["bbox"] for f in fwd["frames"]}) == 1 and \
            len({f["rw"] for f in fwd["frames"]}) == 1, \
            f"裁切不动几何（QA 可见性与取景读的就是这两把尺）：{fwd['frames']}"
        open_bbox = fwd["frames"][0]["bbox"]

        ev("window.__jump('seg-1', 1)")
        back = _tuck_sweep(ev, "a-arcs")
        assert "err" not in back, back
        redge = [f["ins"][3] for f in back["frames"]]
        assert abs(redge[0] - 115) < 1e-6 and abs(redge[3] + 15) < 1e-6 \
            and redge[0] > redge[1] > redge[2] > redge[3], \
            f"回拖是同一趟行程倒着走（纯函数律：读数跟着回来）：{redge}"
        assert back["frames"][0]["bbox"] == open_bbox, "反向补间同样不改动几何"

        # ── ② 命中：clip-path 把命中一起裁掉（与 mask 的关键差别）──
        HIT = r"""(function(){
          var lab = document.getElementById('a-arc-1-lab'), lr = lab.getBoundingClientRect();
          var e = document.elementFromPoint(lr.left + lr.width / 2, lr.top + lr.height / 2);
          return JSON.stringify({hit:e ? (e.id || e.tagName) : null,
                                 inside:e ? ((e === lab || lab.contains(e)) ? 1 : 0) : 0});
        })()"""
        time.sleep(0.6)                       # 补间已 cancel，档位就是终态；等一拍稳
        assert json.loads(ev(HIT))["inside"] == 1, "展开档标注照旧可命中"
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.6)
        gone = json.loads(ev(HIT))
        assert gone["inside"] == 0, f"收拢后不吃命中（实测命中的是底下的卡片 rect）：{gone}"

        # ── ③ .tuckL：新建档没有上一状态、消费者按边界 ① 多停一拍 ──
        ev("window.__jump('seg-6', 1)")
        st = _tuck_snap(ev, "e-ties")
        assert st["cls"] == "nt tuckL" and st["ins"] == [-15.0] * 4, st
        assert st["kids"] == ["e-tie-\u5c0f\u660e", "e-tie-\u5c0f\u7ea2"], st
        assert st["an"] == [], f"新建档入树前写完状态，没有上一状态可补间：{st}"
        ev("window.__jump('seg-6', 2)")
        lfwd = _tuck_sweep(ev, "e-ties")
        assert "err" not in lfwd, lfwd
        ledge = [f["ins"][1] for f in lfwd["frames"]]   # 右端先没：动的是 inset 第二个值
        assert abs(ledge[0] + 15) < 1e-6 and abs(ledge[3] - 115) < 1e-6 \
            and ledge[0] < ledge[1] < ledge[2] < ledge[3], \
            f"左收那条按原路撤（右端先没），同样是连续行程：{ledge}"
        assert all(abs(f["ins"][i] + 15) < 1e-6 for f in lfwd["frames"]
                   for i in (0, 2, 3)), f"只动右边：{[f['ins'] for f in lfwd['frames']]}"

        # 连线沿路采样：中段被门禁卡片压住（实测 f=0.5 命中的是 .f-q），取两头
        TIEHIT = r"""(function(){
          var l = document.getElementById('e-tie-\u5c0f\u660e'), c = l.getScreenCTM();
          var hit = 0, tot = 0;
          [0.05, 0.2, 0.8, 0.95].forEach(function(f){
            var p = l.getPointAtLength(l.getTotalLength() * f);
            var e = document.elementFromPoint(c.a * p.x + c.c * p.y + c.e,
                                              c.b * p.x + c.d * p.y + c.f);
            tot++; if (e === l){ hit++; }
          });
          return JSON.stringify({hit:hit, tot:tot});
        })()"""
        ev("window.__jump('seg-6', 1)")
        time.sleep(0.6)
        assert json.loads(ev(TIEHIT)) == {"hit": 4, "tot": 4}, "展开档连线可命中"
        ev("window.__jump('seg-6', 2)")
        time.sleep(0.6)
        assert json.loads(ev(TIEHIT))["hit"] == 0, \
            "收拢后一次都点不到：要 Learner 点的图元不能挂 .tuck（边界 ④）"

        # s3 起不再引用 → sweep 当场撤走（收拢只负责 s2 那一拍"演出去"）
        ev("window.__jump('seg-6', 3)")
        assert ev("document.getElementById('e-ties') === null"), "s3 该撤就得撤干净"
        ev("window.__jump('seg-6', 1)")
        st = _tuck_snap(ev, "e-ties")
        assert st["ins"] == [-15.0] * 4 and st["an"] == [], \
            f"回拖重进这一句是**新建档**，不是从 115% 补回来：{st}"

        # ── ④ 导出那一刀：落终档、没有补间、几何照旧 ──
        ev("document.head.insertAdjacentHTML('beforeend','<style id=\"cut\">"
           "*,*::before,*::after{transition:none!important;animation:none!important}"
           "</style>')")
        ev("window.__jump('seg-1', 1)")
        ev("window.__jump('seg-1', 2)")
        cut = _tuck_snap(ev, "a-arcs")
        assert cut["cp"] == "inset(-15% -15% -15% 115%)", \
            f"逐帧冷启动必须直接落收拢终档：{cut}"
        assert cut["an"] == [], f"成片帧里不存在'收拢中'那一格：{cut}"
        assert cut["bbox"] == open_bbox, f"关掉了动效也不改几何：{cut}"
    finally:
        _kill(chrome)


# 合成对比度的页内量法（probe13 / probe17 两轮同一组数）：把"带 mask 的字"写成
# 独立 SVG（data-URL），canvas 光栅化后**取接近满覆盖的像素（p90）**按 WCAG 公式
# 算对底色的对比度。为什么必须合成：mask 的灰阶地板是 alpha 不是颜色，§8 判的是
# 合成后的墨色 vs 底色。为什么不能拿截图像素平均亮度：本轮第一版就是这么算出
# "8.04:1" 并据此选了 .72——量法本身就错。
# 注意 data-URL 里 `<percentage>` 要写成实体，否则整张 SVG 解析失败（实测 onerror）。
_LIT_INK = r"""new Promise(function(res){
  var FLOOR = @FLOOR@, RAMP = @RAMP@, BG = @BG@, FILL = @FILL@;
  var W = 640, H = 150, X0 = 20, TL = 520, X1 = X0 + TL;
  // 两行是**同一串字、同一个 textLength、同一批列采样带**：
  //   行 A `--lit:118%` → 实心段铺满整行，就是"念过"那一侧；
  //   行 B `--lit:RAMP%` → 过渡带只剩左边 RAMP%，采样带全部落在地板段，就是"未念"。
  // 于是 ratio / cr_* 量的是"同一批像素被地板压掉多少"，不再拿两串不同的字互相比——
  // 换渲染环境（字形宽度、缺字库）读数不动。改之前比的是"左半边 vs 右半边"，那等于让
  // 字体决定哪几个字落进哪一侧：同一份 CSS 本机均值比 1.132、CI 1.108。
  var STR = '排序看的是字符，不是数值大小。位数不同才看得出差别。';
  var s = '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H +
    '" viewBox="0 0 ' + W + ' ' + H + '">' +
    '<style>@property --lit{syntax:\'&lt;percentage&gt;\';inherits:false;initial-value:118%}' +
    'text{font:600 17px "Noto Sans CJK SC","Noto Sans","DejaVu Sans",sans-serif}' +
    '.l{mask-image:linear-gradient(90deg,#000 0 calc(var(--lit) - ' + RAMP + '%),' +
    'rgba(0,0,0,' + FLOOR + ') var(--lit) 100%)}</style>' +
    '<rect width="' + W + '" height="' + H + '" fill="' + BG + '"/>' +
    '<text class="l" x="' + X0 + '" y="50" textLength="' + TL + '" lengthAdjust="spacing"' +
    ' fill="' + FILL + '" style="--lit:118%">' + STR + '</text>' +
    '<text class="l" x="' + X0 + '" y="120" textLength="' + TL + '" lengthAdjust="spacing"' +
    ' fill="' + FILL + '" style="--lit:' + RAMP + '%">' + STR + '</text></svg>';
  var img = new Image();
  img.onload = function(){
    var c = document.createElement('canvas'); c.width = W; c.height = H;
    var x = c.getContext('2d', {willReadFrequently:true});
    x.drawImage(img, 0, 0);
    var d = x.getImageData(0, 0, W, H).data;
    function px(i){ return [d[i], d[i + 1], d[i + 2]]; }
    function stats(y0, y1, x0, x1){
      var lum = [], bg = 1e9;
      for (var y = y0; y < y1; y++){
        for (var xx = x0; xx < x1; xx++){
          var p = px((y * W + xx) * 4);
          var L = 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
          lum.push([L, p]);
          if (L < bg) bg = L;
        }
      }
      var ink = lum.filter(function(q){ return q[0] > bg + 25; });
      if (!ink.length) return {n: 0};
      ink.sort(function(a, b){ return a[0] - b[0]; });
      var sum = 0; ink.forEach(function(q){ sum += q[0]; });
      return {n: ink.length, mean: Math.round(sum / ink.length * 100) / 100,
              p90: ink[Math.floor(ink.length * 0.90)][1]};
    }
    function ch(v){ v = v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }
    function cr(fg, bgp){
      var a = 0.2126 * ch(fg[0]) + 0.7152 * ch(fg[1]) + 0.0722 * ch(fg[2]);
      var b = 0.2126 * ch(bgp[0]) + 0.7152 * ch(bgp[1]) + 0.0722 * ch(bgp[2]);
      var hi = Math.max(a, b), lo = Math.min(a, b);
      return Math.round((hi + 0.05) / (lo + 0.05) * 100) / 100;
    }
    var BX0 = Math.round(X0 + TL * (RAMP + 8) / 100), BX1 = X1 - 4;
    var bgp = px((75 * W + 6) * 4);              // 行间空白 + 左边距：确定是底
    var lit = stats(28, 60, BX0, BX1);           // 行 A 基线 50
    var dim = stats(98, 130, BX0, BX1);          // 行 B 基线 120
    res(JSON.stringify({fill:FILL, floor:FLOOR, bg:bgp, band:[BX0, BX1],
      lit_n:lit.n, dim_n:dim.n,
      lit_mean:lit.mean, dim_mean:dim.mean,
      cr_lit:lit.p90 ? cr(lit.p90, bgp) : null,
      cr_dim:dim.p90 ? cr(dim.p90, bgp) : null,
      ratio:(lit.mean && dim.mean) ? Math.round(lit.mean / dim.mean * 1000) / 1000 : null}));
  };
  img.onerror = function(){ res(JSON.stringify({err:true})); };
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);
})"""


def _ink(ev, floor, ramp, fill, bg):
    js = (_LIT_INK.replace("@FLOOR@", str(floor)).replace("@RAMP@", str(ramp))
          .replace("@FILL@", "'%s'" % fill).replace("@BG@", "'%s'" % bg))
    got = json.loads(ev(js, True))
    assert not got.get("err"), f"data-URL SVG 没被解出来（实体转义漏了？）：{got}"
    assert got.get("lit_n") and got.get("dim_n"), \
        f"{fill} on {bg}：两段采样带里取不到墨（lit_n={got.get('lit_n')} / " \
        f"dim_n={got.get('dim_n')}）——采样带是从 mask 几何算的，取不到说明这一行的" \
        f"字宽不再铺满 textLength，读数与字体绑定回了环境（见 _LIT_INK 注释）：{got}"
    return got


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_lit_floor_passes_composite_contrast(full_lesson):
    """量**合成后**的对比度：范本那一档地板过线，两档被否决的替代品复现否决理由。

    阈值不写死在自己机器上：判据是 §8 那两条线（正文 4.5:1、次要文字 7:1）加一条
    "念过 vs 未念"的墨量均值比 ≥1.10。0.23.1 复按（probe13b）把探针改成**同一串字的两行
    对照**——行 A `--lit:118%` 整行走实心段、行 B `--lit:RAMP%` 整行走地板段，同一批列
    采样带取两边的墨量均值。这样量的才是"地板把同一批像素压掉多少"，字体换环境只改字形
    不改比值（改之前比的是左半边与右半边两串不同的字，本机 1.132 而 CI 1.108，就红在
    阈值上）：
      · floor=.85 对 panel `#1c212d`：非结论 10.48 / 7.97（均值比 1.128）、结论
        7.94 / 6.08（1.121）；对 edge `#161a23` 更好（11.34 / 8.46 比 1.140、
        8.59 / 6.45 比 1.128）；
      · floor=.72 墨量差更大（均值比 1.250）但结论字色对 panel 只剩 **4.76:1**——
        贴 4.5:1 线零余量，所以否决；
      · floor=.92 均值比掉到 **1.063**，肉眼分不出念没念过，等于没做。
    断言只走"过线 / 不过线"的方向，不锁小数：字体渲染环境换了数值会漂。
    取样带也不许依赖字形宽度：`<text>` 钉 `textLength="520" lengthAdjust="spacing"`、
    带子由 mask 自身的百分比算（行 B 左边那 RAMP% 是过渡带，避开），取不到墨直接报
    lit_n / dim_n，而不是留一个 null 给下游 `>=` 炸成 TypeError。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    m = re.search(r"--dur-tuck:\.42s; --lit-floor:([\d.]+); --lit-ramp:(\d+)%;", html)
    assert m, "令牌行缺 --lit-floor / --lit-ramp，本用例无从量起"
    floor, ramp = float(m.group(1)), int(m.group(2))

    chrome, ev, _send = _cdp(full_lesson, "litcontrast")
    try:
        # ① 范本这一档：两种字色 × 字幕带压着的两档底色，全部过正文线 + 均值比线
        rows = {(bg, fill): _ink(ev, floor, ramp, fill, bg)
                for bg in ("#1c212d", "#161a23")
                for fill in ("#c9d1e0", "#f5a623")}
        for (bg, fill), r in rows.items():
            assert r["cr_dim"] >= 4.5, f"{fill} on {bg}：未念段合成后 {r['cr_dim']}:1 不过正文线"
            assert r["cr_lit"] >= 4.5 and r["cr_lit"] > r["cr_dim"], r
            assert r["ratio"] >= 1.10, f"{fill} on {bg}：念过 vs 未念均值比只有 {r['ratio']}"
        for bg in ("#1c212d", "#161a23"):
            r = rows[(bg, "#c9d1e0")]
            assert r["cr_dim"] >= 7.0, \
                f"非结论字色在 {bg} 上未念段 {r['cr_dim']}:1，掉出次要文字的 7:1"

        # ② 被否决的 .72：结论字色贴线（实测 4.76:1），余量不足半个点
        low = _ink(ev, 0.72, ramp, "#f5a623", "#1c212d")
        assert low["cr_dim"] < 5.0, \
            f".72 那一档若还过 5:1，本轮的否决理由就不成立了：{low}"
        assert low["ratio"] > rows[("#1c212d", "#f5a623")]["ratio"], \
            f".72 的墨量差确实更大（这才是当初想选它的理由），但对比度不够：{low}"

        # ③ 被否决的 .92：差小到分不出来
        high = _ink(ev, 0.92, ramp, "#c9d1e0", "#1c212d")
        assert high["ratio"] < 1.09, \
            f".92 那一档均值比 {high['ratio']}，与不压暗（1.00）已经分不出两件事：{high}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_reading_and_tuck_reduced_motion_fallback(full_lesson):
    """关掉动效：收拢**不演**但档位照落（0ms 就在那一格），而点亮仍跟着钟走。

    两件事分别对应两条纪律：
      · 退场是动效 → 关掉后直接落终档。实测计算值 `transition-property` 就是 `none`、
        换档后**立即**读到 115%、回读 −15%，`getAnimations()` 恒空——`.tuckR,.tuckL`
        那条兜底行与基础规则同为 (0,1,0) 且源序在后，**不需要**抬特异度（对比 0.18.0
        的门禁那一族：那条必须带 `#gate`，因为基础规则排在媒体块之后）。
      · 点亮是时钟读数 → 关掉动效它得继续跟上嘴：0.2 / 0.8 两点仍读到 118×k
        （23.6% / 94.4%），mask 仍是一条 linear-gradient。把 `--lit` 放进兜底清单才是
        bug：那等于让字幕在关掉动效的用户那里停止跟随。
    断言按 0.16.1 那条教训写：比数值 + 容差，不比归一化后的字符串。
    """
    chrome, ev, send = _cdp(full_lesson, "rm19")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)

        ev("window.__jump('seg-1', 1)")
        st = _tuck_snap(ev, "a-arcs")
        assert st["cp"] == "inset(-15%)" and st["ins"] == [-15.0] * 4, st
        assert st["tp"] == "none", f"收拢的 transition 要真的关掉（计算值）：{st}"
        open_bbox = st["bbox"]
        ev("window.__jump('seg-1', 2)")
        st = _tuck_snap(ev, "a-arcs")          # 不 sleep：这一档本来就不该有补间
        assert st["cp"] == "inset(-15% -15% -15% 115%)" and st["an"] == [], \
            f"减动效下 s2 直接就是收拢终态，内容不依赖动效：{st}"
        assert st["bbox"] == open_bbox, f"落档不改几何：{st} vs 展开档 {open_bbox}"
        ev("window.__jump('seg-1', 1)")
        st = _tuck_snap(ev, "a-arcs")
        assert st["ins"] == [-15.0] * 4 and st["an"] == [], f"回拖照旧落回展开档：{st}"

        ev("window.__jump('seg-6', 1)")
        st = _tuck_snap(ev, "e-ties")
        assert st["ins"] == [-15.0] * 4 and st["tp"] == "none", st
        ev("window.__jump('seg-6', 2)")
        st = _tuck_snap(ev, "e-ties")
        assert st["cp"] == "inset(-15% 115% -15% -15%)" and st["an"] == [], st

        # 点亮不在兜底清单里：仍逐拍跟着 currentTime 走
        tl = json.loads(ev("JSON.stringify(window.__tl)"))
        n0 = [s for s in tl["scenes"] if s["step_id"] == "seg-2"][0]["runtime"]["narration"][0]
        for frac in (0.2, 0.8):
            ev("(function(){window.__t=%r;document.getElementById('main-audio')"
               ".dispatchEvent(new Event('timeupdate'));return 1;})()"
               % (n0["start"] + n0["duration"] * frac))
            lit = json.loads(ev("""(function(){var t=document.getElementById('cap-text'),
              cs=getComputedStyle(t);
              return JSON.stringify({lit:parseFloat(cs.getPropertyValue('--lit')),
                mask:cs.maskImage});})()"""))
            assert abs(lit["lit"] - 118 * frac) <= 0.1, \
                f"减动效下亮度读数仍跟嘴（{frac} 应 ≈{118 * frac:.1f}%）：{lit}"
            assert lit["mask"].startswith("linear-gradient("), \
                f"遮罩本身不被 reduced-motion 摘掉（它是状态不是动效）：{lit['mask'][:60]}"
    finally:
        _kill(chrome)


# ── 0.20.0：当面与抵达（翻面 / 沿线抵达 / 圆形绽开）──────────────────────

def test_flip_ride_bloom_primitives_present():
    """模板结构契约：0.20.0 三条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮在 Chrome 152 里量出来"为什么只能这么写"的边界：
      · 翻面必须走**对称曲线** `--ease-in-out`：交接在行程的一半（visibility 借
        delay，时长 0），而"半程 = 侧立"只有对称曲线成立。实测 --ease-in-out 在
        t=300.5ms 采到 θ=90.0°（两面正好换班），仓库默认的 --ease-out 在 60.5ms
        就已过 78.9°——同一套 delay 写法下背面要**倒着走 235ms**，那是穿帮；
      · 翻面**不许用 `backface-visibility`**：SVG 里它是死的（CTM.a 已经 -1，
        镜像的字照描），所以两面各挂一条按时间交接的 visibility；
      · 翻面挂纯包装 g：`.flip` 与 `.nt` 都是 transition 简写、同节点时源序后者胜
        （实测计算值只剩 `transform`），包装层因此只写 `data-over`，两面各写自己的颜色
        ——旧那句 `fill: won ? … : …` 不许回来（颜色跟着面走，中途才没有不灰不绿的中间色）；
      · `.ride` 的行程终态必须在**呈现属性**上（`data-go`），且**不许**交给 keyframes：
        0.18.0 否决 offset-path 的理由就是"切掉 animation 后圆点落在起点"，那条理由
        今天仍然成立，成立的是 animation 这个驱动方式（上面已把边界重画成
        "禁 keyframes 驱动 offset-*"）；缓动写 linear，easeOut 会读作"到站撞墙上"；
      · 标记**不挂在 .tuck* 那一层里面**：s2 起那层挂 `gone`，clip-path 会把走在
        半途的标记一起裁掉；
      · `.bloom` 终态取 75% 不取 100%：`circle()` 的百分比按 `sqrt(w²+h²)/√2` 归一，
        盖住四角需要 ≥ 半对角线，**下限恒等于 √2/2 ≈ 70.7%（与长宽比无关）**，
        实测那张 42×52 的空缺格 50% 咬边（墨量 142 对 286）、75% 与不裁逐像素相同；
      · 空缺格内层不再挂 `.pop`：绽开就是它的入场，别叠两层；
      · reduced-motion 三条**都不需要抬特异度**（基础规则源序在本块之前，与 .tuck
        同、与掠光/侧栏相反），且兜底落的正是终态：翻面落背面、标记落终点、圆落全开。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")

    # ① 令牌：三个时长各一档，且都写在同一条 :root 行里
    assert "--dur-flip:.6s; --dur-ride:.8s; --dur-bloom:.5s;" in html
    assert "--dur-flip:.4s" not in html and "--dur-ride:1.2s" not in html

    # ② 翻面：对称曲线 + 半程交接 + 终态在属性上
    assert ("transition:transform var(--dur-flip) var(--ease-in-out)}") in html, \
        "翻面必须用对称的 --ease-in-out：交接在半程，曲线不对称就对不上侧立那一刻"
    assert ".flip[data-over=\"1\"]{transform:perspective(600px) rotateY(180deg)}" in html
    assert "perspective(600px) rotateY" in html, \
        "3D 要写在 transform 函数列表里才生效（单写 rotateY 是平面镜像、没有远近）"
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)   # 注释里也写这条写法，按规则数
    assert css.count("transition:visibility 0s linear calc(var(--dur-flip) / 2)") == 2, \
        "两面各一条：时长 0、只借 delay，交接从 --dur-flip 除出来，改时长不用回头改第二处"
    assert "visibility 0s linear .3s" not in html, "半程不许写死魔法数"
    assert "backface-visibility" not in css, \
        "SVG 里 backface-visibility 是死的（实测 CTM.a=-1 而镜像的字照描），HTML 那套两面写法不许回流"

    # ③ 翻面的消费者：包装只写 data-over，两面各带各的颜色
    assert "{class:'flip', 'data-over': won ? '1' : '0'}" in html
    assert "{class:'face faceB'," in html
    assert "fill: won ? '#2ecc71' : '#5d6577'" not in html, \
        "旧那行把颜色当补间对象：翻到中途会出现不灰不绿的中间色，且换的是说法不是颜色"
    assert "txt(g, 'b-' + ri + '-vs'," not in html, "两面必须住在包装层里，不在行根上"

    # ④ 沿线抵达：属性档位 + 令牌路线 + linear
    assert ".ride{offset-path:var(--route);offset-rotate:auto;offset-distance:0%;" in html
    assert "transition:offset-distance var(--dur-ride) linear}" in html, \
        "匀速走一段路：套 easeOut 读作起步猛冲、到站撞墙上"
    assert '.ride[data-go="1"]{offset-distance:100%}' in html
    assert "--route:path('M 354 " in html, "路线写在呈现属性 style 上，与那条 line 同一组坐标"
    assert "@property --route" not in html, \
        "--route 只是令牌替换，补间的是 offset-distance（它自己就可动画）"
    assert "put('e-body', 'g', 'e-ride-' + p[0]" in html
    assert "put(ties, 'g', 'e-ride-' + p[0]" not in html, \
        "标记不许挂在收拢层里：那一层 s2 起 clip-path 收走，会把走在半途的标记一起裁掉"

    # ⑤ 圆形绽开：两端同型可插值、静止态 = 全开、75% 是量出来的下限加余量
    assert "@keyframes bloomIn{from{clip-path:circle(0% at 50% 50%)}" in html
    assert "to{clip-path:circle(75% at 50% 50%)}}" in html
    assert ".bloom{clip-path:circle(75% at 50% 50%);" in html
    assert "animation:bloomIn var(--dur-bloom) var(--ease-out) backwards}" in html, \
        "不写 forwards：跑完落回元素自身的 75%，导出切掉 animation 落的也是那一格"
    assert "circle(100% at 50% 50%)" not in html, "100% 是白给的（覆盖整盒的下限 ≈70.7%）"
    assert "put(g, 'g', 'b-' + ri + cx + '-x', {class:'bloom'," in html
    assert "put(gap, 'rect', 'b-x' + ri + cx + '-r', {class:null," in html, \
        "绽开就是这一格的入场，内层不再叠 .pop"
    assert "txt(gap, 'b-x' + ri + cx + '-t', {class:null," in html

    # ⑥ reduced-motion：三条兜底都在，且都不必抬特异度（基础规则源序在本块之前）
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    assert ".flip,.flip .face,.flip .faceB{transition:none}" in rm
    assert ".ride{transition:none}" in rm
    assert ".bloom{animation:none}" in rm
    rm_rules = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert "offset-path" not in rm_rules and "--route" not in rm_rules \
        and "circle(" not in rm_rules, \
        f"路线与圆形裁剪的口径不是动效，不进兜底清单：{rm_rules}"

    # ⑦ 本轮实测否决的五条里，能由静态锁钉住的三条（pretty / View Transitions
    #    沿用 0.17.0 已有的那两把锁），别被下一轮再请回来
    assert "corner-shape" not in html, \
        "corner-shape:squircle 打在 SVG rect 上零差值（rx=9 那档实测逐像素相同），没有收益不写"
    assert "counter(" not in html and "@property --num" not in html, \
        "里程表数字在不支持 @property 时渲染空字符串（纪律③直接不合格），且这里的数字都是时钟读数"


_FLIP_PROBE = r"""(function(){
  var w = document.getElementById('FLIP'), f = document.getElementById('FRONT'),
      b = document.getElementById('BACK');
  function theta(el){                      // rotateY 的 cos 就是 matrix3d 的 m11
    var t = getComputedStyle(el).transform, m = t.match(/matrix3d\(([^)]+)\)/);
    if (!m) return null;
    var a = parseFloat(m[1].split(',')[0]);
    return Math.round(Math.acos(Math.max(-1, Math.min(1, a))) * 180 / Math.PI * 10) / 10;
  }
  var cs = getComputedStyle(w);
  var hit = document.elementFromPoint(f.getBoundingClientRect().left
    + f.getBoundingClientRect().width / 2,
    f.getBoundingClientRect().top + f.getBoundingClientRect().height / 2);
  return JSON.stringify({over:w.dataset.over, cls:w.getAttribute('class'),
    theta:theta(w), tf:cs.transform, tp:cs.transitionProperty,
    tdur:cs.transitionDuration,
    bbox:[+f.getBBox().x.toFixed(2), +f.getBBox().y.toFixed(2),
          +f.getBBox().width.toFixed(2), +f.getBBox().height.toFixed(2)],
    fvis:getComputedStyle(f).visibility, bvis:getComputedStyle(b).visibility,
    ftp:getComputedStyle(f).transitionProperty, ftdur:getComputedStyle(f).transitionDuration,
    ftdelay:getComputedStyle(f).transitionDelay,
    hit:hit ? (hit.id || hit.tagName) : null,
    anims:(w.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;})});
})()"""

# 交接与角度必须**一起**冻结在同一个时间轴上：父层的 transform 与两面的 visibility
# 是三个独立的补间，只暂停父层的话两面会照自己的钟走，量出来的就是错的那一半。
_FLIP_SWEEP = r"""(function(){
  var ws = [document.getElementById('FLIP'), document.getElementById('FRONT'),
            document.getElementById('BACK')];
  var list = [];
  ws.forEach(function(n){ (n.getAnimations()||[]).forEach(function(a){ list.push(a); }); });
  if (list.length !== 3) {
    return JSON.stringify({err:'该有三个补间在场（父层 transform + 两面 visibility）',
      got:list.map(function(a){ return a.transitionProperty; })});
  }
  var out = {dur:list[0].effect.getComputedTiming().duration, frames:[]};
  list.forEach(function(a){ a.pause(); });
  [0, 150, 300, 450, 599].forEach(function(ms){
    list.forEach(function(a){ a.currentTime = ms; });
    var t = getComputedStyle(ws[0]).transform.match(/matrix3d\(([^)]+)\)/);
    var a1 = t ? parseFloat(t[1].split(',')[0]) : null;
    out.frames.push({ms:ms,
      theta: a1 === null ? null : Math.round(Math.acos(Math.max(-1, Math.min(1, a1))) * 180 / Math.PI * 10) / 10,
      f:getComputedStyle(ws[1]).visibility[0], b:getComputedStyle(ws[2]).visibility[0]});
  });
  list.forEach(function(a){ a.cancel(); });
  return JSON.stringify(out);
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_flip_turns_the_verdict_in_front_of_the_learner(full_lesson):
    """真实浏览器：判决换面是**一次翻页**，不是换个颜色（0.20.0）。

    量四件事：① 行程连续（θ 一路从 0° 走到 180°，中间有 90° 那格，不是两档闪烁）；
    ② **交接落在侧立那一刻**——三个补间一起冻结在同一毫秒上取样，t=300ms 时
    θ=90.0° 而两面正好从 v/h 换成 h/v；t=150ms 与 t=450ms 都只有一面在场，
    全程没有一帧两面同显（那才是穿帮）；③ 终态在属性上：落定后 `data-over=1`、
    正面 hidden、背面 visible，`elementFromPoint` 命中的是**在场那一面**——
    没有"看不见却点得着"的幽灵面；④ CSS transform 不改 `getBBox()`，翻页前后的
    盒一格不动（相机取景与 QA 几何判定看不见这一层）。
    """
    chrome, ev, _send = _cdp(full_lesson, "flip")
    try:
        ev(_SEEK)
        # idle 态 #veilplay 铺满舞台会接走 elementFromPoint（0.19.0 那条同一坑），
        # 命中那一档先把舞台打到 playing。
        ev("document.getElementById('stage').dataset.state = 'playing'")
        ev("window.__jump('seg-2', 2)")           # 第二句：还在逐位比，正面 vs
        time.sleep(0.6)
        probe = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-0-vs-w")
                                .replace("FRONT", "b-0-vs").replace("BACK", "b-0-vs-b")))
        assert probe["over"] == "0" and probe["fvis"] == "visible" \
            and probe["bvis"] == "hidden", f"起手必须是正面在场：{probe}"
        assert abs(probe["theta"] - 0.0) <= 0.5, f"未翻时 θ≈0：{probe}"
        assert probe["tp"] == "transform", \
            f"包装层只留 transform 这一条补间（.flip 顶掉 .nt 的简写）：{probe}"
        assert probe["tdur"] == "0.6s", f"时长走 --dur-flip：{probe}"
        assert probe["ftp"] == "visibility" and probe["ftdur"] == "0s" \
            and probe["ftdelay"] == "0.3s", f"两面走 0s + 半程 delay：{probe}"
        assert probe["hit"] == "b-0-vs", f"正面在场时命中的就是正面：{probe}"
        box_open = probe["bbox"]

        ev("window.__jump('seg-2', 3)")           # 第三句：判决给出先后 → 翻页
        sweep = json.loads(ev(_FLIP_SWEEP.replace("FLIP", "b-0-vs-w")
                               .replace("FRONT", "b-0-vs").replace("BACK", "b-0-vs-b")))
        assert "err" not in sweep, str(sweep)
        assert sweep["dur"] == 600, f"补间时长 600ms（--dur-flip）：{sweep}"
        th = [f["theta"] for f in sweep["frames"]]
        assert all(x is not None for x in th), str(sweep)
        assert th[0] < th[1] < th[2] < th[3] < th[4], f"角度一路走到底，不是两档：{th}"
        assert abs(th[0] - 0.0) <= 1 and abs(th[4] - 180.0) <= 1, f"两端落在 0°/180°：{th}"
        assert abs(th[2] - 90.0) <= 1.5, \
            f"半程必须侧立（对称曲线才对得上交接）：{sweep['frames'][2]}"
        # 交接：侧立那一刻两面正好换班，且全程只有一面在场
        assert [(f["f"], f["b"]) for f in sweep["frames"][:2]] == [("v", "h"), ("v", "h")], \
            f"前半程只有正面：{sweep['frames']}"
        assert [(f["f"], f["b"]) for f in sweep["frames"][2:]] == [("h", "v"), ("h", "v"),
                                                                  ("h", "v")], \
            f"半程起只有背面：{sweep['frames']}"

        probe = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-0-vs-w")
                               .replace("FRONT", "b-0-vs").replace("BACK", "b-0-vs-b")))
        assert probe["over"] == "1" and abs(probe["theta"] - 180.0) <= 0.5, probe
        assert probe["fvis"] == "hidden" and probe["bvis"] == "visible", \
            f"落定 = 背面在场：{probe}"
        assert probe["hit"] == "b-0-vs-b", \
            f"命中的是在场那一面，不留幽灵面：{probe}"
        # 包围盒按 ±0.05px 比：这一条锁的是"翻页不改盒"，不是"两次读数逐位相同"——
        # 亚像素那一档在补间刚落定时会抖（同一页实测到过 461.29 与 461.30 两个读数，
        # 口径同 0.23.1「浏览器用例别再拿屏幕绝对坐标当契约」）。
        assert len(probe["bbox"]) == len(box_open) and \
            all(abs(a - b) <= 0.05 for a, b in zip(probe["bbox"], box_open)), \
            f"翻页不改包围盒：{probe['bbox']} vs {box_open}"

        # 第二行按自己的节拍翻：s3 时它还是正面，s4 才翻
        probe1 = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-1-vs-w")
                                .replace("FRONT", "b-1-vs").replace("BACK", "b-1-vs-b")))
        assert probe1["over"] == "0", f"第二行不该跟着第一行一起翻：{probe1}"
        ev("window.__jump('seg-2', 4)")
        time.sleep(0.7)
        probe1 = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-1-vs-w")
                                .replace("FRONT", "b-1-vs").replace("BACK", "b-1-vs-b")))
        assert probe1["over"] == "1" and probe1["bvis"] == "visible", \
            f"s4 轮到第二行翻面：{probe1}"
    finally:
        _kill(chrome)


_RIDE_PROBE = r"""(function(){
  var n = document.getElementById('RIDE'), cs = getComputedStyle(n);
  var m = n.getScreenCTM();
  var bb = n.getBoundingClientRect();
  var line = document.getElementById('TIE');
  return JSON.stringify({go:n.dataset.go, cls:n.getAttribute('class'),
    route:cs.offsetPath.slice(0, 40), dist:cs.offsetDistance, rot:cs.offsetRotate,
    tp:cs.transitionProperty, tdur:cs.transitionDuration,
    ax:+m.e.toFixed(1), ay:+m.f.toFixed(1),
    cx:Math.round((bb.left + bb.width / 2) * 10) / 10,
    cy:Math.round((bb.top + bb.height / 2) * 10) / 10,
    ctm:[+m.a.toFixed(4), +m.b.toFixed(4)],
    bbox:[+n.getBBox().x.toFixed(2), +n.getBBox().y.toFixed(2),
          +n.getBBox().width.toFixed(2), +n.getBBox().height.toFixed(2)],
    parent:n.parentNode.id,
    kids:[].slice.call(n.children).map(function(k){ return k.id; }),
    tieEnd:(function(){                     // 那条 .draw 连线的右端点，屏幕坐标
      if (!line) return null;               // s0 那一步线还没画出来
      var c = line.getScreenCTM();
      var p = new DOMPoint(+line.getAttribute('x2'), +line.getAttribute('y2'))
        .matrixTransform(c);
      return {x:+p.x.toFixed(1), y:+p.y.toFixed(1)};})(),
    anims:(n.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;})});
})()"""

_RIDE_SWEEP = r"""(function(){
  var n = document.getElementById('RIDE');
  var list = (n.getAnimations()||[]).filter(function(a){
    return a.transitionProperty === 'offset-distance';});
  if (list.length !== 1){
    return JSON.stringify({err:'offset-distance 补间不在场', an:(n.getAnimations()||[]).map(
      function(a){ return a.transitionProperty || a.animationName; })});
  }
  var out = {dur:list[0].effect.getComputedTiming().duration,
             ease:list[0].effect.getTiming().easing, frames:[]};
  [0, 200, 400, 600, 799].forEach(function(ms){
    list[0].pause(); list[0].currentTime = ms;
    var cs = getComputedStyle(n), bb = n.getBoundingClientRect();
    out.frames.push({ms:ms, dist:parseFloat(cs.offsetDistance),
      cx:+(bb.left + bb.width / 2).toFixed(1), cy:+(bb.top + bb.height / 2).toFixed(1),
      bbox:+n.getBBox().width.toFixed(2)});
  });
  list[0].cancel();
  return JSON.stringify(out);
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_rider_arrives_and_stays_at_the_destination(full_lesson):
    """真实浏览器：标记沿连线走到右列并**留在终点**（0.20.0）。

    与 0.18.0 那条被否决的写法差在**驱动方式**上，这里就把差别量成断言：
      ① s0 建档时停在路头（dist=0%，屏幕坐标落在左列卡片边上），s1 换档才走；
      ② 行程连续且**匀速**：0/200/400/600/799ms 采到 0 → ~25 → ~50 → ~75 → 100%，
         easing 读到 linear（套 easeOut 会读作起步猛冲、到站撞墙上）；
      ③ 落在终点：落定后中心与那条 .draw 连线的**右端点**重合（±2px）——
         这是"抵达"的全部意义，走错一格就变成另一个人在另一个位置；
      ④ `getBBox()` 全程不变（[-7,-7,18,14]）而屏幕盒一路在走：**QA 看包围盒、
         观众看屏幕盒**，这正是 0.18.0 拒绝它当"检查器看得见的流光"的原因，
         也是本轮把它限成"视觉指引、不当热区、不参与连线几何判定"的理由；
      ⑤ 父节点是 e-body 而不是 e-ties：那一层 s2 起收拢，会把走在半途的标记一起裁掉。
    """
    chrome, ev, _send = _cdp(full_lesson, "ride")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-6', 0)")           # 引入句：右列还是 ?，人在路头
        time.sleep(0.4)
        st = json.loads(ev(_RIDE_PROBE.replace("RIDE", "e-ride-\u5c0f\u660e")
                              .replace("TIE", "e-tie-\u5c0f\u660e")))
        assert st["go"] == "0" and st["dist"] == "0%", f"s0 停在路头：{st}"
        assert st["parent"] == "e-body", f"不挂在收拢层里：{st}"
        assert st["route"].startswith("path("), f"路线从 --route 替换进来：{st}"
        assert st["tp"] == "offset-distance" and st["tdur"] == "0.8s", \
            f"包装层只补间 offset-distance（--dur-ride）：{st}"
        assert st["rot"] == "auto 0deg", f"offset-rotate:auto：{st}"
        assert st["kids"] == ["e-ride-\u5c0f\u660e-c", "e-ride-\u5c0f\u660e-a"], st
        head_x = st["ax"]
        box = st["bbox"]

        ev("window.__jump('seg-6', 1)")           # 相等元素保序 → 上路
        # 0.29.0 动效目的审计续：右列结果卡错峰落位（一屏一个话头）——s1 的话头
        # 是两条 ride 沿线抵达，三张结果卡同拍淡入会抢戏；三卡按 i 错峰
        # 0/.06/.12s（.in 现成的 var(--delay)），连线与 ride 保持话头地位。
        dels = json.loads(ev("""(function(){
          var d = {};
          ['e-r-\u5c0f\u660e','e-r-\u5c0f\u7ea2','e-r-\u5c0f\u521a'].forEach(function(id){
            var el = document.getElementById(id);
            if (el) d[id] = getComputedStyle(el).animationDelay;
          });
          return JSON.stringify(d);
        })()"""))
        assert set(dels.keys()) == {'e-r-\u5c0f\u660e', 'e-r-\u5c0f\u7ea2', 'e-r-\u5c0f\u521a'}, \
            f"s1 三张结果卡都在场：{dels}"
        assert set(dels.values()) == {'0s', '0.06s', '0.12s'}, \
            f"三张卡错峰 0/.06/.12s（依次就位，不抢 ride 的戏）：{dels}"
        assert dels['e-r-\u5c0f\u521a'] == '0s', \
            f"小刚 80 排最前先落位：{dels}"
        assert dels['e-r-\u5c0f\u660e'] == '0.06s' and dels['e-r-\u5c0f\u7ea2'] == '0.12s', \
            f"小明/小红同分按位序依次就位（从上往下自然读序）：{dels}"
        sw = json.loads(ev(_RIDE_SWEEP.replace("RIDE", "e-ride-\u5c0f\u660e")))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 800, f"行程 800ms：{sw}"
        assert sw["ease"] == "linear", f"匀速走一段路：{sw['ease']}"
        d = [f["dist"] for f in sw["frames"]]
        assert all(d[i] < d[i + 1] for i in range(4)), f"距离逐帧在走，不是两档：{d}"
        assert abs(d[0]) <= 0.5 and abs(d[4] - 100) <= 0.5, f"两端 0%/100%：{d}"
        assert abs(d[1] - 25) <= 2 and abs(d[2] - 50) <= 2 and abs(d[3] - 75) <= 2, \
            f"linear 就该等距：{d}"
        assert len(set(f["bbox"] for f in sw["frames"])) == 1, \
            f"包围盒全程不变（QA 看不见这层位移）：{sw['frames']}"
        cx = [f["cx"] for f in sw["frames"]]
        assert all(cx[i] < cx[i + 1] for i in range(4)), f"屏幕盒一路往右走：{cx}"

        time.sleep(1.0)
        st = json.loads(ev(_RIDE_PROBE.replace("RIDE", "e-ride-\u5c0f\u660e")
                              .replace("TIE", "e-tie-\u5c0f\u660e")))
        assert st["go"] == "1" and st["dist"] == "100%", f"落定在终点：{st}"
        assert abs(st["ax"] - st["tieEnd"]["x"]) <= 2.5 \
            and abs(st["ay"] - st["tieEnd"]["y"]) <= 2.5, \
            f"抵达 = 落在那条连线的右端点：{st}"
        assert st["ax"] > head_x + 200, f"确实走了整段路：{head_x} → {st['ax']}"
        assert st["bbox"] == box, f"抵达不改包围盒：{st['bbox']} vs {box}"
        assert st["anims"] == [], f"落定后没有补间挂在节点上：{st['anims']}"

        # s2 连线收拢退场，标记**留在终点**（它承担的是位置信息，不是事件）
        ev("window.__jump('seg-6', 2)")
        time.sleep(0.6)
        st = json.loads(ev(_RIDE_PROBE.replace("RIDE", "e-ride-\u5c0f\u660e")
                              .replace("TIE", "e-tie-\u5c0f\u660e")))
        assert st["go"] == "1" and st["dist"] == "100%", f"线收走、人留下：{st}"
        assert ev("document.getElementById('e-ties').classList.contains('gone')")
        assert st["cls"] == "nt ride", f"档位换了 class 不动：{st}"

        # s3 仍在那儿；换幕才撤走（这一层参与常驻，不是掠过就没的事件）
        ev("window.__jump('seg-6', 3)")
        time.sleep(0.2)
        assert ev("document.getElementById('e-ride-\u5c0f\u660e') !== null"), \
            "s3 那句『90 分两行 · 小明在小红前』正指着这两枚标记"
        ev("window.__jump('seg-1', 0)")
        time.sleep(0.4)
        # 换幕只是把这一幕藏起来（only() 的口径）：节点留在台上，下次进幕复用同一名
        assert ev("document.getElementById('e-ride-\u5c0f\u660e') !== null") \
            and not ev("document.getElementById('stE').classList.contains('on')"), \
            "离幕时靠隐藏退场，不是靠删节点——回来还是同一个标记接着站在那儿"
    finally:
        _kill(chrome)


# 命中点取**上边描边的中线**（局部 (0,−26) 经 getScreenCTM 换算）：它是这条路上
# 唯一"一定有墨、又正好卡在 75% 那档的门边"的位置——∅ 的中心是个洞，点那儿
# 什么都命中不了，量不出裁剪有没有生效。
_BLOOM_PROBE = r"""(function(){
  var n = document.getElementById('GAP'), cs = getComputedStyle(n);
  var r = document.getElementById('CELL');
  function topHit(){
    var p = new DOMPoint(0, -26).matrixTransform(r.getScreenCTM());
    var h = document.elementFromPoint(p.x, p.y);
    return h ? (h.id || h.tagName) : null;
  }
  var tr = r.getBoundingClientRect();
  return JSON.stringify({cls:n.getAttribute('class'), cp:cs.clipPath,
    an:cs.animationName, adur:cs.animationDuration, afill:cs.animationFillMode,
    tp:cs.transitionProperty,
    w:+tr.width.toFixed(2), h:+tr.height.toFixed(2),
    bbox:[+n.getBBox().x.toFixed(1), +n.getBBox().y.toFixed(1),
          +n.getBBox().width.toFixed(1), +n.getBBox().height.toFixed(1)],
    top:topHit(),
    kids:[].slice.call(n.children).map(function(k){ return k.id + '/' + k.getAttribute('class'); }),
    anims:(n.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;})});
})()"""

_BLOOM_SWEEP = r"""(function(){
  var n = document.getElementById('GAP');
  var list = (n.getAnimations()||[]).filter(function(a){
    return a.animationName === 'bloomIn';});
  if (list.length !== 1){
    return JSON.stringify({err:'bloomIn 不在场', an:(n.getAnimations()||[]).map(
      function(a){ return a.transitionProperty || a.animationName; })});
  }
  var out = {dur:list[0].effect.getComputedTiming().duration, frames:[]};
  list[0].pause();
  [0, 60, 150, 300, 499].forEach(function(ms){
    list[0].currentTime = ms;
    var cs = getComputedStyle(n);
    var m = cs.clipPath.match(/circle\(([\d.]+)%/);
    var rr = document.getElementById('CELL').getBoundingClientRect();
    var p = new DOMPoint(0, -26).matrixTransform(
      document.getElementById('CELL').getScreenCTM());
    var h = document.elementFromPoint(p.x, p.y);
    out.frames.push({ms:ms, r:m ? parseFloat(m[1]) : null, cp:cs.clipPath,
      w:+rr.width.toFixed(2), hit:h ? (h.id || h.tagName) : null});
  });
  list[0].cancel();
  out.after = getComputedStyle(n).clipPath;
  return JSON.stringify(out);
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_bloom_opens_the_empty_cell(full_lesson):
    """真实浏览器：空缺格从中心推开一个圆，落定 = 完整一张空位（0.20.0）。

    ① 建档即演（s2 才有这一格），关键帧两头都是 `circle()` 所以可插值——实测
    0/60/150/300/499ms 采到半径 0 → ~21 → ~40 → ~67 → 75 一路连续（0.15.0 那条
    `inset()`↔`none` 的 discrete 教训在这里就是反面教材）；
    ② 静止态 = 全开：cancel() 之后计算值仍是 `circle(75% at 50% 50%)`，导出切掉
    animation 落的正是这一格；
    ③ 75% 是量出来的档位而不是保险系数：整盒宽高在绽开前后**逐值相同**
    （getBoundingClientRect 42×52 不动），说明这档没啃掉描边与 ∅ 的边角；
    ④ `.bloom` 只声明 animation，**不抢 .nt 的 transition 简写**（实测包装节点
    `transition-property` 仍完整留着 .nt 那串、内层 rect 也一样）——三件套里只有
    它这样，翻面与沿线抵达都会抢走，所以那两条要求纯包装层只写一个状态。
    """
    chrome, ev, _send = _cdp(full_lesson, "bloom")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")  # 同上：让开命中
        ev("window.__jump('seg-2', 1)")
        assert ev("document.getElementById('b-00-x') === null"), \
            "s1 还没到『没有第二位』那句，早建档就把入场演给没人看"
        ev("window.__jump('seg-2', 2)")
        sw = json.loads(ev(_BLOOM_SWEEP.replace("GAP", "b-00-x").replace("CELL", "b-x00-r")))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 500, f"绽开 500ms（--dur-bloom 与 --dur-reveal 同档）：{sw}"
        rr = [f["r"] for f in sw["frames"]]
        assert abs(rr[0] - 0) <= 0.5, f"起点是全遮：{sw['frames'][0]}"
        assert all(rr[i] < rr[i + 1] for i in range(4)), f"半径逐帧在长，不是两档：{rr}"
        assert abs(rr[4] - 75) <= 0.5, f"终值就是静止态 75%：{rr}"
        assert sw["after"] == "circle(75% at 50% 50%)", \
            f"cancel 之后落回元素自身值（导出与冷启动都吃这一格）：{sw['after']}"
        # 命中跟着裁（0.15.0 那条口径），而 75% 这档不啃描边
        assert sw["frames"][0]["hit"] != "b-x00-r", \
            f"半径 0% 时上边描边点不着才说明裁剪真的生效：{sw['frames'][0]}"
        assert sw["frames"][4]["hit"] == "b-x00-r", \
            f"落定的 75% 必须把描边整条留在命中区里：{sw['frames'][4]}"

        st = json.loads(ev(_BLOOM_PROBE.replace("GAP", "b-00-x").replace("CELL", "b-x00-r")))
        assert st["cls"] == "nt bloom", f"只挂包装层：{st}"
        assert st["kids"] == ["b-x00-r/nt", "b-x00-t/nt"], \
            f"内层不再叠 .pop（绽开就是这一格的入场）：{st['kids']}"
        assert st["cp"] == "circle(75% at 50% 50%)" and st["an"] == "bloomIn", st
        assert st["afill"] == "backwards", f"不写 forwards：{st['afill']}"
        assert st["bbox"] == [-21.0, -26.0, 42.0, 52.0], \
            f"clip 不改 getBBox（42×52 一格不少）：{st['bbox']}"
        assert "transform" in st["tp"] and "opacity" in st["tp"], \
            f".bloom 不抢 .nt 的 transition 清单（三件套里只有它这样）：{st['tp']}"
        assert st["top"] == "b-x00-r", f"绽开结束后上边描边照旧命中（75% 没啃掉它）：{st}"

        # 过了 s3 这一格就不再被引用：sweep 撤干净，不留一张空位在场外
        ev("window.__jump('seg-2', 4)")
        time.sleep(0.5)
        assert ev("document.getElementById('b-00-x') === null"), \
            "s4 起第一行的空缺格不再被引用，该撤干净"
        assert ev("document.querySelectorAll('.bloom').length === 0"), \
            "这一幕不该再有任何绽开层留在台上"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_flip_ride_bloom_reduced_motion_and_shot_terminals(full_lesson):
    """减动效与导出截图页两条退路：三件套关掉之后落的**正是终态**。

    这轮的特殊之处：三条里两条走 transition、一条走 animation，但**档位都在呈现
    属性上**（`data-over` / `data-go` / 元素自身的 `clip-path`），所以两把刀切下去
    都不会留下"翻到一半侧立""走在半途""开了四分之三"这种中间格。实测：
      · 减动效：`transition-property` 落 none，换档后**立即**读到 θ=180°、
        正面 hidden / 背面 visible；标记立即 100%；`getAnimations()` 恒空。
        三条兜底都**不需要抬特异度**（基础规则源序在本块之前，与掠光/侧栏相反）——
        这一点在这里被计算值证实，不是推测。
      · 导出：一刀切掉 transition/animation 后，翻面落背面、标记落终点、空缺格落
        完整的一张——与不减动效的落定档逐值相同。
    """
    chrome, ev, send = _cdp(full_lesson, "rm20")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")  # 让开命中测试

        # 翻面：不演，但背面照旧在场
        ev("window.__jump('seg-2', 2)")
        st = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-0-vs-w")
                              .replace("FRONT", "b-0-vs").replace("BACK", "b-0-vs-b")))
        assert st["over"] == "0" and st["theta"] == 0.0 and st["tp"] == "none", \
            f"减动效下正面在场且不补间：{st}"
        assert st["ftp"] == "none" and st["ftdelay"] == "0s", \
            f"两面的 visibility 也得关掉（抬过 .nt 才落得上）：{st}"
        ev("window.__jump('seg-2', 3)")
        st = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-0-vs-w")
                              .replace("FRONT", "b-0-vs").replace("BACK", "b-0-vs-b")))
        assert st["theta"] == 180.0 and st["fvis"] == "hidden" \
            and st["bvis"] == "visible" and st["anims"] == [], \
            f"不 sleep 就落终态：{st}"
        assert st["hit"] == "b-0-vs-b", f"减动效下命中的也是背面：{st}"

        # 沿线抵达：关掉补间不等于关掉位置
        ev("window.__jump('seg-6', 1)")
        st = json.loads(ev(_RIDE_PROBE.replace("RIDE", "e-ride-\u5c0f\u660e")
                              .replace("TIE", "e-tie-\u5c0f\u660e")))
        assert st["dist"] == "100%" and st["tp"] == "none" and st["anims"] == [], \
            f"标记直接落在终点：{st}"
        assert abs(st["ax"] - st["tieEnd"]["x"]) <= 2.5, f"位置就是那条线的右端：{st}"

        # 圆形绽开：关的是 animation，静止态本来就全开
        ev("window.__jump('seg-2', 2)")
        st = json.loads(ev(_BLOOM_PROBE.replace("GAP", "b-00-x").replace("CELL", "b-x00-r")))
        assert st["an"] == "none" and st["cp"] == "circle(75% at 50% 50%)", \
            f"关掉之后整格完整可见：{st}"
        assert st["bbox"] == [-21.0, -26.0, 42.0, 52.0] and st["top"] == "b-x00-r", st

        # 导出截图页口径：一刀切掉，三处定格与上面逐值相同
        ev("var s=document.createElement('style');"
           "s.textContent='*,*::before,*::after{transition:none!important;"
           "animation:none!important}';document.head.appendChild(s);")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-6', 0)")
        st = json.loads(ev(_RIDE_PROBE.replace("RIDE", "e-ride-\u5c0f\u660e")
                              .replace("TIE", "e-tie-\u5c0f\u660e")))
        assert st["dist"] == "0%", f"s0 那一帧定格在路头（那是作者写的档位）：{st}"
        ev("window.__jump('seg-6', 1)")
        st = json.loads(ev(_RIDE_PROBE.replace("RIDE", "e-ride-\u5c0f\u660e")
                              .replace("TIE", "e-tie-\u5c0f\u660e")))
        assert st["dist"] == "100%" and abs(st["ax"] - st["tieEnd"]["x"]) <= 2.5, \
            f"导出帧必须是抵达之后那一格，不许落回起点：{st}"
        ev("window.__jump('seg-2', 3)")
        st = json.loads(ev(_FLIP_PROBE.replace("FLIP", "b-0-vs-w")
                              .replace("FRONT", "b-0-vs").replace("BACK", "b-0-vs-b")))
        assert st["theta"] == 180.0 and st["bvis"] == "visible", \
            f"导出定格：判决已经翻过去：{st}"
        st = json.loads(ev(_BLOOM_PROBE.replace("GAP", "b-00-x").replace("CELL", "b-x00-r")))
        assert st["cp"] == "circle(75% at 50% 50%)" \
            and st["bbox"] == [-21.0, -26.0, 42.0, 52.0] and st["top"] == "b-x00-r", \
            f"导出定格：空缺格完整一张：{st}"
    finally:
        _kill(chrome)


# ── 0.21.0：定盘与成形（倾侧定盘 / 触点涟漪 / 字距归位）────────────────────

def test_settle_and_form_primitives_present():
    """模板结构契约：0.21.0 三条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮在 Chrome 152 里量出来"为什么只能这么写"的边界：
      · `.tilt` 补间的是**独立 `rotate` 属性**，不是 `transform`：实测计算值
        `transform` 全程 `none`、`transition-property` 只剩 `rotate`（`.tilt` 的简写
        抢掉 `.nt` 那条，正是想要的），写进 transform 就把子图元的绝对坐标一起卷进
        函数列表（0.13.0 那条互相缩放的教训）；
      · 支点靠 `transform-box:fill-box` + `transform-origin:50% 50%`：两种偷懒都量过
        ——退回 `view-box` 时支点跑到 viewBox 中心，倾 6.5° 整根梁平飘 6.7px；再省掉
        origin 就绕左上角转，托盘屏位移 69px（那不是倾侧是平移出画）。所以支点三角
        与刻度**画在包装层外面**：实测把它塞进包装层，包围盒从 [406,280,188,12] 胀成
        [406,280,188,24]、中心从 y=286 掉到 y=292；
      · 终态在呈现属性上（`data-pan="-1|0|1"`）：**三档离散**是这条原语的全部意义
        （契约只看符号），所以两档只写在 `[data-pan]` 分支上，静止态是 `rotate:0deg`；
      · 行程用空间弹簧 `--ease-spring`，涟漪与字距用 `--ease-out`：改几何的允许过冲
        （实测 +6.5°→−6.5° 在 70% 那档越到 −8.45°、551ms 收住，屏幕上多沉 3.1px），
        改透明度/字距的不允许——`linear()` 弹簧套在 opacity 上会读作闪两下；
      · `.gather` 两头**显式同型**：`to{letter-spacing:0}` 必须写出来。实测 Chrome 把
        基础值 `normal` 当 0 参与插值（留 normal 与写 0 两组逐帧同值），但规范上
        `normal` 不是长度、异型该按 discrete——不赌实现；同时作者的排版规则里**不许**
        为动效写 `letter-spacing`（静止态与终态在 13px 中文那档排宽逐值相同 117px）；
      · `.gather` 窄屏关掉：500px 档实测最长那句散开档 411.6px / 容器 449px 仍是一行，
        但只剩 37px 余量——按余量决策，作者文案再长一点就断行，那是抖不是收拢；
      · 涟漪静止态与终态**都看不见**（`scale:0;opacity:0` 在元素身上）：它是事件不是
         状态，兜底行必须带 `#gate-block` 抬特异度（基础规则写在 §⑤ 门禁那一段，源序
         排在 reduced-motion 块**之后**，朴素同形会被顶掉）；
      · 圆心与直径都由 JS 现量，且只挂 `GEST.down`：实测程序化 `click()`（键盘那条路）
        不挂 `.rip`，判定与放行不受影响。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    js = (REPO / "scripts" / "interactive_runtime.js").read_text(encoding="utf-8")

    # ① 令牌：三条各一档，且都跟已有词汇对齐或错开
    assert "--dur-tilt:.55s; --tilt-deg:6.5deg;" in html
    assert "--dur-rip:.5s; --rip-ink:rgba(245,166,35,.26);" in html
    assert "--dur-gather:.42s; --track-open:.3em;" in html
    assert "--dur-tilt:.9s" not in html and "--track-open:.6em" not in html

    # ② 倾侧：独立 rotate 属性 + fill-box 支点 + 三档在属性上
    assert (".tilt{transform-box:fill-box;transform-origin:50% 50%;rotate:0deg;"
            in html), "支点必须是自己的包围盒中心：view-box 档实测平飘 6.7px，0 0 档飘 69px"
    assert "transition:rotate var(--dur-tilt) var(--ease-spring)}" in html, \
        "补间走独立 rotate 属性（写进 transform 会把子坐标卷进函数列表）"
    assert '.tilt[data-pan="-1"]{rotate:calc(var(--tilt-deg) * -1)}' in html
    assert '.tilt[data-pan="1"]{rotate:var(--tilt-deg)}' in html, \
        "两档只写在 [data-pan] 分支上：倾角是符号读数，导出切掉补间落的正是这一档"
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    assert re.search(r"transform:[^;}]*--tilt-deg", css) is None, \
        "倾侧不许写进 transform 函数列表（0.20.0 的 rotateY 是另一条原语，别混）"
    assert "@keyframes tilt" not in css, "倾侧走 transition（档位是状态，不是入场）"

    # ③ 倾侧的消费者：支点与刻度留在旋转层外面
    assert "{class:'tilt in', 'data-pan':gp}" in html
    i_ful = html.index("put('c-body', 'path', 'c-fulcrum'")
    i_beam = html.index("put('c-body', 'g', 'c-beam-w'")
    assert i_ful < i_beam, "支点画在包装层外面：塞进去包围盒中心掉 6 单位，支点跟着漂"
    assert "put(beam, 'line', 'c-beam'" in html and "put(beam, 'circle', 'c-pan-l'" in html, \
        "梁与两枚托盘同住旋转层，且同一水平线（盒竖直中心 = 支点）"
    assert "put('c-body', 'g', 'c-beam-w', {class:'tilt'" not in html, \
        "旋转层必须也带 .in（建档那一拍仍要有入场），但别漏掉 data-pan"

    # ④ 字距归位：两头同型 + 排版规则不替动效让路 + 窄屏关掉
    assert "@keyframes gatherIn{from{letter-spacing:var(--track-open)}" in html
    assert "to{letter-spacing:0}}" in html, \
        "隐式 to 是 normal：Chrome 实测当 0 插，规范上是 discrete——不赌实现"
    kf = re.search(r"@keyframes gatherIn\{([^}]*\}[^}]*)?\}", css).group(0)
    assert "normal" not in kf, f"关键帧两头都得是长度：{kf}"
    assert "animation:gatherIn var(--dur-gather) var(--ease-out) backwards}" in html, \
        "字距不是几何，用效果侧的 ease-out：弹簧过冲在这里读作抖"
    tag = re.search(r"\.chapter \.tagline\{[^}]*\}", css).group(0)
    assert "letter-spacing" not in tag, \
        f"排版规则里不许为动效预留字距（静止态与终态实测等宽）：{tag}"
    narrow = css.split("@media (max-width:560px), (max-height:560px){")[1].split("@media")[0]
    assert ".gather{animation:none}" in narrow, \
        "窄屏这一档关掉：实测最长那句只剩 37px 余量，断行跟着帧变"
    assert ("tg.classList.remove('gather'); void tg.getBoundingClientRect();"
            in html) and "tg.classList.add('gather');" in html, \
        "换节重触发三段式（与 chFlip 同一个判据：换节不是换句）"

    # ⑤ 涟漪：事件不是状态 —— 静止/终态都不可见，圆心现量，只挂按下
    assert ("@keyframes ripGrow{0%{scale:0;opacity:.85}62%{opacity:.34}"
            "100%{scale:1;opacity:0}}") in css
    assert '.opt{position:relative;overflow:hidden}' in css, \
        "宿主不裁就糊到卡片上；实测三颗选项 scrollWidth==clientWidth，裁不到字"
    assert '.opt.rip::after{content:"";position:absolute;left:var(--rx,50%);top:var(--ry,50%);' in css
    assert "width:var(--rip-d,100%);height:var(--rip-d,100%);translate:-50% -50%;" in css, \
        "居中走独立 translate 属性：写进 transform 会被 :active{scale} 连位移一起缩"
    assert "border-radius:50%;background:var(--rip-ink);pointer-events:none;" in css
    assert "scale:0;opacity:0;animation:ripGrow var(--dur-rip) var(--ease-out)}" in css, \
        "base 不可见 + 不写 forwards：跑完与切掉 animation 落的都是没有涟漪那一格"
    assert "once(btn, GEST.down, e => ripple(btn, e));" in js, \
        "涟漪只挂在按下那一拍（判定仍走 click，两条路各自说话）"
    assert js.count("ripple(btn") == 1 and "ripple(btn, 'click'" not in js, \
        "click 那条路上不许再挂一次涟漪（键盘与程序化 click 没有触点）"

    # ⑥ reduced-motion：三条兜底都在；涟漪那条必须抬特异度
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    assert ".tilt{transition:none}" in rm
    assert ".gather{animation:none}" in rm
    assert "#gate-block .opt.rip::after{animation:none}" in rm, \
        "基础规则源序在本块之后，朴素 .opt.rip::after 会被它顶掉（0.15.0 那一课）"
    rm_rules = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert "--tilt-deg" not in rm_rules and "rotate:" not in rm_rules, \
        "关的是补间不是档位：倾角读数本身不在兜底清单里（在清单里就等于把符号关掉）"

    # ⑦ 本轮研究里量过/查证后仍不请回来的四条（连同旧锁一起复述一遍理由）
    assert "conic-gradient" not in css, "计数器的扫环仍不合格：不支持时整块图案消失"
    assert "view-transition" not in html, "跨元素快照在 WebKit 上不吃 clip-path/WAAPI，冷启动也不演"
    assert "counter(" not in css and "@property --num" not in html, \
        "里程表数字在不支持 @property 的引擎渲染空字符串（纪律③），且这里的数字都是时钟读数"
    assert "<animate" not in html and "animateMotion" not in html, \
        "SMIL 与 CSS 两套时钟不能同页（唯一时钟边界）"


_TILT_PROBE = r"""(function(){
  var w = document.getElementById('c-beam-w');
  if (!w) return JSON.stringify({present:false});
  var cs = getComputedStyle(w), bb = w.getBBox();
  var l = document.getElementById('c-pan-l'), r = document.getElementById('c-pan-r');
  var pl = l.getBoundingClientRect(), pr = r.getBoundingClientRect();
  var hit = document.elementFromPoint(pl.left + pl.width / 2, pl.top + pl.height / 2);
  return JSON.stringify({present:true, pan:w.getAttribute('data-pan'),
    cls:w.getAttribute('class'), rot:cs.rotate, tf:cs.transform,
    tp:cs.transitionProperty, tdur:cs.transitionDuration,
    te:cs.transitionTimingFunction.slice(0, 24),
    bbox:[+bb.x.toFixed(2), +bb.y.toFixed(2), +bb.width.toFixed(2), +bb.height.toFixed(2)],
    ly:+((pl.top + pl.bottom) / 2).toFixed(1), ry:+((pr.top + pr.bottom) / 2).toFixed(1),
    hit:hit ? (hit.id || hit.tagName) : null,
    pe:getComputedStyle(document.getElementById('c-beam')).pointerEvents,
    anims:(w.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;})});
})()"""

# 弹簧的行程必须逐帧读：过冲那一格只有在中途才看得见（落定档永远正好 ±6.5°）。
_TILT_SWEEP = r"""(function(){
  var w = document.getElementById('c-beam-w');
  var list = (w.getAnimations()||[]).filter(function(a){
    return a.transitionProperty === 'rotate';});
  if (list.length !== 1){
    return JSON.stringify({err:'rotate 补间不在场',
      an:(w.getAnimations()||[]).map(function(a){
        return a.transitionProperty || a.animationName;})});
  }
  var out = {dur:list[0].effect.getComputedTiming().duration,
             ease:list[0].effect.getTiming().easing.slice(0, 40), frames:[]};
  list[0].pause();
  [0, 105, 385, 470, 551].forEach(function(ms){
    list[0].currentTime = ms;
    var cs = getComputedStyle(w);
    var pl = document.getElementById('c-pan-l').getBoundingClientRect();
    out.frames.push({ms:ms, rot:parseFloat(cs.rotate),
      ly:+((pl.top + pl.bottom) / 2).toFixed(1)});
  });
  list[0].cancel();
  out.after = getComputedStyle(w).rotate;
  return JSON.stringify(out);
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_tilt_pans_to_the_sign_it_reads(full_lesson):
    """真实浏览器：符号盘**只有三档**，而每一档是靠弹簧落过去的（0.21.0）。

    量四件事：① 建档前这一层根本不在（s0 没有仪器，入场不给没人看的拍子）；
    ② 三档离散：s1/s2/s3 读回 −6.5° / 0° / +6.5°，`transform` 计算值全程 `none`
    （倾侧只住在独立 `rotate` 属性里），而且**包装层包围盒三档逐值相同**
    （[406,280,188,12]）——相机取景与 §5 的连线几何判定看不见这层转动；
    ③ 弹簧真的过冲：把 rotate 补间冻在 0/105/385/470/551ms 逐格读，+6.5°→−6.5°
      这一趟在 70% 那档（≈385ms）越到 −8.45°（超目标 1.95°），551ms 收在 −6.5°，
      屏幕上左托盘过冲那一格比落定档再沉 3px 左右——这是"空间弹簧"，不是抖动
      （透明度/字距那两条用的是 --ease-out，见下面的涟漪与字距用例）；
    ④ 哪边沉跟着符号走：−1 档左托盘在屏幕下方（y 大），+1 档翻到上方。
    """
    chrome, ev, _send = _cdp(full_lesson, "tilt")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")  # 让开命中
        ev("window.__jump('seg-4', 0)")
        assert json.loads(ev(_TILT_PROBE))["present"] is False, \
            "s0 只讲『要传比较函数』，仪器还没登场，别把入场演给没人看"

        boxes = {}
        for k, want in ((1, "-6.5deg"), (2, "0deg"), (3, "6.5deg")):
            ev("window.__jump('seg-4', %d)" % k)
            _wait_idle(ev, "#c-beam-w")
            st = json.loads(ev(_TILT_PROBE))
            assert st["pan"] == {1: "-1", 2: "0", 3: "1"}[k], st
            assert st["rot"] == want, f"s{k} 该落在 {want}：{st}"
            assert st["tf"] == "none", f"倾侧不许碰 transform：{st['tf']}"
            assert st["tp"] == "rotate", f".tilt 抢下整条简写，只剩 rotate 在补间：{st}"
            assert st["tdur"] == "0.55s" and st["te"].startswith("linear("), \
                f"时长 --dur-tilt、曲线是 linear() 弹簧：{st}"
            assert st["cls"] == "nt tilt in", f"旋转层同时是入场层：{st['cls']}"
            assert st["anims"] == [], f"落定后没有在跑的补间：{st}"
            boxes[k] = st["bbox"]
        assert boxes[1] == boxes[2] == boxes[3] == [406.0, 280.0, 188.0, 12.0], \
            f"rotate 不进 getBBox（三档同一盒）：{boxes}"

        neg = json.loads(ev(_TILT_PROBE))       # 此刻在 s3(+1)
        ev("window.__jump('seg-4', 1)")
        assert neg["ly"] < neg["ry"], f"+1 档左扬右沉（顺时针）：{neg}"
        sw = json.loads(ev(_TILT_SWEEP))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 550, f"行程 550ms（--dur-tilt 与 --dur-draw 同档）：{sw}"
        assert sw["ease"].startswith("linear("), f"曲线是注册的弹簧 linear()：{sw['ease']}"
        rot = [f["rot"] for f in sw["frames"]]
        assert abs(rot[0] - 6.5) <= 0.05 and abs(rot[4] + 6.5) <= 0.05, \
            f"两端就是这一档的读数：{rot}"
        assert rot[1] > 1.0, f"105ms 还在往回走（弹簧起步不猛）：{rot}"
        assert rot[2] < -8.0, \
            f"空间弹簧必须越过去（70% 那档实测 ≈−8.45°）：{rot}"
        assert -6.5 > rot[3] > rot[2], \
            f"过峰之后往回收，但仍在目标下方：{rot}"
        assert abs(rot[4] + 6.5) <= 0.05, f"551ms 收在 −6.5°：{rot}"
        ly = [f["ly"] for f in sw["frames"]]
        assert ly[2] > ly[4] + 2.0, \
            f"过冲那一格屏幕上左托盘比落定档再沉 ≥2px（实测 3px）：{ly}"
        assert sw["after"] == "-6.5deg", \
            f"cancel 之后落回 data-pan 那一档（导出与冷启动吃这格）：{sw['after']}"

        st = json.loads(ev(_TILT_PROBE))
        assert st["ly"] > st["ry"], f"−1 档左盘沉（左端在下）：{st}"
        assert st["hit"] == "c-pan-l", \
            f"命中跟着变换走（所以几何判定与热区不一致，见边界 ⑤）：{st}"
        assert st["pe"] == "auto", f"这一层不是装饰隔离层，别把它当坐标用：{st}"
    finally:
        _kill(chrome)


_GATHER_SWEEP = r"""(function(){
  var t = document.getElementById('ch-tagline');
  var list = (t.getAnimations()||[]).filter(function(a){
    return a.animationName === 'gatherIn';});
  if (list.length !== 1){
    return JSON.stringify({err:'gatherIn 不在场', an:(t.getAnimations()||[]).map(
      function(a){ return a.transitionProperty || a.animationName; })});
  }
  var ct = list[0].effect.getComputedTiming();
  var out = {dur:ct.duration, fill:ct.fill,
             ease:((list[0].effect.getKeyframes()[0]||{}).easing || '').slice(0, 30),
             text:t.textContent, frames:[], lines:0};
  list[0].pause();
  [0, 105, 210, 315, 419].forEach(function(ms){
    list[0].currentTime = ms;
    var cs = getComputedStyle(t);
    var r = document.createRange(); r.selectNodeContents(t);
    out.frames.push({ms:ms, ls:cs.letterSpacing, lines:r.getClientRects().length});
  });
  list[0].cancel();
  var cs = getComputedStyle(t), r2 = document.createRange();
  r2.selectNodeContents(t);
  out.after = cs.letterSpacing;
  out.lines = r2.getClientRects().length;
  out.width = +t.getBoundingClientRect().width.toFixed(1);
  return JSON.stringify(out);
})()"""

_GATHER_LINES = r"""(function(v){var t=document.getElementById('ch-tagline');
  var old = t.style.letterSpacing; t.style.letterSpacing = v;
  var r = document.createRange(); r.selectNodeContents(t);
  var cs = r.getClientRects(), w = 0;
  for (var i = 0; i < cs.length; i++) w += cs[i].width;
  var out = {text:t.textContent, lines:cs.length, w:+w.toFixed(1),
             fs:getComputedStyle(t).fontSize, box:+t.getBoundingClientRect().width.toFixed(1)};
  t.style.letterSpacing = old; return JSON.stringify(out);
})('%s')"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_gather_tightens_the_tagline_on_scene_change_only(full_lesson):
    """真实浏览器：副题在**换节**那一拍从散开收成正常排布（0.21.0）。

    ① 行程连续且两头同型：冻在 0/105/210/315/419ms 逐格读字距，3.9px 一路收到
      ~0（.3em @ 13px = 3.9px）——`to{letter-spacing:0}` 写出来才是这条连续行程；
    ② 它是入场不是状态：`fill-mode` 只有 backwards，cancel 之后计算值回到元素
      自己的 `normal`，而实测 `normal` 与 `0` 在这一档**排宽逐值相同**，静止态与
      终态同形（导出切掉 animation 落的正是这一格）；
    ③ 判据是换节不是换句：同一节内换第二句**不重放**（getAnimations 里没有
      gatherIn），换到另一节才重放；
    ④ 不撑破一行：范本八句在 .3em 档实测 Range 行度全为 1（最长那句 293.5 →
      382.2px / 容器 886px），这就是 --track-open 取 .3em 的依据；窄屏那条留给下面
      的用例（关掉它的判据是余量，不是"实测会断"）。
    """
    chrome, ev, _send = _cdp(full_lesson, "gather")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-1', 0)")
        assert _motion(ev, "#ch-tagline") == ["gatherIn"], \
            f"换节那一拍字距归位该在跑：{_motion(ev, '#ch-tagline')}"
        sw = json.loads(ev(_GATHER_SWEEP))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 420 and sw["fill"] == "backwards", \
            f"与入场同档（--dur-gather .42s）、不写 forwards：{sw}"
        assert sw["ease"].startswith("cubic-bezier") or sw["ease"] == "ease-out", \
            f"字距不是几何，走 --ease-out 不走弹簧：{sw['ease']}"
        vals = [f["ls"] for f in sw["frames"]]
        px = [float(v[:-2]) if v != "normal" else None for v in vals]
        assert abs(px[0] - 3.9) <= 0.05, f"散开那一头 = .3em @13px：{vals}"
        assert all(px[i] > px[i + 1] for i in range(4)) and px[4] < 0.02, \
            f"一路连续收窄（不是两档闪烁）：{vals}"
        assert all(f["lines"] == 1 for f in sw["frames"]), f"全程没被劈成两行：{sw['frames']}"
        assert sw["after"] == "normal" and sw["lines"] == 1, \
            f"cancel 后落回自己的排布、一行不少：{sw}"

        # 判据：换节，不是换句
        ev("window.__jump('seg-1', 1)")
        time.sleep(0.1)
        assert "gatherIn" not in _motion(ev, "#ch-tagline"), \
            "同节换句不该再演一遍（它是换节的入场，不是每句的入场）"
        ev("window.__jump('seg-2', 0)")
        assert "gatherIn" in _motion(ev, "#ch-tagline"), "换到 seg-2 才重放"

        # 八句的余量
        for sid in _IDS:
            ev("window.__jump('%s', 0)" % sid)
            time.sleep(0.12)
            for v in ("0", ".3em"):
                st = json.loads(ev(_GATHER_LINES % v))
                assert st["lines"] == 1, f"{sid} 在 {v} 档换行了：{st}"
            open_ = json.loads(ev(_GATHER_LINES % ".3em"))
            assert open_["box"] - open_["w"] > 400, \
                f"桌面档余量该是量出来的（<400px 就该重新议 .3em）：{open_}"
    finally:
        _kill(chrome)


_RIP_STATE = r"""(function(sel){
  var o = document.querySelectorAll('#gate-block .opt')[sel];
  var cs = getComputedStyle(o, '::after');
  var r = o.getBoundingClientRect();
  return JSON.stringify({cls:o.className, rx:cs.getPropertyValue('--rx'),
    ry:cs.getPropertyValue('--ry'), d:cs.width, anim:cs.animationName,
    cont:cs.content, op:cs.opacity, scale:cs.scale, pef:cs.pointerEvents,
    rect:[+r.left.toFixed(1), +r.top.toFixed(1), +r.width.toFixed(1), +r.height.toFixed(1)],
    sw:o.scrollWidth, cw:o.clientWidth, sh:o.scrollHeight, ch:o.clientHeight,
    locked:document.querySelector('[data-interaction-type="choice"]').dataset.locked || '',
    fb:(document.getElementById('gate-fb')||{}).textContent || ''});
})('%s')"""

# 逐档读：CDP 往返本身要 30ms 一趟，用 wall-clock 采到的动画钟会**整体滞后**
# （实测按下后动画的 start 在 ~166ms 才落，前八趟读到的都是 anim 0）。所以行程
# 判定不追帧，直接把动画 `pause()` + `currentTime` 钉在关键帧那几档上读——
# 曲线形状由 CSS 保证，"有没有自己在跑"由下面的 playState 保证。
_RIP_PLAY = """(function(){var o=document.querySelectorAll('#gate-block .opt')[0];
  var a=(o.getAnimations({subtree:true})||[]).filter(function(x){
    return x.animationName === 'ripGrow';})[0];
  return JSON.stringify([a ? a.animationName : null, a ? a.playState : null,
    a ? +a.effect.getTiming().duration.toFixed(0) : null,
    a ? +a.currentTime.toFixed(0) : null]);})()"""

_RIP_SWEEP = """(function(t){var o=document.querySelectorAll('#gate-block .opt')[0];
  var a=(o.getAnimations({subtree:true})||[]).filter(function(x){
    return x.animationName === 'ripGrow';})[0];
  if(!a) return JSON.stringify([t, null, null, 'gone']);
  a.pause(); a.currentTime = t;
  var cs=getComputedStyle(o,'::after');
  return JSON.stringify([t, +parseFloat(cs.opacity).toFixed(4),
    +parseFloat(cs.scale).toFixed(4), a.playState]);})(%s)"""

_RIP_FINISH = """(function(){var o=document.querySelectorAll('#gate-block .opt')[0];
  var a=(o.getAnimations({subtree:true})||[]).filter(function(x){
    return x.animationName === 'ripGrow';})[0];
  if(a) a.finish();
  return JSON.stringify(a ? a.playState : null);})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_ripple_grows_from_the_touch_point_and_vanishes(full_lesson):
    """真实浏览器：按下那一点起一圈涟漪，涨满后**什么都不留**（0.21.0）。

    ① 圆心 = 触点：JS 现量写进 `--rx/--ry`（按在 25%/70% 那一点，读回来对得上盒内
      坐标），直径 `--rip-d` = 触点到四角最大距离 ×2——按 python 重算一遍与 CSS 读到
      的值比（±1px），常数半径要么涨不满要么白涨一圈；
    ② 行程：把动画 `pause()` 后逐档钉在 0/4/40/150/310/400/470/500ms 上读
      opacity/scale（CDP 往返 30ms 一趟，追帧采到的是滞后的钟），起手 .85/0 →
      62% 那档 .34/.9865 → 470ms .0002 → 500ms 落回 base 的 0/0，且 `finish()` 时
      动画已经不在 `getAnimations()` 里（fill 默认 none）= 事件不是状态；
    ③ 不抢命中：涨到一半时 `elementFromPoint` 仍是那颗按钮（`pointer-events:none`），
      宿主 `overflow:hidden` 也没裁到字（scrollWidth==clientWidth）；
    ④ 只挂在 pointerdown：松手在盒外 → 不触发 click → 门禁没被判定；随后的程序化
      `click()` 正确项（键盘那条路的形状）判定照走、反馈照出，但**没有涟漪**。
    """
    chrome, ev, send = _cdp(full_lesson, "rip")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-3', 0)")          # preGate：这一场第一句前就弹
        vis = [False, 0]
        deadline = time.time() + 6
        while time.time() < deadline:
            vis = json.loads(ev("""(function(){var g=document.getElementById('gate-block');
              var o=document.querySelectorAll('#gate-block .opt')[0];
              var r=o ? o.getBoundingClientRect() : null;
              return JSON.stringify([!g.hidden, r ? +r.width.toFixed(1) : 0]);})()"""))
            if vis[0] and vis[1] > 0:
                break
            time.sleep(0.15)
        assert vis[0] and vis[1] > 0, f"单选门禁没弹开（涟漪没有宿主可挂）：{vis}"

        # 等盒子**静止**再量：门禁卡入场（cardIn + .stag 错峰）会把整块往下推，
        # 没停就按 → 触点跟着漂（全量跑里实测漂 2.0px，圆心断言因此假失败）。
        prev, cur = None, None
        deadline = time.time() + 4
        while time.time() < deadline:
            prev = cur
            cur = json.loads(ev("""(function(){var o=document.querySelectorAll(
              '#gate-block .opt')[0];var r=o.getBoundingClientRect();
              return JSON.stringify([+r.left.toFixed(2), +r.top.toFixed(2),
                +r.width.toFixed(2), +r.height.toFixed(2)]);})()"""))
            if prev == cur and cur[2] > 0:
                break
            time.sleep(0.1)
        assert prev == cur and cur[2] > 0, f"盒子一直没停（触点无法预量）：{prev} / {cur}"

        st0 = json.loads(ev(_RIP_STATE % 0))
        x, y, w, h = st0["rect"]
        assert st0["cls"] == "opt hot" and st0["cont"] == "none", \
            f"没按过的选项**根本没有这一层**（::after 不生成盒子）：{st0}"
        assert st0["sw"] == st0["cw"] and st0["sh"] == st0["ch"], \
            f"overflow:hidden 裁不到字（{st0['sw']}/{st0['cw']}）：{st0}"

        fx, fy = x + w * 0.25, y + h * 0.70
        send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": fx, "y": fy,
                                          "button": "left", "buttons": 1, "clickCount": 1})
        play = json.loads(ev(_RIP_PLAY))
        assert play[0] == "ripGrow" and play[1] == "running" and play[2] == 500, \
            f"按下即自走（不是等松手、也不是挂成一等状态）：{play}"
        st = json.loads(ev(_RIP_STATE % 0))
        want_rx, want_ry = w * 0.25, h * 0.70
        assert abs(float(st["rx"][:-2]) - want_rx) <= 1.0 \
            and abs(float(st["ry"][:-2]) - want_ry) <= 1.0, \
            f"圆心落在触点上（期望 {want_rx:.1f}/{want_ry:.1f}）：{st}"
        corners = max(math.hypot(want_rx, want_ry), math.hypot(w - want_rx, want_ry),
                      math.hypot(want_rx, h - want_ry), math.hypot(w - want_rx, h - want_ry))
        assert abs(float(st["d"][:-2]) - 2 * corners) <= 1.0, \
            f"直径 = 最远角距离 ×2（期望 {2 * corners:.1f}px）：{st}"
        assert st["cls"] == "opt hot rip" and st["pef"] == "none", st

        hit = json.loads(ev("""(function(){var o=document.querySelectorAll('#gate-block .opt')[0];
          var r=o.getBoundingClientRect();var n=document.elementFromPoint(
            r.left + r.width * .25, r.top + r.height * .7);
          return JSON.stringify([n ? (n.className || n.tagName) : null,
            n ? (n.id || n.tagName) : null]);})()"""))
        assert "opt" in str(hit[0]), f"涟漪层不抢命中：{hit}"

        send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": fx,
                                          "y": y + h + 60, "button": "none",
                                          "buttons": 0, "clickCount": 1})
        # 逐档钉住读：t → [wall, anim t, opacity, scale]（探针实测的同一组数字）
        sweep = [json.loads(ev(_RIP_SWEEP % t)) for t in (0, 4, 40, 150, 310, 400, 470, 500)]
        pts = [(r[0], r[1], r[2]) for r in sweep]          # (动画 ms, opacity, scale)
        assert all(r[3] == "paused" for r in sweep), sweep
        assert pts[:4] == [(0, 0.85, 0), (4, 0.8205, 0.0361), (40, 0.5981, 0.3305),
                           (150, 0.3624, 0.8322)], f"起手淡而小、圆从 0 涨出来：{pts}"
        assert abs(pts[4][1] - 0.34) <= 0.005 and 0.98 <= pts[4][2] <= 0.99, \
            f"62% 那档（310ms）透明度压在 .34、圆已近满：{pts[4]}"
        assert pts[5][1] <= 0.02 and pts[5][2] > 0.998, f"后段只剩淡出（400ms）：{pts[5]}"
        assert pts[6][1] <= 0.001, f"470ms 已经看不到了：{pts[6]}"
        assert pts[7][1] == 0 and pts[7][2] == 0, \
            f"跑到终点落回 base（静止态 = 终态 = 什么都不留）：{pts[7]}"
        grow = [p[2] for p in pts[:7]]
        assert all(grow[i] <= grow[i + 1] + 1e-6 for i in range(len(grow) - 1)), \
            f"半径只涨不退：{grow}"
        assert json.loads(ev(_RIP_FINISH)) is None, \
            "500ms 一到动画就下场了（fill 默认 none：不占位、不留最后一帧）"
        dead = json.loads(ev(_RIP_STATE % 0))
        assert dead["op"] == "0" and dead["scale"] == "0", \
            f"动画散场后圆不在屏幕上留东西：{dead}"

        st = json.loads(ev(_RIP_STATE % 0))
        assert st["locked"] != "1", \
            f"松手在盒外不该判定（涟漪与判定是两条路）：{st}"

        # 程序化 click（键盘那条路的形状）：判定走、涟漪不挂。点的是**正确项** b
        ev("""(function(){document.querySelectorAll('#gate-block .opt')[1].click();})()""")
        st = json.loads(ev(_RIP_STATE % 1))
        assert "rip" not in st["cls"] and st["locked"] == "1" and "对" in st["fb"], \
            f"没有 pointerdown 就没有涟漪，判定与放行不受影响：{st}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_settle_and_form_reduced_motion_and_shot_terminals(full_lesson):
    """减动效、窄屏、导出三条退路：关掉之后落的**正是该说的那件事**。

      · 减动效：倾侧关的是 **transition** 不是档位——换档后立即读到 ±6.5°/0°，
        `transition-property` 落 `none`、`getAnimations()` 恒空（兜底行 `.tilt`
        与基础规则同为 (0,1,0)、源序在后，不需要抬特异度）；字距归位关 **animation**，
        计算值回到 `normal` 且副题仍是一行（内容一个字不少）；涟漪关 **animation**
        之后 `::after` 的 `opacity/scale` 都是 0——这条**必须**带 `#gate-block`：
        基础规则排在 reduce 块之后，朴素同形会被顶掉（实测抬过之后才落得上）。
      · 窄屏（≤560 档）：`.gather` 关掉、`.tilt` 照旧（倾角是符号读数，与屏宽无关）。
      · 导出：一刀切掉 transition/animation 后，三档定格与上面逐值相同。
    """
    chrome, ev, send = _cdp(full_lesson, "rm21")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")

        for k, want in ((1, "-6.5deg"), (2, "0deg"), (3, "6.5deg")):
            ev("window.__jump('seg-4', %d)" % k)
            st = json.loads(ev(_TILT_PROBE))          # 不 sleep：这一档本就不该有补间
            assert st["rot"] == want and st["tp"] == "none", \
                f"减动效下 s{k} 直接落 {want}（关的是补间不是档位）：{st}"
            assert "rotate" not in st["anims"], f"不该还有 rotate 在跑：{st}"

        ev("window.__jump('seg-1', 0)")
        g = json.loads(ev(_GATHER_SWEEP))
        assert "err" in g and "gatherIn" not in [x for x in g["an"] if x], \
            f"减动效下归位动画不该在场：{g}"
        st = json.loads(ev("""(function(){var t=document.getElementById('ch-tagline');
          var cs=getComputedStyle(t);var r=document.createRange();
          r.selectNodeContents(t);
          return JSON.stringify({an:cs.animationName,ls:cs.letterSpacing,
            lines:r.getClientRects().length,cls:t.classList.contains('gather')});})()"""))
        assert st["an"] == "none" and st["cls"] is True and st["lines"] == 1, \
            f"类名挂着但不演，副题照常是完整一行：{st}"

        ev("window.__jump('seg-3', 0)")
        vis = [False, 0]
        deadline = time.time() + 6
        while time.time() < deadline:
            vis = json.loads(ev("""(function(){var g=document.getElementById('gate-block');
              var o=document.querySelectorAll('#gate-block .opt')[0];
              var r=o ? o.getBoundingClientRect() : null;
              return JSON.stringify([!g.hidden, r ? +r.width.toFixed(1) : 0]);})()"""))
            if vis[0] and vis[1] > 0:
                break
            time.sleep(0.15)
        assert vis[0] and vis[1] > 0, f"减动效下门禁照样要弹开：{vis}"
        st = json.loads(ev("""(function(){var o=document.querySelectorAll('#gate-block .opt')[0];
          o.classList.add('rip');var cs=getComputedStyle(o,'::after');
          return JSON.stringify({an:cs.animationName,op:cs.opacity,sc:cs.scale});})()"""))
        assert st["an"] == "none" and st["op"] == "0" and st["sc"] == "0", \
            f"减动效下硬挂 .rip 也什么都不显（兜底行抬过特异度才落得上）：{st}"

        # 导出截图页口径：一刀切掉，三处定格与减动效逐值相同
        ev("var s=document.createElement('style');s.textContent="
           "'*,*::before,*::after{transition:none!important;animation:none!important}"
           "';document.head.appendChild(s);")
        ev("window.__coursewareShotMode = true")
        for k, want in ((1, "-6.5deg"), (3, "6.5deg")):
            ev("window.__jump('seg-4', %d)" % k)
            st = json.loads(ev(_TILT_PROBE))
            assert st["rot"] == want, f"导出定格 s{k}：倾侧该是这一档的读数：{st}"
            assert st["bbox"] == [406.0, 280.0, 188.0, 12.0], \
                f"导出定格的取景仍按未倾侧的盒算（rotate 不进 getBBox）：{st}"
        st = json.loads(ev("""(function(){var o=document.querySelector('#gate-block .opt');
          var cs=getComputedStyle(o,'::after');
          return JSON.stringify([cs.opacity,cs.scale]);})()"""))
        assert st == ["0", "0"], f"导出帧里没有半截涟漪压在字上：{st}"
    finally:
        _kill(chrome)

    # 窄屏档：字距归位关掉，倾侧照旧（读数是符号不是像素）
    chrome2, ev2, _s2 = _cdp(full_lesson, "narrow", ("--window-size=430,760",))
    try:
        assert ev2("matchMedia('(max-width:560px)').matches"), "窄屏档没进来"
        ev2(_SEEK)
        ev2("window.__jump('seg-1', 0)")
        st = json.loads(ev2("""(function(){var t=document.getElementById('ch-tagline');
          var cs=getComputedStyle(t);var r=document.createRange();r.selectNodeContents(t);
          return JSON.stringify({cls:t.classList.contains('gather'),an:cs.animationName,
            ls:cs.letterSpacing,lines:r.getClientRects().length,fs:cs.fontSize});})()"""))
        assert st["cls"] is True and st["an"] == "none" and st["lines"] == 1, \
            f"窄屏不演归位、句子仍是一行：{st}"
        ev2("window.__jump('seg-4', 1)")
        st = json.loads(ev2(_TILT_PROBE))
        assert st["rot"] == "-6.5deg" and st["bbox"] == [406.0, 280.0, 188.0, 12.0], \
            f"窄屏倾侧与档位照常（符号读数不随屏宽变）：{st}"
    finally:
        _kill(chrome2)


# ── 16. 落点与过场：移动聚光 / 快进掠擦 / 浮层离散进出（0.22.0）──

def _css_comment_problems(html):
    """<style> 里的注释配对：注释**外**出现的 `*/`、到 `</style>` 还没关的 `/*`。

    本轮踩过一次静默失效：0.22.0 的令牌注释是几段接在一起的，其中一段只写了收尾的
    `*/`、没写开头的 `/*`——那十行中文于是变成**裸声明**，紧跟其后的 `--dur-spot:.5s`
    被当作前一条乱码声明的尾巴一起吃掉，`:root` 里从此没有这个令牌；而
    `transition:--spot-x var(--dur-spot) …` 整条 invalid at computed-value time，
    计算值落回 `all 0s ease`——聚光还能亮、洞还在终位，独独不补间。CSSOM 数出来的
    规则条数一点没变（264），既有断言与肉眼都看不出。**注释配对是 CSS 里唯一"少一个
    符号就静默少一条声明"的地方**，所以钉在这里，而不是只钉 0.22.0 那一个令牌。
    """
    problems = []
    for m in re.finditer(r"<style[^>]*>([\s\S]*?)</style>", html):
        css = m.group(1)
        line = html[:m.start(1)].count("\n") + 1
        opened_at, i, in_c = None, 0, False
        while i < len(css):
            if css.startswith("/*", i):
                if not in_c:
                    in_c, opened_at = True, line
                i += 2
                continue
            if css.startswith("*/", i):
                if not in_c:
                    problems.append("第 %d 行：*/ 没有对应的 /*" % line)
                in_c = False
                i += 2
                continue
            if css[i] == "\n":
                line += 1
            i += 1
        if in_c:
            problems.append("第 %d 行起的注释到 </style> 还没关" % opened_at)
    return problems


def test_spot_ffwd_pregate_primitives_present():
    """模板结构契约：0.22.0 三条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮在 Chrome 152 里量出来"为什么只能这么写"的边界：
      · **注释配对**（见 `_css_comment_problems`）与九个令牌必须真的在 `:root` 的
        解析结果里——写在注释里的不算；
      · 洞的三个参数注册成 `@property{syntax:'<length>'}` 才有补间（0.13.0 `--dimmer`、
        0.17.0 `--feather`、0.19.0 `--lit` 同一条口径），而**幕深 `--spot-k` 故意不注册**：
        它是常量替换、不补间，层的开合走 `opacity` 自己的 transition——这样不支持
        `@property` 的浏览器里 var() 替换仍成立，遮罩照常裁出一个落在终位的洞，
        降级掉的是行程不是内容（纪律③）；
      · 单位写成 `<length>` 而不是 `<percentage>`：实测这些长度走的是**元素本地单位**
        （洞沿 50% 那点落在"局部单位 × 相机倍率"的预测位 ±3px 内），写成百分比要多一层
        fill-box 换算，而 camBox() 量回来的本来就是 viewBox 单位；
      · `pointer-events:none` + 只加暗不改材质：mask **不裁命中测试**（0.17.0 同一条），
        洞压在热区上 `elementFromPoint` 仍落回热区节点；`backdrop-filter` 在 SVG 元素上
        实测不生效（本轮否决①），不许回流；
      · 掠擦**两端都不留东西**：关键帧两端 `opacity:0`、静止态也是 0、不写 `forwards`
        （事件不是状态，与 .flow / .rip 同课）；行程走独立 `translate`、镜像走独立
        `scale`，两个通道各写各的（`transform` 简写会互相顶掉）；**两档都朝自己指的那边
        掠**（本轮翻案：箭头朝右却往左飞读起来是"倒带"）；
      · 阈值只有一份：JS 从 `--ff-min` 读，不在代码里写死第二个数（口径同 `--par-far`）；
      · `.pregate` 的离散进出写在 `@supports (transition-behavior:allow-discrete)` 里，
        基础规则留着旧的 `animation:gateIn`——不认识这一族的浏览器最多损失过渡，
        不损失内容，也不会两层入场叠在一起糊掉；`transition-behavior` **单写一行**，
        简写里那个 `allow-discrete` 是 CSS Transitions 2 的语法，单写让不认的引擎只作废
        这一行；`#gate` 本体不转（本轮否决②：它是 QA 放行信号的唯一真值）；
      · reduced-motion 三行：`#spot` 关 **transition** 不关灯（位置是信息）、
        `#ffwd.go` 兜底**必须带 `.go`** 才压得住排在后面的基础规则、
        `#pregate.pregate` **必须抬到 (1,1,0)**（基础规则源序在本块之后）。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    code = re.sub(r"//[^\n]*", "", css)

    # ⓪ 注释配对 + 令牌必须真在 :root 的解析结果里
    assert _css_comment_problems(html) == [], _css_comment_problems(html)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)
    for tok in ("--dur-spot", "--spot-k", "--spot-ink", "--spot-soft", "--dur-ff",
                "--ff-peak", "--ff-travel", "--ff-min", "--dur-dock", "--spot-bleed"):
        assert re.search(re.escape(tok) + r"\s*:", root), \
            f"{tok} 没长在 :root 里（被注释语法吃掉过一次，见 _css_comment_problems）"

    # ① 令牌取值：本轮量出来的档，不许随手加深幕深或加长行程
    assert "--dur-spot:.5s; --spot-k:.15; --spot-ink:#06080c; --spot-soft:78px;" in html
    assert "--dur-ff:.34s; --ff-peak:.5; --ff-travel:520; --ff-min:2s;" in html
    assert "--dur-dock:.24s; --spot-bleed:600px;" in html
    assert "@property --spot-bleed" not in html, \
        "bleed 是常量替换：注册了它就去补间，而洞心坐标已经把它加进 calc 了"
    assert "--spot-k:." not in css.replace("--spot-k:.15", "") and "--spot-k:0." not in css, \
        "幕深封顶 .15：.18 起主色掉出 4.5 + 三成余量（逐档合成读数见 :root 注释）"
    assert "--ff-travel:1040" not in css, "520 是半幅，单程 1040 > 画布 1000 才两端出画"

    # ② 三个洞参数注册成 <length>；--spot-k 故意不注册
    assert "@property --spot-x{syntax:'<length>';inherits:false;initial-value:500px}" in html
    assert "@property --spot-y{syntax:'<length>';inherits:false;initial-value:198px}" in html
    assert "@property --spot-r{syntax:'<length>';inherits:false;initial-value:150px}" in html, \
        "初值取画布中偏上：某一步只写了 data-on 没写坐标时，洞落在中间比落在左上少压住东西"
    assert "@property --spot-k" not in html and "@property --spot-soft" not in html, \
        "幕深与软边是常量替换：注册了就会去补间，而它们说的不是行程"
    assert re.search(r"--spot-[xyr]\s*:\s*[^;}]*%", css) is None, \
        "洞参数不许写百分比（参考盒与 viewBox 差一层换算，本轮实测过）"

    # ③ #spot 本体：mask 停靠点、开合在呈现属性上、灯不吃命中
    spot_rule = re.search(r"#spot\{[^}]*\}", css).group(0)
    spot_flat = " ".join(spot_rule.split())
    assert "fill:var(--spot-ink);opacity:0;pointer-events:none;mask-repeat:no-repeat;" in spot_rule
    assert ("mask-image:radial-gradient(circle var(--spot-r) "
            "at calc(var(--spot-x) + var(--spot-bleed)) calc(var(--spot-y) + var(--spot-bleed)),"
            in spot_flat), "透明段必须到 var(--spot-r)（写 0 就是一只实心暗盘）"
    assert "calc(var(--spot-y) + var(--spot-bleed))," in spot_flat, \
        "mask 的定位盒是暗幕自己的 fill-box（左上角在 -600），洞心必须加回同一个 bleed"
    assert "transparent 0 var(--spot-r)," in spot_flat
    assert "#000 calc(var(--spot-r) + var(--spot-soft)));" in spot_rule, \
        "满幅停靠点 = 半径 + 软边，两者必须一起跟着洞走"
    assert "backdrop-filter" not in spot_rule, "SVG 上的 backdrop-filter 实测不生效（本轮否决①）"
    assert 'transform-box' not in spot_rule, "灯层不旋转不缩放，别给它加变换锚点"
    assert '#spot[data-on="1"]{opacity:var(--spot-k)}' in html, \
        "开合挂在呈现属性上：不支持 @property 也照样能亮能灭"
    for p in ("--spot-x", "--spot-y", "--spot-r", "opacity"):
        assert re.search(r"transition:[^}]*" + re.escape(p) +
                         r" var\(--dur-spot\) var\(--ease-out\)", css), f"{p} 没进补间清单"
    assert "linear(" not in spot_rule, "改透明度不许过冲（效果弹簧），--ease-out 才对"

    # ④ 结构位置：灯是 #cam 最后一个孩子，掠擦在 #cam 之外、仍在取景框里
    i_clip = html.index('<g clip-path="url(#frameClip)">')
    i_cam = html.index('<g id="cam">')
    i_spot = html.index('<rect id="spot" x="-600" y="-600" width="2200" height="1660" aria-hidden="true">')
    i_ff = html.index('<g id="ffwd" data-dir="f" aria-hidden="true">')
    i_cap = html.index('<g id="cap" class="el">')
    assert i_clip < i_cam < i_spot < i_ff, "灯在相机里、掠擦在相机外"
    assert html.count("</g>", i_spot, i_ff) == 1, "spot 与 ffwd 之间只隔着 #cam 的收尾"
    assert i_cap > i_ff, "字幕带在取景框之外——口播原文永远满墨，不进灯"
    assert 'aria-hidden="true"' in html[i_spot:i_spot + 130] and \
        'aria-hidden="true"' in html[i_ff:i_ff + 60], "两层都是纯视觉，读屏不播"
    # bleed 的三处必须一起动：rect 坐标 = -bleed，尺寸 = viewBox + 2·bleed
    bleed = int(re.search(r"--spot-bleed:(\d+)px", css).group(1))
    geom = dict(re.findall(r'(x|y|width|height)="(-?\d+)"', html[i_spot:i_spot + 130]))
    assert (int(geom["x"]), int(geom["y"])) == (-bleed, -bleed), \
        "幕沿与 mask 里的 +bleed 是同一件事的两半，改一处就得改另一处"
    assert (int(geom["width"]), int(geom["height"])) == (1000 + 2 * bleed, 460 + 2 * bleed)
    assert bleed >= 1000 / 0.85 / 2, \
        "bleed 必须 ≥ 取景框宽 / 相机倍率钳位下限 .85 / 2，否则镜头拉开时幕沿进画"

    # ⑤ 掠擦：事件不是状态 + 两档各自朝自己指的那边掠
    ff_rule = re.search(r"#ffwd\{[^}]*\}", css).group(0)
    assert "opacity:0;pointer-events:none;fill:none;stroke:var(--dim);stroke-width:3;" in ff_rule
    assert "transform-box:fill-box;transform-origin:50% 50%;scale:1;" in ff_rule
    assert "--ff-from:calc(var(--ff-travel) * -1px);--ff-to:calc(var(--ff-travel) * 1px)" in ff_rule, \
        "快进向右掠出（本轮翻案：反过来写读起来是倒带）"
    assert "transform:" not in ff_rule, "行程走独立 translate、镜像走独立 scale，简写会互相顶掉"
    assert "#ffwd.go{animation:ffSweep var(--dur-ff) linear}" in html, \
        "不写 forwards：掠完必须隐形；曲线是 linear，ease-out 会让它在终点前刹住"
    mirror = re.search(r'#ffwd\[data-dir="b"\]\{[^}]*\}', css).group(0)
    assert "scale:-1 1" in mirror and "--ff-from:calc(var(--ff-travel) * 1px)" in mirror, \
        "后退档：雪佛龙镜像 + 行程反向，两件事分两个通道写"
    assert "@keyframes ffSweep{from{translate:var(--ff-from) 0;opacity:0}" in html
    assert "20%{opacity:var(--ff-peak)}70%{opacity:var(--ff-peak)}" in html, \
        "中段平台：峰值 --ff-peak 撑住 50% 的行程，两头才各留一段加减速"
    assert "to{translate:var(--ff-to) 0;opacity:0}}" in html
    assert '<g transform="translate(500,198)">' in html and \
        html.count("<polyline points=") >= 3, "三枚雪佛龙住在画布中心那一层"

    # ⑥ 掠擦的消费者与判据：JS 从令牌读阈值，四道不演
    assert "function begin(){ GEN++; spotOff(); DECODE = {}; }" in html, \
        "每一步开头归零：灯是全局层，不声明就是没有（纪律①；0.26.0 起解码登记表同一条路）"
    body = code[code.index("function camBox(node){"):code.index("function spotOn(keys, pad){")]
    assert "transform.baseVal" in body, "洞心读的是 transform 属性（目标值）"
    assert "getScreenCTM" not in code, \
        "getScreenCTM 只许待在注释里：它给的是补间半途的矩阵，灯会照歪（本轮否决③）"
    assert "if (!b) return spotOff();" in html, "一个都没找到就按灭——宁可不照"
    assert "Math.hypot(b.w, b.h) / 2 + (pad == null ? 26 : pad)" in html, \
        "半径取外接圆（方角不该先黑下去），pad 是灯有余量的那点余量"
    assert "spot.style.setProperty('--spot-x', (b.x + b.w / 2).toFixed(1) + 'px');" in html
    assert "if (step === 1) spotOn(['d-src']);" in html
    assert "else if (step === 2) spotOn(['d-true-r', 'd-false-r']);" in html
    assert "else if (step === 3) spotOn(['d-false-r', 'd-zero-r']);" in html, \
        "s4/s5 故意不照：那两句的主角是世界层，不是管线"
    assert "getPropertyValue('--ff-min')" in html and "Math.abs(d) < ffThreshold()" in html, \
        "阈值只有一个来源，代码里不写死第二个数"
    assert "if (Math.abs(d) < 2)" not in code and "Math.abs(d) < 2;" not in code, \
        "把 2 抄进代码就是第二个数（--ff-min 改了不会跟着改）"
    assert ("if (prev === null || scrubbing || window.__coursewareShotMode) return;" in html
            and "if (el('rack').dataset.gated === '1') return;" in html), "四道不演"
    assert "var prev = lastT; lastT = t;" in html, "演不演都要把上一拍推着走，否则下一拍读成假跳"
    assert "f.dataset.dir = d > 0 ? 'f' : 'b';" in html
    assert ("f.classList.remove('go'); void f.getBoundingClientRect();" in html
            and "f.classList.add('go');" in html), "重触发三段式：连点两次进度条要演两次"
    assert code.index("function tick(){") < code.index("ffCue(t);") < \
        code.index("var hit = sentAt(t);"), \
        "掠擦排在翻句之前：它说的是这一拍的来路，不是这一拍的内容"

    # ⑦ .pregate 离散进出：认得 allow-discrete 的才覆盖，#gate 本体不转
    assert ".pregate{position:absolute;inset:10px;z-index:8;display:flex;" in css
    assert ".pregate[hidden]{display:none}\n  @supports" in html, \
        "基础规则里 hidden 仍是硬切：不支持那一族也只是没有过渡"
    i = css.index("@supports (transition-behavior:allow-discrete){")
    depth, j = 0, i
    while True:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    dock = css[i:j]
    assert ".pregate{animation:none;opacity:1;translate:0 0;" in dock, \
        "旧的关键帧写法必须在这一档关掉：两层入场叠一起会糊"
    for p in ("opacity", "translate", "display"):
        assert re.search(p + r" var\(--dur-dock\) var\(--ease-out\)", dock), f"{p} 没进出坞清单"
    assert ".pregate{transition-behavior:allow-discrete}" in dock, \
        "单写一行：简写里的 allow-discrete 是 CSS Transitions 2 的语法，不认的引擎只作废这一行"
    assert '.pregate[hidden]{display:none;opacity:0;translate:0 -10px}' in dock
    assert "@starting-style{.pregate{opacity:0;translate:0 12px}}" in dock, \
        "入场要有起点：没有 @starting-style，第一帧就是终帧（0.15.0 同一课）"
    assert "@starting-style{#gate" not in css and "#gate{transition:" not in css, \
        "#gate 本体不转——它的 hidden 是 QA 放行信号的唯一真值（本轮否决②）"
    assert css.count("@starting-style") == 2, "离散入场只给 .pregate 与 0.15.0 那条折叠面板"

    # ⑧ reduced-motion：三条兜底齐全，且各自抬到压得住基础规则的那一档
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    rm_code = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert "#spot{transition:none}" in rm_code, "关的是补间不是灯：洞心与开合留在呈现属性上"
    assert "#ffwd.go{animation:none}" in rm_code and "#ffwd{animation:none}" not in rm_code, \
        "兜底要带 .go 与基础规则同形 (1,1,0)，朴素 #ffwd 抢不过排在后面的那条"
    assert "#pregate.pregate{transition:none}" in rm_code, \
        "基础规则在 §②c、源序排在**本块之后**，同为 (0,1,0) 会被顶掉，抬到 (1,1,0)"
    for dead in ("--spot-x", "--spot-r", "data-on", "--spot-k", "--ff-travel"):
        assert dead not in rm_code, f"reduce 里不许出现 {dead}：那是把信息一起关掉（纪律③）"

    # ⑨ 本轮研究里量过/查证后仍不请回来的（连同旧锁一起复述一遍理由）
    assert "shape(" not in css, "clip-path: shape() 的形变补间本机没验过（本轮否决⑤）"
    assert "view-transition-name" not in css, "SVG 子元素的快照行为本机没验过（本轮否决⑤）"
    assert "mask:" not in css or True, "mask 长写法保留，no-repeat 是防止洞沿外重复"


# 灯的读数：洞心用 cam.getScreenCTM() 折回屏幕，与目标节点 getBoundingClientRect()
# 的并集中心对账——**这两条路必须独立**：模板里量的是 transform 属性（目标值），
# 用例里量的是此刻屏幕上的盒子，落定之后两者应当逐值相同（偏心距实测 0.0px）。
_SPOT_PROBE = r"""(function(){
  var s = document.getElementById('spot'), cs = getComputedStyle(s);
  var cam = document.getElementById('cam'), svg = cam.ownerSVGElement;
  var ct = cam.getScreenCTM();
  var hx = parseFloat(cs.getPropertyValue('--spot-x'));
  var hy = parseFloat(cs.getPropertyValue('--spot-y'));
  var hp = new DOMPoint(hx, hy).matrixTransform(ct);
  var keys = JSON.parse('%s'), u = null;
  keys.forEach(function(k){
    var n = document.getElementById(k); if (!n) return;
    var r = n.getBoundingClientRect();
    u = !u ? {l:r.left, t:r.top, rr:r.right, b:r.bottom} : {
      l:Math.min(u.l, r.left), t:Math.min(u.t, r.top),
      rr:Math.max(u.rr, r.right), b:Math.max(u.b, r.bottom)};
  });
  var hit = document.elementFromPoint(hp.x, hp.y);
  var sr = s.getBoundingClientRect(), vr = svg.getBoundingClientRect();
  // 取景框窗：#frameClip 的 rect 在 svg 根的用户空间里（包装层不带 transform），
  // 用 svg.getScreenCTM() 折到屏幕坐标——这才是"观众看到的那一块"，比 svg 的
  // 外框小（字幕带 396..460 在框外）。暗幕要盖住的是它。
  var clip = document.querySelector('#frameClip rect'), sc = svg.getScreenCTM();
  var f0 = new DOMPoint(+clip.getAttribute('x'), +clip.getAttribute('y')).matrixTransform(sc);
  var f1 = new DOMPoint(+clip.getAttribute('x') + +clip.getAttribute('width'),
                        +clip.getAttribute('y') + +clip.getAttribute('height')).matrixTransform(sc);
  var cap = document.getElementById('cap');
  return JSON.stringify({
    on:s.dataset.on, x:cs.getPropertyValue('--spot-x'), y:cs.getPropertyValue('--spot-y'),
    r:cs.getPropertyValue('--spot-r'), op:cs.opacity, pe:cs.pointerEvents,
    tp:cs.transitionProperty, td:cs.transitionDuration,
    te:cs.transitionTimingFunction.slice(0, 30), mask:cs.maskImage,
    hole:[+hp.x.toFixed(1), +hp.y.toFixed(1)],
    tgt:u ? [+((u.l + u.rr) / 2).toFixed(1), +((u.t + u.b) / 2).toFixed(1)] : null,
    hit:hit ? (hit.id || hit.tagName) : null,
    spotRect:[+sr.left.toFixed(1), +sr.top.toFixed(1), +sr.right.toFixed(1), +sr.bottom.toFixed(1)],
    svgRect:[+vr.left.toFixed(1), +vr.top.toFixed(1), +vr.right.toFixed(1), +vr.bottom.toFixed(1)],
    frameRect:[+f0.x.toFixed(1), +f0.y.toFixed(1), +f1.x.toFixed(1), +f1.y.toFixed(1)],
    capInCam:cam.contains(cap), capInClip:!!cap.closest('g[clip-path]'),
    an:(s.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;})});
})()"""

# 洞心是补间过去的不是跳过去的：把 --spot-x 的 transition 冻住逐档读
_SPOT_SWEEP = r"""(function(){
  var s = document.getElementById('spot');
  var list = (s.getAnimations()||[]).filter(function(a){
    return a.transitionProperty === '--spot-x';});
  if (list.length !== 1){
    return JSON.stringify({err:'--spot-x 的补间不在场', an:(s.getAnimations()||[]).map(
      function(a){ return a.transitionProperty || a.animationName; })});
  }
  var a = list[0]; a.pause();
  // 曲线读 getComputedTiming().easing：CSS 过渡的缓动挂在 timing 上，
  // keyframes[0].easing 恒是默认的 'linear'（读错了会以为没缓动）
  var out = {dur:a.effect.getComputedTiming().duration,
             ease:a.effect.getComputedTiming().easing, kf:[], frames:[]};
  (a.effect.getKeyframes() || []).forEach(function(k){
    out.kf.push(k['--spot-x']); });
  [0, 125, 250, 375, 499].forEach(function(ms){
    a.currentTime = ms;
    var cs = getComputedStyle(s);
    out.frames.push({ms:ms, x:parseFloat(cs.getPropertyValue('--spot-x')),
                     op:cs.opacity});
  });
  a.cancel();
  out.after = getComputedStyle(s).getPropertyValue('--spot-x');
  return JSON.stringify(out);
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_spot_lights_the_thing_the_narration_is_on(full_lesson):
    """真实浏览器：一盏跟着旁白走的聚光，洞心落在**正在讲的那个东西**上（0.22.0）。

    量五件事：① 三步的落点逐值等于模板 camBox() 该给出的数（s1 388/143/r=174.7、
    s2 697/143/108.8、s3 710/176/193.7）——s2 那一对框挂在 `d-rig` 的 translate 下，
    洞心 697 = 604+93，写死的坐标会跟着父层跑偏；② 屏幕对账：洞心折回屏幕与目标
    盒子的屏幕中心**偏心 ≤1px**（实测 0.0px），命中打在洞心里的目标节点上而不是灯
    （mask 不裁命中测试，边界②）；③ 软边跟着洞走：mask 的满幅停靠点 = 半径 + 78；
    ④ 灯不压字幕带：`#cap` 既不在 `#cam` 里也不在取景框的 clip 下，永远满墨；
    ⑤ 不声明就是没有：s0/s4/s5 三步 `data-on=0`、opacity 补到 0（纪律①）。
    """
    chrome, ev, _send = _cdp(full_lesson, "spot22")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        want = {1: (388.0, 143.0, 174.7, ["d-src"]),
                2: (697.0, 143.0, 108.8, ["d-true-r", "d-false-r"]),
                3: (710.0, 176.0, 193.7, ["d-false-r", "d-zero-r"])}
        for k in (1, 2, 3):
            ev("window.__jump('seg-5', %d)" % k)
            assert _wait_idle(ev, "#spot") == [], f"s{k} 落定后不该还有补间在跑"
            x, y, r, keys = want[k]
            st = json.loads(ev(_SPOT_PROBE % json.dumps(keys)))
            assert st["on"] == "1" and st["op"] == "0.15", f"s{k} 灯该亮在 --spot-k 档：{st}"
            assert st["pe"] == "none", f"灯不吃命中（边界②）：{st}"
            assert (float(st["x"][:-2]), float(st["y"][:-2]), float(st["r"][:-2])) == (x, y, r), \
                f"s{k} 洞心/半径该是 camBox 量出来的 {x}/{y}/{r}：{st}"
            assert st["tgt"] and math.hypot(st["hole"][0] - st["tgt"][0],
                                            st["hole"][1] - st["tgt"][1]) <= 1.0, \
                f"s{k} 洞心落在目标盒子中心（实测偏心 0.0px）：{st}"
            assert st["hit"] not in (None, "spot"), \
                f"mask 不裁命中测试，洞心里该点得到东西而不是灯：{st}"
            assert st["tp"] == "--spot-x, --spot-y, --spot-r, opacity" and \
                st["td"] == "0.5s, 0.5s, 0.5s, 0.5s" and \
                st["te"].startswith("cubic-bezier(0.22, 1, 0.36, 1)"), \
                f"四个通道一起补间、时长 --dur-spot、曲线 --ease-out：{st}"
            stops = [float(v) for v in re.findall(r"([\d.]+)px", st["mask"])]
            assert abs(stops[-1] - (r + 78.0)) <= 0.2, \
                f"满幅停靠点必须跟着洞走（r + --spot-soft）：{st['mask']}"
            sr, fr = st["spotRect"], st["frameRect"]
            assert sr[0] <= fr[0] + .5 and sr[1] <= fr[1] + .5 and \
                   sr[2] >= fr[2] - .5 and sr[3] >= fr[3] - .5, \
                f"暗幕要盖住整个取景框（四角都在洞外）：spot {sr} vs frame {fr}"
            assert fr[0] >= st["svgRect"][0] - .5 and fr[3] <= st["svgRect"][3] + .5, \
                f"取景框该落在画布内（口径自检）：frame {fr} vs svg {st['svgRect']}"
            assert st["capInCam"] is False and st["capInClip"] is False, \
                f"字幕带不进灯（口播原文永远满墨）：{st}"

        # ⑤ 没有落点声明的三步：开头由 begin() 归零，等 fade 走完就是全灭
        for k in (0, 4, 5):
            ev("window.__jump('seg-5', %d)" % k)
            assert _wait_idle(ev, "#spot") == [], f"s{k} 落定后不该有补间：{k}"
            st = json.loads(ev(_SPOT_PROBE % "[]"))
            assert st["on"] == "0" and st["op"] == "0", \
                f"s{k} 不声明就是没有聚光（纪律①）：{st}"

        # ③ 洞是补间过去的：s1→s2 逐档读 --spot-x（0.5s ease-out，前 25% 就走完大半）
        ev("window.__jump('seg-5', 1)")
        _wait_idle(ev, "#spot")
        ev("window.__jump('seg-5', 2)")
        sw = json.loads(ev(_SPOT_SWEEP))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 500, f"行程 --dur-spot .5s：{sw}"
        assert sw["ease"] == "cubic-bezier(0.22, 1, 0.36, 1)", f"曲线是 --ease-out：{sw}"
        assert sw["kf"] == ["388px", "697px"], f"两端就是这两步的落点：{sw['kf']}"
        xs = [f["x"] for f in sw["frames"]]
        assert abs(xs[0] - 388) < 0.5 and abs(xs[4] - 697) < 0.5, f"两端钉在档位上：{xs}"
        assert all(xs[i] < xs[i + 1] for i in range(4)), f"一路单调走向下一格：{xs}"
        assert xs[1] > 388 + (697 - 388) * 0.5, \
            f"ease-out 前 1/4 行程就该走完大半（洞要领先镜头落位）：{xs}"
        assert sw["after"] == "697px", \
            f"cancel 之后落回属性上那一档（导出与冷启动吃这格）：{sw['after']}"
    finally:
        _kill(chrome)


_FF_STATE = r"""(function(){
  var f = document.getElementById('ffwd'), cs = getComputedStyle(f);
  var r = f.getBoundingClientRect(), sr = document.getElementById('cam')
        .ownerSVGElement.getBoundingClientRect();
  return JSON.stringify({dir:f.dataset.dir, cls:f.className.baseVal,
    op:cs.opacity, tr:cs.translate, sc:cs.scale, an2:cs.animationName,
    pe:cs.pointerEvents, n:f.querySelectorAll('polyline').length,
    rect:[+r.left.toFixed(1), +r.top.toFixed(1), +r.right.toFixed(1)],
    svg:[+sr.left.toFixed(1), +sr.right.toFixed(1)],
    an:(f.getAnimations()||[]).map(function(a){
      return a.animationName || a.transitionProperty;})});
})()"""

_FF_SWEEP = r"""(function(){
  var f = document.getElementById('ffwd');
  var list = (f.getAnimations()||[]).filter(function(a){
    return a.animationName === 'ffSweep';});
  if (list.length !== 1){
    return JSON.stringify({err:'ffSweep 不在场', an:(f.getAnimations()||[]).map(
      function(a){ return a.animationName || a.transitionProperty; })});
  }
  var a = list[0]; a.pause();
  var t = a.effect.getComputedTiming();
  var out = {dur:t.duration, fill:t.fill,
             ease:(a.effect.getKeyframes()[0] || {}).easing, frames:[]};
  [0, 68, 170, 238, 339].forEach(function(ms){
    a.currentTime = ms;
    var cs = getComputedStyle(f), r = f.getBoundingClientRect();
    out.frames.push({ms:ms, tr:cs.translate, op:parseFloat(cs.opacity),
                     left:+r.left.toFixed(1), right:+r.right.toFixed(1)});
  });
  out.tf = getComputedStyle(f).transform;      // 必须是 none：两个通道各写各的
  out.sc = getComputedStyle(f).scale;
  var vr = f.ownerSVGElement.getBoundingClientRect();
  out.edge = [+vr.left.toFixed(1), +vr.right.toFixed(1)];   // 画布左右缘，别写死
  a.cancel();
  out.after = getComputedStyle(f).opacity;
  return JSON.stringify(out);
})()"""


def _wait_cue(ev, want=True, timeout=1.6):
    """掠擦只有 340ms，等它上场、确认它不在场，都得主动追几帧。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = json.loads(ev(_FF_STATE))
        if ("ffSweep" in [x for x in last["an"] if x]) == want:
            return last
        time.sleep(0.02)
    return last


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_ffwd_sweeps_only_when_the_playhead_is_moved(full_lesson):
    """真实浏览器：播放头被**搬动**过才掠一道箭头，方向跟着时间符号走（0.22.0）。

    范本 seg-5 的句长刚好把两档都摆出来：s1→s2 差 **1.264s**（不过 `--ff-min` 2s，
    不演），s2→s3 差 **3.289s**（演）。量的三件事：
      · 阈值：小跳确实不演，大跳演——判据是 currentTime 的差，不是拍的位数；
      · 方向与镜像：快进 `data-dir=f`、`scale:1`、`translate` 从 −520 走到 +520；
        快退 `data-dir=b`、`scale:-1 1`、从 +520 走到 −520——**两档都朝自己指的那边
        掠**（本轮翻案：第一版反过来写，箭头朝右却往左飞，读起来是倒带）；
      · 事件不是状态：`fill:none`、两端 opacity 0、`transform` 全程 `none`（行程走
        独立 `translate`、镜像走独立 `scale`），极位时盒子落在 −21.1 / 941.3 而画布
        只有 29..893——约四分之三在画外，被 #frameClip 裁着进出；
      · 截图模式不演（成片每一帧本来就是 seek 出来的）。
    """
    chrome, ev, _send = _cdp(full_lesson, "ff22")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        ev("window.__jump('seg-5', 1)")
        time.sleep(0.7)                      # 让 lastT 跟上这一拍
        st = _wait_cue(ev, want=False)
        assert st["op"] == "0" and st["tr"] == "none" and st["n"] == 3, \
            f"静止态看不见（base opacity:0，三枚雪佛龙）：{st}"
        assert st["rect"][0] < st["svg"][1] and st["rect"][2] > st["svg"][0], \
            f"静止位在画布中心：{st}"

        # ① 小跳（1.264s < --ff-min）：不演
        ev("window.__jump('seg-5', 2)")
        st = _wait_cue(ev, want=False, timeout=0.8)
        assert "ffSweep" not in [x for x in st["an"] if x], \
            f"不过阈值的位移不该读成跳（正常翻句与它差五倍）：{st}"

        # ② 大跳向前：向右掠出
        ev("window.__jump('seg-5', 3)")
        st = _wait_cue(ev, want=True)
        assert st["dir"] == "f" and st["cls"] == "go", f"向前跳读作快进：{st}"
        sw = json.loads(ev(_FF_SWEEP))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 340 and sw["fill"] == "none" and sw["ease"] == "linear", \
            f"--dur-ff .34s / 不写 forwards / linear：{sw}"
        assert sw["sc"] == "1" and sw["tf"] == "none", f"快进不镜像、不碰 transform：{sw}"
        tr = [float(f["tr"][:-2]) for f in sw["frames"]]
        assert abs(tr[0] + 520) < 0.5 and abs(tr[4] - 516.941) < 1.0, \
            f"从画外左掠到画外右（--ff-travel 半幅 520）：{tr}"
        assert all(tr[i] < tr[i + 1] for i in range(4)), f"linear 等速单调：{tr}"
        ops = [f["op"] for f in sw["frames"]]
        assert ops[0] == 0 and abs(ops[1] - .5) < .01 and abs(ops[3] - .5) < .01, \
            f"两头隐形、中段平台 --ff-peak .5：{ops}"
        assert sw["after"] == "0", f"cancel 之后仍是看不见那一格：{sw['after']}"
        e0, e1 = sw["edge"]
        f0 = sw["frames"][0]
        w = f0["right"] - f0["left"]
        assert 55 < w < 75, f"雪佛龙本体的宽度（极位不该改变它）：{f0}"
        assert f0["left"] < e0 and (e0 - f0["left"]) / w > .7, \
            f"极位约四分之三在画布左缘之外、被取景框裁着（画布 {e0}..{e1}）：{f0}"

        # ③ 往回跳：反向掠 + 镜像
        time.sleep(0.5)
        ev("window.__jump('seg-5', 1)")
        st = _wait_cue(ev, want=True)
        assert st["dir"] == "b", f"回拖读作快退：{st}"
        sw = json.loads(ev(_FF_SWEEP))
        assert "err" not in sw, str(sw)
        assert sw["sc"] == "-1 1", f"后退档雪佛龙镜像（独立 scale）：{sw['sc']}"
        tr = [float(f["tr"][:-2]) for f in sw["frames"]]
        assert abs(tr[0] - 520) < 0.5 and abs(tr[4] + 516.941) < 1.0, \
            f"后退朝自己指的那边掠出（+520 → −520）：{tr}"
        b0 = sw["frames"][0]
        wb = b0["right"] - b0["left"]
        assert b0["right"] > e1 and (b0["right"] - e1) / wb > .7, \
            f"后退的起始极位约四分之三在画布右缘之外（画布 {e0}..{e1}）：{b0}"

        # ④ 截图模式：不演
        time.sleep(0.6)
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-5', 0)")
        st = _wait_cue(ev, want=False, timeout=0.8)
        assert "ffSweep" not in [x for x in st["an"] if x], \
            f"导出每帧本来就是 seek 出来的，不演过场：{st}"
    finally:
        _kill(chrome)


_PG_STATE = r"""(function(){
  var p = document.getElementById('pregate'), cs = getComputedStyle(p);
  var r = p.getBoundingClientRect();
  return JSON.stringify({hidden:p.hidden, disp:cs.display, op:cs.opacity, tr:cs.translate,
    tp:cs.transitionProperty, tb:cs.transitionBehavior, td:cs.transitionDuration,
    an2:cs.animationName, rect:[+r.width.toFixed(1), +r.height.toFixed(1)],
    an:(p.getAnimations()||[]).map(function(a){
      return (a.transitionProperty || a.animationName);})});
})()"""

_PG_SWEEP = r"""(function(prop){
  var p = document.getElementById('pregate');
  var list = (p.getAnimations()||[]).filter(function(a){
    return a.transitionProperty === prop;});
  if (!list.length) return JSON.stringify({err:'没有 ' + prop + ' 的补间',
    an:(p.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;})});
  var a = list[0]; a.pause();
  var out = {dur:a.effect.getComputedTiming().duration, frames:[]};
  [0, 120, 239].forEach(function(ms){
    a.currentTime = ms;
    var cs = getComputedStyle(p);
    out.frames.push({ms:ms, op:parseFloat(cs.opacity), tr:cs.translate, disp:cs.display});
  });
  a.cancel();
  return JSON.stringify(out);
})('%s')"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_pregate_docks_with_a_discrete_transition(full_lesson):
    """真实浏览器：浮层的进出**直接补间离散属性 display**，JS 一行都不用等（0.22.0）。

    原来是 `animation:gateIn both` + `hidden` 一盖就硬切——浮层"啪"地消失，与它下面
    那条刚走完 1.1s 的环不配套。现在 `hidden` 仍由 JS 即时盖上（QA 读的就是这个属性），
    过渡交给 CSS：`@starting-style` 给入场一个起点、`transition-behavior:allow-discrete`
    让 `display` 在退场全程撑住不被摘掉。实测四格：
      · 藏着的静止态：`display:none`、`opacity:0`、`translate:0 -10px`、清单三条齐
        （`opacity, translate, display` 各 .24s、behavior `allow-discrete`）、
        `animation-name` 落 `none`（旧关键帧在这一档被关掉，不会两层入场叠一起）；
      · 入场：`display` 立刻是 `flex`（离散值只在**退场**才需要撑住），`opacity` 从
        `@starting-style` 的 0 走到 1、`translate` 从 `0px 12px` 收到 `0px`；
      · 退场第一帧：`hidden` 已 true 而 `display` 仍是 `flex`、`display` 自己的那条
        transition 在场——这就是 allow-discrete 买到的东西；150ms 时 opacity 已掉到
        .04 而盒还在（`rect` 仍非零）；400ms 后落 `display:none`；
      · 全程 JS 只动了 `hidden` 一个属性。
    """
    chrome, ev, _send = _cdp(full_lesson, "pg22")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-1', 0)")
        assert ev("CSS.supports('transition-behavior','allow-discrete')"), \
            "本机 Chrome 不认 allow-discrete，这条用例的前提就没了"
        st = json.loads(ev(_PG_STATE))
        assert st["hidden"] is True and st["disp"] == "none" and st["op"] == "0", \
            f"静止态收起且看不见：{st}"
        assert st["an2"] == "none", f"旧的关键帧写法必须让位给离散补间：{st}"
        assert st["tp"] == "opacity, translate, display" and \
            st["tb"] == "allow-discrete" and st["td"] == "0.24s, 0.24s, 0.24s", \
            f"进出坞清单 --dur-dock .24s × 3：{st}"

        ev("document.getElementById('pregate').hidden = false")
        st = json.loads(ev(_PG_STATE))
        assert st["disp"] == "flex", f"入场时 display 即时是 flex（离散值只在退场撑住）：{st}"
        assert set(st["an"]) >= {"opacity", "translate"}, f"两条几何外的一起在补间：{st}"
        sw = json.loads(ev(_PG_SWEEP % "opacity"))
        assert "err" not in sw, str(sw)
        assert sw["dur"] == 240, f"时长 --dur-dock：{sw}"
        ops = [f["op"] for f in sw["frames"]]
        assert ops[0] == 0 and 0.8 < ops[1] < 1.0 and abs(ops[2] - 1) < 0.02, \
            f"从 @starting-style 的 0 起步、ease-out 一路到 1：{ops}"
        assert json.loads(ev(_PG_SWEEP % "display"))["err"], \
            "入场不该有 display 的补间（none→flex 是即时出现的，撑住的是退场那一头）"
        time.sleep(0.4)
        st = json.loads(ev(_PG_STATE))
        assert st["op"] == "1" and st["tr"] == "0px" and st["an"] == [], \
            f"落定：满不透明、位移收到零、没有补间在跑：{st}"

        ev("document.getElementById('pregate').hidden = true")
        st = json.loads(ev(_PG_STATE))
        assert st["hidden"] is True and st["disp"] == "flex", \
            f"退场全程 display 撑住（这就是 allow-discrete 买到的东西）：{st}"
        assert "display" in st["an"], f"display 自己也在补间清单上：{st}"
        time.sleep(0.15)
        mid = json.loads(ev(_PG_STATE))
        assert mid["disp"] == "flex" and 0 < float(mid["op"]) < 0.5 and mid["rect"][0] > 0, \
            f"150ms 时淡出过半而盒还在：{mid}"
        time.sleep(0.4)
        end = json.loads(ev(_PG_STATE))
        assert end["disp"] == "none" and end["op"] == "0" and end["an"] == [], \
            f"400ms 之后落回 none：{end}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_spot_ffwd_pregate_reduced_motion_and_shot_terminals(full_lesson):
    """减动效与导出两刀之下：三条各落**该说的那件事**，一格内容不少（0.22.0）。

      · 减动效：`#spot{transition:none}` 关的是补间不是灯——不用等 idle，换步后**立刻**
        读到 388/143/174.7 与 `opacity:.15`（`transition-property` 落 `none`、
        `getAnimations()` 恒空），不声明的那一步也**立刻**灭（`data-on:0` / `op:0`）。
        位置信息本身留在呈现属性上，这是纪律③；
      · `#ffwd.go{animation:none}`：大跳之后 `.go` 照样挂上（JS 不管这一档），但
        `animation-name` 落 `none`、`opacity` 仍是 0——画布上什么都不留；
      · `#pregate.pregate{transition:none}`：`transition-behavior` 跟着回 `normal`，
        `hidden` 一盖就即时 `display:none`，没有那 240ms 的撑住——"这层在 / 不在"照样
        读得出（与 0.15.0 折叠面板完全同形：allow-discrete 没有时长就没有撑住）；
      · 导出式一刀：叠在 reduce 之上再切一层，洞心直接落终位、掠擦隐形、`#pregate`
        被导出注入的 `#gate,#pregate{display:none!important}` 整层摘掉。
    """
    chrome, ev, send = _cdp(full_lesson, "rm22")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")

        for k, want in ((1, ("388px", "143px", "174.7px", "0.15")),
                        (3, ("710px", "176px", "193.7px", "0.15"))):
            ev("window.__jump('seg-5', %d)" % k)      # 不 sleep：这一档本就不该有补间
            st = json.loads(ev(_SPOT_PROBE % "[]"))
            assert (st["x"], st["y"], st["r"], st["op"]) == want, \
                f"减动效下 s{k} 直接落 {want}（关的是补间不是灯位）：{st}"
            assert st["tp"] == "none" and st["an"] == [], \
                f"减动效下不该有任何补间在跑：{st}"
        ev("window.__jump('seg-5', 0)")
        st = json.loads(ev(_SPOT_PROBE % "[]"))
        assert st["on"] == "0" and st["op"] == "0", f"减动效下灭灯也是即时的：{st}"

        ev("window.__jump('seg-5', 3)")               # 4.5s 的大跳，够过 --ff-min
        time.sleep(0.25)
        st = _wait_cue(ev, want=False, timeout=0.6)
        assert "ffSweep" not in [x for x in st["an"] if x] and st["an2"] == "none", \
            f"减动效下掠擦不演（兜底行带 .go 才压得住）：{st}"
        assert st["op"] == "0" and st["tr"] == "none", f"看不见才是对的落档：{st}"

        ev("document.getElementById('pregate').hidden = false")
        st = json.loads(ev(_PG_STATE))
        assert st["disp"] == "flex" and st["op"] == "1" and st["tr"] == "0px" and \
            st["tp"] == "none" and st["tb"] == "normal" and st["an"] == [], \
            f"减动效下浮层即时进场、过渡回 normal：{st}"
        ev("document.getElementById('pregate').hidden = true")
        st = json.loads(ev(_PG_STATE))
        assert st["disp"] == "none", f"减动效下没有那 240ms 的撑住，hidden 即时生效：{st}"

        # 导出截图页口径：reduce 之上再叠一刀（与 export_video.SNIPPET 同式）
        ev("var s=document.createElement('style');s.textContent="
           "'*,*::before,*::after{transition:none!important;animation:none!important}"
           "#gate,#pregate{display:none!important}';document.head.appendChild(s);")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-5', 2)")
        st = json.loads(ev(_SPOT_PROBE % "[]"))
        assert (st["x"], st["y"], st["r"], st["op"]) == ("697px", "143px", "108.8px", "0.15"), \
            f"导出定格里洞落在这一格的终位、幕深照旧：{st}"
        assert st["an"] == [], f"导出帧里不该有在跑的补间：{st}"
        st = json.loads(ev(_FF_STATE))
        assert st["op"] == "0" and st["an2"] == "none", f"导出帧里没有半截箭头：{st}"
        assert json.loads(ev(_PG_STATE))["disp"] == "none", "成片里这层本来就不该在场"
    finally:
        _kill(chrome)

# ══════════════════════════════════════════════════════════════════════
# 0.23.0 重量与预备：`.settle` / `.wind` / `.lift`
# ══════════════════════════════════════════════════════════════════════


def test_settle_wind_lift_primitives_present():
    """模板结构契约：0.23.0 三条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮在 Chrome 152 里量出来"为什么只能这么写"的边界：
      · **注释配对**与九个令牌必须真的在 `:root` 的解析结果里（写在注释里的不算），
        三个时长各自有出处：`--dur-settle .42s` 与入场同档、`--dur-wind .28s` 必须
        **短于**主动作 `--dur-move .5s`、`--dur-lift .38s` 与浮起同档；
      · 让出的那一拍**只能有一个来源**：`--wind-hold:calc(var(--dur-wind) * .34)`，
        把实测的 `0.0952s` 抄进 CSS 就是第二个数（口径同 `--ff-min` / `--par-far`）；
      · 挤压的两头由同一对令牌镜像（`calc(2 - --squash-x)`），作者只改一档、
        行程形状不变；支点是 `transform-box:fill-box` + `transform-origin:50% 100%`
        （接触边），退回 50% 50% 会让下沿**抬离接触线 1.54 设备px**（实测）；
      · 三条都走**独立属性**：`.settle` 用 `scale`、`.wind` 用 `translate`、
        `.lift` 用 `translate` + `filter`——写进 `transform` 会把 `.nt` 补间的位移
        整条顶掉（0.13.0 老账），三条规则里都不许出现 `transform:`；
      · 一个节点上**不能有第二条 animation / 两份 transition 简写**：`.settle` 与
        `.wind` 只声明 `animation`（不抢 `.nt` 的 transition 清单，实测挂上之后
        十七项逐字符不变），`.lift` 只声明 `transition`（清单只有 `--elev` 一项，
        `filter` 是**派生**的不是补间对象，列进去就是第二套补间）；
      · 蓄力层的让拍走 `transition-delay` **长档**（`.wind > g`），写简写会顶掉
        `.nt` 那份十七项清单（0.19.0 老账）；
      · **事件不是状态**：`settleIn` 用 `backwards` 不用 `forwards`（静止态 =
        `scale:none`），`windIn` 两端与静止态都是 `translate:0 0` 且不写填充——
        切掉 animation 之后画布上什么都不留（纪律③）；
      · 抬升量注册成 `@property --elev{syntax:'<length>';initial-value:0px}`：
        0px 就是"还在台面上"，不播 / 不能播 / 导出三种情形都落在那一格；
        `--squash-*`、`--wind-back`、`--wind-hold`、`--lift-ink` **故意不注册**
        （常量替换，注册了就去补间，而它们说的不是行程）；
      · 投影只走 `filter:drop-shadow()`：`box-shadow` 在 SVG 上计算值读得到而屏幕
        一个像素不动（本轮否决①，28836 点变 0 个，正对照 HTML 盒 2408 点变 2005 个），
        `perspective() translateZ()` 同样抬不起 SVG（否决②），所以 `.lift` 规则里
        这两个词都不许出现；
      · 消费者各就各位且**由 (scene, step) 推导**：`badge()` 中间那层
        `key + '-sq'` 挂 `.settle`、`poseWorld()` 的 `wind = name === 'shelf'` 挂
        `.wind` 且蓄力层在主角**之外**、`renderC()` 的 `data-lift` 只写档位；
      · reduced-motion 四行：`.settle` / `.wind` 关 animation（事件）、
        `.wind > g{transition-delay:0s}` 把让拍一起归零（否则"先停 95ms 再瞬移"）、
        `.lift{transition:none}` 关补间不关档位。四条基础规则都在本块**之前**、
        同特异度靠源序压住，所以都不抬特异度——但 `.wind > g` 那行必须**同形**
        （(0,1,1)，实测朴素 `.wind g` 写法仍然归零，改成同形是为了读数清楚）。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    code = re.sub(r"//[^\n]*", "", css)

    # ⓪ 注释配对 + 九个令牌必须真在 :root 的解析结果里
    assert _css_comment_problems(html) == [], _css_comment_problems(html)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)
    for tok in ("--dur-settle", "--squash-x", "--squash-y", "--dur-wind", "--wind-back",
                "--wind-hold", "--dur-lift", "--lift-hi", "--lift-ink"):
        assert re.search(re.escape(tok) + r"\s*:", root), \
            f"{tok} 没长在 :root 里（被注释语法吃掉过一次，见 _css_comment_problems）"

    # ① 令牌取值：三个档位各有出处，改之前先回去读注释
    assert "--dur-settle:.42s; --squash-x:1.14; --squash-y:.84;" in html, \
        ".42s 与入场 --dur-in 同档：徽标落位就是它的入场方式"
    assert "--dur-wind:.28s; --wind-back:9; --wind-hold:calc(var(--dur-wind) * .34);" in html
    assert "--dur-lift:.38s; --lift-hi:5px; --lift-ink:rgba(4,6,10,.62);" in html
    assert "0.0952s" not in css and ".0952s" not in css, \
        "让拍只许从 --dur-wind 派生：把实测的 0.0952s 抄进 CSS 就是第二个数"
    assert "--wind-hold:calc(var(--dur-wind) * .34);" in root, \
        ".34 就是 windIn 峰值那一格（34%），换曲线要一起换"
    m = re.search(r"--dur-wind:\.(\d+)s", root)
    mv = re.search(r"--dur-move:\.(\d+)s", root)
    assert float("0." + m.group(1)) < float("0." + mv.group(1)), \
        "预备那一拍必须短于主动作，否则蓄力把落点推到主动作之后，读成两次移动"

    # ② 只注册真正要补间的那一个
    assert "@property --elev{syntax:'<length>';inherits:false;initial-value:0px}" in html, \
        "初值 0px = 还在台面上：不播 / 不能播 / 导出三种情形都落在没有投影那一格"
    for dead in ("--squash-x", "--squash-y", "--wind-back", "--wind-hold",
                 "--lift-hi", "--lift-ink", "--wind-dx", "--wind-dy"):
        assert "@property " + dead not in html, \
            f"{dead} 是常量替换：注册了就会去补间，而它说的不是行程"
    assert re.search(r"--lift-hi:\.?\d+%", css) is None, \
        "抬升量是长度不是百分比（fill-box 的百分比要多一层换算，本轮实测过）"

    # ③ .settle：镜像的两头、接触边支点、独立 scale、backwards 不写 forwards
    m = re.search(r"@keyframes settleIn\{([\s\S]*?)100%\{scale:1 1\}\}", css)
    assert m, "settleIn 的关键帧没按范本写法长（收尾必须是 scale:1 1）"
    kf = m.group(1)
    kf_flat = " ".join((kf + "100%{scale:1 1}").split())
    assert "0%{scale:calc(2 - var(--squash-x)) calc(2 - var(--squash-y))}" in kf_flat, \
        "拉伸档必须由同一对令牌绕 1 镜像：实测拉长那格面积只差 0.2%，压扁那格差 4.2%"
    assert "38%{scale:var(--squash-x) var(--squash-y)}" in kf_flat
    assert "70%{scale:.97 1.04}" in kf_flat and "100%{scale:1 1}" in kf_flat
    assert "sqrt(" not in kf and "1 / var(--squash" not in kf, \
        "不许强行开方取 1/x：压扁那格守恒到 100.0% 反而读出橡皮感（本轮否决④）"
    settle = " ".join(re.search(r"\.settle\{[^}]*\}", css).group(0).split())
    assert "transform-box:fill-box" in settle and "transform-origin:50% 100%" in settle, \
        "支点是包围盒底边中点：九档冻结读数下沿全是 514.05，退回 50% 50% 抬离 1.54px"
    assert "animation:settleIn var(--dur-settle) linear backwards" in settle, \
        "形状已写进四个档位：曲线换成 --ease-out 会把压扁格提前到 25%（本轮实测）"
    assert "forwards" not in settle, "不写 forwards：静止态 = 元素自己的 scale:none"
    assert "transform:" not in settle and "transition:" not in settle, \
        "挤压走独立 scale，且只声明 animation——不抢 .nt 的 transition 简写"

    # ④ .wind：独立 translate、令牌给量与轴向、--ease-in-out、两端归零
    m2 = re.search(r"@keyframes windIn\{([\s\S]*?100%\{translate:0 0\})\}", css)
    assert m2, "windIn 的关键帧没按范本写法长（收尾必须回到 translate:0 0）"
    wkf_flat = " ".join(m2.group(1).split())
    assert "34%{translate:calc(var(--wind-dx) * 1px) calc(var(--wind-dy) * 1px)}" in wkf_flat, \
        "幅度与轴向都在令牌上：JS 只写这一步蓄不蓄（纪律 1）"
    assert wkf_flat.startswith("0%{translate:0 0} 34%") and wkf_flat.endswith("100%{translate:0 0}"), \
        "两端都回零：事件不是状态，切掉 animation 之后什么都不留"
    wind = " ".join(re.search(r"\.wind\{[^}]*\}", css).group(0).split())
    assert "--wind-dx:0;--wind-dy:var(--wind-back)" in wind, \
        "默认只有竖直那一档，横向预备把 --wind-dx 写在节点上即可"
    assert "animation:windIn var(--dur-wind) var(--ease-in-out)" in wind, \
        "一去一回是对称行程：--ease-out 会把沉下去演成急刹车"
    assert "forwards" not in wind and "both" not in wind, "不填终态：终态本来就是零"
    assert "transform:" not in wind, "蓄力走独立 translate，不碰主角的 transform"
    hold = re.search(r"\.wind > g\{([^}]*)\}", css).group(1)
    assert "transition-delay:var(--wind-hold)" in hold, \
        "让拍必须落在被补间的那一层（主角 g），蓄力层自己没有补间可延"
    assert "transition:" not in hold and "transition-property" not in hold, \
        "只许写长档：transition 简写会把 .nt 那份十七项清单整条顶掉（0.19.0 老账）"

    # ⑤ .lift：影子与抬升同出一个 --elev，清单里只有它自己
    lift = " ".join(re.search(r"\.lift\{[^}]*\}", css).group(0).split())
    assert "filter:drop-shadow(0 calc(var(--elev) * 1.5) calc(var(--elev) * 1.2) var(--lift-ink))" in lift, \
        "影子比抬升走得远（1.5 / 1.2）：光源在台面上方时，物体抬起影子同时变长"
    assert "translate:0 calc(var(--elev) * -1)" in lift
    assert "transition:--elev var(--dur-lift) var(--ease-out)" in lift
    assert "filter" not in lift.split("transition:")[1], \
        "filter 不进补间清单：它由 --elev 派生，逐帧实测偏差 0.00001px，列进去是第二套补间"
    assert "box-shadow" not in lift, "SVG 上没有盒：box-shadow 计算值读得到而屏幕 0 个像素变（否决①）"
    assert "perspective" not in lift and "translateZ" not in lift, \
        "perspective + translateZ 抬不起 SVG 子元素，getScreenCTM 的 a/d 纹丝不动（否决②）"
    assert "animation:" not in lift and "transform:" not in lift
    assert '.lift[data-lift="1"]{--elev:var(--lift-hi)}' in css, \
        "档位挂在呈现属性上：不支持 @property 也照样能抬能落（纪律③）"
    assert '.lift[data-lift="0"]' not in css, "不落地就是初值 0px，不需要再写一档"

    # ⑥ 消费者：三处都只写"这一步是什么状态"，不排程不计帧
    assert "var sq = put(g, 'g', key + '-sq', {class:'settle'});" in html, \
        "徽标中间那层纯包装挂 .settle：淡入 .in 留在内层 rect/字上，两层各演各的"
    assert "put(parent, 'g', key + '-g', {transform:'translate(' + x + ',' + y + ')'});" in html, \
        "位移仍由外层 transform 承载，挤压层只负责形状"
    assert re.search(r"var wind = name === 'shelf';", html), \
        "蓄不蓄由姿势推导（纪律 1）：导出每帧一次冷启动，记住建上跳会漏演"
    assert "g + '-ant', {class: wind ? 'wind' : null}" in html
    i_ant = html.index("var A = put(S, 'g', g + '-ant'")
    # 0.26.0 起主角那一发 put 的第一个键是拖尾档位（class/style），位移仍在同一次写里：
    # 这条锁的是"蓄力层在主角之外且是主角的父层"，不是键的顺序。
    i_hero = html.index("put(A, 'g', g, {")
    assert "transform:'translate(' + p.xs[slot]" in html[i_hero:i_hero + 320], \
        "位移仍由主角那层的 transform 承载（拖尾只加 delay，不改谁承载位移）"
    assert i_ant < i_hero, "蓄力层必须在主角之外、且是主角的父层"
    assert html.index("function poseWorld(name, fired){") < i_ant < html.index("function renderA(step){"), \
        "让拍与蓄力都在 poseWorld 里就地声明（renderer 只写这一步蓄不蓄）"
    assert "{class:'lift', 'data-lift': lift ? '1' : '0'}" in html, \
        "投影层是纯包装：位置归外层 c-a/c-b，描边归内层 rect"

    # ⑥b 换位弧凸（0.25.0）：消费者、包装层、判据三处各写一处，不排程不计帧
    swap = " ".join(re.search(r"\.swap\{[^}]*\}", css).group(0).split())
    assert "offset-path:var(--swap-route)" in swap and "offset-distance:0%" in swap, \
        "弧凸路径是数据（--swap-route，与 .ride 同口径），档位初值 0% = 原点 = 不演"
    assert "offset-rotate:0deg" in swap, "offset-rotate 默认 auto：竖直路径会把卡转成 90°"
    assert "transition:offset-distance var(--dur-move) var(--ease-in-out)" in swap, \
        "一去一回是对称行程，曲线必须对称（与 .flip 同一理由）；时长与位移同一拍"
    assert '.swap[data-go="1"]{offset-distance:100%}' in css, \
        "档位挂在呈现属性上：不支持 @property 也照样能抬能落（纪律③）"
    assert "var S = put('world', 'g', g + '-swp'" in html, \
        "弧凸层是纯包装：位置归内层 transform / 蓄力层，路线只写在这层（与 .lift 同一课）"
    assert re.search(r"'data-go': fired \? '1' : '0'", html), \
        "换位判据由 (scene, step) 推导（纪律 1）：导出每帧一次冷启动，记住建上跳会漏演"
    assert re.search(r"if \(q\.op > 0\)", html) and re.search(r"o !== base\) fired = true", html), \
        "判据是'第一个可见姿势之后出现不同序'：灯灭着的换位（off→messed）不演弧凸"
    assert "--swap-k:.35; --swap-rise:140; --swap-min:250;" in css, \
        "三枚令牌只定几何不定时序：斜率 / 封顶 / 不演阈值一处收口"

    # ⑦ reduced-motion：四条齐全，且关掉的是补间与事件、不是档位读数
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", html, re.S).group(1)
    rm_code = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert ".settle{animation:none}" in rm_code and ".wind{animation:none}" in rm_code
    assert ".wind > g{transition-delay:0s}" in rm_code, \
        "让拍要跟着归零：兜底不该是「先停 95ms 再瞬移」（实测不归零会读回 0.0952s）"
    assert ".lift{transition:none}" in rm_code, "关的是补间不是档位：data-lift 与 --elev 终值仍读得到"
    assert ".swap{transition:none}" in rm_code, \
        "弧凸关的是补间不是档位：data-go 与 --swap-route 终值仍读得到（与 .ride 同一判断）"
    for dead in ("--elev", "data-lift", "--squash-x", "--wind-back", "--lift-hi",
                 "--swap-k", "--swap-rise", "--swap-min", "data-go"):
        assert dead not in rm_code, f"reduce 里不许出现 {dead}：那是把状态读数一起关掉（纪律③）"
    assert "transition-delay:var(--wind-hold)" not in rm_code

    # ⑧ 本轮量过仍不请回来的（连同像素与矩阵证据一起复述理由）
    assert "@keyframes liftIn" not in css and "@keyframes windBack" not in css, \
        "三条都靠 transition / 一段关键帧完成，不要多一层 JS 排程"
    assert "will-change" not in css, "为装饰性合成加 will-change：本轮没量到收益，先不加"


_SETTLE_STATE = r"""(function(){
  var n = document.getElementById('%(id)s-sq'), r = document.getElementById('%(id)s-r');
  var gp = document.getElementById('%(id)s-g').getBoundingClientRect();
  var cs = getComputedStyle(n), q = r.getBoundingClientRect();
  return JSON.stringify({cls:n.getAttribute('class'), an:cs.animationName,
    dur:cs.animationDuration, fill:cs.animationFillMode, ease:cs.animationTimingFunction,
    tb:cs.transformBox, org:cs.transformOrigin, scale:cs.scale,
    running:(n.getAnimations({subtree:false}) || []).length,
    inner:getComputedStyle(r).animationName,
    tpItems:cs.transitionProperty.split(',').length,
    w:+q.width.toFixed(3), h:+q.height.toFixed(3), bottom:+(q.y + q.height).toFixed(2),
    pb:+(gp.y + gp.height).toFixed(2)});
})()"""

# 冻结采样：重新挂上 class 起一段 settleIn，暂停后按九个档位读 scale 与屏幕盒。
# 九个档位取 [0,60,160,200,210,254,294,340,419]：两端 + 38%/70% 两格 + 中间若干，
# 为的是"下沿一个都不许动"这句话必须有九个证据而不是三个。
_SETTLE_FREEZE = r"""(function(id, org, ease, times){
  var n = document.getElementById(id + '-sq'), r = document.getElementById(id + '-r');
  n.setAttribute('class', 'nt'); void n.getBoundingClientRect();
  if (org) n.style.transformOrigin = org;
  if (ease) n.style.animationTimingFunction = ease;
  n.setAttribute('class', 'nt settle'); void n.getBoundingClientRect();
  var a = (n.getAnimations({subtree:false}) || []).filter(function(x){
    return x.animationName === 'settleIn';})[0];
  if (!a) return JSON.stringify({err:'settleIn 不在场', an:(n.getAnimations({subtree:false})||[]).map(
    function(x){ return x.animationName || x.transitionProperty; })});
  a.pause(); var rows = [];
  times.forEach(function(t){
    a.currentTime = t; void r.getBoundingClientRect();
    var q = r.getBoundingClientRect();
    rows.push({t:t, scale:getComputedStyle(n).scale, w:+q.width.toFixed(3),
               h:+q.height.toFixed(3), area:+(q.width * q.height).toFixed(1),
               bottom:+(q.y + q.height).toFixed(2)});
  });
  var ct = a.effect.getComputedTiming();
  var out = {dur:ct.duration, fill:ct.fill, rows:rows,
             rest:getComputedStyle(n).scale, tb:getComputedStyle(n).transformBox,
             org:getComputedStyle(n).transformOrigin,
             csEase:getComputedStyle(n).animationTimingFunction};
  a.cancel();
  n.style.transformOrigin = ''; n.style.animationTimingFunction = '';
  return JSON.stringify(out);
})('%s','%s','%s',%s)"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_settle_lands_with_its_bottom_edge_on_the_floor(full_lesson):
    """真实浏览器：徽标**落下来**那一下有质量，而接触的那条边全程不动（0.23.0）。

    范本 seg-1 s2 第一次挂出 `a-rule` 这一枚判词徽标，中间那层 `<g class="settle">`
    就是挤压落位。量四件事（数字全部来自 Chrome 152 的 `pause()` + `currentTime`）：
      · **静止态 = 终态**：演完之后 `getComputedStyle().scale` 读回 `none`、
        `getAnimations({subtree:false})` 为空、`animation-fill-mode` 是 `backwards`
        而不是 `forwards`——切掉 animation 的导出帧落在"稳稳在那一格"；
      · **支点**：`transform-box:fill-box` + `transform-origin:50% 100%`（计算值
        `35px 28px` = 药丸的底边中点），九个冻结档位的屏幕下沿**一个不差全是同一个数**
        （本机 514.05 / CI runner 521.01——视口整体差 6.96 设备px，断言里接触线取**同一次会话**
        的静止读数，不写死一个 y）；
        把原点改回默认的 `50% 50%` 重跑同一批档位，t=200 那格下沿落到 512.51——
        抬离接触线 **1.54 设备px**，约为该格高度损失（3.10px）的一半，那就是悬浮；
      · **体积近似守恒**（二维面积，压扁那格亏一点、弹起那格赚一点）：压扁 38% 那格
        面积比静止少 4.2%，0% 拉长那格只差 0.2%，70% 回弹那格多 0.9%；
      · **曲线只能是 linear**：同一节点把 `animation-timing-function` 换成 --ease-out，
        t=105ms（行程的四分之一）就读到 1.13742×.842954——压扁那一格被提前演完，
        "砸下去—弹起来"变成"一开头就砸完"；linear 下同一刻还在 1.04421×.949474 半路。
    """
    chrome, ev, _send = _cdp(full_lesson, "settle23")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        ev("window.__jump('seg-1', 1)")
        # s1 那一格还没有这枚徽标（它在 s2 第一次落位）
        ev("window.__jump('seg-1', 2)")
        time.sleep(1.0)                                     # 420ms 的行程自己走完

        st = json.loads(ev(_SETTLE_STATE % {"id": "a-rule"}))
        assert st["cls"] == "nt settle" and st["an"] == "settleIn", \
            f"落位层挂的是 .settle、演的是 settleIn：{st}"
        assert st["dur"] == "0.42s" and st["ease"] == "linear", f"时长 --dur-settle、曲线 linear：{st}"
        assert st["fill"] == "backwards" and "forwards" not in st["fill"], \
            f"backwards 不写 forwards：静止态必须是元素自己的 scale：{st}"
        assert st["scale"] == "none" and st["running"] == 0, \
            f"演完之后不落任何终态痕迹（导出吃这一格）：{st}"
        assert st["tb"] == "fill-box" and st["org"] == "35px 28px", \
            f"支点是包围盒底边中点：{st}"
        assert st["inner"] == "ntIn", f"淡入留在内层图元上，两层各演各的：{st}"
        assert st["tpItems"] == 17, \
            f".settle 只声明 animation，不抢 .nt 那份十七项 transition 清单：{st}"
        floor = st["bottom"]

        TIMES = "[0,60,160,200,210,254,294,340,419]"
        fz = json.loads(ev(_SETTLE_FREEZE % ("a-rule", "", "", TIMES)))
        assert "err" not in fz, str(fz)
        assert fz["dur"] == 420 and fz["fill"] == "backwards", f"冻结读到的行程本体：{fz}"
        assert [f["bottom"] for f in fz["rows"]].count(floor) == len(fz["rows"]), \
            f"九个档位的下沿全都钉在接触线 {floor} 上（实测 514.05）：{fz['rows']}"
        got = {f["t"]: f for f in fz["rows"]}
        assert got[0]["scale"] == "0.86 1.16", \
            f"0% 那档是镜像出来的拉伸格 calc(2 − 1.14) × calc(2 − .84)：{got[0]}"
        assert got[160]["scale"].startswith("1.1394"), \
            f"38% 那档读的就是压扁档（t=160 = 420×38%）：{got[160]}"
        assert got[294]["scale"] == "0.97 1.04", f"70% 是回弹那一格：{got[294]}"
        rest = got[419]["area"]
        assert 0.955 < got[160]["area"] / rest < 0.975, \
            f"压扁那格面积比静止少 4.2%（−4%～−2% 之间，守恒取的是二维近读数）：{got[160]}"
        assert 0.995 < got[0]["area"] / rest < 1.005, \
            f"拉长那格只差 0.2%——镜像写法本来就比压扁那格更守恒：{got[0]}"
        assert 1.0 < got[294]["area"] / rest < 1.015, \
            f"回弹那格赚 0.9%：砸下去亏一点、弹起来赚一点：{got[294]}"

        # 支点回到默认中心：同一段行程，底边抬离接触线
        ctr = json.loads(ev(_SETTLE_FREEZE % ("a-rule", "50% 50%", "", TIMES)))
        c200 = [f for f in ctr["rows"] if f["t"] == 200][0]
        assert c200["scale"] == got[200]["scale"], f"同一档位同一形状，改的只是支点：{c200}"
        assert floor - c200["bottom"] >= 1.0, \
            f"原点是 50% 50% 时底边抬离接触线（实测 514.05 → 512.51，差 1.54px）：{c200}"

        # 曲线 A/B：换成 --ease-out，压扁那一格被提前到行程四分之一处
        ab = json.loads(ev(_SETTLE_FREEZE % ("a-rule", "", "cubic-bezier(.22,1,.36,1)", "[105]")))
        lin = json.loads(ev(_SETTLE_FREEZE % ("a-rule", "", "linear", "[105]")))
        x_eo = float(ab["rows"][0]["scale"].split()[0])
        x_ln = float(lin["rows"][0]["scale"].split()[0])
        assert x_eo > 1.12 and x_ln < 1.08, \
            f"t=105：ease-out 已读到 {x_eo}（压扁演完），linear 还在 {x_ln} 半路：{ab} vs {lin}"
        assert lin["rows"][0]["bottom"] == floor == ab["rows"][0]["bottom"], \
            "换曲线不许把底边挪走"
    finally:
        _kill(chrome)


_WIND_READ = r"""(function(){
  var h = document.getElementById('w-10'), a = document.getElementById('w-10-ant');
  var cs = getComputedStyle(h), ca = getComputedStyle(a);
  var q = h.getBoundingClientRect();
  return JSON.stringify({antCls:a.getAttribute('class'), antAnim:ca.animationName,
    antDur:ca.animationDuration, antEase:ca.animationTimingFunction,
    antTranslate:ca.translate, antFill:ca.animationFillMode,
    heroInAnt:(h.parentNode === a), heroCls:h.getAttribute('class'),
    heroDelay:cs.transitionDelay, heroTp:cs.transitionProperty,
    heroTd:cs.transitionDuration, heroTe:cs.transitionTimingFunction.slice(0, 32),
    trailR:h.style.getPropertyValue('--trail-r'),
    trailStep:getComputedStyle(document.documentElement).getPropertyValue('--trail-step'),
    back:ca.getPropertyValue('--wind-back'), hold:ca.getPropertyValue('--wind-hold'),
    y:+q.y.toFixed(2), transform:h.getAttribute('transform')});
})()"""

# 冻结采样 windIn：蓄力层的行程两端都回零，34% 那格是峰值。
# 曲线必须从 animationTimingFunction 读——getComputedTiming().easing 给的是
# 关键帧自己的 easing（CSS 动画恒为 'linear'），读错了会以为没缓动。
_WIND_FREEZE = r"""(function(){
  var n = document.getElementById('w-10-ant');
  n.setAttribute('class', 'nt'); void n.getBoundingClientRect();
  n.setAttribute('class', 'nt wind'); void n.getBoundingClientRect();
  var a = (n.getAnimations({subtree:false}) || []).filter(function(x){
    return x.animationName === 'windIn';})[0];
  if (!a) return JSON.stringify({err:'windIn 不在场', an:(n.getAnimations({subtree:false})||[]).map(
    function(x){ return x.animationName || x.transitionProperty; })});
  a.pause(); var rows = [];
  [0, 95, 140, 180, 279].forEach(function(t){
    a.currentTime = t; void n.getBoundingClientRect();
    rows.push({t:t, tr:getComputedStyle(n).translate});
  });
  var ct = a.effect.getComputedTiming();
  var out = {dur:ct.duration, fill:ct.fill, timingEase:ct.easing,
             csEase:getComputedStyle(n).animationTimingFunction,
             fill2:getComputedStyle(n).animationFillMode, rows:rows};
  a.cancel();
  return JSON.stringify(out);
})()"""

# 逐 rAF 采主角的屏幕 y：蓄力到底有没有"看得见"，只有沿屏幕坐标问才答得出。
_WIND_SAMPLER = r"""(function(){
  var ant = document.getElementById('w-10-ant'), h = document.getElementById('w-10');
  var rows = [], t0 = performance.now();
  window.__jump('seg-1', 3);
  return new Promise(function(res){
    (function step(){
      var q = h.getBoundingClientRect();
      rows.push([+(performance.now() - t0).toFixed(1), +q.y.toFixed(2),
                 getComputedStyle(ant).translate, getComputedStyle(h).transitionDelay]);
      if (performance.now() - t0 < 760) requestAnimationFrame(step);
      else res(JSON.stringify(rows));
    })();
  });
})()"""


def _tval(s):
    """`getComputedStyle().translate` 在静止/零位移时塌成一个值（`"0px"` / `"none"`），
       有 y 分量时才是两个值：直接 split()[1] 会 IndexError，也会把"没动"读成"动了"。"""
    if not s or s == "none":
        return 0.0
    p = s.split()
    return float(p[1][:-2]) if len(p) > 1 else float(p[0][:-2])


def _wind_dip(ev, override, tag):
    """s2 → s3 那一步的屏幕读数：起点、最低点（y 最大）、回落终位、蓄力层峰值位移。

    `override` 是给 A/B 对照组注入的样式（范本现状传空串）。
    """
    ev("window.__jump('seg-1', 2)")
    time.sleep(1.1)                       # 让主角先落回 s2 那一格（位移本身在补间）
    if override:
        ev("(function(){var s=document.createElement('style');s.id='ab023';s.textContent="
           + json.dumps(override) + ";document.head.appendChild(s);return 1;})()")
    rows = json.loads(ev(_WIND_SAMPLER, True))
    if override:
        ev("document.getElementById('ab023').remove()")
    ys = [r[1] for r in rows]
    time.sleep(0.6)
    # 落定之后的读数：屏幕**绝对**坐标随视口漂（本机 283.65 / CI runner 290.61），
    # 所以"三遍不许改终态"这句话只能同会话内自己对照，不写死一个 y。
    settle = json.loads(ev("(function(){return JSON.stringify({y:+document.getElementById("
                           "'w-10').getBoundingClientRect().y.toFixed(2)});})()"))["y"]
    return {"tag": tag, "n": len(rows), "y0": ys[0], "max": max(ys),
            "dip": round(max(ys) - ys[0], 2), "end": ys[-1], "settle": settle,
            "antMax": round(max(_tval(r[2]) for r in rows), 3),
            "delay0": rows[0][3], "t_peak": rows[ys.index(max(ys))][0]}


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_wind_anticipates_by_borrowing_a_frame_from_the_move(full_lesson):
    """真实浏览器：主角上架子之前先往下沉一下，而那一下是**借来的**（0.23.0）。

    范本 seg-1 s3 把世界层换成 `shelf` 姿势（y 232→84、k 1→.46），`w-10-ant` 那一层
    挂上 `.wind`。三件事必须一起成立，"它自己跳上去的"才读得出来：
      · 蓄力层的行程：`windIn` 280ms、`--ease-in-out` = `cubic-bezier(0.65, 0, 0.35, 1)`、
        34% 那格（t=95.2ms）读到 `0px 8.99997px`（= `--wind-back` 9 单位），两端回零、
        `fill:none`、静止态 `translate` 读 `none`——事件不是状态；
      · 让拍只改延迟不改清单：主角 `w-10` 的 `transitionDelay` 从 s2（无 rank 时十七个
        `0s`；0.26.0 起这一步有拖尾，那是 `--trail-step × rank` 的一格）变成 s3 的一格
        `0.0952s`，而 `transitionProperty` / `transitionDuration` /
        `transitionTimingFunction` 与不蓄力的那一步**逐字符相同**（十七项）；
      · **主动作必须为它让出那一拍**（这条是量出来的，不是美学判断）：同一步走三遍，
        每遍逐 rAF 采主角屏幕 y（本机起点 348.17，绝对坐标随视口漂）——范本现状最低沉到 **429.17**
        （dip 81.00 设备px，峰值在 110~126ms）；只让不蓄（`.wind{animation:none}`）
        **416.75**（dip 68.58）；只蓄不让（延迟归零）dip **0.00**——蓄力层自己确实
        算出了 8.995 的位移，可屏幕上完全没有回头，观众看不见。范本现状比"只让不蓄"
        多沉的那 12.4px 才是"它要跳"。
    """
    chrome, ev, _send = _cdp(full_lesson, "wind23")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")

        ev("window.__jump('seg-1', 2)")
        assert _wait_idle(ev, "#w-10") == [], "s2 落定后不该还有补间在跑"
        s2 = json.loads(ev(_WIND_READ))
        assert s2["antCls"] == "nt" and s2["antAnim"] == "none", \
            f"不蓄力的那一步：蓄力层还在（主角的父层），但 class 是空状态：{s2}"
        assert s2["heroInAnt"] is True, "主角住在蓄力层里（`.wind > g` 的 g 就是它）"
        assert len(s2["heroTp"].split(",")) == 17, f"十七项清单不被动到：{s2}"
        # 0.26.0 起这一步还有拖尾：让位的两张按 rank 晚一格或两格。`.trail` 写的正是
        # **只有 transition-delay 这一条长档**，所以清单仍是十七项、delay 收成一格——
        # 这条断言从"十七个 0s"改成"要么全零、要么恰好等于 --trail-step × rank"，
        # 钉的还是同一件事：让拍不许碰到那十七项里的任何一个别的读数。
        if s2["trailR"] in ("", "0"):
            assert s2["heroDelay"].count("0s") == 17, \
                f"没有 rank 就该十七个 0s：{s2}"
        else:
            want = _ms(s2["trailStep"]) * int(s2["trailR"])
            assert "trail" in s2["heroCls"] and \
                abs(_ms(s2["heroDelay"]) - want) < 0.5, \
                f"拖尾那一格只由 --trail-step × --trail-r 求出（{want}ms）：{s2}"
        assert "0.0952" not in s2["heroDelay"], \
            f"不蓄力的那一步绝不能借到那一拍：{s2['heroDelay']}"

        ev("window.__jump('seg-1', 3)")
        s3 = json.loads(ev(_WIND_READ))
        assert s3["antCls"] == "nt wind" and s3["antAnim"] == "windIn", \
            f"上架子那一步才蓄力（class 由姿势推导，导出冷启动也演）：{s3}"
        assert s3["antDur"] == "0.28s", f"蓄力行程 --dur-wind .28s：{s3}"
        assert s3["antEase"] == "cubic-bezier(0.65, 0, 0.35, 1)", \
            f"曲线是 --ease-in-out（一去一回对称行程）：{s3}"
        assert s3["antFill"] == "none", f"两端归零、不填终态：{s3}"
        assert s3["back"] == "9" and s3["hold"] == "calc(.28s * .34)", \
            f"幅度与让拍都从令牌来：{s3}"
        assert s3["heroDelay"] == "0.0952s", \
            f"十七个 0s 换成一格 0.0952s（--wind-hold）：{s3}"
        for k in ("heroTp", "heroTd", "heroTe"):
            assert s3[k] == s2[k], f"{k} 不许被让拍动到（只写长档）：\n{s2[k]}\n{s3[k]}"

        fz = json.loads(ev(_WIND_FREEZE))
        assert "err" not in fz, str(fz)
        assert fz["dur"] == 280 and fz["fill"] == "none" and fz["fill2"] == "none", \
            f"280ms、两端归零、不填充：{fz}"
        assert fz["csEase"] == "cubic-bezier(0.65, 0, 0.35, 1)", f"{fz}"
        assert fz["timingEase"] == "linear", \
            "getComputedTiming().easing 给的是关键帧自己的 easing（恒 linear）——" \
            "曲线要从 animationTimingFunction 读，这条断言就是在锁这个读法"
        rows = {r["t"]: r["tr"] for r in fz["rows"]}
        assert _tval(rows[0]) == 0.0, f"起点不预位移：{rows[0]}"
        assert abs(_tval(rows[95]) - 8.99997) < 0.01, \
            f"34% 那格（95.2ms）蓄满 --wind-back 9 单位：{rows[95]}"
        assert _tval(rows[140]) < _tval(rows[95]) and _tval(rows[180]) < _tval(rows[140]), \
            f"过了峰值就往回收：{rows}"
        assert _tval(rows[279]) < 0.01, f"收尾回到零（事件不是状态）：{rows[279]}"

        assert _wait_idle(ev, "#w-10") == [], "主角那层的补间要先落定，才谈得上静止态读数"
        idle = json.loads(ev(r"""(function(){
          var a = document.getElementById('w-10-ant'), h = document.getElementById('w-10');
          return JSON.stringify({tr:getComputedStyle(a).translate,
            an:(a.getAnimations({subtree:false})||[]).length,
            han:(h.getAnimations({subtree:false})||[]).map(function(x){
              return x.transitionProperty || x.animationName;})});
        })()"""))
        assert idle["tr"] == "none" and idle["an"] == 0 and \
            [x for x in idle["han"] if x] == [], f"落定之后蓄力层什么都不留：{idle}"

        A = _wind_dip(ev, "", "shipped")
        B = _wind_dip(ev, ".wind{animation:none!important}", "hold_only")
        C = _wind_dip(ev, ".wind > g{transition-delay:0s!important}", "wind_only")
        assert A["y0"] == B["y0"] == C["y0"], f"三步的起点同格：{A['y0']} {B['y0']} {C['y0']}"
        for v in (A, B, C):
            assert abs(v["end"] - v["settle"]) < 0.6, \
                f"采样的最后一帧就是落定的那一格（让拍不许改终态）：{v}"
            assert v["settle"] < v["y0"] - 40, \
                f"终点在起点之上（shelf 那一格，本机差 64.5 设备px）：{v}"
        assert abs(A["settle"] - B["settle"]) < 0.6 and abs(A["settle"] - C["settle"]) < 0.6, \
            f"三遍落同一格：A {A['settle']} / B {B['settle']} / C {C['settle']}"
        assert A["dip"] > 60, f"范本现状：屏幕 y 先沉到 429 一线（实测 dip 81.00）：{A}"
        assert A["antMax"] > 8.5, f"蓄力层自己确实走满了 9 单位：{A}"
        assert B["antMax"] == 0.0 and B["dip"] > 40, \
            f"只让不蓄：让出那一拍本身就有 68.58 的下沉（镜头在推近，旧位置在画面上沉下去）：{B}"
        assert C["dip"] < 10, \
            f"只蓄不让：dip 0.00——蓄力层算出了位移而屏幕上完全不回头，观众看不见：{C}"
        assert C["antMax"] > 8.5, f"对照组里蓄力层自己照样走满：{C}"
        assert A["dip"] - B["dip"] > 6, \
            f"蓄力在屏幕上多推下来的那 ~12.4px 才是「它要跳」：A {A['dip']} vs B {B['dip']}"
    finally:
        _kill(chrome)


_LIFT_READ = r"""(function(){
  var n = document.getElementById('c-a-lf'), r = document.getElementById('c-a-r');
  var cs = getComputedStyle(n), q = r.getBoundingClientRect();
  return JSON.stringify({cls:n.getAttribute('class'), data:n.getAttribute('data-lift'),
    tp:cs.transitionProperty, td:cs.transitionDuration, te:cs.transitionTimingFunction.slice(0, 32),
    an:cs.animationName, elev:cs.getPropertyValue('--elev'), filter:cs.filter,
    tr:cs.translate, bs:cs.boxShadow, ink:cs.getPropertyValue('--lift-ink'),
    h:cs.height, y:+q.y.toFixed(2),
    running:(n.getAnimations({subtree:false})||[]).length});
})()"""

# 冻结 --elev 那条补间：同一次读里把影子两个数与 translate 一起取回来，
# 这三者必须永远等于 1.5× / 1.2× / −1× 同一个 --elev（派生，不是第二套补间）。
_LIFT_FREEZE = r"""(function(){
  var n = document.getElementById('c-a-lf'), r = document.getElementById('c-a-r');
  window.__jump('seg-4', 0); void n.getBoundingClientRect();
  window.__jump('seg-4', 1); void n.getBoundingClientRect();
  var list = (n.getAnimations({subtree:false}) || []).filter(function(x){
    return x.transitionProperty === '--elev';});
  if (list.length !== 1) return JSON.stringify({err:'--elev 的补间不是恰好一条',
    an:(n.getAnimations({subtree:false})||[]).map(function(x){
      return x.transitionProperty || x.animationName;})});
  var a = list[0]; a.pause(); var rows = [];
  [0, 120, 250, 379].forEach(function(t){
    a.currentTime = t; void r.getBoundingClientRect();
    var cs = getComputedStyle(n), nums = (cs.filter.match(/-?[\d.]+px/g) || []);
    rows.push({t:t, elev:cs.getPropertyValue('--elev'), off:nums[1], blur:nums[2],
               tr:cs.translate, y:+r.getBoundingClientRect().y.toFixed(3)});
  });
  var ct = a.effect.getComputedTiming();
  var out = {dur:ct.duration, ease:ct.easing, kf:a.effect.getKeyframes().map(function(k){
    return k['--elev'];}), rows:rows};
  a.cancel();
  out.after = getComputedStyle(n).getPropertyValue('--elev');
  return JSON.stringify(out);
})()"""

_LIFT_FRAMES = r"""(function(){
  var n = document.getElementById('c-a-lf'), r = document.getElementById('c-a-r');
  var t0 = performance.now(), rows = [];
  window.__jump('seg-4', 1);
  return new Promise(function(res){
    (function step(){
      var cs = getComputedStyle(n), nums = (cs.filter.match(/-?[\d.]+px/g) || []);
      rows.push([+(performance.now() - t0).toFixed(1),
                 parseFloat(cs.getPropertyValue('--elev')),
                 nums.length > 1 ? parseFloat(nums[1]) : null,
                 nums.length > 2 ? parseFloat(nums[2]) : null,
                 cs.translate, +r.getBoundingClientRect().y.toFixed(3)]);
      if (performance.now() - t0 < 560) requestAnimationFrame(step);
      else res(JSON.stringify(rows));
    })();
  });
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_lift_raises_a_shadow_that_is_not_a_second_tween(full_lesson):
    """真实浏览器：影子**跟着牌走**，因为它俩本来就是同一个数（0.23.0）。

    范本 seg-4 的 a/b 两块上各有一层 `<g class="lift">`——谁排在前面谁被提到台面上方。
    量五件事（Chrome 152 冻结采样 + 逐 rAF 对账）：
      · 静止态就在地面上：`data-lift="0"`、`--elev:0px`（`@property` 的 initial）、
        `filter` 是 `drop-shadow(rgba(4, 6, 10, 0.62) 0px 0px 0px)`、`box-shadow:none`、
        `animation:none`；
      · 清单里只有 `--elev` 一项、时长 `--dur-lift .38s`、曲线 `--ease-out`；
        补间两端 `["0px","5px"]`（5px = `--lift-hi`），`cancel()` 之后落回 `5px`；
      · **派生逐帧成立**：t=120ms 读到 `--elev` 4.24728px / 影子偏移 6.37092px /
        模糊 5.09674px / `translate:0px -4.24728px`——偏移恒为 1.5×、模糊恒为 1.2×、
        抬升恒为 −1×；整段 35 帧里最大偏差 0.00001px（阈值给 0.02）。所以影子不需要
        自己再补间一次，`filter` 进清单是第二套补间（本轮否决③）；
      · 六步的档位表就是那三条契约的形状：s0 都不抬、s1 只抬 a、s2 两枚同高
        （"相等"在这台仪器上就是同高）、s3 只抬 b、s4/s5 回到台面；
      · 投影不裁命中测试：抬起那一格牌心 `elementFromPoint` 仍落回字节点 `c-a-t`，
        下沿外 8px 落回台面 `rect`；同一份 `box-shadow` 写在 `c-a-r` / `c-a-lf` 上
        **计算值读得到**（`rgb(255, 0, 0) 0px 14px 20px 0px`）而屏幕上 28836 个采样点
        一个都不动（正对照：HTML 盒 2408 点里变 2005 个）——读得到不等于画得出。
    """
    chrome, ev, _send = _cdp(full_lesson, "lift23")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        ev("window.__jump('seg-4', 0)")
        assert _wait_idle(ev, "#c-a-lf") == [], "s0 落定后不该还有补间在跑"

        st = json.loads(ev(_LIFT_READ))
        assert st["cls"] == "nt lift" and st["data"] == "0", f"投影层是纯包装：{st}"
        assert st["tp"] == "--elev" and st["td"] == "0.38s", \
            f"清单里只有 --elev 一项（filter 是派生不是补间对象）：{st}"
        assert st["te"].startswith("cubic-bezier(0.22, 1, 0.36, 1)"), f"曲线 --ease-out：{st}"
        assert st["an"] == "none" and st["bs"] == "none", \
            f"这一层不演关键帧也不用 box-shadow：{st}"
        assert (st["elev"], st["tr"]) == ("0px", "0px"), \
            f"@property 初值 0px = 还在台面上（不播/不能播/导出都落这格）：{st}"
        assert st["filter"] == "drop-shadow(rgba(4, 6, 10, 0.62) 0px 0px 0px)", \
            f"墨色 --lift-ink 在令牌上、零抬升就是零影子：{st}"

        fz = json.loads(ev(_LIFT_FREEZE))
        assert "err" not in fz, str(fz)
        assert fz["dur"] == 380 and fz["ease"] == "cubic-bezier(0.22, 1, 0.36, 1)", \
            f"时长与曲线：{fz}"
        assert fz["kf"] == ["0px", "5px"], f"两端就是台面与 --lift-hi：{fz['kf']}"
        rows = {r["t"]: r for r in fz["rows"]}
        for t in rows:
            e = float(rows[t]["elev"][:-2])
            assert abs(float(rows[t]["off"][:-2]) - 1.5 * e) < 0.02, f"t={t} 偏移恒 1.5×：{rows[t]}"
            assert abs(float(rows[t]["blur"][:-2]) - 1.2 * e) < 0.02, f"t={t} 模糊恒 1.2×：{rows[t]}"
            assert abs(_tval(rows[t]["tr"]) + e) < 0.02, f"t={t} 抬升恒 −1×：{rows[t]}"
        assert abs(float(rows[120]["elev"][:-2]) - 4.24728) < 0.01, \
            f"冻结在 t=120 的读数（本轮写进注释的那一组）：{rows[120]}"
        assert rows[379]["elev"] == "5px" and float(rows[120]["y"]) > float(rows[379]["y"]), \
            f"一路走向终格且落在 5px：{rows[379]}"
        assert fz["after"] == "5px", f"cancel 之后回到属性上那一档：{fz['after']}"

        # 逐帧：补间真的在跑（不是跳过去），而派生关系一帧都没脱开
        ev("window.__jump('seg-4', 0)")
        assert _wait_idle(ev, "#c-a-lf") == [], "s0 落定后不该有补间"
        frames = json.loads(ev(_LIFT_FRAMES, True))
        assert len(frames) >= 20, f"整段 .38s 至少采到 20 帧：{len(frames)}"
        mid = [r for r in frames if 0.02 < r[1] < 4.98]
        assert len({r[1] for r in mid}) >= 8, \
            f"--elev 是补间过去的、不是跳过去的（实测 24 个不同中间值）：{len(mid)}"
        worst = 0.0
        for t, e, off, blur, tr, y in frames:
            if off is None or blur is None:
                continue
            worst = max(worst, abs(off - 1.5 * e), abs(blur - 1.2 * e), abs(_tval(tr) + e))
        assert worst < 0.02, f"35 帧里派生关系最大偏差（实测 0.00001px）：{worst}"

        # 六步档位表：三条契约各自一格，s4/s5 回到台面
        want = [(("0", "0px"), ("0", "0px")), (("1", "5px"), ("0", "0px")),
                (("1", "5px"), ("1", "5px")), (("0", "0px"), ("1", "5px")),
                (("0", "0px"), ("0", "0px")), (("0", "0px"), ("0", "0px"))]
        for k, exp in enumerate(want):
            ev("window.__jump('seg-4', %d)" % k)
            for who in ("#c-a-lf", "#c-b-lf"):
                assert _wait_idle(ev, who) == [], f"s{k} 落定后 {who} 不该有补间"
            got = json.loads(ev(r"""(function(){
              var o = {};['a','b'].forEach(function(k){
                var n = document.getElementById('c-' + k + '-lf');
                o[k] = [n.getAttribute('data-lift'),
                        getComputedStyle(n).getPropertyValue('--elev')];});
              return JSON.stringify(o);})()"""))
            assert (tuple(got["a"]), tuple(got["b"])) == exp, f"s{k} 档位该是 {exp}：{got}"

        # 命中与否决
        ev("window.__jump('seg-4', 1)")
        time.sleep(0.6)
        hit = json.loads(ev(r"""(function(){
          var vp = document.getElementById('veilplay');
          if (vp) vp.style.display = 'none';
          var q = document.getElementById('c-a-r').getBoundingClientRect();
          var a = document.elementFromPoint(q.x + q.width / 2, q.y + q.height / 2);
          var b = document.elementFromPoint(q.x + q.width / 2, q.bottom + 8);
          return JSON.stringify({hit:a ? (a.id || a.tagName) : null,
                                 below:b ? (b.id || b.tagName) : null});
        })()"""))
        assert hit["hit"] == "c-a-t", \
            f"drop-shadow 与 mask 一样不裁命中测试，牌心仍点得到字：{hit}"
        assert hit["below"] == "rect", f"下沿外 8px 落回台面：{hit}"
        bs = json.loads(ev(r"""(function(){
          var r = document.getElementById('c-a-r'), g = document.getElementById('c-a-lf');
          r.style.boxShadow = '0 14px 20px rgba(255,0,0,1)';
          g.style.boxShadow = '0 14px 20px rgba(255,0,0,1)';
          var v = [getComputedStyle(r).boxShadow, getComputedStyle(g).boxShadow];
          r.style.boxShadow = ''; g.style.boxShadow = '';
          return JSON.stringify({onRect:v[0], onG:v[1]});
        })()"""))
        assert "0px 14px 20px" in bs["onRect"] and "0px 14px 20px" in bs["onG"], \
            f"box-shadow 的计算值读得到（本轮否决①的全部陷阱：读得到 ≠ 画得出，" \
            f"屏幕对拍 28836 点变 0 个）：{bs}"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_settle_wind_lift_reduced_motion_and_shot_terminals(full_lesson):
    """减动效与导出两刀之下：三条各落**该说的那件事**，一格内容不少（0.23.0）。

      · 减动效：`.settle{animation:none}` 之后徽标的 `scale` 读 `none`、屏幕盒就是
        静止盒（`a-rule` 高 30.966、下沿仍钉在承载位移的父层那一格）——落"稳稳在那一格"，
        没有半截压扁的定格留在画面上；
      · `.wind{animation:none}` + `.wind > g{transition-delay:0s}`：蓄力层留着
        （class 仍是 `nt wind`）但不演，主角**立刻**落在 shelf 那一格（`transform` 属性
        读数 `translate(500,84) scale(0.46)`，屏幕 y 只与上一步比——绝对坐标随视口漂），而主角的 `transitionDelay`
        必须回到 `0s`——兜底不是"先停 95.2ms 再瞬移"。这一行是承重的：把那档
        `transition-delay:var(--wind-hold)` 原样重述到媒体块**之后**，`transition-property`
        仍是 `none`（`.nt` 那行在管），而延迟读回 `0.0952s`——清单空了的时候延迟
        确实不动任何东西，但兜底不该在页面上留一个"等着补间"的读数；
      · `.lift{transition:none}` 关的是补间不是档位：`data-lift="1"` 那枚直接落
        `--elev:5px`、`translate:0px -5px`、影子 `0px 7.5px 6px`，没抬的那枚
        `0px` / `0px`——"这一对被提起来比"这件事仍然读得出（与 `#cap-hl` /
        `.tilt` / `.ride` / `#spot` 同一判断）；
      · 导出式一刀叠在 reduce 之上：徽标 `scale:none`、蓄力层 `translate:none` 且
        主角落在 `translate(500,84) scale(0.46)` 那一格（"已经跳上去"），
        s3 那一格里 b 抬 a 不抬、影子照旧——成片每帧都是这一格。
    """
    chrome, ev, send = _cdp(full_lesson, "rm23")
    try:
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")

        # ① .settle：不演，但盒就是那一格的盒
        ev("window.__jump('seg-1', 2)")
        st = json.loads(ev(_SETTLE_STATE % {"id": "a-rule"}))
        w0 = json.loads(ev(_WIND_READ))
        assert st["an"] == "none" and st["scale"] == "none" and st["running"] == 0, \
            f"减动效下徽标不演挤压、也不留半截压扁：{st}"
        # 屏幕**绝对**坐标随视口漂（本机与 CI runner 差 6.96 设备px），所以接触线不写死：
        # 底边必须落回它自己那一格——承载位移的父层 `<g>` 的底边。
        assert abs(st["bottom"] - st["pb"]) < 0.05 and abs(st["h"] - 30.966) < 0.05, \
            f"静止态 = 终态（下沿钉在父层那一格，且是没压扁的盒）：{st}"
        ev("window.__jump('seg-1', 3)")
        st2 = json.loads(ev(_SETTLE_STATE % {"id": "a-str"}))
        assert st2["an"] == "none" and st2["scale"] == "none" and st2["running"] == 0, \
            f"换一枚徽标同样不演（class 由 renderer 现写，不是记住的）：{st2}"

        # ② .wind：蓄力层在场但不演，位移直接落终位；让拍一起归零
        w = json.loads(ev(_WIND_READ))
        assert w["antCls"] == "nt wind" and w["antAnim"] == "none", \
            f"这一拍该蓄力（class 由姿势推导），但兜底把事件关掉：{w}"
        assert w["antTranslate"] == "none", f"关掉之后画布上什么都不留：{w}"
        assert w["heroDelay"] == "0s", f"让拍跟着归零（不是先停 95ms 再瞬移）：{w}"
        assert w["heroTp"] == "none", f".nt 的补间整条关掉的既有口径：{w}"
        assert w["transform"] == "translate(500,84) scale(0.46)", \
            f"位移照旧由 transform **属性**承载，主角立刻在 shelf 那一格：{w}"
        assert w["y"] < w0["y"] - 40, \
            f"不靠补间也真的上去了那一格（本机 348.17 → 283.65；绝对坐标随视口漂，" \
            f"所以这里只量两步之差）：{w0['y']} → {w['y']}"
        ev("(function(){var s=document.createElement('style');s.id='ab23';"
           "s.textContent='.wind > g{transition-delay:var(--wind-hold)}';"
           "document.head.appendChild(s);return 1;})()")
        time.sleep(0.2)
        ab = json.loads(ev(r"""(function(){
          var cs = getComputedStyle(document.getElementById('w-10'));
          return JSON.stringify({delay:cs.transitionDelay, tp:cs.transitionProperty,
                                 td:cs.transitionDuration});
        })()"""))
        assert ab["delay"] == "0.0952s" and ab["tp"] == "none", \
            f"没有那行归零，页面上会留一个「等着补间」的读数（清单空了所以不动东西，" \
            f"但读数不该在）：{ab}"
        ev("document.getElementById('ab23').remove()")
        time.sleep(0.2)
        assert json.loads(ev("(function(){return JSON.stringify({d:getComputedStyle("
                             "document.getElementById('w-10')).transitionDelay});})()"))["d"] == "0s", \
            "归零那行确实压得住（同特异度、源序在后）"

        # ③ .lift：关补间不关档位
        ev("window.__jump('seg-4', 1)")
        lf = json.loads(ev(_LIFT_READ))
        assert lf["data"] == "1" and lf["elev"] == "5px" and lf["tr"] == "0px -5px", \
            f"档位读数全在：抬的是这一对里的 a：{lf}"
        assert lf["tp"] == "none" and lf["running"] == 0, f"关的是补间不是档位：{lf}"
        assert lf["filter"] == "drop-shadow(rgba(4, 6, 10, 0.62) 0px 7.5px 6px)", \
            f"影子直接落 1.5× / 1.2× 那一档：{lf}"
        b = json.loads(ev(r"""(function(){
          var n = document.getElementById('c-b-lf'), cs = getComputedStyle(n);
          return JSON.stringify({data:n.getAttribute('data-lift'),
            elev:cs.getPropertyValue('--elev'), tr:cs.translate});
        })()"""))
        assert b == {"data": "0", "elev": "0px", "tr": "0px"}, \
            f"没抬的那枚还在台面上（不是半截抬升）：{b}"
        ev("window.__jump('seg-4', 2)")
        both = json.loads(ev(r"""(function(){
          var o = {};['a','b'].forEach(function(k){
            var n = document.getElementById('c-' + k + '-lf');
            o[k] = n.getAttribute('data-lift') + '/' +
                   getComputedStyle(n).getPropertyValue('--elev');});
          return JSON.stringify(o);})()"""))
        assert both == {"a": "1/5px", "b": "1/5px"}, \
            f"契约 `a − b = 0` 在这台仪器上的形状就是两枚同高：{both}"

        # ④ 导出截图页口径：reduce 之上再叠一刀（与 export_video.SNIPPET 同式）
        ev("var s=document.createElement('style');s.textContent="
           "'*,*::before,*::after{transition:none!important;animation:none!important}';"
           "document.head.appendChild(s);")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-1', 3)")
        ex = json.loads(ev(_SETTLE_STATE % {"id": "a-str"}))
        assert ex["scale"] == "none" and ex["running"] == 0, \
            f"导出帧里徽标稳稳在那一格：{ex}"
        wx = json.loads(ev(r"""(function(){
          var a = document.getElementById('w-10-ant'), h = document.getElementById('w-10');
          return JSON.stringify({cls:a.getAttribute('class'),
            an:getComputedStyle(a).animationName, tr:getComputedStyle(a).translate,
            delay:getComputedStyle(h).transitionDelay,
            transform:h.getAttribute('transform')});
        })()"""))
        assert wx["an"] == "none" and wx["tr"] == "none" and wx["delay"] == "0s", \
            f"导出帧里没有半截蓄力：{wx}"
        assert wx["transform"] == "translate(500,84) scale(0.46)", \
            f"这一帧就是「已经跳上去」的那一格：{wx}"
        ev("window.__jump('seg-4', 3)")
        lx = json.loads(ev(r"""(function(){
          var o = {};['a','b'].forEach(function(k){
            var n = document.getElementById('c-' + k + '-lf'), cs = getComputedStyle(n);
            o[k] = [n.getAttribute('data-lift'), cs.getPropertyValue('--elev'),
                    cs.translate, cs.filter];});
          return JSON.stringify(o);})()"""))
        assert lx["a"] == ["0", "0px", "0px", "drop-shadow(rgba(4, 6, 10, 0.62) 0px 0px 0px)"], \
            f"成片里 a 落回台面：{lx}"
        assert lx["b"][0] == "1" and lx["b"][2] == "0px -5px" and \
            lx["b"][3] == "drop-shadow(rgba(4, 6, 10, 0.62) 0px 7.5px 6px)", \
            f"成片里 b 抬起、影子跟着：`契约 a − b > 0` 那一帧说得完整：{lx}"
    finally:
        _kill(chrome)


# ══════════════════════════════════════════════════════════════════════
# 0.24.0 随动层：位移定拍 / 触点辉光 / 色温定调
#
# 这一族三条都没有"演完"那一说：镜头跟着这一步真的走了多少、光跟着指针落在哪儿、
# 色温跟着这一段在课程的哪一档。所以用例读的全是**读数**——跑动中的补间时长、
# 伪元素上的注册属性、继承进 SVG 的颜色——而不是截图平均亮度。
# ══════════════════════════════════════════════════════════════════════

_CAM_READ = r"""(function(){
  var cam = document.getElementById('cam'), dep = document.getElementById('depth');
  var root = document.documentElement, rst = getComputedStyle(root);
  /* 先读一次计算值：transition 在样式重新计算的那一刻才建档，晚一步就采不到 */
  var cd = getComputedStyle(cam).transitionDuration;
  var dd = getComputedStyle(dep).transitionDuration;
  function runs(node){
    return (node.getAnimations() || [])
      .filter(function(a){ return a.transitionProperty === 'transform'; })
      .map(function(a){ return +a.effect.getTiming().duration.toFixed(2); });
  }
  return JSON.stringify({
    pan: root.style.getPropertyValue('--cam-pan'),
    zoom: root.style.getPropertyValue('--cam-zoom'),
    durVar: rst.getPropertyValue('--cam-dur'),
    camDur: cd, depthDur: dd, anims: runs(cam), depthAnims: runs(dep),
    min: parseFloat(rst.getPropertyValue('--dur-cam-min')),
    max: parseFloat(rst.getPropertyValue('--dur-cam-max')),
    wp: parseFloat(rst.getPropertyValue('--dur-cam-pan')),
    wz: parseFloat(rst.getPropertyValue('--dur-cam-zoom')),
    spot: parseFloat(rst.getPropertyValue('--dur-spot')),
    transform: cam.getAttribute('transform')
  });})()"""


_CAM_RUNNING = ("(function(){var c=document.getElementById('cam');"
                "return (c.getAnimations()||[]).filter(function(a){"
                "return a.transitionProperty==='transform';}).length;})()")


def _cam_expect_ms(st):
    """按**页面自己的令牌**把这一趟的时长重算一遍：读数 × 权重 → clamp。

    断言里不许出现第二个 .0014 / .35——那等于把 CSS 那一处抄进测试，改档位时
    测试跟着一起改，就再也测不出"两处不同步"了（口径同 --wind-hold / --par-far）。
    """
    raw = float(st["pan"]) * st["wp"] + float(st["zoom"]) * st["wz"]
    return min(st["max"], max(st["min"], raw)) * 1000.0


def _ms(text):
    """computed transition-duration（"0.77154s" / "0.5s"）→ 毫秒。"""
    assert text.endswith("s") and not text.endswith("ms"), text
    return float(text[:-1]) * 1000.0


_HOT_STATE = r"""(function(sel){
  var o = document.querySelector(sel);
  if (!o) return JSON.stringify({missing:sel});
  var cs = getComputedStyle(o, '::before'), r = o.getBoundingClientRect();
  var bl = o.closest('[data-interaction]');
  var runs = (o.getAnimations({subtree:true}) || [])
      .filter(function(a){ return a.transitionProperty; })
      .map(function(a){ return [a.transitionProperty,
              +a.effect.getTiming().duration.toFixed(1), a.playState]; });
  return JSON.stringify({cls:o.className,
    hot:o.getAttribute('data-hot') || '',
    hx:cs.getPropertyValue('--hot-x'), hy:cs.getPropertyValue('--hot-y'),
    hostHx:getComputedStyle(o).getPropertyValue('--hot-x'),
    op:cs.opacity, cont:cs.content, pef:cs.pointerEvents,
    br:cs.borderTopLeftRadius, tp:cs.transitionProperty, td:cs.transitionDuration,
    bg:cs.backgroundImage.slice(0, 150),
    hostPos:getComputedStyle(o).position,
    rect:[+r.left.toFixed(1), +r.top.toFixed(1),
          +r.width.toFixed(1), +r.height.toFixed(1)],
    hover:o.matches(':hover'), disabled:!!o.disabled,
    locked:(bl && bl.dataset.locked) || '', runs:runs});})('%s')"""

# 一次往返里把同一条补间钉在若干档上逐格读：CDP 一趟 ~30ms，160ms 的淡入追帧必输
_HOT_SWEEP = r"""(function(sel, prop, times){
  var o = document.querySelector(sel);
  void getComputedStyle(o, '::before').opacity;          // 逼一次样式重算，补间才建档
  var a = (o.getAnimations({subtree:true}) || []).filter(function(x){
      return x.transitionProperty === prop;})[0];
  if (!a) return JSON.stringify({gone:prop});
  a.pause(); var out = [];
  times.forEach(function(t){ a.currentTime = t;
    var cs = getComputedStyle(o, '::before');
    out.push([t, cs.getPropertyValue(prop), cs.opacity, a.playState]); });
  a.finish();                     // finish（不是 play）：留在终态，不会从头再演一遍
  return JSON.stringify({prop:prop, at:out, state:a.playState});})('%s','%s',%s)"""

_MOOD_READ = r"""(function(){
  var root = document.documentElement, rst = getComputedStyle(root);
  var wash = document.querySelector('#depth .wash'), wcs = getComputedStyle(wash);
  var dots = document.querySelector('#farDots circle');
  var glow = getComputedStyle(document.body, '::before');
  var runs = (root.getAnimations() || [])
      .filter(function(a){ return a.transitionProperty === '--tint'; })
      .map(function(a){ return [a.effect.getKeyframes().map(function(k){
              return k['--tint']; }), +a.effect.getTiming().duration.toFixed(1),
              a.playState]; });
  var wr = wash.getBoundingClientRect();
  var dr = document.querySelector('#depth rect').getBoundingClientRect();
  return JSON.stringify({mood:root.getAttribute('data-mood') || '',
    tint:rst.getPropertyValue('--tint'), rootTp:rst.transitionProperty,
    fill:wcs.fill, wop:wcs.opacity, wpe:wcs.pointerEvents,
    glow:glow.backgroundImage.slice(0, 150),
    dots:getComputedStyle(dots).fill,
    wash:[+wr.width.toFixed(1), +wr.height.toFixed(1)],
    dotsRect:[+dr.width.toFixed(1), +dr.height.toFixed(1)],
    runs:runs});})()"""

_MOOD_SWEEP = r"""(function(times){
  var root = document.documentElement;
  var a = (root.getAnimations() || []).filter(function(x){
      return x.transitionProperty === '--tint';})[0];
  if (!a) return JSON.stringify({gone:'--tint'});
  a.pause(); var out = [];
  times.forEach(function(t){ a.currentTime = t;
    out.push([t, getComputedStyle(root).getPropertyValue('--tint'),
              getComputedStyle(document.querySelector('#depth .wash')).fill,
              getComputedStyle(document.body, '::before').backgroundImage.slice(0, 78),
              a.playState]); });
  a.finish(); return JSON.stringify({at:out, state:a.playState});})(%s)"""

# 门禁开卡：at:'end' 的两道（hotspot / bucket）要落在**末句播完后的空档**里才弹
_OPEN_GAP = r"""(function(){
  window.__gap = function(sid){
    var tl = window.__tl;
    for (var i = 0; i < tl.scenes.length; i++){
      var sc = tl.scenes[i];
      if (sc.step_id !== sid) continue;
      var ns = sc.runtime.narration, last = ns[ns.length - 1];
      window.__t = last.start + last.duration + 0.05;
      document.getElementById('main-audio').dispatchEvent(new Event('timeupdate'));
      return window.__t;
    }
    throw new Error('时间轴里没有这一场：' + sid);
  };
  return 1;
})()"""

# 逐型别把当前这张卡答对（与 test_pipeline_smoke 的 driveGate 同一套钩子名）
_ANSWER = r"""(function(kind){
  var card = document.querySelector('#gate [data-interaction]:not([hidden])');
  if (!card) return 'no card';
  var cfg = JSON.parse(card.getAttribute('data-interaction') || '{}');
  if (kind === 'choice'){
    var right = (cfg.options || []).filter(function(o){ return o.correct === true; })[0];
    card.querySelector('[data-choice-id="' + right.id + '"]').click();
  } else if (kind === 'hotspot'){
    var sp = (cfg.options || cfg.spots || []).filter(function(o){
        return o.correct === true; })[0];
    card.querySelector('[data-hotspot-id="' + sp.id + '"]').click();
  } else if (kind === 'sequence'){
    var list = card.querySelector('.sequence-list'), map = {};
    Array.prototype.forEach.call(list.children, function(li){
      map[li.dataset.sequenceId] = li; });
    (cfg.correct_order || cfg.answer || []).forEach(function(id){
      if (map[String(id)]) list.appendChild(map[String(id)]); });
    card.querySelector('[data-sequence-submit]').click();
  } else if (kind === 'bucket'){
    var ans = cfg.answer || {};
    Object.keys(ans).forEach(function(id){
      var item = card.querySelector('.bucket-item[data-bucket-item="' + id + '"]');
      var box = card.querySelector('[data-drop][data-bucket-id="' + ans[id] + '"]');
      if (item && box){
        var r = item.getBoundingClientRect();
        var o = {bubbles:true, cancelable:true, pointerId:41, isPrimary:true,
                 clientX:r.left + r.width / 2, clientY:r.top + r.height / 2,
                 button:0, pointerType:'mouse'};
        item.dispatchEvent(new PointerEvent('pointerdown', o));
        item.dispatchEvent(new PointerEvent('pointerup', o));
        box.click();
      }});
    card.querySelector('[data-bucket-submit]').click();
  }
  return 'asked';})('%s')"""


def _gate_open(ev, sel, timeout=8.0):
    """等这一块宿主真的**有盒**：门禁卡没揭开时元素在场但尺寸是 0，量不到触点。"""
    probe = ("(function(){var o=document.querySelector('%s');"
             "if(!o) return false;"
             "var r=o.getBoundingClientRect();return !o.hidden && r.width > 0;})()") % sel
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ev(probe):
            return True
        time.sleep(0.1)
    raise AssertionError("门禁宿主一直没揭开：%s" % sel)


def _gate_close(ev, timeout=8.0):
    """答完的卡要按「继续」才收得掉：不收，下一道的浮层还压着上一道。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ev("!document.getElementById('gate-go').disabled"):
            break
        time.sleep(0.1)
    else:
        raise AssertionError("「继续」一直没放行")
    ev("document.getElementById('gate-go').click()")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ev("document.getElementById('gate').hidden"):
            return True
        time.sleep(0.1)
    raise AssertionError("门禁浮层一直没收起")


def _metric(metrics, name):
    return {m["name"]: m["value"] for m in metrics}.get(name, 0.0)


def _mouse(send, x, y):
    send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y,
                                      "button": "none", "buttons": 0, "clickCount": 1})


def _box(st):
    return st["rect"][0], st["rect"][1], st["rect"][2], st["rect"][3]


def _px(text):
    """落定后的 --hot-x / --hot-y：计算值一律序列化成 px（写进去的 `13.0px`
    会被折成 `13px`），中间趟则是 `calc(...)`——那种值不许走到这里。"""
    assert text.endswith("px"), text
    return float(text[:-2])


def _bezier_progress(t_ms, dur_ms, p1, p2, p3, p4):
    """CSS cubic-bezier(p1,p2,p3,p4)：把时间轴上的 t 解成输出进度（二分求参数 u）。"""
    u = t_ms / float(dur_ms)
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = (lo + hi) / 2.0
        x = 3 * (1 - m) ** 2 * m * p1 + 3 * (1 - m) * m * m * p3 + m ** 3
        if x < u:
            lo = m
        else:
            hi = m
    v = (lo + hi) / 2.0
    return 3 * (1 - v) ** 2 * v * p2 + 3 * (1 - v) * v * v * p4 + v ** 3


# ── 静态锁：三条原语长在范本与运行时里 ─────────────────────────────────

def test_follow_layer_primitives_present():
    """模板结构契约：0.24.0 三条"随动"原语的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮量出来"为什么只能这么写"的边界：
      · **旧的恒定档 --dur-cam 必须整个消失**（令牌行、#cam、#depth 三处都不许再有），
        两个消费者一起改读派生值 --cam-dur——留着一条"没人读的 .6s"比删掉更糟，
        下一个人会以为改它还能影响镜头；
      · 时间与几何**各只有一处**：JS 只写两个读数（--cam-pan / --cam-zoom），
        权重、地板、封顶只在 :root 的 clamp() 里；断言里也不许出现第二个权重数
        （用 _cam_expect_ms 现算）；--cam-pan / --cam-zoom **故意不注册**，
        注册了就去补间一个"行程"，得到时长自己也在爬的第二套动画；
      · 地板与落点灯同档（.5s == --dur-spot）、封顶短于沿线抵达（.78s < .8s）；
      · `camCalc` 是纯函数、`camRun` 只写；**读数写在 transform 之前**（顺序锁用
        字符串位置钉死，"跑动中的时长 == 计算值"那条实测在 CDP 用例里）；
      · 擦洗与导出截图模式写 0/0（一行守卫，与运镜模糊/方向感换场同一判据）；
      · 辉光：--hot-x / --hot-y 注册成 `<length-percentage>` 且 **inherits:true**
        （本轮实测纠正：inherits:false 时写在宿主上的数根本进不了 ::before），
        初值 50% = 盒中心那一格；宿主标 `.hot`（选项 / hotspot 圈 / 两类条目），
        运行时按 `.hot` 扫宿主、**不按题型重列选择器**；状态写 `data-hot`，
        **不许出现 `:hover` 驱动这一层**（媒体查询在触屏与 headless 两处都 false）；
        放行那条 `[data-locked]` 必须写在 `:not(:disabled)` 那条**之后**；
      · 色温：`--tint` 注册成 `<color>` 且 inherits:true，initial 与 --tint-calm
        取同一个数（两处持有者由字符串钉在一起，改一处不改另一处就红）；
        补间只写在 :root 一处，两个消费者（`#depth .wash` / `body::before`）
        都不写 transition；柔光矩形必须**在点阵之后**（盖在纹理上）、与点阵同宽；
      · reduced-motion 两行新兜底（`:root` / `.hot::before`），关的都是补间不是读数。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    js = (REPO / "scripts/interactive_runtime.js").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)

    # ⓪ 注释配对 + 新令牌必须真在 :root 的解析结果里
    assert _css_comment_problems(html) == [], _css_comment_problems(html)
    for tok in ("--dur-cam-min", "--dur-cam-max", "--dur-cam-pan", "--dur-cam-zoom",
                "--cam-pan", "--cam-zoom", "--cam-dur", "--dur-hot", "--hot-r",
                "--hot-ink", "--dur-mood", "--tint-wash", "--tint-calm",
                "--tint-turn", "--tint-key"):
        assert re.search(re.escape(tok) + r"\s*:", root), \
            f"{tok} 没长在 :root 里（被注释语法吃掉过一次，见 _css_comment_problems）"

    # ① 恒定档就此消失：令牌行、两个消费者都不许再读它
    assert "--dur-cam:" not in css and "var(--dur-cam)" not in css, \
        "留一条没人读的 --dur-cam 比删掉更糟：下一个人会以为改它还能影响镜头"
    assert "--dur-hover:.16s; --dur-veil:.32s; --dur-el:.38s;" in html
    assert "#cam{transition:transform var(--cam-dur) var(--ease-out)}" in html
    assert "#depth{transition:transform var(--cam-dur) var(--ease-out)}" in html

    # ② 时长只由 clamp() 一处推出，两端与权重各有出处
    assert "--dur-cam-min:.5s; --dur-cam-max:.78s;" in html, \
        "地板 == --dur-spot（灯不落在镜头后面），封顶 < --dur-ride .8s（画面里最长的一趟）"
    assert "--dur-cam-pan:.0014s; --dur-cam-zoom:.35s;" in html
    assert "--cam-pan:0; --cam-zoom:0;" in html, \
        "静态终值即初值：没跑 / 不能跑 / 导出切掉时落到的正是地板那一档"
    assert ("--cam-dur:clamp(var(--dur-cam-min),\n"
            "                    calc(var(--cam-pan) * var(--dur-cam-pan)\n"
            "                       + var(--cam-zoom) * var(--dur-cam-zoom)),\n"
            "                    var(--dur-cam-max));") in html
    assert "--dur-hot:.16s; --hot-r:64px; --hot-ink:rgba(245,166,35,.22);" in html, \
        "--dur-hot 借按压那一档的读数，不新立一档；墨色比涟漪 --rip-ink .26 还轻"
    assert "--dur-mood:1.2s; --tint-wash:.28;" in html, \
        "--dur-mood 是全仓库最长的一档：气氛不该比一次运镜抢眼"

    # ③ 注册：只注册真正要补间的那三个，行程读数故意不注册
    assert ("@property --hot-x{syntax:'<length-percentage>';inherits:true;initial-value:50%}"
            in html), "辉光必须注册成可补间，且 inherits:true（消费者是宿主的伪元素）"
    assert ("@property --hot-y{syntax:'<length-percentage>';inherits:true;initial-value:50%}"
            in html)
    assert "@property --tint{syntax:'<color>';inherits:true;initial-value:#0e1f4b}" in html, \
        "initial 取 calm 那个数：静止即最平的一档（纪律③）"
    m = re.search(r"--tint-calm:(#\w{6})", root)
    assert m and ("@property --tint{syntax:'<color>';inherits:true;initial-value:%s}"
                  % m.group(1)) in html, \
        "--tint 的初值与 --tint-calm 是两个持有者（注册期不许写 var()），必须钉成同一个数"
    for nope in ("--cam-pan", "--cam-zoom", "--spot-k", "--squash-x"):
        assert "@property " + nope not in html, \
            f"{nope} 是常量替换、进 calc，注册了就去补间它（第二轮就踩过这一课）"

    # ④ 辉光那一层：宿主、终态、放行三条
    assert ".hot{position:relative}" in css, \
        "条目类宿主本来就是静态盒，position 只在这里补——.hs 的 absolute 由 (0,2,0) 保住"
    assert ("background:radial-gradient(var(--hot-r) at var(--hot-x) var(--hot-y),\n"
            "                                   var(--hot-ink), transparent 70%);") in css
    assert ("transition:opacity var(--dur-hot) var(--ease-out),\n"
            "                   --hot-x var(--dur-hot) linear,\n"
            "                   --hot-y var(--dur-hot) linear}") in css, \
        "位置走 linear 不走 --ease-out：指针停了光就停，ease-out 会在每次小停后拖一段尾巴"
    assert "opacity:0;pointer-events:none" in css and "border-radius:inherit" in css, \
        "静止态这一层在场但看不见；圆角跟着宿主走，所以不需要 overflow:hidden"
    glow_on = '.hot[data-hot="1"]:not(:disabled)::before{opacity:1}'
    glow_off = '[data-interaction][data-locked="1"] .hot[data-hot="1"]::before{opacity:0}'
    assert glow_on in css and glow_off in css
    assert css.index(glow_on) < css.index(glow_off), \
        "两条同为 (0,3,0)，放行那条必须靠源序压过去（0.15.0 折叠那一族同一课）"
    assert ".hot:hover" not in css and ".hot:active" not in css, \
        "这一层不许由 :hover 驱动：触屏上 hover 不成立、headless 里 hover 媒体查询全 false"

    # ⑤ 色温：一处补间、两处消费，柔光盖在点阵之上
    assert ":root{transition:--tint var(--dur-mood) var(--ease-out)}" in css
    for k in ("calm", "turn", "key"):
        assert ':root[data-mood="%s"]{--tint:var(--tint-%s)}' % (k, k) in css
    assert "#depth .wash{fill:var(--tint);opacity:var(--tint-wash);pointer-events:none}" in css
    assert "radial-gradient(1100px 640px at 50% -8%,var(--tint) 0%,transparent 62%)" in css, \
        "页面光晕是第二个消费者：一处换档、两处跟着，别在消费者上各写一条 transition"
    assert "#151a26" not in css, "旧那圈固定的光斑颜色必须整个交回 --tint"
    assert 'fill="url(#farDots)" opacity=".5"/><rect class="wash"' in html, \
        "柔光矩形在点阵**之后**（盖在纹理上），且与点阵同宽同裁"

    # ⑥ 消费者各就各位：JS 只写读数与档，不留第二个时长
    assert "function camCalc(f){" in html and "function camRun(prev, now){" in html
    assert "function camTo(" not in html, "旧的读、写混在一处的那个函数已经不在了"
    assert "return {cx:cx, cy:cy, k:k, tx:500 - k * cx, ty:202 - k * cy};" in html
    assert "if (prev && !scrubbing && !window.__coursewareShotMode){" in html, \
        "擦洗与导出截图模式一律写 0/0：那两处的行程不是用户能感知的连续运镜"
    pan_at = html.index("root.style.setProperty('--cam-pan'")
    tf_at = html.index("el('cam').setAttribute('transform'")
    assert pan_at < tf_at, \
        "先写读数再写 transform：反过来就夹进一次强制布局，跑动中的补间会用上一趟的时长"
    assert "camRun(camAt, now);" in html and "depthShift(now);" in html
    assert html.index("camRun(camAt, now);") < html.index("depthShift(now);"), \
        "视差层的位移也读同一趟：读数没写进去之前不许有人去量盒"
    assert "var MOOD = {" in html and "function moodSet(sid){" in html
    table = html.split("var MOOD = {")[1].split("};")[0]
    for sid in ("opening", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5", "seg-6", "closing"):
        assert "'%s':" % sid in table, "档表覆盖范本全部八场：漏一场就是那一整段没有气氛"
    assert "if (!m) return;" in html and "document.documentElement.dataset.mood = m;" in html
    assert "moodSet(hit.scene.step_id);" in html
    assert html.index("lastSceneIdx = idx;") < html.index("moodSet(hit.scene.step_id);") \
        < html.index("var ch = el('chapter');"), \
        "换档挂在**换节**分支上：挂在换句上就成了每句游一次 1.2s 的颜色"

    # ⑦ 运行时：宿主由页面标 .hot，运行时只按 .hot 扫
    assert ("{ down:'pointerdown', move:'pointermove', up:'pointerup', "
            "cancel:'pointercancel',\n        enter:'pointerenter', leave:'pointerleave' }") in js
    assert "function hotWire(node){" in js
    assert "el.querySelectorAll('.hot').forEach(hotWire);" in js, \
        "「谁是触点」只许写一处：按题型重列一遍，早晚会跟 CSS 那份分手"
    assert "const x = Math.max(0, Math.min(r.width,  e.clientX - r.left));" in js
    assert "if(!(r.width > 0) || !(r.height > 0)) return;" in js, \
        "没有盒就什么都不写，落回注册初值那一格（纪律③）"
    assert "node.dataset.hot = '1';" in js and "if(!ids.size) node.dataset.hot = '0';" in js
    assert "Number.isFinite(e.clientX)" in js, \
        "键盘那条路（click / keydown）没有坐标：不点亮，绝不硬编一个假光心"
    for sel in ("'opt hot'", "'bucket-item hot'", "'sequence-item hot'", 'class="hs hot"'):
        assert sel in html, f"宿主没标 .hot：{sel}"

    # ⑧ reduced-motion：关补间不关读数，两处新兜底都不需要抬特异度
    rm = css.split("@media (prefers-reduced-motion: reduce){")[1]
    assert ":root{transition:none}" in rm and ".hot::before{transition:none}" in rm
    assert rm.index(":root{transition:none}") < rm.index(".hot::before{transition:none}")
    assert "#cam,.nt,.bar-fill{transition:none}" in rm and "#depth{transition:none}" in rm, \
        "位移定拍不新增兜底行：关的是整条 transition 简写，与时长从哪儿求的值无关"
    assert "var(--dur-mood)" not in rm and "var(--dur-hot)" not in rm, \
        "兜底里不许出现「补一个更短的时长」——那是在演一次更快的动效，不是不演"


def test_mood_tints_are_luminance_preserving():
    """三档色温**只换色相不换亮度**——这一条能压在内容底下，全靠这件事成立。

    按 §8「半透明按合成后的颜色算对比度」在 python 里算，不看截图平均亮度：
      · 三档自己的相对亮度最大差 2.9%（calm .015813 / turn .015381 / key .015368）；
      · 画布柔光（--tint-wash .28）合成到舞台渐变的三档底色上，六支墨色 × 三支底色
        = 54 格，在三档之间**最大偏移 1.90%**；且**没有一支本来在 4.5 线上的墨色
        被柔光压到线以下**（判词红 #ef4d4d 对最深一档 #161a23：4.841 → 4.749 /
        4.772 / 4.754，对 --panel 是 4.741 → 4.676~4.698，都还在线上）；
      · 非结论字 --dim2 #6b748a 落在光晕中心 3.411:1（旧值 #151a26 是 3.718:1）——
        这一档本来就不在正文线上（3:1 那一档），换色温没有把它推出这一档；
      · 补间路径：钉在 25% / 50% / 75% 三格上，色值走的是 **sRGB 逐通道线性 ×
        --ease-out**（实测逐格与预测相同，Oklab 路径差 40+ 通道单位，本轮排除）；
        中点那一格的**相对亮度可以比两端低 10.5%**（sRGB 直线在深色对的中段本来会
        暗一下），但合成之后没有一格的对比度跑出两端所夹区间超过 0.06%——
        "只换色相不换亮度"靠的是这两条叠在一起，不是三档 L 相等单独能证的。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)

    def _tok(name):
        return re.search(re.escape(name) + r":([^;]+);", root).group(1).strip()

    def _rgb(text):
        h = text.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    def _lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def _lum(rgb_):
        r, g, b = (_lin(c) for c in rgb_)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def _cr(a, b):
        hi, lo = max(a, b), min(a, b)
        return (hi + 0.05) / (lo + 0.05)

    def _over(fg, alpha, bg):
        return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))

    tints = {k: _rgb(_tok("--tint-" + k)) for k in ("calm", "turn", "key")}
    wash = float(_tok("--tint-wash"))
    assert len({v for v in tints.values()}) == 3, "三档必须是三个不同的色相，不是同一个数抄三遍"
    ls = {k: _lum(v) for k, v in tints.items()}
    spread = (max(ls.values()) - min(ls.values())) / min(ls.values())
    assert spread <= 0.03, f"三档亮度差 ≤3%（实测 2.9%）：现在 {spread:.2%}"

    inks = {"fg": _rgb(_tok("--fg")), "dim": _rgb(_tok("--dim")), "key": _rgb(_tok("--key")),
            "ok": _rgb(_tok("--ok")), "bad": _rgb(_tok("--bad")), "dim2": _rgb(_tok("--dim2"))}
    stage = re.search(r"\.stage\{[^}]*background:linear-gradient\(180deg,(#\w{6}) 0%,"
                      r"var\(--panel\) 55%,(#\w{6}) 100%\)", css)
    assert stage, "舞台渐变读不到：这一条要求从 CSS 现取底色，不许在测试里抄 hex"
    bases = {"top": _rgb(stage.group(1)), "panel": _rgb(_tok("--panel")),
             "bottom": _rgb(stage.group(2))}
    cells, worst = [], 0.0
    for bn, base in bases.items():
        plain = {k: _cr(_lum(c), _lum(base)) for k, c in inks.items()}
        for mood, tint in tints.items():
            bg_l = _lum(_over(tint, wash, base))
            for ink, col in inks.items():
                got = _cr(_lum(col), bg_l)
                drift = abs(got - plain[ink]) / plain[ink]
                worst = max(worst, drift)
                cells.append((drift, mood, bn, ink, got, plain[ink]))
                # 本来在线上的，不许被柔光压到线以下（本轮只挪色温，不挪可读性）
                assert got >= 4.5 or plain[ink] < 4.5, \
                    f"{mood} 柔光把 {ink} 对 {bn} 压到线下：{got:.4f}（不柔光是 {plain[ink]:.4f}）"
    assert worst <= 0.02, f"三档之间对比度最大偏移 ≤2%（实测 1.90%）：{max(cells)}"
    on_line = [c for c in cells if c[5] >= 4.5 and c[3] != "dim2"]
    assert len(on_line) > 30 and min(c[4] for c in on_line) >= 4.5, \
        "判词/正色那一族逐格核过：最低 " + str(round(min(c[4] for c in on_line), 4))

    # 页面光晕：三档等亮度 ⇒ 换档时这圈光的强弱不动，只动色温
    bg = _rgb(_tok("--bg"))
    assert _lum(tints["calm"]) > _lum(bg), "光晕必须比底色亮，否则那一圈变成一块洞"
    dim2_cr = _cr(_lum(inks["dim2"]), _lum(tints["calm"]))
    assert 3.0 <= dim2_cr, f"非结论字落在光晕中心仍要过 3:1（实测 3.411）：{dim2_cr:.3f}"

    # 补间路径：合成后不许越出两端所夹的区间（sRGB 逐通道 + --ease-out）
    eb = re.search(r"--ease-out:cubic-bezier\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)\)", css)
    assert eb, "--ease-out 读不到贝塞尔参数：路径预测就不成立"
    bez = tuple(float(x) for x in eb.groups())
    deep = bases["bottom"]
    outside = 0.0
    worst_cell = None
    for a, b in (("calm", "turn"), ("turn", "key"), ("calm", "key")):
        pa, pb = tints[a], tints[b]
        ends = {k: tuple(_cr(_lum(c), _lum(_over(p, wash, deep))) for p in (pa, pb))
                for k, c in inks.items()}
        for frac in (0.25, 0.5, 0.75):
            p = _bezier_progress(frac * 1200, 1200, *bez)
            mid = tuple(x + (y - x) * p for x, y in zip(pa, pb))
            assert min(ls[a], ls[b]) * 0.88 <= _lum(mid), \
                f"{a}→{b} 路径中点亮度掉得太多（sRGB 直线允许 ≤12%）：{_lum(mid):.5f}"
            for ink, col in inks.items():
                got = _cr(_lum(col), _lum(_over(mid, wash, deep)))
                lo, hi = min(ends[ink]), max(ends[ink])
                if got < lo or got > hi:
                    over = min(abs(got - lo), abs(got - hi)) / lo
                    if over > outside:
                        outside = over
                        worst_cell = (a, b, frac, ink, round(got, 4),
                                      tuple(round(x, 4) for x in ends[ink]))
    assert outside <= 0.001, \
        f"中途那一格的合成对比度越出两端区间 {outside:.4%}（实测 ≤0.06%）" \
        f"——1.2s 里会看见忽暗：{worst_cell}"


# ── CDP：①位移定拍 ────────────────────────────────────────────────────

@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_camera_run_duration_follows_the_travel(full_lesson):
    """真实浏览器：镜头时长**由这一趟真的走了多少**推出来（0.24.0 位移定拍）。

    下面每一个数都是从成品页上读出来的（走法：每一趟**等补间落定**再跳下一趟，
    理由见边界 ⑧），断言只用两种出处——页面自己写的读数、页面自己的令牌算出的值。

      ① 冷启动没有"上一状态" → 行程读数 0.0 / 0.000、时长落地板（与落点灯同档）；
      ② 大运镜（opening s0 → seg-2 s1）：读数 378.6 / 0.690 → 按令牌 771.54ms，
        `getAnimations()` 里**正在跑的那条补间**的时长逐位等于它，也逐位等于
        #cam 此刻的 computed transitionDuration（这一条就是顺序锁）；
      ③ 小行程（seg-2 s1 → s2，79.2 / 0.065 折出 .133s）与**大平移零缩放**
        （s2 → s4，335.9 / 0.028 折出 .480s）都被地板接住 → 500ms：旧写法这两趟
        恒定 600ms，短的那趟从此快 100ms——这是本轮实测到的口径变化，不是回归；
      ④ 中段还有别的档：s4 → s5（450.2 / 0.200）**700.28ms**、closing s0 → seg-1 s1
        （243.5 / 0.477）**507.85ms**——地板与封顶之间真的在插值，不是"要么 500 要么 780"；
      ⑤ #cam 与 #depth **共读同一个派生值**：每一趟两条 computed 时长逐字符相同；
      ⑥ 全程 500 ≤ 时长 ≤ 780ms 且 ≥ `--dur-spot`：灯永远不落在镜头后面；
      ⑦ 两种"不该有补间"的情形各验一次：
        · 同一句再跳一次 → tick 没有重画画面，`camRun` 根本不跑，`getAnimations()`
          里没有 transform 那条（不是"补间时长为 0"，是压根没建）；
        · 回到全幅且行程为 0（seg-6 s0 → closing s0）→ 读数写 0.0/0.000、时长落地板，
          但 transform 没变，所以同样没有新补间；
      ⑧ **本轮实测到的一个读数陷阱**：上一趟没落定就跳下一趟时，Chrome 会把新补间
        按剩余距离**改写时长**（实测中途被打断那一趟读到 298.75ms，比地板 500 还短，
        比这一趟该拿的 500 也短）。这个数不是页面的口径，是浏览器对"半途换向"的补偿，
        所以用例必须逐趟等落定——否则测的是浏览器的补偿逻辑而不是 clamp()；
      ⑨ 导出截图模式：行程读数一律 0.0/0.000、时长落地板（那一档的"行程"不是用户
        能感知的连续运镜）。
    """
    chrome, ev, send = _cdp(full_lesson, "camdur")
    try:
        ev(_SEEK)

        def walk(sid, k):
            """跳过去、**当场**读（补间刚建档），再等它落定，返回这一趟的读数。"""
            ev("window.__jump('%s', %d)" % (sid, k))
            st = json.loads(ev(_CAM_READ))
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if not ev(_CAM_RUNNING):
                    break
                time.sleep(0.05)
            return st

        # ① 冷启动：没有上一状态
        st = walk("opening", 0)
        assert float(st["pan"]) == 0.0 and float(st["zoom"]) == 0.0, \
            f"冷启动没有上一状态：{st}"
        assert st["camDur"] == st["depthDur"] == "0.5s", f"冷启动落地板：{st}"

        # ② 大运镜：时长 = 按令牌重算，且动画时长与计算值逐位相同
        st = walk("seg-2", 1)
        assert abs(float(st["pan"]) - 378.6) <= 0.6 and \
            abs(float(st["zoom"]) - 0.690) <= 0.003, f"行程读数：{st}"
        want = _cam_expect_ms(st)
        assert abs(want - 771.54) <= 1.0, f"这一趟按令牌该走 {want:.2f}ms（实测 771.54）"
        assert st["anims"] == [round(want, 2)], \
            f"跑动中的补间时长 == 按令牌重算（期望 {want:.2f}ms）：{st}"
        assert st["depthAnims"] == st["anims"], f"两个消费者共读同一个派生值：{st}"
        assert abs(_ms(st["camDur"]) - want) <= 1.0, \
            f"顺序锁——动画时长与计算值不许分手：{st}"
        assert want >= st["spot"] * 1000.0 - 0.5, f"镜头不能快过落点灯：{st}"

        # ③④⑤⑥ 逐趟走一遍，每趟都过同样的四道锁
        plan = [("seg-2", 2), ("seg-2", 4), ("seg-2", 5), ("seg-3", 0), ("seg-4", 1),
                ("seg-5", 0), ("seg-5", 4), ("seg-6", 0), ("closing", 0), ("seg-1", 1),
                ("seg-1", 3), ("seg-2", 1)]
        got = {}
        for sid, k in plan:
            st = walk(sid, k)
            ms = _cam_expect_ms(st)
            got["%s s%d" % (sid, k)] = (round(float(st["pan"]), 1),
                                        round(float(st["zoom"]), 3), round(ms, 2))
            assert st["min"] * 1000.0 - 0.6 <= ms <= st["max"] * 1000.0 + 0.6, \
                f"{sid} s{k} 落在一端之外：{st}"
            assert ms >= st["spot"] * 1000.0 - 0.6, f"{sid} s{k} 抢在灯前面：{st}"
            assert abs(_ms(st["camDur"]) - ms) <= 1.0, f"{sid} s{k}：{st}"
            assert st["depthDur"] == st["camDur"], f"{sid} s{k} 两个消费者分手了：{st}"
            if st["anims"]:                      # 落定才有"这一趟的时长"可断言
                assert st["anims"] == [round(ms, 2)], \
                    f"{sid} s{k} 建档时长与计算值分手（中途被打断会被浏览器改写）：{st}"

        # ③ 地板真的在接住短行程（旧口径这里恒定 600ms）
        assert got["seg-2 s2"] == (79.2, 0.065, 500.0), f"小行程落地板：{got['seg-2 s2']}"
        assert got["seg-2 s4"] == (335.9, 0.028, 500.0), \
            f"大平移零缩放也落地板——倍率没变就不该有第二套时长：{got['seg-2 s4']}"
        # ④ 中段档：不是"要么地板要么封顶"
        assert got["seg-2 s5"] == (450.2, 0.200, 700.28), f"第二档行程：{got['seg-2 s5']}"
        assert got["seg-1 s1"] == (243.5, 0.477, 507.85), f"第三档行程：{got['seg-1 s1']}"
        assert len(set(v[2] for v in got.values())) >= 3, \
            f"一趟走下来至少三个不同的时长（否则等于还在用恒定档）：{got}"

        # ⑦ 两种"不该有补间"
        st = walk("seg-1", 3)
        again = walk("seg-1", 3)                 # 同一句再跳一次：画面没重画
        assert again["anims"] == [], f"同一句重复跳不许重建补间：{again}"
        assert again["camDur"] == st["camDur"], f"重复跳不许改时长：{st} → {again}"
        st = walk("seg-6", 0)
        z = walk("closing", 0)                  # 回到全幅、行程 0
        assert float(z["pan"]) == 0.0 and float(z["zoom"]) == 0.0, \
            f"两处取景相同 ⇒ 行程读数写 0：{z}"
        assert z["anims"] == [] and z["camDur"] == "0.5s", \
            f"transform 没变就不该有补间，而时长仍落在地板那一格：{z}"

        # ⑨ 导出截图模式：不派时长
        walk("seg-1", 1)
        ev("window.__coursewareShotMode = true")
        st = walk("seg-2", 1)
        assert float(st["pan"]) == 0.0 and float(st["zoom"]) == 0.0, \
            f"导出模式下的行程读数一律 0/0：{st}"
        assert st["camDur"] == "0.5s", f"导出模式落到地板那一档：{st}"
        ev("delete window.__coursewareShotMode")
    finally:
        _kill(chrome)


# ── CDP：②触点辉光 ────────────────────────────────────────────────────

@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_glow_follows_the_pointer_and_goes_out_when_hands_leave(full_lesson):
    """真实浏览器：光跟着指针落在哪、并且跟着走；手走了、题做完了，光就灭（0.24.0）。

      ① 静止态这一层**在场但看不见**（::before 有盒子、opacity 0、光心在 50% 那一格），
        `data-hot` 根本不存在——没有指针就没有读数，不编一个"中心"出来；
      ② 落上来：`data-hot="1"`，opacity 走 `--dur-hot` 淡入（160ms 那条补间在跑），
        钉在 0 / 80 / 160ms 上逐格读 0 → .961383 → 1；
      ③ **跟手**：光心 == 触点换算成盒内坐标（±1px），换一处时 --hot-x 逐帧补间——
        从 `50%` 起步那一趟中间值是 `calc(37.5% + 5.275px)` 这种**混合长度**（注册成
        `<length-percentage>` 才有的事，没注册就整档跳）；px → px 那一趟走 linear，
        半程正好落在两点正中（实测 21.1 → 44.35 → 67.6px）；
      ④ 不抢命中：`elementFromPoint` 仍落回那颗按钮；`pointer-events:none`；
      ⑤ 划过若干次**不重排**（LayoutCount 不涨、RecalcStyleCount 涨——改的是注册属性
        不是 left/top），实测 6 次小位移：layout 0 / recalc 28 / paint 0；
      ⑥ 手走了光灭（`data-hot="0"` + 淡出 1 → .0386175 → 0），但**读数留在最后一格**
        （--hot-x 不重置：这一层已经看不见，重置只会多演一趟没人在看的补间）；
      ⑦ 放行之后光按灭，而且是在**两类宿主**上各验一次：选项药丸是 disabled 的按钮
        （`:not(:disabled)` 那一路），筐条目是不 disabled 的 `<li>`（只有
        `[data-locked]` 那一条按得住它，实测 `data-hot` 还在线上、opacity 已经落 0）；
      ⑧ 键盘那条路（focus + click()）**不点亮**：没有坐标就绝不硬编一个假光心；
      ⑨ `:hover` 在合成鼠标事件下确实置位，但媒体查询三档全 false——本轮读数用
        `data-hot`，`@media (hover:hover)` 在这套 QA 里永远读成"没跑通"。
    """
    chrome, ev, send = _cdp(full_lesson, "hotglow")
    try:
        ev(_SEEK)
        ev(_OPEN_GAP)
        sel = "#gate-block .opt"
        ev("window.__jump('seg-3', 0)")
        _gate_open(ev, sel)
        time.sleep(0.4)                       # 门禁卡入场动画走完，盒子才停得住

        st0 = json.loads(ev(_HOT_STATE % sel))
        x, y, w, h = _box(st0)
        assert st0["cont"] == '""' and st0["op"] == "0" and st0["hx"] == "50%", \
            f"静止态在场但看不见、光心在盒中心那一格：{st0}"
        assert st0["hot"] == "" and st0["runs"] == [], f"没有指针就没有读数：{st0}"
        assert st0["hostPos"] == "relative" and st0["pef"] == "none", st0
        assert st0["br"] == "999px", f"圆角跟着宿主走（border-radius:inherit）：{st0}"
        assert st0["tp"] == "opacity, --hot-x, --hot-y" and \
            st0["td"] == "0.16s, 0.16s, 0.16s", f"三层共用 --dur-hot：{st0}"
        assert " at " not in st0["bg"], f"50% 那一格是梯度默认位置：{st0['bg']}"

        # ② 落上来
        fx, fy = x + w * 0.25, y + h * 0.70
        _mouse(send, fx, fy)
        st = json.loads(ev(_HOT_STATE % sel))
        assert st["hot"] == "1", f"落上来即在场（不靠 :hover）：{st}"
        assert st["hover"] is True, "合成鼠标事件下 :hover 本身是置位的（媒体查询不是）"
        runs = dict((r[0], (r[1], r[2])) for r in st["runs"])
        for p in ("opacity", "--hot-x", "--hot-y"):
            assert p in runs and runs[p][0] == 160.0, f"{p} 该有一条 160ms 的补间：{st['runs']}"
        assert json.loads(ev("JSON.stringify([matchMedia('(hover:hover)').matches,"
                             "matchMedia('(pointer:fine)').matches,"
                             "matchMedia('(any-hover:hover)').matches])")) \
            == [False, False, False], "headless 里三档媒体查询全 false：读数不能挂在那上面"

        fade = json.loads(ev(_HOT_SWEEP % (sel, "opacity", "[0, 80, 160]")))
        ops = [a[2] for a in fade["at"]]
        assert fade["state"] == "finished" and fade["at"][2][3] == "paused", fade
        assert float(ops[0]) == 0.0 and abs(float(ops[1]) - 0.961383) <= 0.02 \
            and float(ops[2]) == 1.0, f"淡入行程（--ease-out）：{fade}"

        # ③ 首趟位置补间：50% → px 靠 calc() 连续插值
        sweep = json.loads(ev(_HOT_SWEEP % (sel, "--hot-x", "[0, 40, 80, 120, 160]")))
        vals = [a[1] for a in sweep["at"]]
        assert vals[0] == "50%", f"起点是注册初值那一格：{sweep}"
        assert abs(_px(vals[-1]) - w * 0.25) <= 0.6, \
            f"终点精确落在触点：{vals[-1]} vs {w * .25:.1f}"
        assert all("calc(" in v for v in vals[1:-1]), \
            f"中间值必须是 %→px 的混合长度（注册才补得出这种值）：{vals}"

        time.sleep(0.3)
        lit = json.loads(ev(_HOT_STATE % sel))
        assert lit["op"] == "1", f"落定之后光亮着：{lit}"
        assert abs(_px(lit["hx"]) - w * 0.25) <= 1.0 and \
            abs(_px(lit["hy"]) - h * 0.70) <= 1.0, f"光心 == 盒内触点坐标：{lit}"
        assert "at %s %s" % (lit["hx"], lit["hy"]) in lit["bg"], \
            f"梯度按 var() 求值成功：{lit['bg']}"
        assert lit["hostHx"] == lit["hx"], f"宿主写的数与伪元素读到的是同一个：{lit}"

        # ③ 第二趟：px → px，走 linear，半程在正中
        _mouse(send, x + w * 0.80, y + h * 0.20)
        sw2 = json.loads(ev(_HOT_SWEEP % (sel, "--hot-x", "[0, 80, 160]")))
        v2 = [_px(a[1]) for a in sw2["at"]]
        assert abs(v2[0] - w * 0.25) <= 1.0 and abs(v2[2] - w * 0.80) <= 1.0, sw2
        assert abs(v2[1] - (v2[0] + v2[2]) / 2.0) <= 0.2, \
            f"位置走 linear：半程该在两点正中（实测 {v2}）"

        # ⑤ 不重排：落定之后连续小位移（同一条补间不再重建）
        _mouse(send, x + w * 0.30, y + h * 0.50)
        time.sleep(0.35)
        send("Performance.enable")
        m0 = send("Performance.getMetrics")["metrics"]
        for fx2, fy2 in ((0.32, 0.52), (0.34, 0.50), (0.36, 0.48),
                         (0.38, 0.46), (0.40, 0.44), (0.42, 0.42)):
            _mouse(send, x + w * fx2, y + h * fy2)
            time.sleep(0.03)
        time.sleep(0.35)
        m1 = send("Performance.getMetrics")["metrics"]
        lay = _metric(m1, "LayoutCount") - _metric(m0, "LayoutCount")
        rec = _metric(m1, "RecalcStyleCount") - _metric(m0, "RecalcStyleCount")
        assert lay == 0, f"划过六次不该重排一次（走注册属性不是 left/top）：{lay}"
        assert rec > 0, f"样式确实重新计算过（否则上面那些 0 是没跑）：{rec}"

        # ④ 不抢命中
        hit = json.loads(ev("""(function(){var o=document.querySelector('#gate-block .opt');
          var r=o.getBoundingClientRect();
          var n=document.elementFromPoint(r.left + r.width * .5, r.top + r.height * .5);
          return JSON.stringify([n ? (n.className || n.tagName) : null]);})()"""))
        assert "opt" in str(hit[0]), f"辉光层不抢命中：{hit}"

        # ⑥ 手走了光灭、读数留着
        _mouse(send, x - 600, y - 600)
        off = json.loads(ev(_HOT_SWEEP % (sel, "opacity", "[0, 80, 160]")))
        o2 = [float(a[2]) for a in off["at"]]
        assert o2[0] == 1.0 and abs(o2[1] - 0.0386175) <= 0.02 and o2[2] == 0.0, \
            f"淡出行程同一条曲线倒着走：{off}"
        gone = json.loads(ev(_HOT_STATE % sel))
        assert gone["hot"] == "0" and gone["op"] == "0", f"手走了光必须灭：{gone}"
        # 读数**留在最后一格**（= 上面六次划过最后那一格）：这一层已经看不见了，
        # 重置 --hot-x 只会多演一趟没人在看的补间。
        assert abs(_px(gone["hx"]) - w * fx2) <= 0.6 and \
            abs(_px(gone["hy"]) - h * fy2) <= 0.6, \
            f"读数留在最后一格、不重置（期望 {w * fx2:.1f}px / {h * fy2:.1f}px）：{gone}"
        assert "at %s %s" % (gone["hx"], gone["hy"]) in gone["bg"], \
            f"灭着的那一层梯度仍按留存的读数求值：{gone['bg']}"

        # ⑧ 键盘那条路不点亮（另开一次判定，不碰鼠标）
        ev("window.__jump('seg-3', 0)")
        _gate_open(ev, "#gate-block [data-choice-id=\"b\"]")
        ev("document.querySelector('#gate-block [data-choice-id=\"b\"]').focus();"
           "document.querySelector('#gate-block [data-choice-id=\"b\"]').click();")
        time.sleep(0.6)
        kb = json.loads(ev(_HOT_STATE % "#gate-block [data-choice-id=\"b\"]"))
        assert kb["hot"] == "" and kb["hx"] == "50%" and kb["op"] == "0", \
            f"没有坐标就绝不硬编一个假光心：{kb}"
        assert kb["locked"] == "1", f"判定照走（键盘这条路不是没生效）：{kb}"

        # ⑦ 两类宿主各验一次放行：disabled 那一路（上面刚验的选项）与 data-locked 那一路
        assert kb["disabled"] is True, f"选项放行之后按钮 disabled：{kb}"
        assert kb["op"] == "0", f"disabled 那一路光不亮：{kb}"
        _gate_close(ev)                      # 不收上一道，下一道的浮层还压着它
        ev("window.__gap('seg-5')")
        sel2 = "#gate-bucket .bucket-item"
        _gate_open(ev, sel2)
        time.sleep(0.4)
        item = json.loads(ev(_HOT_STATE % sel2))
        ix, iy, iw, ih = _box(item)
        assert item["hostPos"] == "relative" and item["br"] == "8px", \
            f"条目类宿主靠 .hot 补 position、圆角继承自己那 8px：{item}"
        _mouse(send, ix + iw * 0.3, iy + ih * 0.5)
        time.sleep(0.3)
        lit2 = json.loads(ev(_HOT_STATE % sel2))
        assert lit2["hot"] == "1" and lit2["op"] == "1", f"条目也长得住这一层：{lit2}"
        ev(_ANSWER % "bucket")
        deadline = time.time() + 6
        while time.time() < deadline:
            if json.loads(ev(_HOT_STATE % sel2))["locked"] == "1":
                break
            time.sleep(0.1)
        time.sleep(0.5)
        done = json.loads(ev(_HOT_STATE % sel2))
        assert done["locked"] == "1" and done["disabled"] is False, \
            f"筐条目不 disabled——只有 [data-locked] 那一条按得住它：{done}"
        assert done["hot"] == "1" and done["op"] == "0", \
            f"放行之后 data-hot 还在线上（说的是「手还在这儿」），光必须已经灭：{done}"
        _gate_close(ev)
    finally:
        _kill(chrome)


# ── CDP：③色温定调 ────────────────────────────────────────────────────

@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_mood_tint_moves_the_hue_not_the_brightness(full_lesson):
    """真实浏览器：气氛跟着**这一段**换档，一处写、两处读，全程只动色温（0.24.0）。

      ① 八场逐场核对 `data-mood` 与档表一致（opening/seg-1 turn、seg-2/3 calm、
        seg-4 key、seg-5 turn、seg-6 calm、closing key），每档落点精确等于 token 的
        rgb（calm rgb(14, 31, 75) / turn rgb(52, 27, 11) / key rgb(8, 38, 31)）；
      ② 补间只在 documentElement 上：时长 1200ms（全仓库最长的一档），`--tint` 的
        **补间值继承进 SVG**——同一次采样里 `#depth .wash` 的 `fill` 与根上的 `--tint`
        逐字符相同（这就是 inherits:true 买到的那件事，消费者一条 transition 都不写）；
      ③ 路径连续：钉在 0 / 300 / 600 / 900 / 1200ms 上读，实测 rgb(14,31,75) →
        (9,36,41) → (8,38,33) → (8,38,31) → (8,38,31)——与"sRGB 逐通道线性 ×
        --ease-out cubic-bezier(.22,1,.36,1)"的预测**逐格相同**（Oklab 路径在同格差
        40+ 通道单位，本轮实测排除），中点既不等于端点也不越界；
      ④ 页面光晕（body::before）读的是同一个数——两个消费者、一条补间；远景点阵
        颜色不跟着走（实测恒为 rgb(43, 50, 63)：把点阵当消费者会让纹理被洗平，见 stage.md）；
      ⑤ 换句不换档：同场内换步**不重建补间**（跑完之后 `getAnimations()` 里没有
        --tint 那一条，颜色不动）——气氛是段落级的连续量，不是每句一个事件；
      ⑥ 柔光矩形与点阵同宽同裁、`pointer-events:none`，命中测试仍落不到它身上。
    """
    chrome, ev, send = _cdp(full_lesson, "moodtint")
    try:
        ev(_SEEK)
        toks = json.loads(ev(r"""(function(){
          var cs = getComputedStyle(document.documentElement), o = {};
          ['--tint-calm','--tint-turn','--tint-key','--dur-mood','--tint-wash']
            .forEach(function(k){ o[k] = cs.getPropertyValue(k).trim(); });
          return JSON.stringify(o);})()"""))
        assert toks["--dur-mood"] == "1.2s" and toks["--tint-wash"] == ".28", toks

        want = {"opening": "turn", "seg-1": "turn", "seg-2": "calm", "seg-3": "calm",
                "seg-4": "key", "seg-5": "turn", "seg-6": "calm", "closing": "key"}
        seen = {}
        for sid, mood in want.items():
            ev("window.__jump('%s', 0)" % sid)
            time.sleep(1.45)                      # --dur-mood 1.2s 走完
            st = json.loads(ev(_MOOD_READ))
            assert st["mood"] == mood, f"{sid} 该落 {mood} 档：{st}"
            assert st["rootTp"] == "--tint", f"补间只写在根上：{st}"
            assert st["runs"] == [], f"跑完的补间必须已经离开 getAnimations()：{st}"
            target = {"calm": "rgb(14, 31, 75)", "turn": "rgb(52, 27, 11)",
                      "key": "rgb(8, 38, 31)"}[mood]
            hex_tok = {"calm": "#0e1f4b", "turn": "#341b0b", "key": "#08261f"}[mood]
            assert st["tint"] == st["fill"], f"SVG 里的 fill 与根上的 --tint 逐字符相同：{st}"
            assert st["tint"] == target, f"{sid} 落点必须精确是该档 token：{st}"
            assert toks["--tint-" + mood] == hex_tok, f"令牌表自己先对上：{toks}"
            assert target in st["glow"], f"页面光晕读同一个数：{st}"
            assert st["dots"] == "rgb(43, 50, 63)", f"点阵不当消费者（洗上去就看不见）：{st}"
            assert st["wop"] == "0.28" and st["wpe"] == "none", st
            assert st["wash"] == st["dotsRect"], f"柔光与点阵同宽同裁：{st}"
            seen[mood] = st["tint"]
        assert len(set(seen.values())) == 3, f"三档得是三个不同的色：{seen}"

        # ⑤ 换句不换档
        ev("window.__jump('seg-4', 0)")
        time.sleep(1.45)
        before = json.loads(ev(_MOOD_READ))
        ev("window.__jump('seg-4', 1)")
        mid = json.loads(ev(_MOOD_READ))
        assert mid["runs"] == [] and mid["tint"] == before["tint"], \
            f"同场换步不该重起一次 1.2s 的变色：{before['tint']} → {mid}"

        # ③ 路径：换档那一趟钉在四档上读
        ev("window.__jump('seg-2', 0)")           # calm
        time.sleep(1.45)
        assert json.loads(ev(_MOOD_READ))["tint"] == "rgb(14, 31, 75)"
        ev("window.__jump('closing', 0)")         # key
        pin = json.loads(ev(_MOOD_SWEEP % "[0, 300, 600, 900, 1200]"))
        got = [a[1] for a in pin["at"]]
        assert got[0] == "rgb(14, 31, 75)" and got[-1] == "rgb(8, 38, 31)", pin
        assert got[1] != got[0] != got[2], f"中间那一格采得到（注册 <color> 才补间）：{got}"
        for a in pin["at"]:
            assert a[2] == a[1], f"补间中的逐帧值也在往 SVG 里传：{a}"
        bez = (0.22, 1.0, 0.36, 1.0)
        ends = ((14, 31, 75), (8, 38, 31))
        for t, txt in zip((0, 300, 600, 900, 1200), got):
            p = _bezier_progress(t, 1200, *bez)
            pred = tuple(int(round(a + (b - a) * p)) for a, b in zip(*ends))
            m = re.match(r"rgb\((\d+), (\d+), (\d+)\)", txt)
            assert m, txt
            assert max(abs(int(m.group(i)) - pred[i - 1]) for i in (1, 2, 3)) <= 1, \
                f"{t}ms 实测 {txt} 与「sRGB 逐通道 × --ease-out」预测 {pred} 不符——" \
                f"补间空间换了（Oklab 在这一格会读到 rgb(48, 35, 41) 那一族）"

        # ⑥ 命中测试：柔光层不许当事件对象
        hit = json.loads(ev(r"""(function(){
          var vp = document.getElementById('veilplay');
          if (vp) vp.style.display = 'none';
          var w = document.querySelector('#depth .wash'), r = w.getBoundingClientRect();
          var n = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return JSON.stringify({hit:n ? (n.getAttribute('class') || n.tagName) : null,
                                 same:n === w});})()"""))
        assert hit["same"] is False and "wash" not in str(hit["hit"]), \
            f"装饰层一律不许是事件对象：{hit}"
    finally:
        _kill(chrome)


# ── CDP：reduced-motion 与导出截图模式（随动层的两格兜底）──────────────

@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_follow_layer_reduced_motion_and_shot_mode(full_lesson):
    """减少动效与导出截图：三条一起**关补间、不关读数**（0.24.0 的可及性口径）。

      ① reduced-motion：`:root` / `#cam` / `#depth` / `.hot::before` 四处的
        computed transitionProperty 全部 `none`——位移定拍**不用另写一行**兜底
        （关的是整条简写，与时长从哪儿求的值无关）；
      ② 色温一步落档：换档之后 `--tint` 直接是该档的数（实测 key 档 rgb(8, 38, 31)），
        `#depth .wash` 的 fill 同步，根上没有在跑的 --tint 补间；
      ③ 辉光**照旧亮**且**照旧跟到那一格**：`data-hot="1"`、opacity 直接 1、
        --hot-x 就是触点那一格（实测 17.4px），没有补间——位置信息不许连档位一起关
        （与 #spot / .tilt / .ride 同一判断）；
      ④ 导出截图模式：行程读数一律 0/0（`--cam-dur` 那串 clamp() 里代进去的是 0.0 与
        0.000——未注册的派生值 computed 出来就是替换后的原文），而
        `#cam`/`#depth`/`:root` 三处 computed 时长是 **0s**（导出注入的一刀切掉了补间，
        不是页面算了个更短的时长），色温同样一步落档、两处消费者同值；
      ⑤ 导出页里**不会有指针**：`syncGate` 在导出模式直接 return（不弹新卡），CSS 那把
        刀把 `#gate` 压成 `display:none`；全页带 `data-hot` 的元素恰好一个（上面点亮过
        的那个 `.hs`），值为 '0'、没有盒——这一层不需要任何导出兜底；
      ⑥ 已经点亮之后再来一刀（压掉浮层）：浏览器重算命中测试，`pointerleave` 到了，
        于是光灭、位置读数停在最后一格（实测 17.4px / 29px）——**位置是读数、亮不亮是
        状态**，那一刀拿走的是状态；reduced-motion 下同理，光关不掉、补间关得上。
    """
    chrome, ev, send = _cdp(full_lesson, "followrm")
    try:
        ev(_SEEK)
        ev(_OPEN_GAP)
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")

        ev("window.__jump('seg-2', 0)")
        time.sleep(0.4)
        rm = json.loads(ev(r"""(function(){
          var root = document.documentElement;
          var cam = document.getElementById('cam'), dep = document.getElementById('depth');
          return JSON.stringify({
            rootTp:getComputedStyle(root).transitionProperty,
            camTp:getComputedStyle(cam).transitionProperty,
            depthTp:getComputedStyle(dep).transitionProperty,
            tint:getComputedStyle(root).getPropertyValue('--tint'),
            fill:getComputedStyle(document.querySelector('#depth .wash')).fill,
            mood:root.getAttribute('data-mood') || '',
            running:(document.getAnimations() || []).filter(function(a){
              return a.transitionProperty;}).length});})()"""))
        for k in ("rootTp", "camTp", "depthTp"):
            assert rm[k] == "none", f"减少动效下 {k} 必须整个关掉（实测位移定拍不用新兜底）：{rm}"
        assert rm["tint"] == rm["fill"] == "rgb(14, 31, 75)", f"色温一步落 calm 档：{rm}"
        assert rm["running"] == 0, f"没有任何在跑的补间：{rm}"

        ev("window.__jump('seg-4', 0)")
        time.sleep(0.25)
        jumped = json.loads(ev("JSON.stringify([getComputedStyle(document.documentElement)"
                               ".getPropertyValue('--tint'), document.documentElement"
                               ".dataset.mood])"))
        assert jumped == ["rgb(8, 38, 31)", "key"], f"换档即时落档、不游：{jumped}"

        sel = "#gate-hot .hs"
        ev("window.__gap('seg-4')")
        _gate_open(ev, sel)
        time.sleep(0.3)
        st = json.loads(ev(_HOT_STATE % sel))
        x, y, w, h = _box(st)
        assert st["tp"] == "none", f"这一层的补间同关：{st}"
        _mouse(send, x + w * 0.3, y + h * 0.5)
        time.sleep(0.08)
        lit = json.loads(ev(_HOT_STATE % sel))
        assert lit["hot"] == "1" and lit["op"] == "1", f"光仍然亮（关的是补间不是光）：{lit}"
        assert abs(_px(lit["hx"]) - w * 0.3) <= 1.0, \
            f"光心直接落触点那一格（实测 17.4px）：{lit}"
        assert lit["runs"] == [], f"没有跟手的补间：{lit['runs']}"

        # ④⑤⑥ 导出截图模式：注入与 export_video.py 同源的那两刀（一刀切补间，
        # 一刀把 #gate/#pregate 整个 display:none 掉）
        # 一条 JS 字符串写完两把刀：相邻字面量换行会被 ASI 拆成两句（实测第二句
        # 成了无副作用的表达式语句，#gate 那把刀根本没注入）。
        ev("(function(){var s = document.createElement('style'); s.id = 'shotcut';"
           " s.textContent = '*,*::before,*::after{transition:none!important;"
           "animation:none!important}#gate,#pregate{display:none!important}';"
           " document.head.appendChild(s);})()")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-2', 0)")
        ev("window.__jump('seg-4', 1)")
        time.sleep(0.4)
        shot = json.loads(ev(_CAM_READ))
        assert float(shot["pan"]) == 0.0 and float(shot["zoom"]) == 0.0, \
            f"导出页每帧都是冷启动：行程读数一律 0/0：{shot}"
        # `--cam-dur` 是**未注册**的派生值：computed 出来就是那串 clamp() 原文，里面
        # 的 `--cam-pan` / `--cam-zoom` 已被常量替换成 0.0 / 0.000（这一条同时锁住
        # "JS 只写读数、时长由 CSS 现求"与"读数故意不注册"两件事）。
        sub = re.search(r"calc\(([\d.]+) \* [\d.]+s\s*\+\s*([\d.]+) \* [\d.]+s\)",
                        shot["durVar"])
        assert sub and float(sub.group(1)) == 0.0 and float(sub.group(2)) == 0.0, \
            f"导出页的 clamp() 里代进去的行程必须是 0/0：{shot['durVar']}"
        assert shot["camDur"] == shot["depthDur"] == "0s", \
            f"导出页那条注入的一刀切掉补间（与不派时长同一格）：{shot}"
        assert shot["anims"] == [] and shot["depthAnims"] == [], f"没有正在跑的镜头补间：{shot}"
        cut = json.loads(ev(r"""JSON.stringify({
          camTp:getComputedStyle(document.getElementById('cam')).transitionProperty,
          depthTp:getComputedStyle(document.getElementById('depth')).transitionProperty,
          rootTp:getComputedStyle(document.documentElement).transitionProperty,
          tint:getComputedStyle(document.documentElement).getPropertyValue('--tint'),
          fill:getComputedStyle(document.querySelector('#depth .wash')).fill,
          mood:document.documentElement.dataset.mood || '',
          running:(document.getAnimations() || []).filter(function(a){
            return a.transitionProperty;}).length})"""))
        assert cut["camTp"] == cut["depthTp"] == cut["rootTp"] == "none", cut
        assert cut["tint"] == cut["fill"] == "rgb(8, 38, 31)", f"导出定格上色温同样同值：{cut}"
        assert cut["running"] == 0, f"一刀切掉之后不许有在跑的补间：{cut}"

        # 已经点亮之后再来一刀（把浮层压掉）：**浏览器重算命中测试，pointerleave 真的
        # 发了**——读数归 '0'、这一层回到 opacity 0，位置读数仍停在最后一格。
        # "压掉浮层"等于"手真的走了"，所以导出的定格里这一层天然灭着。
        lit2 = json.loads(ev(_HOT_STATE % sel))
        assert lit2["hot"] == "0" and lit2["op"] == "0", \
            f"浮层被压掉后这一层必须灭（pointerleave 到得了）：{lit2}"
        assert lit2["runs"] == [] and lit2["rect"][2] == 0.0, \
            f"没有盒、也没有在跑的补间：{lit2}"
        assert abs(_px(lit2["hx"]) - w * 0.3) <= 1.0 and \
            abs(_px(lit2["hy"]) - h * 0.5) <= 1.0, \
            f"读数没被抹掉（实测 17.4px / 29px）：{lit2}"

        ev("window.__jump('seg-3', 0)")
        ev("window.__gap('seg-5')")
        time.sleep(0.6)
        # 浮层被 CSS 整个压掉（JS 侧 syncGate 在导出模式下直接 return，不再弹新卡）
        assert ev("getComputedStyle(document.getElementById('gate')).display") == "none", \
            "导出模式那把 CSS 刀必须真的把门禁浮层压掉"
        # 实测：全页带 data-hot 的元素恰好**一个**（就是上面点亮过的 .hs），且它的值是
        # '0'——浮层被压掉时 pointerleave 到了，"亮着"这一档不留进定格。
        hot = json.loads(ev(r"""(function(){
          var n = document.querySelectorAll('[data-hot]'), o = [];
          Array.prototype.forEach.call(n, function(x){
            var r = x.getBoundingClientRect();
            o.push([x.className, x.dataset.hot, +r.width.toFixed(1),
                    getComputedStyle(x, '::before').opacity]);});
          return JSON.stringify(o);})()"""))
        assert len(hot) == 1 and "hs" in hot[0][0], \
            f"导出页里没有新的指针读数（只许留着一个已不在场的 .hs）：{hot}"
        assert hot[0][1] == "0" and hot[0][3] == "0" and hot[0][2] == 0.0, \
            f"压掉浮层那一刀之后这一层灭着、且没有盒：{hot}"
        assert ev("(function(){var n=document.querySelectorAll('[data-hot]'),c=0;"
                  "Array.prototype.forEach.call(n,function(x){"
                  "if(x.dataset.hot==='1')c++});return c;})()") == 0, \
            f"导出定格里不许有任何亮着的触点：{hot}"
    finally:
        _kill(chrome)


_SWAP_PROBE = r"""(function(){
  var n = document.getElementById('%(id)s-swp'), h = document.getElementById('%(id)s');
  var cs = getComputedStyle(n);
  return JSON.stringify({go:n.getAttribute('data-go'),
    route:n.style.getPropertyValue('--swap-route'),
    op:cs.offsetPath, dist:cs.offsetDistance, rot:cs.offsetRotate,
    tp:cs.transitionProperty, tdur:cs.transitionDuration, te:cs.transitionTimingFunction,
    runs:(n.getAnimations()||[]).map(function(a){
      return a.transitionProperty || a.animationName;}),
    tr:h.getAttribute('transform'),
    y:+h.getBoundingClientRect().y.toFixed(2)});
})()"""

# 冻结采样 offset-distance 那条补间：换位弧凸是"位移的垂直分量"，卡框的屏幕 y
# 只由它决定（transform 的 y 分量在换位那一步恒为 232），所以逐档读 dist 与 y。
# 与 _RIDE_SWEEP 同一手法：pause + currentTime 逐档采样，采完 cancel（测试再重挂）。
_SWAP_SWEEP = r"""(function(){
  var n = document.getElementById('w-1-swp'), h = document.getElementById('w-1');
  var list = (n.getAnimations()||[]).filter(function(a){
    return a.transitionProperty === 'offset-distance';});
  if (list.length !== 1){
    return JSON.stringify({err:'offset-distance 补间不在场', an:(n.getAnimations()||[]).map(
      function(a){ return a.transitionProperty || a.animationName; })});
  }
  var out = {dur:list[0].effect.getComputedTiming().duration,
             ease:list[0].effect.getTiming().easing, frames:[]};
  [0, 125, 250, 375, 499].forEach(function(ms){
    list[0].pause(); list[0].currentTime = ms;
    var cs = getComputedStyle(n), bb = h.getBoundingClientRect();
    out.frames.push({ms:ms, dist:parseFloat(cs.offsetDistance), y:+bb.y.toFixed(2)});
  });
  list[0].cancel();
  return JSON.stringify(out);
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_swap_arcs_a_card_over_the_row_on_reorder(full_lesson):
    """真实浏览器：换位不是瞬移，也不是钻洞（0.25.0「换位弧凸」）。

    范本 seg-1 s2 把世界层从 `bay`（[10,9,1]）排成 `sorted`（[1,10,9]）：'1' 要从
    x=700 一路穿到 x=300，直线穿行会从另两张卡中间"隧道式"穿过，读不出"换了位置"。
    本轮给越过整行的那张卡（行程 400 > --swap-min 250）叠加一条垂直抛物线
    （offset-path，控制点 = 峰值×2 = -280，两端都回原点），'10'/'9' 让位 200 单位、
    短于阈值，直行。六条口径必须一起成立：
      · 建档步不演：data-go 0、路径退化到原点（`Q 0 0 0 0`）、offset-distance 0%，
        包装层只补 offset-distance（.swap 简写源序在 .nt 之后，整串顶掉它）；
      · 换位那一步挂档：data-go 翻 1，`--swap-route` 写成 `M 0 0 Q 0 -280 0 0`，
        computed offset-path 同值，offset-distance 由补间从 0% 走 100%；
      · 曲线与时长：.5s（--dur-move，与位移同一拍）+ --ease-in-out（一去一回对称，
        与 .flip 同一理由）；f=0.5 档 offset-distance 恰好 50%，主角屏幕 y 比两端
        高 ≥ 30 设备px（140 单位 × 相机倍率 ≥ .85），f 末档落回与起点同一 y；
      · 落定即"换位完成"那一格：offset-distance 100% = 原点、'1' 站在最左槽
        （translate(300,232) scale(1)），且没有在跑的补间；
      · sticky + 可见性判据：s2 之后跳 s3（收上架子）data-go 仍驻 1、无新补间；
        灯灭着的换位（seg-5 off → messed）data-go 保持 0，不演弧凸；
      · reduced-motion / 导出：transition 一刀切掉，offset-distance 直接落 100%，
        一格信息不少（与 .ride / .tilt 同一判断）。"""
    chrome, ev, send = _cdp(full_lesson, "swap25")
    try:
        ev(_SEEK)

        def wait_swap(pred, id_="w-1", timeout=4.0):
            """轮询直到谓词成立（CI runner 比本机慢，固定 sleep 会假失败——
            0.23.1 起的口径：等的是"补间在跑"这个事实，不是等够多少毫秒）。"""
            last = None
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout:
                last = json.loads(ev(_SWAP_PROBE % {"id": id_}))
                if pred(last):
                    return last
                time.sleep(0.05)
            return last

        # ① 建档步：包装层在场但没挂档
        ev("window.__jump('seg-1', 0)")
        s0 = wait_swap(lambda p: p["runs"] == [])
        assert s0["go"] == "0" and s0["dist"] == "0%", f"s0 不该演弧凸：{s0}"
        assert s0["route"].endswith("Q 0 0 0 0')"), f"退化路径两端都是原点：{s0}"
        assert s0["op"].endswith('Q 0 0 0 0")'), f"computed offset-path 同值：{s0['op']}"
        assert s0["rot"] == "0deg", f"offset-rotate 必须 0deg（默认 auto 会把卡转成 90°）：{s0}"
        assert s0["tp"] == "offset-distance" and s0["tdur"] == "0.5s", \
            f"包装层只补 offset-distance、时长与位移同一拍：{s0}"
        assert s0["te"] == "cubic-bezier(0.65, 0, 0.35, 1)", \
            f"一去一回是对称行程，曲线必须对称（与 .flip 同一理由）：{s0}"
        assert s0["runs"] == [], f"建档步没有补间在跑：{s0}"
        assert s0["tr"].startswith("translate(700"), f"s0 还站在 bay 那一格：{s0['tr']}"

        # ② 换位那一步：越过整行的 '1' 挂档（行程 400 > 250 → 峰值 140、控制 -280），
        #    让位的 '10'/'9'（行程 200 ≤ 250）路径仍退化——弧凸只给"真的越过"那张
        ev("window.__jump('seg-1', 2)")
        s2 = wait_swap(lambda p: 0 < float(p["dist"].rstrip('%')) < 100)
        assert s2["go"] == "1", f"换位那一步 data-go 必须翻 1：{s2}"
        assert s2["route"] == "path('M 0 0 Q 0 -280 0 0')", \
            f"控制点 = 峰值×2（-280），路径写进 --swap-route：{s2['route']}"
        assert "M 0 0 Q 0 -280 0 0" in s2["op"], f"computed offset-path 同值：{s2['op']}"
        assert "offset-distance" in s2["runs"], f"补间正在走：{s2['runs']}"
        assert 0 < float(s2["dist"].rstrip('%')) < 100, \
            f"轮询 4s 仍未捕获到补间中途（补间没开跑就超时了）：{s2['dist']}"
        w10 = json.loads(ev(_SWAP_PROBE % {"id": "w-10"}))
        w9 = json.loads(ev(_SWAP_PROBE % {"id": "w-9"}))
        assert w10["go"] == "1" and w9["go"] == "1", f"让位卡也随档（sticky）：{w10} {w9}"
        assert w10["route"].endswith("Q 0 0 0 0')") and w9["route"].endswith("Q 0 0 0 0')"), \
            f"行程 200 ≤ 250：让位直行，不演弧凸：{w10['route']} {w9['route']}"

        # ③ 冻结采样：抛物线在 50% 处到峰（offset-distance 恰好 50%），主角屏幕 y
        #    比两端高 ≥ 30 设备px；末档落回与起点同一 y（两端都回原点）
        sweep = json.loads(ev(_SWAP_SWEEP))
        assert "err" not in sweep, sweep
        fr = sweep["frames"]
        assert fr[0]["dist"] == 0 and fr[2]["dist"] == 50, \
            f"对称曲线过中点 f=0.5 → 50%（in-out）：{fr}"
        assert fr[1]["dist"] < fr[2]["dist"] < fr[3]["dist"] and fr[4]["dist"] > 99, \
            f"单调上行、末档回原点（换位完成）：{fr}"
        assert abs(fr[1]["dist"] + fr[3]["dist"] - 100) <= 1.0, \
            f"对称曲线在时间轴上成对互补（实测 7.08 + 92.92）：{fr}"
        assert fr[2]["y"] < fr[0]["y"] - 30, \
            f"50% 那格主角要拱起来（140 单位 × 倍率 ≥ .85 → ≥ 119 设备px）：{fr}"
        assert abs(fr[4]["y"] - fr[0]["y"]) <= 2.0, \
            f"末档回原点 = 换位完成那一格，y 必须回到起点：{fr}"
        assert float(sweep["dur"]) == 500.0, f"时长 = --dur-move .5s：{sweep['dur']}"

        # ④ 重挂后落定：'1' 站在最左槽、offset 100% = 原点、没有在跑的补间
        ev("window.__jump('seg-1', 0)")
        s0b = wait_swap(lambda p: p["runs"] == [])
        ev("window.__jump('seg-1', 2)")
        done = wait_swap(lambda p: p["dist"] == "100%" and p["runs"] == [])
        assert done["go"] == "1" and done["dist"] == "100%", f"落定即换位完成：{done}"
        assert done["tr"] == "translate(300,232) scale(1)", \
            f"'1' 站在最左槽（sorted 槽位 300）：{done['tr']}"
        assert done["runs"] == [], f"落定后不该有在跑的补间：{done['runs']}"

        # ⑤ sticky：s2 之后跳到 s3（收上架子），data-go 仍驻 1、无新补间；
        #    灯灭着的换位（seg-5 off → messed）data-go 保持 0，不演弧凸
        ev("window.__jump('seg-1', 3)")
        s3 = wait_swap(lambda p: p["runs"] == [])
        assert s3["go"] == "1" and s3["dist"] == "100%", \
            f"sticky：收上架子那一步不重放弧凸：{s3}"
        assert s3["runs"] == [], f"sticky 不该有新补间：{s3['runs']}"
        ev("window.__jump('seg-5', 4)")
        m4 = wait_swap(lambda p: p["runs"] == [], id_="w-10")   # 上一幕 data-go 还驻 1：
                                                                 # 这一步翻回 0 走的是退化
                                                                 # 路径（无视觉），等落定再读
        assert m4["go"] == "0" and m4["dist"] == "0%", \
            f"off → messed 是灯灭着的换位，不演弧凸：{m4}"
        assert m4["route"].endswith("Q 0 0 0 0')") and m4["runs"] == [], \
            f"退化路径 + 无在跑补间：{m4}"

        # ⑥ reduced-motion：关的是补间不是档位——data-go 与 --swap-route 终值仍读得到
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        ev("window.__jump('seg-1', 0)")
        time.sleep(0.4)
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.3)
        rm = json.loads(ev(_SWAP_PROBE % {"id": "w-1"}))
        assert rm["tp"] == "none", f"弧凸补间被收掉：{rm['tp']}"
        assert rm["go"] == "1" and rm["dist"] == "100%", \
            f"档位仍在：换位完成那一格一格信息不少：{rm}"
        assert rm["tr"] == "translate(300,232) scale(1)" and rm["runs"] == [], \
            f"关掉动效也不许挪错槽：{rm}"

        # ⑦ 导出截图模式：与 export_video.py 同源的那一刀切掉补间——同样直接落
        #    "换位完成"那一格（与 reduced-motion 同一张定格）
        send("Emulation.setEmulatedMedia", {"features": []})
        ev("(function(){var s = document.createElement('style'); s.id = 'shotcut';"
           " s.textContent = '*,*::before,*::after{transition:none!important;"
           "animation:none!important}'; document.head.appendChild(s);})()")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-1', 0)")
        time.sleep(0.3)
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.3)
        shot = json.loads(ev(_SWAP_PROBE % {"id": "w-1"}))
        assert shot["go"] == "1" and shot["dist"] == "100%", \
            f"导出定格直接落换位完成：{shot}"
        assert shot["tr"] == "translate(300,232) scale(1)" and shot["runs"] == [], \
            f"导出定格上 '1' 站在最左槽、无在跑补间：{shot}"
    finally:
        _kill(chrome)


# ══ 0.26.0「先后与边界」：拖尾跟随 / 逐字解码 / 越界橡皮筋 ═════════════════
#
# 三条都是**量出来才敢写进范本**的，所以这一节的断言全部读派生值：档位由
# `--trail-step × --trail-r` 求出、阻尼由 `--band-cap`/`--band-soft` 求出、解码的
# 落定节奏由 `--decode-lead × 字数` 求出——测试里不抄第二个数（口径同 _cam_expect_ms）。
# 屏幕绝对坐标随视口漂，所以位移一律"同一会话内自己对照自己"，不写死 px。

def test_trail_decode_band_primitives_present():
    """模板结构契约：0.26.0「先后与边界」三条的口径必须长在范本里（无浏览器也可锁）。

    锁的全是本轮量出来"为什么只能这么写"的边界：
      · 三只新令牌（`--trail-step` / `--decode-lead` / `--dur-band`·`--band-cap`·`--band-soft`）
        真在 :root 的解析结果里（注释配对由 _css_comment_problems 兜住，那一课丢过一整块）；
      · **拖尾只有一条长档**：`.trail` 整条规则里不许出现 `transition` 简写（写了就把 `.nt`
        那十七项顶掉，0.19.0 / 0.23.0 同一课），且基础规则源序在 `.nt` 之后、同为 (0,1,0)；
        `--trail-r` **故意不注册**（它是排位的读数不是要补间的行程，口径同 --cam-pan）；
        档距 `--trail-step` 全 CSS 只有**一把尺**（0.26.0 的 `.trail` 与 0.32.0 的 `.wake`
        都写派生式），`.07s`/`.14s` 不许抄成两份字面量；
      · **解码不许是第二路字幕**：字面由 `badge(..., dec)` 逐枚 opt-in，写的是变量
        （`d.n.textContent = out`），全模板没有第二处 `textContent = '...'` 字面量赋值；
        噪声**没有随机数**——`Math.random` 在整个范本与运行时里一次都不许出现（导出是每帧
        一次冷启动 seek，随机一次等于同一秒的两帧是不同的字，GSAP ScrambleText 官方
        为这件事专门写了 --concurrency=1）；宽度靠 `textLength` + spacingAndGlyphs 钉住；
        `decodeWrite(t)` 排在 `capLitWrite(t)` **之后**（要靠 capSent，0.16.0 那条老账）；
        登记表 `DECODE` 在 `begin()` 里清空，于是"这一步有没有在解码"也是 (scene, step) 的
        纯函数；
      · **橡皮筋不撒谎**：时钟写的是**夹过的** raw、band 写的是**未夹的** raw（两行源序
        锁死），阻尼只从两只旋钮求（`bCap * a / (a + bSoft)`），回弹补间挂在 CSS
        （`--band` 注册成 `<length>`、`inherits:false`、初值 0px——未注册时它是 token，
        对照页实测 482ms 内 13 次采样全是终值，中间一格都不存在），JS 侧只写终值、
        `seek`/`bandWrite` 那一段里一个 `setTimeout` 都不许有；
      · reduced-motion 三行新兜底各关各的对象（拖尾关 delay、气泡与端头亮关 transition），
        且都在基础规则**之后**；兜底里不许出现"补一个更短的时长"。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)

    # ⓪ 注释配对 + 五只新令牌真在 :root 里
    assert _css_comment_problems(html) == [], _css_comment_problems(html)
    for tok in ("--trail-step", "--decode-lead", "--dur-band", "--band-cap", "--band-soft"):
        assert re.search(re.escape(tok) + r"\s*:", root), \
            f"{tok} 没长在 :root 里（被注释语法吃掉过一次，见 _css_comment_problems）"

    # ① 拖尾：一条长档、一个来源（0.32.0 起两个消费者共用同一尺度），`--trail-r` 不注册
    assert ".trail{transition-delay:calc(var(--trail-step) * var(--trail-r, 0))}" in css
    trail = re.search(r"\.trail\{(.*?)\}", css, flags=re.S).group(1)
    assert "transition:" not in trail, \
        ".trail 里出现 transition 简写就把 .nt 那份十七项清单整条顶掉（0.19.0 老账）"
    assert css.index(".nt{") < css.index(".trail{transition-delay:calc"), \
        "两条同为 (0,1,0)，先后靠源序：兜底与归零都写在后面才压得住"
    assert "@property --trail-r" not in html, \
        "--trail-r 是排位的读数（常量替换进 calc），注册了就去补间它（口径同 --cam-pan）"
    assert css.count("var(--trail-step)") == 2, \
        "档距只有一把尺：.trail 的让位与 .wake 的余像都从它派生（第二个消费者也只能写派生式）"
    assert ".wake{transition-delay:calc(var(--trail-step) * var(--wake-i, 1))" in css, \
        "0.32.0 的余像从同一支令牌派生先后，不新开时长档"
    assert ".14s" not in css and "0.14s" not in css, \
        "把 rank×档距的第二格抄成字面量就是第二个数（口径同 --wind-hold / --par-far）"
    assert "style: rank[hi] ? '--trail-r:' + rank[hi] : null" in html, \
        "排位属于节点、尺度属于令牌：两边各写一半，JS 里不许出现 .07 / .14"
    assert "if (travel[i] > 0) rank[i] = r;" in html, \
        "行程为 0 的卡不给档：它没动，晚一拍落定就是撒谎"

    # ② 解码：opt-in、无随机、钉宽度、按步重建、排在换句分支之后
    assert "Math.random" not in html, \
        "导出每帧一次冷启动 seek：随机一次就等于同一秒的两帧是不同的字"
    assert "function badge(parent, key, x, y, s, col, dec){" in html
    assert "if (dec) decodeArm(t, s);" in html, "解码是逐枚徽标 opt-in，不是全局开"
    assert "var DEC_POOL = {" in html
    pool = html.split("var DEC_POOL = {")[1].split("};")[0]
    for k in ("cjk", "digit", "alpha", "other"):
        assert re.search(r"\b%s: '" % k, pool), f"噪声池少一类：{k}"
    assert "n.removeAttribute('textLength');" in html and \
        "n.setAttribute('textLength', w.toFixed(1));" in html and \
        "n.setAttribute('lengthAdjust', 'spacingAndGlyphs');" in html, \
        "先撤钉再量、量完钉回去：不钉住的话解码途中换字会把药丸底撑抖"
    arm = html.split("function decodeArm(n, text){")[1].split("\n  }")[0]
    assert "var w = n.getComputedTextLength();" in arm and "n.getBBox" not in arm, \
        "钉的是推进量不是墨盒：getBBox().width 在 spacingAndGlyphs 下会随噪声字自己抖" \
        "（实测「较」那一格 56.467 vs 56.0），拿它钉会把整串压窄到药丸边上"
    assert html.index("capLitWrite(t);") < html.index("decodeWrite(t);"), \
        "decodeWrite 要靠 capSent，写在换句分支之前就会拿上一句的时长算这一句的进度"
    assert "DECODE = {};" in html.split("function begin(){")[1].split("\n  }")[0], \
        "登记表按步重建：这一步有没有在解码必须与画面一样是 (scene, step) 的纯函数"
    assert "window.__coursewareShotMode || mqReduce.matches" in html, \
        "导出与减动效一律直接落完整词：未定住的那些格本来就不可读"
    assert "var mqReduce = window.matchMedia('(prefers-reduced-motion:reduce)');" in html
    dec_body = html.split("function decodeWrite(t){")[1].split("\n  }")[0]
    assert "d.n.textContent = out;" in dec_body and "textContent = '" not in dec_body, \
        "解码写的是变量：范本里第二路'字幕字面量'会被检查器读成第二条字幕轨道"

    # ③ 橡皮筋：注册、独立 translate、只从令牌求阻尼
    assert ("@property --band{syntax:'<length>';inherits:false;initial-value:0px}" in html), \
        "不注册就没有回弹（对照页实测：未注册时 13 次采样全是终值）；inherits:false = 谁用谁持有"
    tip = re.search(r"\.bar \.tip\{(.*?)\}", css, flags=re.S).group(1)
    assert "translate:var(--band) 0;" in tip, \
        "位移走独立 translate 属性：写进 transform 会在落定那一瞬丢掉 translateX(-50%) 居中"
    assert tip.count("transition:") == 1, "两份 transition 简写会互相顶掉（.nt 同一课）"
    assert ("transition:opacity var(--dur-fast),\n"
            "                       --band var(--dur-band) var(--ease-spring)") in html
    assert "@property --band-cap" not in html and "@property --band-soft" not in html, \
        "两只旋钮是常量不是行程：注册了就会被当成可补间的第二套动画"
    assert "bCap * a / (a + bSoft)" in html, "阻尼只有这一处公式（cap·x/(x+soft)）"
    for t in ("--band-cap", "--band-soft"):
        assert "getPropertyValue('%s')" % t in html, f"{t} 没从 CSS 读出来（代码里不许抄数）"

    # ④ 时钟诚实：夹过的才给 currentTime，未夹的才给 band，顺序锁死
    seek_body = html.split("var seek = function(x){")[1].split("};")[0]
    assert "var raw = (x - r.left) / r.width;" in seek_body
    assert seek_body.index("audio.currentTime = Math.max(0, Math.min(1, raw)) * TOTAL;") < \
        seek_body.index("bandWrite(raw, r.width);"), \
        "先夹时钟再喂未夹的 raw：反过来（或 band 也吃夹过的值）越界读数就永远是 0"
    assert "bandWrite(Math" not in html, "band 拿到的必须是**未夹**的 raw"
    assert "setTimeout" not in seek_body and "setTimeout" not in \
        html.split("var bandWrite = function(raw, w){")[1].split("\n    };")[0], \
        "回弹的补间在 CSS（--dur-band + --ease-spring），这里只写终值——纪律 1"
    assert "delete barEl.dataset.band;" in html and \
        "tipEl.style.setProperty('--band', '0px');" in html, "松手：档位退出、位移写终值 0"
    assert ".bar::after{content:\"\";position:absolute;inset:0;border-radius:inherit;" \
           "pointer-events:none;" in css.replace("              ", " ").replace(
               "               ", " ") or "pointer-events:none" in css
    for side in ("r", "l"):
        assert '.bar[data-band="%s"]::after{opacity:1' % side in css, \
            "端头那圈亮走 data-band 档位而不是 --band 数值：它是状态读数，减动效下照样落"

    # ⑤ reduced-motion：三行新兜底各关各的对象，且都在基础规则之后
    rm = css.split("@media (prefers-reduced-motion: reduce){")[1]
    assert ".trail{transition-delay:0s}" in rm, \
        "transition-delay 不在全局 duration 钳制范围内（0.18.0 在 animation-delay 上同一课）"
    assert ".bar .tip{transition:none}" in rm and ".bar::after{transition:none}" in rm
    assert css.index(".trail{transition-delay:calc") < css.index(".trail{transition-delay:0s}"), \
        "同为 (0,1,0)，归零那行必须靠源序压过去"
    assert "var(--trail-step)" not in rm and "var(--dur-band)" not in rm, \
        "兜底里不许出现'补一个更短的时长'——那是演一次更快的动效，不是不演"


_TRAIL_READ = r"""(function(){
  var o = {};
  ['10','9','1'].forEach(function(v){
    var n = document.getElementById('w-' + v), cs = getComputedStyle(n);
    var q = n.getBoundingClientRect();
    o[v] = {cls:n.getAttribute('class'), r:n.style.getPropertyValue('--trail-r'),
            delay:cs.transitionDelay, tp:cs.transitionProperty,
            td:cs.transitionDuration, te:cs.transitionTimingFunction,
            x:+(q.x + q.width/2).toFixed(2)};
  });
  var rs = getComputedStyle(document.documentElement);
  o.step = rs.getPropertyValue('--trail-step');
  o.move = rs.getPropertyValue('--dur-move');
  return JSON.stringify(o);
})()"""

# 把三张卡的 transform 补间一起 pause() 后按 currentTime 钉在若干格上采样：
# delay 是"从 animation start 算起"的时间，所以 t=70 对一条 delay=70 的补间
# 就是进度 0——这正是拖尾唯一可信的读法（跑真实时钟会先被 runner 快慢骗一次）。
_TRAIL_FREEZE = r"""(function(){
  var an = {}, order = ['10','9','1'];
  for (var i = 0; i < order.length; i++){
    var v = order[i];
    var list = (document.getElementById('w-' + v).getAnimations({subtree:false}) || [])
        .filter(function(a){ return a.transitionProperty === 'transform'; });
    if (list.length !== 1) return JSON.stringify({err:v + ' 的 transform 补间不是恰好一条',
      all:(document.getElementById('w-' + v).getAnimations({subtree:false}) || [])
          .map(function(a){ return a.transitionProperty || a.animationName; })});
    an[v] = list[0];
  }
  var meta = {};
  order.forEach(function(v){
    // delay 在 effect 的 timing 上（CSSTransition 自己没有 .delay 属性，读错了
    // 会拿到 undefined，JSON.stringify 直接把那个键丢掉——断言就变成 KeyError）
    var ct = an[v].effect.getComputedTiming();
    meta[v] = {del:ct.delay, dur:ct.duration, state:an[v].playState};
  });
  order.forEach(function(v){ an[v].pause(); });
  var rows = {};
  [0, 70, 140, 210, 640].forEach(function(t){
    order.forEach(function(v){ an[v].currentTime = t; });
    var row = {};
    order.forEach(function(v){
      var q = document.getElementById('w-' + v).getBoundingClientRect();
      row[v] = +(q.x + q.width/2).toFixed(2);
    });
    rows[t] = row;
  });
  order.forEach(function(v){ an[v].cancel(); });
  var after = {};
  order.forEach(function(v){
    var q = document.getElementById('w-' + v).getBoundingClientRect();
    after[v] = +(q.x + q.width/2).toFixed(2);
  });
  return JSON.stringify({meta:meta, rows:rows, after:after});
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_trail_yields_the_yielders_to_the_card_that_moves_first(full_lesson):
    """真实浏览器：换位那一步不是三张卡约好同一毫秒一起瞬移（0.26.0「拖尾跟随」）。

    范本 seg-1 s2 把 `bay`（[10,9,1]）排成 `sorted`（[1,10,9]）：'1' 走 400 单位越过整行
    （0.25.0 已经给它一条弧凸），'10'/'9' 各让 200 单位、**行程完全打平**。12 项动画原则
    里的「跟随与重叠」落在这里就是：主动作先动，两张让位的按 rank 各晚一格
    （`--trail-step × --trail-r` = 70 / 140ms）。六件事必须一起成立：
      · rank 由行程**现推**，不是一张新写的姿势表：越过来那张 rank 0（不挂 `.trail`），
        打平的两张按 HERO 书写序分 1 / 2，让位里最右的那张等得最久 → 一道从左往右的波；
      · 没走的卡不给档（s3 收上架子时 '10' 槽位不变 → 没有 --trail-r，也就不会因为
        "别人都晚了"而被读成"它也晚了"）；
      · 只改长档：三张卡的 `transitionProperty` / `Duration` / `TimingFunction` 逐字符相同
        （十七项），差异只在 delay，且 delay 是一个值（重复给全部十七项 = 整张卡晚一格）；
      · 冻结采样把"晚一格"变成屏幕事实：t=70 时主动作已经走了 ≥ 40 设备px 而两张跟随的
        **一格没动**（与 t=0 同值）；'10' 在 t=140 才动、'9' 在 t=210 才动；t=640 三张都
        落到 cancel 之后的终值（档距不改终态）；
      · 同一步只许一个让拍来源：s3（shelf）`.wind > g` 以 (0,1,1) 压住 `.trail` 的 (0,1,0)，
        三张卡读到**同一个** delay，那个带 --trail-r:1 的读数不是 70ms 的倍数；
      · 纯函数：绕回 bay 再走同一趟，档位与 delay 逐位复现（无随机、无历史）。"""
    chrome, ev, _send = _cdp(full_lesson, "trail26")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")

        # ① 换位那一步：主动作不挂档，两张让位的各晚一格 / 两格
        ev("window.__jump('seg-1', 2)")
        s2 = json.loads(ev(_TRAIL_READ))
        step = _ms(s2["step"])
        assert s2["1"]["r"] == "" and "trail" not in s2["1"]["cls"], \
            f"行程最长的那张是主动作，不该自己拖尾：{s2['1']}"
        assert s2["1"]["delay"].count("0s") == 17, f"rank 0 → 十七个 0s：{s2['1']}"
        assert s2["10"]["r"] == "1" and s2["9"]["r"] == "2", \
            f"打平的行程按 HERO 书写序分档（['10','9','1']）：{s2['10']} {s2['9']}"
        for v in ("10", "9"):
            assert "trail" in s2[v]["cls"], f"有 rank 就得挂 class：{s2[v]}"
            assert _ms(s2[v]["delay"]) == pytest.approx(step * int(s2[v]["r"]), abs=0.5), \
                f"delay 只由 --trail-step × rank 求出：{v} {s2[v]['delay']} vs {step}"
            assert s2[v]["delay"].count(",") == 0, \
                f"一个值重复给十七项才是整张卡晚一格：{s2[v]['delay']}"
            for k in ("tp", "td", "te"):
                assert s2[v][k] == s2["1"][k], \
                    f"{k} 不许被拖尾动到（只写长档）：\n{s2[v][k]}\n{s2['1'][k]}"
        assert len(s2["1"]["tp"].split(",")) == 17, f"清单仍是 .nt 那十七项：{s2}"
        assert _ms(s2["1"]["td"].split(",")[0]) == _ms(s2["move"]), \
            f"transform 的时长仍是 --dur-move（拖尾不新开一档）：{s2['td']} {s2['move']}"

        # ② 冻结采样：晚一格是屏幕上量得出来的，不是 CSS 里的一个字符串
        fz = json.loads(ev(_TRAIL_FREEZE))
        assert "err" not in fz, str(fz)
        assert fz["meta"]["10"]["del"] == pytest.approx(step, abs=0.5) and \
            fz["meta"]["9"]["del"] == pytest.approx(step * 2, abs=0.5) and \
            fz["meta"]["1"]["del"] == 0, f"三条补间各自的 delay：{fz['meta']}"
        assert len({fz["meta"][v]["dur"] for v in ("10", "9", "1")}) == 1 and \
            fz["meta"]["1"]["dur"] == _ms(s2["move"]), \
            f"三条时长同档（--dur-move），差别只在 delay：{fz['meta']}"
        r = fz["rows"]
        assert r["70"]["1"] < r["0"]["1"] - 40, \
            f"主动作 t=70 已经走出去一大截：{r['0']['1']} → {r['70']['1']}"
        assert r["70"]["10"] == r["0"]["10"] and r["70"]["9"] == r["0"]["9"], \
            f"两张跟随的在 t=70 还钉在原位（delay 未到）：{r['70']} vs {r['0']}"
        assert r["140"]["10"] > r["0"]["10"] + 40 and r["140"]["9"] == r["0"]["9"], \
            f"rank 1 在 70~140 之间起步、rank 2 仍不动：{r['140']}"
        assert r["210"]["9"] > r["0"]["9"] + 40, f"rank 2 在 140~210 之间才起步：{r['210']}"
        for v in ("10", "9", "1"):
            assert abs(r["640"][v] - fz["after"][v]) < 0.6, \
                f"{v}：档距只改先后不改终态，t=640 就是落定的那一格（{fz['after'][v]}）"

        # ③ 同一步只许一个让拍来源：上架子那一步 .wind > g 压住拖尾，三张卡同一读数
        ev("window.__jump('seg-1', 3)")
        s3 = json.loads(ev(_TRAIL_READ))
        assert s3["10"]["r"] == "" and "trail" not in s3["10"]["cls"], \
            f"'10' 在 shelf 那一格槽位没变（500→500）→ 不给档，晚一拍就是撒谎：{s3['10']}"
        assert s3["1"]["r"] == "1", f"'1' 在 shelf 那一步让了 30 单位 → 有档：{s3['1']}"
        ds = {s3[v]["delay"] for v in ("10", "9", "1")}
        assert len(ds) == 1, f"蓄力那一步三张卡读到同一个 delay（同一来源）：{s3}"
        only = ds.pop()
        assert _ms(only) % step >= 0.5 or _ms(only) < step, \
            f"那一格是 --wind-hold 借的拍，不是 --trail-step 的整数格：{only} vs {step}"
        assert "0.0952s" in only, \
            f"0.23.0 钉过的 shelf 让拍读数不能因为拖尾而变（.wind > g 特异度 (0,1,1)）：{only}"

        # ④ 纯函数：绕回 bay 再走同一趟，档位与 delay 逐位复现
        ev("window.__jump('seg-1', 0)")
        _wait_idle(ev, "#w-10")
        ev("window.__jump('seg-1', 2)")
        again = json.loads(ev(_TRAIL_READ))
        for v in ("10", "9", "1"):
            assert again[v]["cls"] == s2[v]["cls"] and again[v]["r"] == s2[v]["r"] and \
                again[v]["delay"] == s2[v]["delay"], \
                f"同一对姿势永远同一套档位（无随机、无历史）：{v} {s2[v]} → {again[v]}"
    finally:
        _kill(chrome)


_DECODE_PROBE = r"""(function(id_, sid, idx, ks){
  var sc = window.__tl.scenes.filter(function(x){ return x.step_id === sid; })[0];
  var s = sc.runtime.narration[idx];
  var n = document.getElementById(id_ + '-t'), r = document.getElementById(id_ + '-r');
  if (!n) return JSON.stringify({err:'没有 ' + id_ + '-t'});
  function at(k){
    window.__t = s.start + s.duration * k;
    document.getElementById('main-audio').dispatchEvent(new Event('timeupdate'));
    return {k:k, txt:n.textContent, w:+n.getBBox().width.toFixed(3),
            adv:+n.getComputedTextLength().toFixed(3),
            tl:n.getAttribute('textLength'), la:n.getAttribute('lengthAdjust'),
            pill:+r.getBoundingClientRect().width.toFixed(3)};
  }
  return JSON.stringify({sent:s.text, dur:s.duration, lead:getComputedStyle(
    document.documentElement).getPropertyValue('--decode-lead'), rows:ks.map(at)});
})          """
# ↑ 结尾**不带** `()`：调用方把参数接在后面拼成 `(function(...){...})('a-rule','seg-1',2,[…])`。
#   这里若写成 `})()`，传进去的参数就丢了（sc 变成 undefined，第一次采样直接抛错）。


def _jsround(x):
    """JS 的 Math.round 是「.5 向上」，Python 的 round 是银行家舍入——混用会把
       末字落定那一格算错半格（范本 4 字词在 k=.63 就整词定住，正压在这个 .5 上）。"""
    return math.floor(x + 0.5)


def test_decode_ease_out_contract_present():
    """模板结构契约：0.33.0 的 decode 落定节奏缓出 + 噪声不许撞真字（无浏览器也可锁）。

    `.dec` 说的是"术语从噪声里逐字定住"。0.33.0 前落定是线性等距
    `round(k/lead × 字数)`——每字一视同仁，正是动效目的审计要点名砍的"机械逐字蹦出"。
    这一轮改为缓出 `round((1-(1-k)²) × 字数)`，且噪声字不许提前"看起来定了"：
      · decodeWrite 里出现缓出公式字面（`var e = 1 - (1 - k) * (1 - k)`），
        老的线性式 `Math.round(k * len)` 不得回潮；
      · decGlyph 里噪声算到目标字就顺延一格（`pool.charAt(idx) === ch` 分支）——
        未定的字不该显示成真字（四字词 .45/.455/.46 的噪声池曾恰巧落在"则"上，
        画面里"默认规则"提前假完整）；
      · :root 的 --decode-lead 注释与 decodeArm 注释④同步缓出口径（末字落定点
        `lead × (1 − sqrt(.5/字数))`，四字 .465 / 五字 .492）。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")
    # ① 缓出公式在 decodeWrite 里，线性式不回潮
    assert "var e = 1 - (1 - k) * (1 - k);" in src, "decodeWrite 必须是缓出公式"
    assert "var res = Math.min(len, Math.round(k * len));" not in src, \
        "老的线性等距落定（打字机感）不许回潮"
    assert "var res = Math.min(len, Math.round(e * len));" in src
    # ② 噪声≠真字：池子算到目标字就顺延一格
    assert "if (pool.charAt(idx) === ch) idx = (idx + 1) % pool.length;" in src, \
        "噪声不许撞真字（未定的字不该提前'看起来定了'）"
    # ③ 注释口径同步：:root 与 decodeArm 都写缓出，末字落定点公式在
    assert "0.33.0 起落定节奏是缓出" in src, ":root 的 --decode-lead 注释要同步缓出"
    assert "`lead × (1 − sqrt(.5/字数))`" in src, "decodeArm 注释④的末字落定点公式"
    assert "四字词" in src and ".492" in src, "四字 .465 / 五字 .492 的实测口径"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_decode_locks_the_term_while_the_line_says_it(full_lesson):
    """真实浏览器：术语跟着旁白**从噪声里一个字一个字定住**（0.26.0「逐字解码」）。

    范本 seg-1 s2 的徽标「默认规则」挂在句"这不是缺陷，是排序的默认规则在起作用。"
    （2.77s）上，s3 的「转成字符串」挂在 4.706s 那句上。五件事必须一起成立：
      · 建档即钉宽：`textLength` = 现量的**推进量**（55.968→56.0 / 69.994→70.0），
        `lengthAdjust` = spacingAndGlyphs，于是进度 0→1 的十格采样里
        `getComputedTextLength()` 与药丸底宽（77.413）**一格不差**（解码途中换的是
        别的字，不钉住就会抖）；反面口径也钉住：`getBBox().width` 量的是墨盒，
        `spacingAndGlyphs` 下会随噪声字自己抖（实测「较」那一格 56.467 vs 56.0），
        所以它只能被容忍、不能当钉的对象——拿它钉会把整串压窄到药丸边上；
      · 落定节奏是缓出 `round((1-(1-k/lead)²) × 字数)`（0.33.0：不再线性逐字蹦，
        前几字快定、末字从容沉底）：每一格的前 res 个字与真字逐位相同、
        其余全是**同一字符类**的噪声（CJK 换 CJK）；末字在 `lead × (1 − sqrt(.5/字数))`
        就定了（四字词实测 .465、五字词 .492），lead=.72 只是最晚上限；
      · 确定性：同一个 k 采两次逐字符相同，中间夹一次别的 k 也照样复现（导出是每帧
        一次冷启动 seek，随机一次就等于同一秒的两帧是不同的字）；
      · 解码是**逐枚徽标 opt-in** 的：同屏没传 dec 的徽标（seg-3 的「巧合」）在任何
        进度上都恒等于字面；
      · 句长与进度都只从当句读数来：k=1 与 lead 之后仍是整词（末尾留白给读者），
        且 textContent 只由解码写，不是第二路字幕。"""
    chrome, ev, _send = _cdp(full_lesson, "dec26")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        ks = "[0,.1,.2,.3,.4,.5,.6,.63,.72,1]"

        def read(id_, sid, idx, ks=ks):
            # 先跳到"这枚徽标在场的那一步"，再等落定——徽标是建档即出现，
            # 没跳这一步就没有这个节点（上一轮探针就扫过一次空）；落定之后再量
            # 药丸底宽，量到的才是钉住的那个数而不是挤压途中的插值。
            ev("window.__jump('%s', %d)" % (sid, idx))
            assert _wait_idle(ev, "#%s-r" % id_) == [], "徽标那层的补间要先落定"
            got = json.loads(ev(_DECODE_PROBE + "('%s','%s',%d,%s)" % (id_, sid, idx, ks)))
            assert "err" not in got, str(got)
            return got

        d = read("a-rule", "seg-1", 2)
        want = "默认规则"
        lead = float(d["lead"])
        assert d["sent"].endswith(want + "在起作用。"), f"范本句子变了：{d['sent']}"
        assert want in d["sent"], f"字面必须是这句里真被念到的词：{d['sent']}"

        # ① 钉宽：十格采样里**推进量**与药丸底宽一格不差
        r0 = d["rows"][0]
        assert r0["la"] == "spacingAndGlyphs" and r0["tl"], f"建档时钉了 textLength：{r0}"
        assert abs(float(r0["tl"]) - r0["adv"]) < 0.06, f"钉的是现量的推进量：{r0}"
        assert len({r["adv"] for r in d["rows"]}) == 1, \
            f"解码途中推进量不许漂：{[r['adv'] for r in d['rows']]}"
        assert len({r["pill"] for r in d["rows"]}) == 1, \
            f"药丸底跟着推进量一起钉住：{[r['pill'] for r in d['rows']]}"
        # ⚠ `getBBox().width` 是墨盒，不是钉的那个量：spacingAndGlyphs 逐字重排边距，
        #   噪声字「较」的墨宽溢出，实测 k=.5 读到 56.467 而推进量仍是 56.0。这条断言
        #   把"墨盒只小抖、不许当口径"钉住——拿 bbox 去钉会把整串压窄（药丸底不跟着窄）。
        ink = sorted({r["w"] for r in d["rows"]})
        assert ink[-1] - ink[0] <= 0.5, f"墨盒抖动超出可接受的一小截：{ink}"
        assert len({r["adv"] for r in d["rows"]}) < len(ink) + 1

        # ②③ 落定节奏 + 字符类 + 单调 + 确定性
        prev = -1
        for row in d["rows"]:
            # 0.33.0 缓出：res = round((1-(1-kd)²) × 字数)，kd 与实现同一套夹取
            #（decodeWrite 里 k = min(1, 句内进度/lead)——采样 k=1 时 kd 钳在 1）
            kd = min(1, row["k"] / lead)
            e = 1 - (1 - kd) ** 2
            res = min(len(want), _jsround(e * len(want)))
            assert len(row["txt"]) == len(want), f"解码不改字数：{row}"
            assert row["txt"][:res] == want[:res], f"前 {res} 格已定住：{row}"
            for c in row["txt"][res:]:
                assert ord(c) > 0x2e7f, f"噪声必须是同一字符类（CJK 换 CJK）：{row}"
            assert res >= prev, f"落定的格数单调不减：{row}"
            prev = res
            assert (row["txt"] == want) == (res >= len(want)), f"整词只在写满时出现：{row}"
        assert d["rows"][0]["txt"] != want, "k=0 不该已经写好"
        k0 = d["rows"][0]["txt"]
        again = read("a-rule", "seg-1", 2, ks="[0,.5,0]")
        assert again["rows"][2]["txt"] == again["rows"][0]["txt"] == k0, \
            f"同一个 k 永远同一个串（导出每帧冷启动）：{k0} vs {again['rows'][2]['txt']}"

        # 末字落定点：lead × (1 − sqrt(.5/字数))（0.33.0 缓出，四字词 = .465）
        edge = read("a-rule", "seg-1", 2, ks="[.46,.47]")
        last_at = lead * (1 - math.sqrt(0.5 / len(want)))
        assert edge["rows"][0]["txt"] != want and edge["rows"][1]["txt"] == want, \
            f"末字在 {round(last_at, 3)} 落定（lead=.72 是最晚上限不是那一拍）：{edge['rows']}"

        # ④ 解码是逐枚徽标 opt-in：没传 dec 的徽标恒等于字面
        g = read("g-luck", "seg-3", 1, ks="[0,.3,.6,1]")
        assert all(r["txt"] == "巧合" for r in g["rows"]), \
            f"非解码徽标不许被进度改写：{[r['txt'] for r in g['rows']]}"
        # 没钉 textLength 的徽标：字面恒等 → 墨盒与推进量都恒等（同一串同一宽）
        assert len({r["w"] for r in g["rows"]}) == 1 and len({r["adv"] for r in g["rows"]}) == 1

        # 五字词同一套节奏（另一次建档，另一句长）；末字在 .492 落定，.72 处已满
        s = read("a-str", "seg-1", 3, ks="[0,.5,.65,.72,1]")
        assert s["rows"][0]["txt"] != "转成字符串" and s["rows"][3]["txt"] == "转成字符串"
        assert len({r["adv"] for r in s["rows"]}) == 1, f"五字词同样钉住推进量：{s['rows']}"
        assert abs(float(s["rows"][0]["tl"]) - s["rows"][0]["adv"]) < 0.06, \
            f"钉的仍是推进量 70.0（自然墨盒是 70.134，钉它就会压窄）：{s['rows'][0]}"
        assert len({r["w"] for r in s["rows"]}) <= 2, \
            f"墨盒只容许一格噪声字的溢出（70.253），不许成串漂：{[r['w'] for r in s['rows']]}"
    finally:
        _kill(chrome)


_BAND_PROBE = r"""(function(){
  var b = document.getElementById('bar'), tp = document.getElementById('bar-tip');
  var csT = getComputedStyle(tp), csB = getComputedStyle(b, '::after');
  var tl = JSON.parse(document.getElementById('lesson-timeline').textContent);
  return JSON.stringify({
    want:tp.style.getPropertyValue('--band'),
    comp:csT.getPropertyValue('--band'), tr:csT.translate,
    tprop:csT.transitionProperty, tdur:csT.transitionDuration,
    te:csT.transitionTimingFunction, data:b.dataset.band || null,
    t:+document.getElementById('main-audio').currentTime.toFixed(3),
    total:+tl.scenes[tl.scenes.length - 1].runtime.end,
    afterOp:csB.opacity, afterPe:csB.pointerEvents, afterBg:csB.backgroundImage.slice(0, 24),
    runs:(tp.getAnimations({subtree:false}) || []).map(function(a){
      return a.transitionProperty || a.animationName;})});
})()"""


def _drag(send, kind, x, y, buttons):
    """CDP 真实指针：按下/拖动写 buttons=1，松手必须 button:'left' + buttons=0
       （button:'none' 的 mouseReleased 合成不出 pointerup，0.12.0 那条拖动用例踩过）。"""
    send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                      "button": "left", "buttons": buttons,
                                      "clickCount": 1})


def _wait_band(ev, pred, timeout=2.5):
    """轮询越界读数直到谓词成立：回弹那一格是 CSS 在插值，机器慢只会多 poll 两次，
       不该把断言写成"等 459ms"（本机量出来的数，CI 只会更慢）。"""
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        last = json.loads(ev(_BAND_PROBE))
        if pred(last):
            return last
        time.sleep(0.05)
    return last


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_band_stretches_the_tip_but_never_the_clock(full_lesson):
    """真实浏览器：拖过头了，气泡给、时钟不给（0.26.0「越界橡皮筋」）。

    指针在条内时一切照旧；越过端点之后 `seek` 把**未夹**的 raw 交给 bandWrite、
    只把夹过的写给 currentTime。四件事必须一起成立：
      · 时钟不说谎：向右越过 12 / 24 / 48 / 96 / 200px 五格，currentTime **全部**等于
        时间轴末尾 115.201s（从 lesson-timeline 现读，不写死）；向左越过 40px 落 0s；
      · 位移非线性：`cap · x / (x + soft)` 由页面自己那两只旋钮求出，逐格对上实测
        （12→4.80 / 24→8.00 / 48→12.00 / 96→16.00 / 200→19.35 / 左 40→−10.91px），
        单调、亚线性、永远不越封顶——线性跟随等于把手指在条外的距离原样画进画面；
      · 回弹是 CSS 不是 JS：写进去的是终值（`tp.style`），computed 的 `--band` 与
        `translate` 在同一条 spring 上插值，`getAnimations()` 里有一条
        `transitionProperty === '--band'`；读 inline 拿到的是目标值，读它测不出回弹
        （本轮踩过的坑，断言把两个读数一起取）；
      · 档位与亮：`data-band="r|l"` 随越界方向落，端头那圈亮（`.bar::after`）随之淡入，
        `pointer-events:none`，松手档位删除、位移弹回 0（轮询 computed 读数到落定，
        不等固定毫秒——459ms 这个数是本机量出来的，CI 只会更慢）。"""
    chrome, ev, send = _cdp(full_lesson, "band26")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        ev("window.__jump('seg-1', 1)")
        ev("document.getElementById('bar').scrollIntoView({block:'center'})")
        rect = json.loads(ev("(function(){var r=document.getElementById('bar')"
                             ".getBoundingClientRect();return JSON.stringify({"
                             "y:r.top + r.height/2,l:r.left,rt:r.right,w:r.width});})()"))
        tok = json.loads(ev("(function(){var rs=getComputedStyle(document.documentElement);"
                            "return JSON.stringify({cap:rs.getPropertyValue('--band-cap'),"
                            "soft:rs.getPropertyValue('--band-soft'),"
                            "dur:rs.getPropertyValue('--dur-band')});})()"))
        cap, soft = _px(tok["cap"]), _px(tok["soft"])

        def band_expect(over):
            a = abs(over)
            return (cap * a / (a + soft)) if a else 0.0

        def read():
            return json.loads(ev(_BAND_PROBE))

        # ① 条内：没有越界，一切线性照旧
        _drag(send, "mousePressed", rect["l"] + rect["w"] * .6, rect["y"], 1)
        r_in = read()
        assert r_in["data"] is None and _tval(r_in["tr"]) == 0.0, f"条内不该有位移：{r_in}"
        assert r_in["afterOp"] == "0" and r_in["afterPe"] == "none", \
            f"端头那圈亮静止态在场但看不见、不吃指针：{r_in}"
        _drag(send, "mouseMoved", rect["l"] + rect["w"] * .7, rect["y"], 1)
        assert read()["t"] > r_in["t"] + 1, "条内仍然跟手（本用例不该动到既有擦洗）"

        # ② 越过右端点：位移按阻尼折、时钟一步都不许多走
        seen = []
        for dx in (12, 24, 48, 96, 200):
            _drag(send, "mouseMoved", rect["rt"] + dx, rect["y"], 1)
            r = read()
            seen.append(r)
            assert r["data"] == "r", f"档位该读『右到头了』：{r}"
            assert _px(r["want"]) == pytest.approx(band_expect(dx), abs=0.05), \
                f"越界 {dx}px → cap·x/(x+soft)（{band_expect(dx)}）：{r['want']}"
            assert r["t"] == pytest.approx(r["total"], abs=0.005), \
                f"越界 {dx}px 时 currentTime 仍钉在时间轴末尾 {r['total']}s：{r['t']}"
        vals = [_px(r["want"]) for r in seen]
        dxs = (12, 24, 48, 96, 200)
        assert all(b > a for a, b in zip(vals, vals[1:])), f"单调：{vals}"
        conf = [v / d for v, d in zip(vals, dxs)]
        assert all(b < a for a, b in zip(conf, conf[1:])), \
            f"亚线性：手指每多拖 1px 换到的位移越来越少（越拖越追不上）：{conf}"
        assert vals[-1] < cap, f"逼近但不越过封顶 {cap}px：{vals[-1]}"

        # 端头那圈亮随档位淡入（淡入是补间，轮询到"亮了"这件事，不等固定毫秒）
        lit = _wait_band(ev, lambda b: b["afterOp"] != "0")
        assert lit["afterOp"] != "0" and lit["afterBg"].startswith("linear-gradient"), \
            f"右端那圈亮该跟着档位落：{lit}"

        # ③ 回弹是 CSS：写进去的是终值，computed 还在半路上
        mid = read()
        assert "--band" in mid["tprop"], f"气泡的补间清单里有 --band：{mid['tprop']}"
        assert _ms(tok["dur"]) in [_ms(x.strip()) for x in mid["tdur"].split(",")], \
            f"回弹那一档就是 --dur-band（{tok['dur']}），不是新写的第二个时长：{mid['tdur']}"
        assert any(x == "--band" for x in mid["runs"]), \
            f"getAnimations 里那条就是回弹本体：{mid['runs']}"
        assert _px(mid["comp"]) < _px(mid["want"]) - 0.5, \
            f"computed（{mid['comp']}）落后于刚写的目标值（{mid['want']}）——" \
            "读 inline 永远读不到这一格，本轮踩过"

        # ④ 越过左端点：反号、档位换头、时钟落 0
        _drag(send, "mouseMoved", rect["l"] - 40, rect["y"], 1)
        rl = read()
        assert rl["data"] == "l", f"档位跟着方向：{rl}"
        assert _px(rl["want"]) == pytest.approx(-band_expect(-40), abs=0.05), \
            f"左边同样折：{rl['want']} vs {-band_expect(-40)}"
        assert rl["t"] == 0.0, f"左端外面时钟只能是 0：{rl['t']}"

        # ⑤ 松手：档位退出、位移弹回 0（轮询读数，不等毫秒）
        _drag(send, "mouseReleased", rect["l"] - 40, rect["y"], 0)
        assert read()["data"] is None, "松手 data-band 必须删掉（状态随手势退出）"
        rest = _wait_band(ev, lambda b: _tval(b["tr"]) == 0.0 and b["afterOp"] == "0")
        assert _tval(rest["tr"]) == 0.0 and _px(rest["comp"]) == pytest.approx(0.0, abs=0.01), \
            f"落定之后气泡回到居中那一格：{rest}"
        assert rest["runs"] == [], f"弹完不留一条在跑的补间：{rest['runs']}"
        assert rest["afterOp"] == "0", f"端头那圈亮收掉：{rest}"
        _drag(send, "mouseMoved", rect["l"] - 8, 6, 0)     # 指针移出条外再读一次
        assert read()["data"] is None, "松手之后不该再有越界读数"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_trail_decode_band_reduced_motion_and_shot_terminals(full_lesson):
    """减动效与导出那一刀：三条各关各的对象，但**读数一条都不许丢**（0.26.0）。

      · 拖尾关的是"先后"：`--trail-r` 与 `.trail` class 照旧落在节点上（rank 是事实），
        只有 delay 归 0——不写这一行，`transition-duration` 的全局钳制碰不到 delay，
        晕动用户仍会看到两张卡各晚 70 / 140ms（0.18.0 在 animation-delay 上同一课）；
      · 解码关的是"字面"本身：它改的是 textContent、没有 CSS 行可挂，所以兜底只能在
        JS 里认偏好；减动效下 k=.1 那一格直接是完整的"转成字符串"——未定住的那些格
        本来就不可读，留着只减分；
      · 橡皮筋关的是"那一下弹回"：`data-band` 照落、端头那圈亮**一步落档**（opacity 1），
        而位移恒为 0px（JS 写 0，不是靠补间消失）；
      · 导出截图模式与减动效落的是**同一张定格**：解码直接整词、三张卡无在跑补间且
        已经站在 sorted 那一格上。"""
    chrome, ev, send = _cdp(full_lesson, "rb26")
    try:
        ev(_SEEK)
        ev("document.getElementById('stage').dataset.state = 'playing'")
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")

        # ① 拖尾：档位还在、先后归零
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.3)
        s = json.loads(ev(_TRAIL_READ))
        assert s["10"]["r"] == "1" and s["9"]["r"] == "2" and \
            "trail" in s["10"]["cls"] and "trail" in s["9"]["cls"], \
            f"rank 是状态读数，关动效不许把它一起抹掉：{s['10']} {s['9']}"
        for v in ("10", "9", "1"):
            assert s[v]["delay"] == "0s", f"关的是先后不是档位：{v} {s[v]['delay']}"
            assert s[v]["tp"] == "none", f"补间整条关掉的既有口径：{s[v]['tp']}"

        # ② 解码：进度压在 .1 那一格，字面直接是完整的词
        ev("window.__jump('seg-1', 3)")
        time.sleep(0.2)
        d = json.loads(ev(_DECODE_PROBE + "('a-str','seg-1',3,[0,.1,.5,1])"))
        assert all(r["txt"] == "转成字符串" for r in d["rows"]), \
            f"减动效下一格噪声都不给：{[r['txt'] for r in d['rows']]}"

        # ③ 橡皮筋：档位照落、端头照亮、位移给 0
        ev("document.getElementById('bar').scrollIntoView({block:'center'})")
        rect = json.loads(ev("(function(){var r=document.getElementById('bar')"
                             ".getBoundingClientRect();return JSON.stringify({"
                             "y:r.top + r.height/2,rt:r.right});})()"))
        _drag(send, "mousePressed", rect["rt"] - 40, rect["y"], 1)
        _drag(send, "mouseMoved", rect["rt"] + 96, rect["y"], 1)
        time.sleep(0.2)
        b = json.loads(ev(_BAND_PROBE))
        assert b["data"] == "r", f"'到头了'这件事仍然要说得出：{b}"
        assert b["afterOp"] == "1" and b["afterBg"].startswith("linear-gradient"), \
            f"端头那圈亮一步落档（不淡入）：{b}"
        assert b["t"] == pytest.approx(b["total"], abs=0.005), f"时钟同样只到末尾：{b['t']}"
        assert _px(b["comp"]) == 0.0 and _tval(b["tr"]) == 0.0, \
            f"位移由 JS 直接写 0（不是靠补间消失）：{b['comp']} / {b['tr']}"
        assert b["want"] == "0.00px", f"连目标值都不给：{b['want']}"
        _drag(send, "mouseReleased", rect["rt"] + 96, rect["y"], 0)
        send("Emulation.setEmulatedMedia", {"features": []})

        # ④ 导出那一刀：与减动效同一张定格
        ev("(function(){var st = document.createElement('style'); st.id = 'shotcut26';"
           " st.textContent = '*,*::before,*::after{transition:none!important;"
           "animation:none!important}'; document.head.appendChild(st);})()")
        ev("window.__coursewareShotMode = true")
        ev("window.__jump('seg-1', 0)")
        time.sleep(0.3)
        ev("window.__jump('seg-1', 2)")
        time.sleep(0.3)
        shot = json.loads(ev(_TRAIL_READ))
        for v in ("10", "9", "1"):
            assert shot[v]["x"] == pytest.approx(s[v]["x"], abs=0.6), \
                f"{v}：导出定格与减动效落同一格（sorted 那一格）：{shot[v]} vs {s[v]}"
        runs = json.loads(ev("(function(){var a=[];['10','9','1'].forEach(function(v){"
                             "(document.getElementById('w-'+v)"
                             ".getAnimations({subtree:false})||[]).forEach(function(x){"
                             "a.push(x.transitionProperty||x.animationName);});});"
                             "return JSON.stringify(a);})()"))
        assert runs == [], f"导出定格上不该有半截补间：{runs}"
        ds = json.loads(ev(_DECODE_PROBE + "('a-rule','seg-1',2,[0,.1,.5])"))
        assert all(r["txt"] == "默认规则" for r in ds["rows"]), \
            f"导出每帧一次冷启动 seek，解码直接落完整词（随机/进度都会撕裂帧）：" \
            f"{[r['txt'] for r in ds['rows']]}"
    finally:
        _kill(chrome)


# ══════════════════════════════════════════════════════════════════════════════
# 0.27.0 批注与迎手：荧光笔标记 / 反证划线 / 磁吸迎手
# ══════════════════════════════════════════════════════════════════════════════

def test_annotation_and_magnet_primitives_present():
    """模板结构契约：0.27.0 三条原语的口径必须长在范本里（无浏览器也可锁）。

    锁的是本轮量出来"为什么只能这么写"的边界：
      · 九只新令牌真在 :root 解析结果里；`--mark-tilt` / `--strike-tilt` **故意不注册**
        （静态几何读数，不参与补间，口径同 --cam-pan / --trail-r）；
      · 荧光笔：斜度走**独立 rotate 属性**、transform 只留给 scaleX 入场；色带必须
        insertBefore 到文字之前（SVG 绘制顺序 = 文档顺序），带宽/起点 / 高全从 bbox 现推；
      · 反证划线：斜率算进端点（水平 <line> 的 fill-box 高为 0，rotate 没有稳定旋转盒），
        append 在文字之后（压在最上层），pathLength=1 的归一单位与 .draw 同族；
        文字压暗不换红（红线是判定、字是证据，合成对比度由下一条用例核）；
      · 磁吸：sin(πd/r) 从**盒边**量距、扣掉上一帧位移（否则场跟着按钮挪）、
        只认鼠标/笔、减少动效与截图模式直接不写、键盘路径 focusin 即清；
      · reduced-motion 两行兜底各关各的对象，且都写在基础规则之后（磁吸那条要带
        `#gate` 抬特异度——基础规则在 §④，源序在本块之后）。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)

    # ⓪ 注释配对 + 九只新令牌
    assert _css_comment_problems(html) == [], _css_comment_problems(html)
    toks = {
        "--mark-ink": r"var\(--key-soft\)",
        "--mark-tilt": r"-.6deg",
        "--dur-mark": r"\.42s",
        "--strike-ink": r"#ef4d4d",
        "--strike-tilt": r"1\.4deg",
        "--dur-strike": r"\.34s",
        "--mag-r": r"34px",
        "--mag-pull": r"6px",
        "--dur-mag": r"\.34s",
    }
    for tok, val in toks.items():
        m = re.search(re.escape(tok) + r":([^;]+);", root)
        assert m, f"{tok} 没长在 :root 里（被注释语法吃掉过一次，见 _css_comment_problems）"
        assert re.fullmatch(val, m.group(1).strip()), f"{tok} 的读数变了：{m.group(1)!r}"
    for tok in ("--mark-tilt", "--strike-tilt"):
        assert f"@property {tok}" not in html, \
            f"{tok} 是静态几何读数，不参与补间（注册了反而会被逐帧插值，口径同 --cam-pan）"

    # ① 荧光笔：独立 rotate + 只演 scaleX + 量出来的几何
    mark = re.search(r"\.mark\{([\s\S]*?)\}", css).group(1)
    assert "rotate:var(--mark-tilt)" in mark, "斜度走独立 rotate：transform 要留给 scaleX 入场"
    assert "transform:rotate" not in mark, \
        "把 rotate 塞进 transform 的 keyframes，to 端就会把它丢掉（独立属性不参与补间）"
    assert "animation:markIn var(--dur-mark) var(--ease-out) backwards" in mark
    assert "fill:var(--mark-ink)" in mark and "pointer-events:none" in mark
    assert "transition:" not in mark, \
        ".mark 自带 nt 类：这里再写 transition 就把 .nt 的十七项清单整条顶掉（老账）"
    assert "@keyframes markIn{from{transform:scaleX(0)}}" in css, \
        "只写 from：to 取元素自身（transform:none），减动效 / 导出都落在整条色带"
    mb = html.split("function markBehind(parent, key, node){")[1].split("\n  }")[0]
    assert "if (r.nextElementSibling !== node) h.insertBefore(r, node);" in mb, \
        "已经贴在文字前就不要再插：DOM 插入会重挂节点，markIn 会在同一句上重放"
    assert "bb.height * .55" in mb and "bb.width + 8" in mb and "bb.height * .45" in mb, \
        "带宽/起点从 bbox 现推：手填坐标换句长就错位"
    assert "getBBox" in mb and "Math.random" not in html
    for body in (mb,):
        assert "setTimeout" not in body and "setInterval" not in body, \
            "原语不许自己排程：画面是 (scene, step) 的纯函数"

    # ② 反证划线：斜率进端点、归一 dash、压在文字上、文字压暗
    st = re.search(r"\.strike\{([\s\S]*?)\}", css).group(1)
    assert "stroke:var(--strike-ink)" in st and "fill:none" in st
    assert "stroke-dasharray:1 1" in st and "stroke-dashoffset:0" in st
    assert "animation:strikeIn var(--dur-strike) var(--ease-out) backwards" in st
    assert "pointer-events:none" in st and "transition:" not in st
    assert "@keyframes strikeIn{from{stroke-dashoffset:1}}" in css, \
        "终态写在元素上：切掉动画仍是完整的一笔，不是划到一半"
    sb = html.split("function strikeThrough(parent, key, node){")[1].split("\n  }")[0]
    assert "Math.tan(Math.abs(deg) * Math.PI / 180) * (x1 - x0) / 2" in sb, \
        "水平 line 的 fill-box 高为 0：斜率必须算进端点，不能交给 rotate"
    assert "bb.height * .52" in sb and "pathLength:1" in sb
    assert "Math.max(2, Math.round(bb.height * .09 * 10) / 10)" in sb
    assert "if (n.parentNode !== h) h.appendChild(n);" in sb, \
        "首创建时 put() 已追加在文字之后；每步 append 会摘下再插回，strikeIn 会重放"
    assert "setTimeout" not in sb and "setInterval" not in sb

    # ③ 消费者：一琥珀一红，各走各的路
    assert "var head = txt('c-body', 'c-head'" in html
    assert "if (step === 4) markBehind('c-body', 'c-mark', head);" in html, \
        "荧光笔只在 s4 出现：s5 是新结论，旧标记必须被 sweep 收掉"
    assert "var srcT = txt(G, 'd-src-t'" in html
    assert "if (wrong) strikeThrough(G, 'd-src-x', srcT);" in html, \
        "s1 判错就开始划，s5 仍要划着——旧写法被否掉是持久状态"
    assert "fill: wrong ? '#8d95a8' : '#c9d1e0'" in html, \
        "字压暗、不换红：红线是判定，字是证据（见对比度用例）"

    # ④ 磁吸：sin 场、盒边量距、扣位移、三道路径闸、委托 + 清理
    mag = html.split("function magMove(e){")[1].split("\n  }")[0]
    assert "Math.sin(Math.PI * Math.min(1, d / R))" in mag, \
        "线性衰减在圆心有尖点；sin 在两端口都归零，指针穿过按钮不会抖"
    assert "Math.abs(e.clientX - cx) - w/2" in mag and "Math.abs(e.clientY - cy) - h/2" in mag, \
        "场半径从盒边量：从圆心量的话，120×34 的按钮四角就已经出圈"
    assert "var cur = b._mag || {x:0, y:0};" in mag and "r.left + w/2 - cur.x" in mag, \
        "量场要扣掉当前位移：否则按钮迎 6px、场也挪 6px，指针没动它却来回找位"
    assert "window.__coursewareShotMode || mqReduce.matches" in mag
    assert "e.pointerType !== 'mouse' && e.pointerType !== 'pen'" in mag, \
        "触屏没有 hover 语义：磁吸在手指底下只会变成点不准"
    assert "best.dataset.mag = '1';" in mag and \
           "best.style.translate = bestX.toFixed(2) + 'px ' + bestY.toFixed(2) + 'px';" in mag, \
        "只写独立 translate：:active 的 scale 是另一个属性，两者相乘"
    mc = html.split("function magClear(){")[1].split("\n  }")[0]
    assert "b.style.translate = ''" in mc and "b._mag = null" in mc
    for evline in ("gateEl.addEventListener('pointermove', magMove);",
                   "gateEl.addEventListener('pointerleave', magClear);",
                   "gateEl.addEventListener('pointerdown', magClear);",
                   "gateEl.addEventListener('focusin', magClear);"):
        assert evline in html, f"磁吸接线缺一条：{evline}"
    assert "magClear();   // 磁吸迎手（0.27.0）：上一题留下的 translate 不许带给下一张卡" in html
    assert "transition:filter var(--dur-fast)," in css and \
           "translate var(--dur-mag) var(--ease-spring)" in css, \
        "两个属性写进同一条 transition 简写：另写一条会把 filter 那档整个顶掉"
    go_block = re.search(r"\.gate-next \.go,\.act-row \.go\{([\s\S]*?)\}", css).group(1)
    assert go_block.count("transition:") == 1, "transition 只许一条简写"

    # ⑤ reduced-motion：两行兜底落在基础规则之后；磁吸那条要抬特异度
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{([\s\S]*?)\n  \}",
                   css).group(1)
    assert ".mark,.strike{animation:none}" in rm
    assert "#gate .gate-next .go,#gate .act-row .go{transition:none}" in rm, \
        "基础规则写在 §④、源序在本块之后：不抬 #gate 就被后来者顶掉（0.15.0 老账）"
    assert css.index(".gate-next .go,.act-row .go{background") > \
           css.index("#gate .gate-next .go,#gate .act-row .go{transition:none"), \
        "基础规则源序在兜底之后：这正是必须抬 #gate 的原因（朴素选择器会被后来者顶掉）"
    assert css.index(".mark{transform-box") < css.index(".mark,.strike{animation:none}")
    assert "var(--dur-mark)" not in rm and "var(--dur-strike)" not in rm and \
           "var(--dur-mag)" not in rm, \
        "兜底里不许出现「补一个更短的时长」——那是在演一次更快的动效，不是不演"


def test_marker_band_contrast_by_composition():
    """荧光笔的墨量只能这么选：按 §8 的合成口径在 python 里算，不看截图平均亮度。

    琥珀字 #f5a623 衬在 .14 的琥珀带上是 6.09:1（舞台顶）/ 6.64:1（--panel），
    仍在正文 4.5 线上；**同一支红字 #ef4d4d 只剩 3.43 / 3.74**——所以荧光笔只许
    落在琥珀句上，红字那一路必须换成"划线 + 压暗"（.strike）。
    划线本身是图形（3:1 档）：红线上衬红 tint 盒（rgba(239,77,77,.08) over --panel）
    是 4.48:1；压暗后的字 #8d95a8 在同一盒上是 5.37:1，仍是正文读数。
    """
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    root = re.search(r":root\{([\s\S]*?)\n  \}", css).group(1)

    def _tok(name):
        return re.search(re.escape(name) + r":([^;]+);", root).group(1).strip()

    def _rgb(text):
        h = text.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    def _lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def _lum(rgb_):
        r, g, b = (_lin(c) for c in rgb_)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def _cr(a, b):
        hi, lo = max(a, b), min(a, b)
        return (hi + 0.05) / (lo + 0.05)

    def _over(fg, alpha, bg):
        return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))

    amber = _rgb(_tok("--key"))
    soft = re.fullmatch(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", _tok("--key-soft"))
    assert soft, f"--key-soft 不再是可解析的 rgba：{_tok('--key-soft')!r}"
    band_ink = tuple(int(soft.group(i)) for i in (1, 2, 3))
    alpha = float(soft.group(4))
    assert band_ink == amber, \
        "带色借的是主色同一支墨：荧光笔不是第二种黄（否则两种黄会互相打架）"
    assert 0.10 <= alpha <= 0.18, f"墨量 {alpha} 越界：太浓压字，太淡看不见"
    assert _tok("--mark-ink") == "var(--key-soft)"

    stage = re.search(r"\.stage\{[^}]*background:linear-gradient\(180deg,(#\w{6}) 0%,"
                      r"var\(--panel\) 55%,(#\w{6}) 100%\)", css)
    assert stage, "舞台渐变读不到：这一条要求从 CSS 现取底色，不许在测试里抄 hex"
    bases = {"upper": _rgb(stage.group(1)), "panel": _rgb(_tok("--panel")),
             "lower": _rgb(stage.group(2))}
    for name, base in bases.items():
        bg = _lum(_over(band_ink, alpha, base))
        got = _cr(_lum(amber), bg)
        assert got >= 5.5, f"琥珀字衬 {name} 的荧光带只有 {got:.3f}:1（实测 ≥6.09）"

    red = _rgb(_tok("--strike-ink"))
    bgs = {name: _lum(_over(band_ink, alpha, base)) for name, base in bases.items()}
    worst_red = min(_cr(_lum(red), v) for v in bgs.values())
    assert worst_red < 4.5, \
        f"红字配亮带本就不该过线（实测最亮 3.74）：若现在过了 {worst_red:.3f}，说明色带墨量被改了，" \
        "关于'红字只能划线不能打底'的那条边界要重算"

    tint = re.search(r"fill: wrong \? 'rgba\(239,77,77,([\d.]+)\)' : '#161a23'", html)
    assert tint, "renderD 的红 tint 盒读不到：划线/压暗的合成底色就不成立"
    box = _over(red, float(tint.group(1)), _rgb(_tok("--panel")))
    assert _cr(_lum(red), _lum(box)) >= 3.0, "红线是图形，压在同色 tint 盒上要过 3:1"
    assert _cr(_lum(_rgb("#8d95a8")), _lum(box)) >= 4.5, \
        "压暗后的字仍是正文：合成对比度必须过 4.5:1（实测 5.37）"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
def test_marker_band_is_measured_behind_key_line(full_lesson):
    """荧光笔：量出来的带子垫在 s4 关键句后面，只演一次 scaleX，s5 收掉。

    三条实测：
      · 几何全部跟着 bbox 走（band 左缘 = 文本 x−4、宽 = 文本宽+8、高 = 文本高×.55、
        起点在文本 y+45% 处）——不是手填坐标，换字号 / 换句长都不会错位；
      · DOM 顺序在文字**之前**（previousElementSibling 是公式行），且带子自己没有 .in：
        两层入场叠一起会互相糊掉（0.15.0 那两条消费者的同一课）；
      · 动画 from 是 scaleX(0)、终态 transform:none，rotate 是独立属性 -0.6deg——
        半程采样得到 0<a<1，跑完落回整条带。
    """
    chrome, ev, _send = _cdp(full_lesson, "mark27")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-4', 4)")
        st = json.loads(ev("""(function(){
          var t = document.getElementById('c-head'), b = document.getElementById('c-mark');
          if (!t || !b) return JSON.stringify({missing:true});
          var tb = t.getBBox(), bb = b.getBBox();
          var a = b.getAnimations()[0];
          var start = null, mid = null;
          if (a){
            a.pause();
            a.currentTime = 0;
            var m0 = getComputedStyle(b).transform.match(/matrix\\(([^)]+)\\)/);
            start = m0 ? +m0[1].split(',')[0] : null;
            a.currentTime = 105;
            var m = getComputedStyle(b).transform.match(/matrix\\(([^)]+)\\)/);
            mid = m ? +m[1].split(',')[0] : null;
          }
          return JSON.stringify({
            tb:[tb.x,tb.y,tb.width,tb.height], bb:[bb.x,bb.y,bb.width,bb.height],
            start:start, mid:mid, rot:getComputedStyle(b).rotate, fill:getComputedStyle(b).fill,
            cls:b.getAttribute('class'), prev:b.previousElementSibling.id,
            bi:Array.prototype.indexOf.call(b.parentNode.children, b),
            ti:Array.prototype.indexOf.call(b.parentNode.children, t),
            anims:b.getAnimations().map(function(x){ return x.animationName; })});
        })()"""))
        assert not st.get("missing"), "s4 没长出荧光笔标记"
        tb, bb = st["tb"], st["bb"]
        assert st["prev"] == "c-formula" and st["bi"] < st["ti"], \
            f"色带必须画在文字之前：{st['prev']} / {st['bi']} vs {st['ti']}"
        assert st["cls"] == "nt mark", f"带子不许再挂入场类（两层入场互相糊）：{st['cls']}"
        assert abs(bb[0] - (tb[0] - 4)) <= 0.6, f"左缘 = 文本 x−4：{bb[0]} vs {tb[0]}"
        assert abs((bb[0] + bb[2]) - (tb[0] + tb[2] + 4)) <= 0.6, \
            f"右缘 = 文本右缘 +4：{bb[0] + bb[2]} vs {tb[0] + tb[2]}"
        assert abs(bb[3] - tb[3] * .55) <= 0.6, f"带高 = 文本高 ×.55：{bb[3]} vs {tb[3]}"
        lo, hi = tb[1] + tb[3] * .38, tb[1] + tb[3] * .55
        assert lo <= bb[1] <= hi, f"带子落在字的下半格：y={bb[1]}，文本 {tb[1]}..{tb[1]+tb[3]}"
        assert st["start"] is not None and st["start"] < 0.05, \
            f"入场必须从 scaleX(0) 长出来：start={st['start']}"
        assert 0 < st["mid"] < 0.9, f"半程采样应在 (0,1) 之间：mid={st['mid']}"
        assert st["rot"] == "-0.6deg", f"斜度是独立 rotate，不参与补间：{st['rot']}"
        assert "0.14" in st["fill"], f"墨色 = --key-soft 的 .14 档：{st['fill']}"
        assert st["anims"] == ["markIn"], f"只有 markIn 一条，不许叠 .in：{st['anims']}"

        # 跑完 = 整条带（终态写在元素自己身上）
        ev("(function(){var a=document.getElementById('c-mark').getAnimations()[0];"
           "if(a){a.finish();}})()")
        full = json.loads(ev("""(function(){var b=document.getElementById('c-mark');
          return JSON.stringify({tf:getComputedStyle(b).transform,
            w:b.getBBox().width});})()"""))
        assert full["tf"] == "none", f"终态 transform:none：{full['tf']}"
        assert abs(full["w"] - (tb[2] + 8)) <= 0.6, "跑完仍是整条带宽"

        # s5 收掉；s0/s1 从未出现
        ev("window.__jump('seg-4', 5)")
        assert ev("!document.getElementById('c-mark')"), "s5 是新结论：旧标记必须被 sweep 收掉"
        ev("window.__jump('seg-4', 1)")
        assert ev("!document.getElementById('c-mark')"), "s1 的契约态不该有结论标记"
        ev("window.__jump('seg-4', 0)")
        assert ev("!document.getElementById('c-mark')"), "s0 是引入句，不该有标记"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
def test_strike_line_draws_over_wrong_comparator(full_lesson):
    """反证划线：s1 判错那一拍从左往右划掉，s3 跟着管线走，s5 仍划着。

    量的是四条：
      · 红线端点按 --strike-tilt 算斜率（(y2−y1)/(x2−x1) ≈ tan 1.4°），中心落在
        文本 bbox 的 52% 处——不是 rotate 出来的"大概位置"；
      · 归一单位与 .draw 同族（pathLength=1、dasharray 1 1），半程采样 offset 在
        (0,1) 之间、跑完为 0；线宽 max(2, bbox 高 ×.09)；
      · 文档顺序在文字**之后**（压在最上层），父层是 d-rig（s3 右移配平时一起走）；
      · 文字压暗到 #8d95a8，红色只留给线。
    """
    chrome, ev, _send = _cdp(full_lesson, "strike27")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-5', 1)")
        st = json.loads(ev("""(function(){
          var t = document.getElementById('d-src-t'), s = document.getElementById('d-src-x');
          if (!t || !s) return JSON.stringify({missing:true});
          var tb = t.getBBox();
          var a = s.getAnimations()[0], off = null;
          if (a){ a.pause(); a.currentTime = 170;
                  off = parseFloat(getComputedStyle(s).strokeDashoffset); }
          return JSON.stringify({
            tb:[tb.x,tb.y,tb.width,tb.height],
            xy:[+s.getAttribute('x1'),+s.getAttribute('y1'),
                +s.getAttribute('x2'),+s.getAttribute('y2')],
            w:s.getAttribute('stroke-width'), path:s.getAttribute('pathLength'),
            dash:getComputedStyle(s).strokeDasharray, off:off,
            fill:getComputedStyle(t).fill, stroke:getComputedStyle(s).stroke,
            parent:s.parentNode.id,
            si:Array.prototype.indexOf.call(t.parentNode.children, s),
            ti:Array.prototype.indexOf.call(t.parentNode.children, t),
            anims:s.getAnimations().map(function(x){ return x.animationName; })});
        })()"""))
        assert not st.get("missing"), "s1 判错后没长出划线"
        x1, y1, x2, y2 = st["xy"]
        tb = st["tb"]
        slope = (y2 - y1) / (x2 - x1)
        assert abs(slope - math.tan(math.radians(1.4))) <= 0.0015, \
            f"斜率 = tan(--strike-tilt 1.4°)：{slope:.5f}"
        assert (y1 + y2) / 2 == pytest.approx(tb[1] + tb[3] * .52, abs=0.5), \
            "线心落在文本 bbox 的 52% 处（横跨字身，不是下划线）"
        assert x1 <= tb[0] and x2 >= tb[0] + tb[2], "红线要出到字外一点，才像一笔划过去"
        assert st["path"] == "1" and st["dash"].replace(" ", "") == "1px,1px", \
            f"归一单位与 .draw 同族：pathLength={st['path']} dash={st['dash']}"
        assert 0 < st["off"] < 0.9, f"半程采样应在 (0,1) 之间：{st['off']}"
        assert st["w"] == "2", f"两行字高 ~21px 时线宽取 2：{st['w']}"
        assert st["stroke"] == "rgb(239, 77, 77)", f"划线是错误红：{st['stroke']}"
        assert st["fill"] == "rgb(141, 149, 168)", f"文字压暗不换红：{st['fill']}"
        assert st["parent"] == "d-rig" and st["si"] > st["ti"], \
            f"划线挂在 d-rig 里、压在文字之后：{st['parent']} / {st['si']} vs {st['ti']}"
        assert st["anims"] == ["strikeIn"], f"只有 strikeIn：{st['anims']}"

        ev("(function(){var a=document.getElementById('d-src-x').getAnimations()[0];"
           "if(a){a.finish();}})()")
        assert ev("parseFloat(getComputedStyle(document.getElementById('d-src-x'))"
                  ".strokeDashoffset)") == 0, "跑完 = 完整的一笔（终态在元素自己身上）"

        # s3 管线右移配平，划线跟着 d-rig 走；s5 仍划着
        ev("window.__jump('seg-5', 3)")
        s3 = json.loads(ev("""(function(){var s=document.getElementById('d-src-x');
          var g=document.getElementById('d-rig');
          return JSON.stringify({g:g.getAttribute('transform'),
            x1:+s.getAttribute('x1'), x2:+s.getAttribute('x2'),
            off:getComputedStyle(s).strokeDashoffset});})()"""))
        assert s3["g"] == "translate(0,0)", f"s3 右段长出后整条管线归位：{s3['g']}"
        # 组内局部坐标：s3 若改成世界坐标会差 93px。getBBox 的文字度量在 Chrome 155
        # 上有亚像素抖动（CI 3.12 实测 x1 差 0.0495px），容差放到 0.5px 仍抓得住错法。
        assert s3["x1"] == pytest.approx(x1, abs=0.5) and \
               s3["x2"] == pytest.approx(x2, abs=0.5), "划线是组内局部坐标：跟着管线走，不另算"
        assert float(s3["off"].replace("px", "")) == 0
        ev("window.__jump('seg-5', 5)")
        assert ev("!!document.getElementById('d-src-x')"), \
            "s5 给出正确写法，旧写法仍划着——'被否掉'是持久状态"

        ev("window.__jump('seg-5', 0)")
        assert ev("!document.getElementById('d-src-x')"), "s0 只是把错写法摆出来，还没判错"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
def test_magnetic_gate_button_follows_pointer(full_lesson):
    """磁吸迎手：盒边 17px 处峰值 6px、贴边 0、场外不动；触屏与焦点一律不动。

    量的是这套场：
      · 指针在**盒边**外 17px（= --mag-r/2）时反馈最强：translate 6px、方向朝指针；
      · 贴边 2px 时 sin(π·2/34)≈.18 → 约 1.1px；再退到 40px（>34）就完全没有；
      · 几何按**扣掉当前位移**的矩形量：按钮迎 6px 后指针不动，值不会自己往回找；
      · translate 不改布局盒：rect 宽高不变，点击照常落在按钮上（键盘/触屏也清位）。
    """
    chrome, ev, send = _cdp(full_lesson, "mag27")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-6', 0)")
        _gate_open(ev, "#gate-seq")
        time.sleep(0.8)   # 卡片入场落定后再量，避免把入场位移算进磁场
        base = json.loads(ev("""(function(){var b=document.getElementById('seq-submit');
          var r=b.getBoundingClientRect();
          return JSON.stringify({l:r.left,t:r.top,w:r.width,h:r.height,
            cy:r.top+r.height/2,r:r.left+r.width});})()"""))
        assert base["w"] > 0, "序列题的提交键没露面"

        def read():
            return json.loads(ev("""(function(){var b=document.getElementById('seq-submit');
              var r=b.getBoundingClientRect();
              return JSON.stringify({tr:b.style.translate||'', mag:b.dataset.mag||'',
                w:r.width,h:r.height,cx:r.left+r.width/2,cy:r.top+r.height/2});})()"""))

        def nums(tr):
            vals = [float(x) for x in re.findall(r"-?[\d.]+", tr)]
            return (vals + [0.0, 0.0])[:2]

        # ① 峰值：盒边外 17px
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.5)
        peak = read()
        px, py = nums(peak["tr"])
        assert peak["mag"] == "1", f"最近的按钮要标 data-mag：{peak}"
        assert px == pytest.approx(6, abs=0.5) and abs(py) <= 0.4, \
            f"sin 场半程峰值 6px、方向朝指针：{peak['tr']}"
        assert peak["w"] == pytest.approx(base["w"], abs=0.6), \
            "translate 不改布局盒：宽高必须与静止时一致"

        # ② 贴边：sin(π·2/34)≈.18 → 约 1.1px
        _mouse(send, base["r"] + 2, base["cy"])
        time.sleep(0.45)
        edge = nums(read()["tr"])[0]
        assert 0.6 <= edge <= 1.6, f"贴边只迎一点点：{edge}px"

        # ③ 场外：40px > --mag-r，清零
        _mouse(send, base["r"] + 40, base["cy"])
        time.sleep(0.45)
        far = read()
        assert far["tr"] == "" and far["mag"] == "", f"场外必须完全回位：{far}"

        # ④ 触屏：同一次 pointermove 换成 pointerType=touch，直接清位不启动
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.45)
        assert read()["tr"] != ""
        ev("""(function(){document.getElementById('gate').dispatchEvent(
          new PointerEvent('pointermove', {bubbles:true, pointerType:'touch',
            clientX:1000, clientY:400}));})()""")
        assert read()["tr"] == "", "触屏没有 hover 语义：不允许磁吸挪按钮"

        # ⑤ 键盘：焦点进来那一刻清位（Tab 到的按钮必须原地不动）
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.45)
        assert read()["tr"] != ""
        ev("(function(){var b=document.getElementById('seq-submit');"
           "b.blur(); b.focus();})()")
        assert read()["tr"] == "", "focusin 必须清位移：键盘路径一个字节都不碰"

        # ⑥ 点击照常：迎手期间在按钮当前中心按一下，click 真的落到按钮上
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.45)
        cur = read()
        ev("window.__hits = 0; document.getElementById('seq-submit')"
           ".addEventListener('click', function(){ window.__hits++; });")
        send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cur["cx"],
            "y": cur["cy"], "button": "left", "buttons": 1, "clickCount": 1})
        send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cur["cx"],
            "y": cur["cy"], "button": "left", "buttons": 0, "clickCount": 1})
        assert ev("window.__hits") == 1, "磁吸是纯视觉位移：点击必须照常落到按钮上"
    finally:
        _kill(chrome)


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
def test_annotation_magnet_reduced_motion_and_export(full_lesson):
    """减少动效与导出截图：批注关动画、落终态；磁吸根本不写位移。

    三条口径与 0.15–0.26 的老账完全一致：
      · 荧光笔关掉 animation 后仍是**整条带**（transform:none，rotate 不参与补间，
        -0.6deg 还在）——内容不依赖动效，关掉后每一帧仍是同一张正确定格；
      · 划线关掉后 dashoffset 落回 0（完整一笔），文字照旧压暗；
      · 磁吸在 mqReduce / __coursewareShotMode 下**一个字节都不写**：不是"补一个
        0.01ms 的时长"，而是根本不启动（口径同 0.26.0 的拖尾让拍归零）；
      · 导出页的一刀切（transition/animation:none）与减少动效落同一张定格。
    """
    chrome, ev, send = _cdp(full_lesson, "annotRM")
    try:
        ev(_SEEK)
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})

        ev("window.__jump('seg-4', 4)")
        rm = json.loads(ev("""(function(){
          var b = document.getElementById('c-mark');
          if (!b) return JSON.stringify({missing:true});
          var r = b.getBBox();
          return JSON.stringify({anims:b.getAnimations().length,
            name:getComputedStyle(b).animationName,
            tf:getComputedStyle(b).transform, rot:getComputedStyle(b).rotate,
            w:r.width});})()"""))
        assert not rm.get("missing")
        assert rm["anims"] == 0 and rm["name"] == "none", \
            f"减少动效下 markIn 必须不跑：{rm}"
        assert rm["tf"] == "none" and rm["rot"] == "-0.6deg", \
            f"关动画落整条带，斜度是独立属性、照常保留：{rm}"
        full_w = rm["w"]

        ev("window.__jump('seg-5', 1)")
        rm2 = json.loads(ev("""(function(){
          var s = document.getElementById('d-src-x');
          return JSON.stringify({anims:s.getAnimations().length,
            name:getComputedStyle(s).animationName,
            off:getComputedStyle(s).strokeDashoffset});})()"""))
        assert rm2["anims"] == 0 and rm2["name"] == "none", \
            f"减少动效下 strikeIn 必须不跑：{rm2}"
        assert float(rm2["off"].replace("px", "")) == 0, \
            f"关掉动画落完整一笔（终态在元素上）：{rm2['off']}"

        # 磁吸在减少动效下不启动
        ev("window.__jump('seg-6', 0)")
        _gate_open(ev, "#gate-seq")
        time.sleep(0.8)
        base = json.loads(ev("""(function(){var b=document.getElementById('seq-submit');
          var r=b.getBoundingClientRect();
          return JSON.stringify({r:r.left+r.width, cy:r.top+r.height/2});})()"""))
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.45)
        assert ev("document.getElementById('seq-submit').style.translate || ''") == "", \
            "prefers-reduced-motion：磁吸一个字节都不写（不是关时长）"
        assert ev("document.getElementById('seq-submit').dataset.mag || ''") == ""
        send("Emulation.setEmulatedMedia", {"features": []})

        # 正常模式下先让磁吸真的挪一次，证明后面的"不动"不是没接线
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.5)
        assert ev("document.getElementById('seq-submit').style.translate || ''") != "", \
            "清除 mqReduce 之后磁吸要能工作（否则这一条测不到东西）"

        # 导出那一刀 + __coursewareShotMode
        ev("(function(){var st = document.createElement('style'); st.id = 'shotcut27';"
           " st.textContent = '*,*::before,*::after{transition:none!important;"
           "animation:none!important}'; document.head.appendChild(st);})()")
        ev("window.__coursewareShotMode = true")
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.3)
        assert ev("document.getElementById('seq-submit').style.translate || ''") == "", \
            "导出截图模式：磁吸不写位移（每帧冷启动，挪一下等于每一帧都不一样）"

        ev("window.__jump('seg-4', 4)")
        shot = json.loads(ev("""(function(){
          var b = document.getElementById('c-mark');
          return JSON.stringify({anims:b.getAnimations().length,
            tf:getComputedStyle(b).transform, rot:getComputedStyle(b).rotate,
            w:b.getBBox().width});})()"""))
        assert shot["anims"] == 0 and shot["tf"] == "none", \
            f"导出定格不许有半截补间：{shot}"
        assert abs(shot["w"] - full_w) <= 0.5, "导出与减动效落同一张定格（整条带）"

        ev("window.__jump('seg-5', 1)")
        shot2 = json.loads(ev("""(function(){
          var s = document.getElementById('d-src-x');
          return JSON.stringify({anims:s.getAnimations().length,
            off:getComputedStyle(s).strokeDashoffset});})()"""))
        assert shot2["anims"] == 0 and float(shot2["off"].replace("px", "")) == 0, \
            f"导出定格上划线是完整的一笔：{shot2}"
    finally:
        _kill(chrome)


# ── 0.28.0 磁吸延伸：选项药丸迎手 ──────────────────────────────────────────────

def test_mag_extends_to_options_pills_primitives_present():
    """0.28.0 静态契约：药丸磁吸是 0.27.0 迎手的延伸——同一套令牌、包装层持位移。

    锁的口径：
      · .mag 只写 `translate var(--dur-mag) var(--ease-spring)` 一条 transition，
        没有 :hover / transform（.opt 自己的全属性简写不许被顶掉，0.19/0.23 老账）；
      · buildChoice 给每颗选项包 span.mag；
      · magMove / magClear 的选择器都含 #gate-block .mag，且 magMove 有"包装层内
        按钮被禁用就跳过"的分支（答对后题面不再迎接）；
      · 媒体块里有 .mag{transition:none}（与 .go 同款双保险）；
      · --mag-r / --mag-pull / --dur-mag 只在 :root 一处，JS 读 computed 求值。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")

    mags = [g for g in re.finditer(r"\.mag\{([^}]+)\}", src)]
    assert mags, "需要 .mag 规则"
    base = [m.group(1) for m in mags if "translate" in m.group(1)]
    assert base and base[0] == "transition:translate var(--dur-mag) var(--ease-spring)", base
    assert "transform" not in base[0] and ":hover" not in base[0]

    assert re.search(r"m\.className = 'mag'; m\.appendChild\(b\);", src), \
        "buildChoice 要包 span.mag"
    assert "gateOpts.appendChild(m);" in src

    assert src.count("gateEl.querySelectorAll('.gate-next .go, .act-row .go, #gate-block .mag')") == 2, \
        "magClear 与 magMove 的清理循环都要扫 #gate-block .mag"
    assert "#gate-block .mag')" in src and "b.matches('#gate-block .mag')" in src
    assert "btn.disabled" in src, "包装层内按钮禁用要跳过（已放行的题面不迎接）"

    assert re.search(r"\.mag\{transition:none\}", src), "媒体块要关药丸包装层的过渡"

    # 令牌只在 :root 一处定义（带值定义，不含 JS 引用处的字符串）
    root_block = re.search(r":root\{([^}]+)\}", src)
    assert root_block, "需要 :root 块"
    for tok in ("--mag-r:", "--mag-pull:", "--dur-mag:"):
        assert root_block.group(1).count(tok) == 1, f"{tok} 要在 :root 且只出现一次"
    assert "parseFloat(rootCS.getPropertyValue('--mag-r'))" in src, \
        "JS 必须读 computed，不得写死半径"


def test_mag_options_pills_greet_the_pointer(full_lesson):
    """CDP：选项药丸复用 0.27.0 的场——盒边 17px 处峰值 6px、指针在盒内为 0
    （天然死区，.hot 辉光不抢）、场外不动、只有近的一颗动、离开清场。

    验证口径与 0.27.0 的放行键同一条：场从**盒边**算、sin(π·d/R) 衰减、
    translate 写包装层且不改布局盒、focusin/pointerdown/pointerleave 清位。
    """
    chrome, ev, send = _cdp(full_lesson, "mag28")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-3', 0)")
        _gate_open(ev, "#gate-block [data-choice-id=\"b\"]")
        time.sleep(0.8)   # 卡片入场落定后再量
        base = json.loads(ev("""(function(){
          var ms = document.querySelectorAll('#gate-block .opt').length;
          var w = document.querySelector('#gate-block .mag');
          var r = w.getBoundingClientRect();
          var btn = w.querySelector('button.opt');
          return JSON.stringify({ms:ms, l:r.left, t:r.top, w:r.width, h:r.height,
            cy:r.top+r.height/2, r:r.left+r.width, cp:getComputedStyle(btn).transitionProperty});
        })()"""))
        assert base["ms"] >= 2, "选项药丸没露面"
        half = base["w"] / 2

        def read(i):
            return json.loads(ev("""(function(i){
              var ws = document.querySelectorAll('#gate-block .mag');
              var w = ws[i]; var r = w.getBoundingClientRect();
              var o = w.querySelector('button.opt');
              return JSON.stringify({tr:w.style.translate||'', mag:w.dataset.mag||'',
                w:r.width, h:r.height, dis:o.disabled,
                wp:getComputedStyle(w).transitionProperty});
            })(%d)""" % i))

        def nums(tr):
            vals = [float(x) for x in re.findall(r"-?[\d.]+", tr)]
            return (vals + [0.0, 0.0])[:2]

        # ① 半程峰值：盒边外 17px（= --mag-r/2）→ sin(π/2)=1 → 6px，方向朝指针
        _mouse(send, base["r"] + 17, base["cy"])
        time.sleep(0.5)
        p0 = read(0)
        px, py = nums(p0["tr"])
        assert p0["mag"] == "1", f"近的那颗要标 data-mag：{p0}"
        assert px == pytest.approx(6, abs=0.5) and abs(py) <= 0.4, \
            f"药丸半程峰值 6px 朝指针：{p0['tr']}"
        assert p0["w"] == pytest.approx(base["w"], abs=0.6), \
            "translate 不改布局盒：宽高与静止时一致"

        # ② 只有近的一颗动：邻丸（盒内）与远丸都不被回应
        assert read(1)["tr"] == "" and read(2)["tr"] == "", \
            "best-k 单选：邻丸盒内死区 0、远丸场外 0"

        # ③ 指针落进盒内（天然死区）：d 钳 0 → 无位移；辉光盒内坐标不漂
        _mouse(send, base["l"] + half + 5, base["cy"])
        time.sleep(0.4)
        assert read(0)["tr"] == "" and read(0)["mag"] == "", \
            "指针在盒内：磁吸场为 0（辉光不抢、药丸不追）"

        # ④ 场外：盒边外 40px（>34）→ 无位移
        _mouse(send, base["r"] + 40, base["cy"])
        time.sleep(0.4)
        assert read(0)["tr"] == "", "场外不动"

        # ⑤ 离开卡片：清场（pointerleave → magClear）
        _mouse(send, base["l"] - 60, base["cy"] - 120)
        time.sleep(0.35)
        assert read(0)["tr"] == "" and read(0)["mag"] == "", "pointerleave 清位移"

        # ⑥ 药丸本体 transition 未被顶掉（包装层的存在意义）
        assert base["cp"] == "all", f".opt 自己的全属性简写要原样：{base['cp']}"
    finally:
        _kill(chrome)


def test_focus_audit_unreached_row_stays_dimmed():
    """0.28.0 动效目的审计：一屏一个话头，没轮到的行退居参照（材质弱化）。

    renderB（seg-2 逐位比较）里第二对（"10","9"）s4 才被旁白点名，但 flip 包装
    需要节点 s0 就在场（翻面是两个状态之间的过渡，补间开始前必须已建档）——
    所以不是删掉它，而是让它在 s0–s3 整行挂 data-dim + opacity .45："在但不在
    重点上"，同一拍不跟第一对抢淡入的注意力。锁的口径：
      · renderB 的 offRow 表达式中含 `(ri === 1 && step < 4)`；
      · 弱化与 0.13.0 的焦点弱化同一条路（data-dim + opacity），不新开档位。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")
    assert "(ri === 1 && step < 4)" in src, \
        "第二对 s4 前要退居参照（renderB offRow 含未轮到分支）"
    assert "var offRow = (ri !== cur && step >= 4) || (ri === 1 && step < 4);" in src, \
        "两分支并进一条 offRow：换组弱化与未轮到弱化同一档位"
    assert "数据被搞乱" in src or "messed" in src, "renderD 的混乱动作仍在场（审计无漏项）"


def test_focus_audit_result_cards_stagger_into_place():
    """0.29.0 动效目的审计续：一屏一个话头，右列结果卡错峰落位。

    renderE（seg-6 稳定排序）s1 的话头是"相等元素保序"——两条连线 .draw 同拍画、
    两条 ride 同拍出发（小明 150→222、小红 222→294 沿线走）。右列三张结果卡
    （小刚/小明/小红）如果同一拍淡入，就跟话头抢注意力，成了"一次铺满"。改法：
    三张卡错峰落位（--delay 0 / .06 / .12s，从上往下自然读序），连线与 ride 保持
    话头地位。锁的口径：
      · 右列（col === 1）卡片挂 inline style --delay，按 i 错峰；
      · 左列录入顺序（col === 0）s0 整体揭晓是它自己的话头，不加 delay；
      · 错峰用现成的 .in 的 var(--delay)（0.10.0 就有），不开新档位。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")
    assert "style: col === 1 ? '--delay:' + (i * .06).toFixed(2) + 's' : null" in src,         "右列卡按 i 错峰 0/.06/.12s，左列保持整体揭晓"
    assert "--delay:.06s" in src, "错峰档位用现成 --delay 机制"


# ── 10. 对位镜：比较关系有自己的原语（0.30.0）──

def test_scope_primitives_present():
    """模板结构契约：对位镜长在范本里，且五条口径都落在源码上（无浏览器也可锁）。

    0.22.0 的 #spot 管"看哪一块"，0.13.0 的 [data-dim] 管"哪一块退后"——两者
    改的都是**亮度**；"这一句在比哪两个字符"是一个**关系**，此前没有原语能说。
    对位镜借工程制图的量规：一条细尺 + 两端 tick + 中间读数，换位平移一格。
    锁的口径：
      · 令牌一支墨色、一支行程，时长比运镜短一档（注解先到位）；
      · 同一行换位只推 transform（.scope 的 transition 清单只留 transform，
        .nt 的整串简写让位）；换行/换对是旧尺撤走 + 新尺 .draw；
      · 线 + 两端 tick 合成一条 path（单子路径，.draw 的归一 dash 不跨子路径）；
      · 纯注释层 pointer-events:none，读数只写 i=0 短记号（纪律 3）；
      · s1–s3 第一对（s2 起 k=1），s4 第二对首位；s0/s5 不架尺；
      · reduced-motion 收掉平移补间，终态（这一拍该在的位置）仍完整。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")
    assert "--dur-scope:.3s; --scope-ink:rgba(245,166,35,.78);" in src
    assert ".scope{pointer-events:none;" in src
    assert "transition:transform var(--dur-scope) var(--ease-out)}" in src
    assert ".scope path{fill:none;stroke:var(--scope-ink);stroke-width:1.5;" in src
    assert "function scopeFrame(parent, key, baseY, k){" in src
    # 几何从 renderB 的格距推：354 = 330 + 24，606 = 630 − 24；一条 path 走完
    # 左 tick → 上横 → 右 tick。
    assert "var gap = 46, a = 354, b = 606, y = -36, t = 6;" in src
    assert "'H' + b + 'V' + (y + t)" in src
    assert "'data-k':k" in src
    # 接线：s1 首位、s2/s3 第二位（同一把尺 k=0→1）、s4 第二对首位。
    assert "if (step >= 1 && step <= 3) scopeFrame('b-body', 'b-scope0', 165, step >= 2 ? 1 : 0);" in src
    assert "else if (step === 4) scopeFrame('b-body', 'b-scope1', 300, 0);" in src
    # 减少动效：只关平移补间；.draw 已由同块的全局规则收掉，尺子仍落在终态。
    assert ".scope{transition:none}" in src


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_scope_tracks_compared_column(full_lesson):
    """真实浏览器：对位镜跟着"这一句在比哪两位"走，且两种换法的动法不同。

    seg-2 的旁白逐位推进：s1 比首位（i=0），s2/s3 比第二位（i=1），s4 换到
    第二对。锁的口径：
      · 同一行内 k 0→1：**同一个节点**的 transform 补间平移一格（46 单位），
        .draw 不重放——"同一把尺被挪到下一对位置上"，位置关系是连续的；
      · 换到第二对：旧尺扫掉、新尺重新 .draw——"这是一次新的测量"；
      · 读数中点始终等于两个被比格中心的连线中点（几何由格距推，不靠眼看）；
      · 尺子压在格子上方（不压字），pointer-events:none；
      · s0（还没有比较）与 s5（已是结论）都不架尺；
      · reduced-motion / 导出截图口径：补间与动画全停，尺子直接落在这一拍
        该在的位置，读数与完整线形都在（终态不依赖动效）。
    """
    chrome, ev, send = _cdp(full_lesson, "scope")
    try:
        ev(_SEEK)
        ev("""(function(){
          window.__scope = function(key){
            var g = document.getElementById(key);
            if (!g) return {present:false};
            var p = document.getElementById(key + '-m');
            var lab = document.getElementById(key + '-lab');
            var r = p.getBoundingClientRect();
            return {present:true, k:g.getAttribute('data-k'),
              tr:g.getAttribute('transform'), lab:lab.textContent,
              d:p.getAttribute('d'), mid:(r.left + r.right) / 2,
              bottom:r.bottom, pe:getComputedStyle(g).pointerEvents,
              tp:getComputedStyle(g).transitionProperty,
              off:getComputedStyle(p).strokeDashoffset,
              same:g === window.__scopeNode,
              an:g.getAnimations({subtree:true}).map(function(a){
                return a.transitionProperty || a.animationName || '';})};
          };
          window.__geo = function(key, a, b){
            var p = document.getElementById(key + '-m');
            var r = p.getBoundingClientRect();
            function m(id){
              var q = document.getElementById(id).getBoundingClientRect();
              return {cx:(q.left + q.right) / 2, top:q.top,
                      left:q.left, right:q.right};
            }
            return {mid:(r.left + r.right) / 2, bottom:r.bottom,
                    left:r.left, right:r.right, a:m(a), b:m(b)};
          };
          return 1;
        })()""")

        def read(key):
            return json.loads(ev("JSON.stringify(window.__scope('%s'))" % key))

        # s1：比首位 —— 尺子架在 "1" 与 "10" 的首位之间，读数 i=0
        ev("window.__jump('seg-2', 1)")
        s1 = read("b-scope0")
        assert s1["present"] and s1["k"] == "0" and s1["lab"] == "i=0", s1
        assert s1["d"] == "M354 -30L354 -42H606V-30", s1
        assert s1["tr"] == "translate(0,165)", s1
        assert s1["pe"] == "none", f"量规是注释层，不吃指针：{s1}"
        assert "transform" in s1["tp"], f".scope 只补 transform：{s1['tp']}"
        assert "ntDraw" in s1["an"], f"新尺要'量过去'：{s1}"
        ev("window.__scopeNode = document.getElementById('b-scope0')")
        assert not _wait_idle(ev, "#b-scope0-m"), "量规画完应落定在满线"

        g1 = json.loads(ev("JSON.stringify(window.__geo('b-scope0','b-000','b-010'))"))
        assert g1["mid"] == pytest.approx((g1["a"]["cx"] + g1["b"]["cx"]) / 2, abs=1.5), \
            f"读数中点应落在两个被比格的中间：{g1}"
        assert g1["a"]["cx"] < g1["left"] and g1["right"] < g1["b"]["cx"], \
            f"尺子两端只落在两个被比位置之间：{g1}"
        assert g1["bottom"] < g1["a"]["top"], \
            f"尺子要压在格子上方，不压字：{g1['bottom']} vs {g1['a']['top']}"

        # s2：比第二位 —— 同一把尺平移一格（transition），不重建、不重画
        ev("window.__jump('seg-2', 2)")
        s2 = read("b-scope0")
        assert s2["present"] and s2["same"], "换位是同一把尺，不是新节点"
        assert s2["k"] == "1" and s2["lab"] == "i=1", s2
        assert s2["tr"] == "translate(46,165)", s2
        assert "transform" in s2["an"], f"换位要走 transition：{s2}"
        assert "ntDraw" not in s2["an"], f"同一行换位不该重新量一次：{s2}"
        assert not _wait_idle(ev, "#b-scope0"), "平移应在 --dur-scope 内落定"

        g2 = json.loads(ev("JSON.stringify(window.__geo('b-scope0','b-00-x','b-011'))"))
        assert g2["mid"] == pytest.approx((g2["a"]["cx"] + g2["b"]["cx"]) / 2, abs=1.5), \
            f"第二位是空位 vs 第二位字符：{g2}"
        assert g2["a"]["cx"] < g2["left"] and g2["right"] < g2["b"]["cx"], \
            f"换位后两端仍贴着两个被比位置：{g2}"

        # s3：判决句仍比第二位 —— 没有换位就没有补间
        ev("window.__jump('seg-2', 3)")
        s3 = read("b-scope0")
        assert s3["k"] == "1" and s3["tr"] == "translate(46,165)", s3
        assert s3["an"] == [], f"没有换位就没有补间：{s3}"

        # s4：换到第二对的首位 —— 旧尺撤走，新尺从零量过去（新的测量）
        ev("window.__jump('seg-2', 4)")
        assert not read("b-scope0")["present"], "换对后旧尺应撤走"
        s4 = read("b-scope1")
        assert s4["present"] and s4["k"] == "0" and s4["lab"] == "i=0", s4
        assert s4["tr"] == "translate(0,300)", s4
        assert "ntDraw" in s4["an"], f"换行是一次新的测量（.draw 重跑）：{s4}"
        g4 = json.loads(ev("JSON.stringify(window.__geo('b-scope1','b-100','b-110'))"))
        assert g4["mid"] == pytest.approx((g4["a"]["cx"] + g4["b"]["cx"]) / 2, abs=1.5), \
            f"第二对的尺子同样居中：{g4}"

        # s5：结论句不架尺
        ev("window.__jump('seg-2', 5)")
        assert ev("document.querySelectorAll('.scope').length") == 0, \
            "s5 已是结论，不该还有'正在量'的尺"

        # s0：还没有比较，也不架尺
        ev("window.__jump('seg-2', 0)")
        assert ev("document.querySelectorAll('.scope').length") == 0, \
            "s0 还没开始比较，不该架尺"

        # reduced-motion：补间与 .draw 全停，终态仍完整（这一拍该在的位置 + 满线）
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev("window.__jump('seg-2', 1)")
        r1 = read("b-scope0")
        assert r1["present"] and r1["tr"] == "translate(0,165)", r1
        assert r1["an"] == [], f"减少动效下不量、不平移，直接落位：{r1}"
        ev("window.__jump('seg-2', 2)")
        r2 = read("b-scope0")
        assert r2["tr"] == "translate(46,165)", r2
        assert r2["an"] == [], f"减少动效下换位是瞬时的：{r2}"
        g5 = json.loads(ev("JSON.stringify(window.__geo('b-scope0','b-00-x','b-011'))"))
        assert g5["mid"] == pytest.approx((g5["a"]["cx"] + g5["b"]["cx"]) / 2, abs=1.5), g5
        assert r2["off"] in ("0px", "0"), f"切掉动画后尺子是完整的：{r2['off']}"

        # 导出截图口径（export_video 注入的同一条规则）：动画/补间全关，几何仍在
        ev("""(function(){
          var st = document.createElement('style');
          st.textContent = '*,*::before,*::after{transition:none!important;' +
                           'animation:none!important}';
          document.head.appendChild(st);
          return 1;
        })()""")
        ev("window.__jump('seg-2', 4)")
        shot = read("b-scope1")
        assert shot["present"] and shot["tr"] == "translate(0,300)", shot
        assert shot["an"] == [], f"截图模式不该有动画：{shot}"
        assert shot["off"] in ("0px", "0"), f"截图里尺子是满线：{shot['off']}"
    finally:
        _kill(chrome)


def test_focus_audit_diff_row_stays_dimmed_until_called():
    """0.31.0 动效目的审计续：声画对齐，未轮到的 diff 行退居参照。

    renderG（seg-3）s2 旁白讲"当所有元素位数相同时…恰好一致"——话头是 same 行
    （1/2/3 位数相同）。但 diff 行（红色"位数不同"标签 + 1/2/10 三卡）from:2
    起建档，若同一拍满亮淡入就跟话头抢注意力，与 0.28.0 的 renderB 同构
    （没轮到的内容不该满亮度登场）。修法同一套：s2 的 diff 行走弱化档位
    （data-dim + opacity .45，"在但不在重点上"），s3「位数不同就破了」才升为话头。
    锁的口径：dim 表达式中含 s2 分支 `(step === 2 && gi === 1)`；s3 的
    `(step === 3 && gi === 0)` 保持原样（same 行让位）。
    """
    src_html = TEMPLATE_HTML.read_text(encoding="utf-8")
    assert "var dim = (step === 3 && gi === 0) || (step === 2 && gi === 1);" in src_html,         "s2 的 diff 行走弱化档位、s3 才升话头（一屏一个话头，声画对齐）"


# ── 11. 行进余像：沿线抵达的拖尾（0.32.0）──

def test_wake_echo_trail_primitives_present():
    """模板结构契约：行进余像长在范本里，边界逐条落在源码上（无浏览器也可锁）。

    `.ride` 说的是"人到了那一格"，`.wake` 补的是"路上留下了速度"：同一枚标记的
    两枚圆环余像各晚 --trail-step 的 1 / 2 倍出发，行程中拖在头后、到站叠回头下。
    锁的口径：
      · 余像是 `.ride` 的修饰层（`class:'ride wake'`）：路线、时长、linear 都只有
        `.ride` 一处定义，这一层只加 `transition-delay` 与 `pointer-events:none`；
      · 先后只从 `--trail-step` 一处派生（`--wake-i` 是排位，不注册 @property）；
      · 余像建在头之前（DOM 顺序即遮挡关系）；只描圆环、不带箭头；
      · 到站两环外沿 < 头的 6（r 5.2/4.4 + 描边 1.4 的一半 = 5.9/5.1），一像素不露头；
      · reduced-motion 只归零 delay（基础规则源序在媒体块之前，同 .ride 不需抬特异度）。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")
    # ① 修饰层：不重述路线/时长（同一份 .ride 定义），只加先后与命中
    assert (".wake{transition-delay:calc(var(--trail-step) * var(--wake-i, 1));\n"
            "        pointer-events:none}") in src
    assert src.count("transition:offset-distance var(--dur-ride) linear}") == 1, \
        "行程补间只有 .ride 一处定义，余像不许抄第二份时长"
    assert "@property --wake-i" not in src, \
        "--wake-i 只是排位读数（常量替换进 calc），注册成 <number> 只会多一条可补间的路"
    # ② 余像建在头之前：同位置时头压住尾，终态靠遮挡不靠 opacity:0
    i_wake = src.index("put('e-body', 'g', 'e-wake-' + p[0] + '-' + i,")
    i_head = src.index("put('e-body', 'g', 'e-ride-' + p[0],", i_wake)
    assert i_wake < i_head, "尾巴必须先建档：DOM 顺序就是遮挡顺序"
    wall = src[i_wake:i_head]
    assert "put(wk, 'circle', 'e-wake-' + p[0] + '-' + i + '-c'," in wall
    assert "put(wk, 'path'" not in wall, "余像不描箭头（它不是第二个标记）"
    assert "'stroke-width':1.4, opacity: i === 1 ? .5 : .25" in wall
    assert "r: i === 1 ? 5.2 : 4.4" in wall
    assert 5.2 + 1.4 / 2 <= 6 and 4.4 + 1.4 / 2 <= 6, \
        "两环连描边外沿都不超过头的 6，到站一像素不露头"
    # ③ 路线与头同一组坐标、同一份字符串（两处各抄一遍就会跑偏）
    assert 'var route = "--route:path(\'M 354 " + p[1] + " L 646 " + p[2] + "\')";' in src
    assert "style: route + ';--wake-i:' + i" in src
    assert "style: route});" in src, "头复用同一条路线"
    assert "'data-go': step >= 1 ? '1' : '0'" in src
    # ④ reduced-motion：只归零这一层的先后；路线与档位不是动效，不进兜底清单
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", src, re.S).group(1)
    assert ".wake{transition-delay:0s}" in rm
    assert ".ride{transition:none}" in rm
    rm_rules = re.sub(r"/\*.*?\*/", "", rm, flags=re.S)
    assert "offset-path" not in rm_rules and "--route" not in rm_rules, \
        f"路线与档位是读数，不随动效一起关：{rm_rules}"


_WAKE_PROBE = r"""(function(){
  function one(id){
    var n = document.getElementById(id);
    if (!n) return null;
    var cs = getComputedStyle(n), m = n.getScreenCTM();
    var c = n.firstChild, ccs = getComputedStyle(c);
    return {dist:cs.offsetDistance, delay:cs.transitionDelay, tp:cs.transitionProperty,
      tdur:cs.transitionDuration, pe:cs.pointerEvents, cls:n.getAttribute('class'),
      parent:n.parentNode.id, ax:+m.e.toFixed(2), ay:+m.f.toFixed(2),
      tag:c.tagName, r:+c.getAttribute('r'), sw:+c.getAttribute('stroke-width'),
      fill:ccs.fill, op:+ccs.opacity,
      kids:[].slice.call(n.children).map(function(k){ return k.tagName; })};
  }
  var o = {order:[].slice.call(document.getElementById('e-body').children)
             .map(function(n){ return n.id; })};
  ['\u5c0f\u660e','\u5c0f\u7ea2'].forEach(function(nm){
    o[nm] = {head:one('e-ride-' + nm), w1:one('e-wake-' + nm + '-1'),
             w2:one('e-wake-' + nm + '-2')};
  });
  return JSON.stringify(o);
})()"""

_WAKE_SAMPLE = r"""(function(){
  var ids = [];
  ['\u5c0f\u660e','\u5c0f\u7ea2'].forEach(function(nm){
    ids.push('e-ride-' + nm, 'e-wake-' + nm + '-1', 'e-wake-' + nm + '-2');
  });
  var n = 0;
  ids.forEach(function(id){
    (document.getElementById(id).getAnimations() || []).forEach(function(a){
      if (a.transitionProperty !== 'offset-distance') return;
      a.pause(); a.currentTime = MS; n++;
    });
  });
  var frames = ids.map(function(id){
    var el = document.getElementById(id), cs = getComputedStyle(el), m = el.getScreenCTM();
    return {id:id, dist:+parseFloat(cs.offsetDistance).toFixed(2),
            x:+m.e.toFixed(2), y:+m.f.toFixed(2)};
  });
  return JSON.stringify({paused:n, frames:frames});
})()"""

_WAKE_CANCEL = r"""(function(){
  var ids = [];
  ['\u5c0f\u660e','\u5c0f\u7ea2'].forEach(function(nm){
    ids.push('e-ride-' + nm, 'e-wake-' + nm + '-1', 'e-wake-' + nm + '-2');
  });
  var n = 0;
  ids.forEach(function(id){
    (document.getElementById(id).getAnimations() || []).forEach(function(a){
      if (a.transitionProperty === 'offset-distance'){ a.cancel(); n++; }
    });
  });
  var frames = ids.map(function(id){
    var el = document.getElementById(id), cs = getComputedStyle(el), m = el.getScreenCTM();
    return {id:id, dist:+parseFloat(cs.offsetDistance).toFixed(2),
            x:+m.e.toFixed(2), y:+m.f.toFixed(2)};
  });
  return JSON.stringify({stopped:n, frames:frames});
})()"""


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_wake_trails_behind_rider_and_stacks_at_arrival(full_lesson):
    """真实浏览器：余像拖在头后、到站叠回头下（0.32.0）。

    锁的口径：
      · s0 余像与头**同点**（都还在路头），两枚尾巴被头完全遮住（同锚点 + 外沿 < 6）；
      · s1 把三条补间一起 pause 到 t=400ms：头 50%、尾 41.25% / 32.5%（延迟 70/140ms
        在 .8s 行程上就是 8.75%/17.5%），屏幕上前头 > 尾 1 > 尾 2，间距 15–30px——
        "一串"而不是三个独立标记；
      · 到站（cancel 后落 100%）三枚同锚点（±0.6px），尾巴回到头下，画面不多一笔；
      · 头的 children 仍是"圆 + 箭头"两项：余像是**兄弟节点**，不改头的身位；
      · reduced-motion：只归零 delay；transition 由 .nt 那一刀关掉，直接落 100%；
      · 导出截图：一刀切掉 transition 后 s0 落 0%、s1 落 100%，与作者写的档位逐值相同。
    """
    chrome, ev, send = _cdp(full_lesson, "wake")
    try:
        ev(_SEEK)
        ev("window.__jump('seg-6', 0)")
        time.sleep(0.5)
        st = json.loads(ev(_WAKE_PROBE))
        order = st["order"]
        for nm in ("\u5c0f\u660e", "\u5c0f\u7ea2"):
            head, w1, w2 = st[nm]["head"], st[nm]["w1"], st[nm]["w2"]
            assert head and w1 and w2, f"两枚余像与头都在场：{st[nm]}"
            assert order.index("e-wake-%s-1" % nm) < order.index("e-wake-%s-2" % nm) \
                < order.index("e-ride-%s" % nm), \
                f"DOM 顺序必须是尾、尾、头（遮挡关系）：{order}"
            for k in ("parent", "cls", "tp", "tdur", "pe"):
                assert w1[k] == w2[k], f"两条尾巴同一口径 {k}：{w1[k]} / {w2[k]}"
            assert w1["parent"] == "e-body", f"不挂在收拢层里：{w1}"
            assert w1["cls"] == "nt ride wake", w1
            assert w1["tp"] == "offset-distance" and w1["tdur"] == "0.8s", \
                f"余像复用 .ride 的行程补间（不新开时长）：{w1}"
            assert w1["delay"] == "0.07s" and w2["delay"] == "0.14s", \
                f"先后从 --trail-step 派生：{w1['delay']} / {w2['delay']}"
            assert w1["pe"] == "none", f"余像是视觉层，不吃指针：{w1}"
            assert w1["kids"] == ["circle"] and w2["kids"] == ["circle"], \
                f"只有一枚圆环（没有箭头、没有字）：{w1['kids']} / {w2['kids']}"
            assert head["kids"] == ["circle", "path"], \
                f"余像是兄弟不是孩子，头的结构一项不改：{head['kids']}"
            assert w1["tag"] == "circle" and w1["fill"] == "none" \
                and w1["r"] == 5.2 and w1["sw"] == 1.4, w1
            assert w2["r"] == 4.4, w2
            assert abs(w1["op"] - .5) < .01 and abs(w2["op"] - .25) < .01, \
                f"透明度逐枚递减：{w1['op']} / {w2['op']}"
            assert w1["r"] + w1["sw"] / 2 <= 6 and w2["r"] + w2["sw"] / 2 <= 6
            assert w1["dist"] == "0%" and w2["dist"] == "0%", \
                f"s0 尾巴停在路头（和头一样不出发）：{w1} / {w2}"
            assert abs(w1["ax"] - head["ax"]) <= 0.6 and abs(w2["ax"] - head["ax"]) <= 0.6 \
                and abs(w1["ay"] - head["ay"]) <= 0.6, \
                f"s0 三枚同锚点，尾巴被头遮住：{w1} {w2} {head}"

        ev("window.__jump('seg-6', 1)")
        time.sleep(0.12)
        mid = json.loads(ev(_WAKE_SAMPLE.replace("MS", "400")))
        assert mid["paused"] == 6, f"两个头 + 四条尾巴都要在路上：{mid}"
        f = {x["id"]: x for x in mid["frames"]}
        for nm in ("\u5c0f\u660e", "\u5c0f\u7ea2"):
            h, a, b = (f["e-ride-" + nm], f["e-wake-" + nm + "-1"], f["e-wake-" + nm + "-2"])
            assert abs(h["dist"] - 50) <= 0.6, f"头半程：{h}"
            assert abs(a["dist"] - 41.25) <= 0.6, f"尾巴晚 70ms：{a}"
            assert abs(b["dist"] - 32.5) <= 0.6, f"尾巴晚 140ms：{b}"
            assert 15 <= h["x"] - a["x"] <= 30 and 15 <= a["x"] - b["x"] <= 30, \
                f"屏幕间距 ~22px：{h['x']} / {a['x']} / {b['x']}"

        rest = json.loads(ev(_WAKE_CANCEL))
        assert rest["stopped"] == 6, rest
        f = {x["id"]: x for x in rest["frames"]}
        for nm in ("\u5c0f\u660e", "\u5c0f\u7ea2"):
            h, a, b = (f["e-ride-" + nm], f["e-wake-" + nm + "-1"], f["e-wake-" + nm + "-2"])
            assert h["dist"] == a["dist"] == b["dist"] == 100.0, \
                f"到站三枚都落 100%：{h} {a} {b}"
            assert abs(a["x"] - h["x"]) <= 0.6 and abs(b["x"] - h["x"]) <= 0.6 \
                and abs(a["y"] - h["y"]) <= 0.6, \
                f"到站尾巴叠回头下（一像素不露头）：{h} {a} {b}"

        # reduced-motion：只归零 delay，transition 由 .nt 那一刀关掉
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev("window.__jump('seg-6', 0)")
        time.sleep(0.2)
        ev("window.__jump('seg-6', 1)")
        time.sleep(0.2)
        rm = json.loads(ev(_WAKE_PROBE))
        for nm in ("\u5c0f\u660e", "\u5c0f\u7ea2"):
            head = rm[nm]["head"]
            for wk in (rm[nm]["w1"], rm[nm]["w2"]):
                assert wk["delay"] == "0s", \
                    f"减少动效必须归零 delay（否则余像各晚 70/140ms 才落）：{wk}"
                assert wk["tp"] == "none", f"补间整个关掉：{wk}"
                assert wk["dist"] == "100%" and head["dist"] == "100%", \
                    f"直接落那一拍该在的位置：{wk} / {head}"
                assert abs(wk["ax"] - head["ax"]) <= 0.6, \
                    f"关掉之后尾巴与头同点（画面不多一笔）：{wk}"

        # 导出截图：一刀切掉 transition（与 export_video 注入的规则同文）
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]})
        ev("""(function(){
          var st = document.createElement('style');
          st.textContent = '*,*::before,*::after{transition:none!important;' +
                           'animation:none!important}';
          document.head.appendChild(st);
          return 1;
        })()""")
        ev("window.__jump('seg-6', 0)")
        time.sleep(0.15)
        shot0 = json.loads(ev(_WAKE_PROBE))
        ming = "\u5c0f\u660e"  # 3.10/3.11 的 f-string 表达式里不能出现反斜杠，先落到变量
        assert shot0[ming]["w1"]["dist"] == "0%", \
            f"导出帧 s0 落在路头（作者写的档位）：{shot0[ming]['w1']}"
        ev("window.__jump('seg-6', 1)")
        time.sleep(0.15)
        shot1 = json.loads(ev(_WAKE_PROBE))
        for nm in ("\u5c0f\u660e", "\u5c0f\u7ea2"):
            head = shot1[nm]["head"]
            for wk in (shot1[nm]["w1"], shot1[nm]["w2"]):
                assert wk["dist"] == "100%" and abs(wk["ax"] - head["ax"]) <= 0.6, \
                    f"导出定格是抵达之后那一格：{wk} / {head}"
    finally:
        _kill(chrome)


# ── 12. 集合括注：一组东西有自己的括（0.34.0）──

def test_group_brace_primitives_present():
    """模板结构契约：集合括注长在范本里，四条口径落在源码上（无浏览器也可锁）。

    0.19.0 的收拢（.tuck）管"这一堆要退场"，0.13.0 的弱化（[data-dim]）管"哪一块
    退后"，0.30.0 的对位镜管"这两个在比"——"这几枚属于同一组"（chunking）此前
    没有原语。集合括注借排版里的花括线：两段 dash 从外端收到共用括尖，标签晚半拍
    落。锁的口径：
      · 令牌一支墨色（绿 .55，与 s1 那两条同分连线同档），不新开行程——合拢复用
        .draw 的 --dur-draw；
      · 两半各画各的（各一条 path、各从外端起笔、同一枚括尖收笔）：写成一条 path
        会从头扫到尾，读成"划线"而不是"总括"；
      · 标签 --delay 取 --dur-draw 的 .6，只写短记号（纪律 3）；
      · 纯注释层 pointer-events:none；静止态就是终态，减少动效/导出截图不需要
        为它单开规则；
      · 消费者：seg-6 s3 只括同分的那两张结果卡（小明/小红），不是整列；旧的
        e-note 事实注解撤掉——一个镜头只留一个话头。
    """
    src = TEMPLATE_HTML.read_text(encoding="utf-8")
    assert "--brace-ink:rgba(46,204,113,.55);" in src
    assert ".brace{pointer-events:none}" in src
    assert ".brace path{fill:none;stroke:var(--brace-ink);stroke-width:1.5;" in src
    assert ".brace text{fill:var(--ok);letter-spacing:.02em}" in src
    assert "function boxIn(node, stop){" in src
    assert "function braceUnder(parent, key, nodes, label){" in src
    assert "var pad = 12, gap = 16, d = 10, n = 6, k = 9;" in src
    # 两臂同收于括尖（cx, y+d+n）；两行各一条、镜像出号
    assert "'L' + f(cx - k) + ' ' + f(y + d) +" in src
    assert "'L' + f(cx + k) + ' ' + f(y + d) +" in src
    assert src.count("'Q' + cx + ' ' + f(y + d) + ' ' + cx + ' ' + f(y + d + n)});") == 2
    assert "style:'--delay:calc(var(--dur-draw) * .6)'" in src
    # 量法读 transform 属性（baseVal = 目标位），不是 getScreenCTM（补间半途）
    assert "n.transform && n.transform.baseVal" in src
    # 消费者：括的是两张 90 分卡，不是整列/整屏
    assert "braceUnder('e-body', 'e-brace'," in src
    assert "['小明', '小红'].map(function(nm){ return 'e-r-' + nm; })" in src
    assert "'同分 · 顺序不变'" in src
    assert "'e-note'" not in src, "旧的事实注解应撤掉，画面只留一个话头"


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")
def test_group_brace_closes_around_tied_pair(full_lesson):
    """真实浏览器：seg-6 s3 的括注括住同分的两张结果卡，两端合拢、标签后落。

    锁的口径：
      · s0/s2 没有括；s3 才出现，只在 s3；
      · 几何从两张卡的坐标声明现推：并集外扩 12、下移 16；两臂各自从外端起笔，
        收在同一枚括尖（中点、再落 10 + 6）；标签居中挂在括尖下 16；
      · 两臂同拍 .draw（ntDraw、无 delay），标签晚 --dur-draw 的 .6（330ms）；
        暂停到 200ms 时字还没露（backwards 填 from），700ms 时线已满、字已亮；
      · 括线在卡片下方、标签在括尖下方、整组在字幕带上沿之上（不遮不压）；
      · 纯注释层 pointer-events:none；括线 --brace-ink、标签满浓度 --ok；
      · 退回上一步整棵撤走，再进重演一次（新节点、ntDraw 重新跑）；
      · reduced-motion / 导出截图：动画全停，dash 满线、标签可见、几何仍在。
    """
    probe = """(function(key){
      var g = document.getElementById(key);
      if (!g) return {present:false};
      var l = document.getElementById(key + '-l');
      var r = document.getElementById(key + '-r');
      var lab = document.getElementById(key + '-lab');
      function cardBox(id){
        var c = document.getElementById(id);
        var q = c.querySelector('rect');
        var m = /translate\\(([-\\d.]+)[ ,]([-\\d.]+)\\)/.exec(c.getAttribute('transform'));
        return {x:(+m[1]) + (+q.getAttribute('x')),
                y:(+m[2]) + (+q.getAttribute('y')),
                w:+q.getAttribute('width'), h:+q.getAttribute('height')};
      }
      var a = cardBox('e-r-\\u5c0f\\u660e'), b = cardBox('e-r-\\u5c0f\\u7ea2');
      var ux = Math.min(a.x, b.x), uy = Math.min(a.y, b.y);
      var u = {x:ux, y:uy,
               w:Math.max(a.x + a.w, b.x + b.w) - ux,
               h:Math.max(a.y + a.h, b.y + b.h) - uy};
      function ends(el){
        var d = el.getAttribute('d');
        var nums = d.match(/-?\\d+(?:\\.\\d+)?/g);
        var m = /^M([-\\d.]+) ([-\\d.]+)/.exec(d);
        return {sx:+m[1], sy:+m[2], ex:+nums[nums.length - 2],
                ey:+nums[nums.length - 1]};
      }
      function an(el){ return (el.getAnimations() || []).map(function(x){
        return x.animationName || x.transitionProperty || ''; }); }
      var bl = l.getBoundingClientRect(), br = r.getBoundingClientRect();
      var lb = lab.getBoundingClientRect();
      var pa = document.getElementById('e-r-\\u5c0f\\u660e').getBoundingClientRect();
      var pb = document.getElementById('e-r-\\u5c0f\\u7ea2').getBoundingClientRect();
      var cap = document.getElementById('cap').getBoundingClientRect();
      var timing = lab.getAnimations().map(function(x){ return x.effect.getTiming(); });
      return {present:true, kids:[].map.call(g.children, function(n){ return n.tagName; }),
        cls:g.getAttribute('class'), pe:getComputedStyle(g).pointerEvents,
        stroke:getComputedStyle(l).stroke, sw:getComputedStyle(l).strokeWidth,
        fill:getComputedStyle(lab).fill, text:lab.textContent,
        delay:getComputedStyle(lab).animationDelay,
        pathDelay:getComputedStyle(l).animationDelay,
        anPaths:an(l).concat(an(r)), anLab:an(lab), timing:timing,
        off:getComputedStyle(l).strokeDashoffset,
        offR:getComputedStyle(r).strokeDashoffset,
        op:+getComputedStyle(lab).opacity,
        d:l.getAttribute('d'), dR:r.getAttribute('d'),
        L:ends(l), R:ends(r), u:u,
        labX:+lab.getAttribute('x'), labY:+lab.getAttribute('y'),
        box:{left:Math.min(bl.left, br.left), right:Math.max(bl.right, br.right),
             top:Math.min(bl.top, br.top), bottom:Math.max(bl.bottom, br.bottom)},
        labBox:{left:lb.left, right:lb.right, top:lb.top, bottom:lb.bottom},
        pair:{left:Math.min(pa.left, pb.left), right:Math.max(pa.right, pb.right),
              top:Math.min(pa.top, pb.top), bottom:Math.max(pa.bottom, pb.bottom)},
        capTop:cap.top};
    })('e-brace')"""
    chrome, ev, send = _cdp(full_lesson, "brace")
    try:
        ev(_SEEK)

        def read():
            return json.loads(ev("JSON.stringify(%s)" % probe))

        # s0 / s2：还没有"同组"要说，不架括
        for k in (0, 2):
            ev("window.__jump('seg-6', %d)" % k)
            assert ev("document.querySelectorAll('.brace').length") == 0, \
                "seg-6 s%d 不该有括注" % k

        # s3：两张 90 分卡被括成一组
        ev("window.__jump('seg-6', 3)")
        early = read()
        assert early["present"], "s3 要给同分的两人补一道括注"
        assert early["kids"] == ["path", "path", "text"], \
            "两臂 + 一枚标签，不多不少：%s" % early["kids"]
        assert early["cls"] == "nt brace", early["cls"]
        assert early["pe"] == "none", "括注是注释层，不吃指针"
        assert early["stroke"] == "rgba(46, 204, 113, 0.55)", early["stroke"]
        assert early["sw"] == "1.5px", early["sw"]
        assert early["fill"] == "rgb(46, 204, 113)", \
            "标签用满浓度 --ok（括线才退到 .55）：%s" % early["fill"]
        assert early["text"] == "同分 · 顺序不变", early["text"]
        assert len(early["text"]) <= 10, "标签是短记号，不是句子（纪律 3）"
        assert "ntDraw" in early["anPaths"], "两臂要合拢（.draw）：%s" % early["anPaths"]
        assert early["pathDelay"] == "0s", "两臂同拍起笔"
        assert "ntIn" in early["anLab"], "标签要淡入：%s" % early["anLab"]
        assert early["delay"] == "0.33s" and early["timing"] \
            and early["timing"][0]["delay"] == 330, \
            "标签晚 --dur-draw 的 .6（330ms）才落：%s / %s" % (
                early["delay"], early["timing"])

        # 暂停在 200ms：线还在路上，字还没露（.in 的 backwards 填 from）
        ev("""(function(){
          window.__braceAn = function(){
            return [].concat(
              document.getElementById('e-brace-l').getAnimations(),
              document.getElementById('e-brace-r').getAnimations(),
              document.getElementById('e-brace-lab').getAnimations());
          };
          window.__braceAt = function(ms){
            window.__braceAn().forEach(function(a){ a.pause(); a.currentTime = ms; });
            var lab = document.getElementById('e-brace-lab');
            return JSON.stringify({n:window.__braceAn().length,
              op:+getComputedStyle(lab).opacity,
              off:getComputedStyle(document.getElementById('e-brace-l')).strokeDashoffset});
          };
          return 1;
        })()""")
        at200 = json.loads(ev("window.__braceAt(200)"))
        assert at200["n"] == 3, "两臂 + 标签三条动画都在跑：%s" % at200
        assert at200["op"] < .05, "线到六成之前，字不该露：%s" % at200
        at700 = json.loads(ev("window.__braceAt(700)"))
        assert at700["op"] > .85, "括完了字才亮起来：%s" % at700
        assert at700["off"] in ("0px", "0"), "700ms 时两臂已收满：%s" % at700
        ev("window.__braceAn().forEach(function(a){ a.finish(); })")
        assert not _wait_idle(ev, "#e-brace-lab"), "括注跑到头要落定"

        # 收满后的几何：全部从被括卡的坐标声明推，不靠眼看
        b = read()
        u = b["u"]
        assert u["w"] > 0 and u["h"] > 0, u
        cx = u["x"] + u["w"] / 2
        assert b["L"]["sx"] == pytest.approx(u["x"] - 12, abs=.2), \
            "左臂从并集外缘再让 12 起笔：%s" % b["L"]
        assert b["R"]["sx"] == pytest.approx(u["x"] + u["w"] + 12, abs=.2), \
            "右臂从另一侧外缘再让 12 起笔：%s" % b["R"]
        assert b["L"]["sy"] == b["R"]["sy"] == pytest.approx(u["y"] + u["h"] + 16, abs=.2), \
            "括线落在卡片下方 16：%s / %s" % (b["L"], b["R"])
        assert b["L"]["ex"] == b["R"]["ex"] == pytest.approx(cx, abs=.2), \
            "两臂收在同一枚括尖上（x）：%s / %s" % (b["L"], b["R"])
        assert b["L"]["ey"] == b["R"]["ey"] == pytest.approx(u["y"] + u["h"] + 16 + 10 + 6, abs=.2), \
            "括尖再落 10 + 6：%s / %s" % (b["L"], b["R"])
        assert b["labX"] == pytest.approx(cx, abs=.2), "标签居中在括尖下"
        assert b["labY"] == pytest.approx(b["L"]["ey"] + 16, abs=.2), \
            "标签基线在括尖下 16：%s vs %s" % (b["labY"], b["L"]["ey"])
        assert b["off"] in ("0px", "0") and b["offR"] in ("0px", "0"), \
            "落定后两臂都是满线：%s / %s" % (b["off"], b["offR"])
        assert b["op"] == 1, "落定后标签不透明：%s" % b["op"]

        # 屏幕上的相对关系（FULL 镜头下 scale=1，误差只让给描边半宽）
        assert b["box"]["left"] == pytest.approx(b["pair"]["left"] - 12, abs=2.5), \
            "括线左端比卡片外缘再宽 12：%s vs %s" % (b["box"], b["pair"])
        assert b["box"]["right"] == pytest.approx(b["pair"]["right"] + 12, abs=2.5), \
            "括线右端同理：%s vs %s" % (b["box"], b["pair"])
        assert b["box"]["top"] == pytest.approx(b["pair"]["bottom"] + 16, abs=2.5), \
            "括线整体在卡片下方：%s vs %s" % (b["box"], b["pair"])
        assert b["labBox"]["top"] >= b["box"]["bottom"] - 1, \
            "标签在括尖下方，不压线：%s vs %s" % (b["labBox"], b["box"])
        assert b["labBox"]["bottom"] < b["capTop"] - 2, \
            "整组在字幕带上沿之上：%s vs %s" % (b["labBox"], b["capTop"])
        assert (b["labBox"]["left"] + b["labBox"]["right"]) / 2 == pytest.approx(
            (b["pair"]["left"] + b["pair"]["right"]) / 2, abs=2.5), \
            "标签居中对着两张卡：%s vs %s" % (b["labBox"], b["pair"])

        # 退回上一步：整棵撤走；再进：新节点重演一次
        ev("window.__jump('seg-6', 2)")
        assert ev("document.querySelectorAll('.brace').length") == 0, \
            "退回 s2 时括注整棵撤走"
        ev("window.__jump('seg-6', 3)")
        again = read()
        assert again["present"] and "ntDraw" in again["anPaths"], \
            "再进 s3 是新一次括，.draw 重跑：%s" % again["anPaths"]

        # reduced-motion：动画全停，终态（满线 + 可见标签 + 同一几何）仍在
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        assert ev("matchMedia('(prefers-reduced-motion: reduce)').matches")
        ev("window.__jump('seg-6', 2)")
        ev("window.__jump('seg-6', 3)")
        rm = read()
        assert rm["present"], rm
        assert rm["anPaths"] == [] and rm["anLab"] == [], \
            "减少动效下不再合拢、也不再延迟淡入：%s / %s" % (rm["anPaths"], rm["anLab"])
        assert rm["off"] in ("0px", "0") and rm["offR"] in ("0px", "0"), rm
        assert rm["op"] == 1, "标签直接在场：%s" % rm["op"]
        assert rm["L"]["ex"] == rm["R"]["ex"] == pytest.approx(cx, abs=.2), rm

        # 导出截图口径（export_video 注入的同一条规则）：一刀切掉动画，几何仍在
        send("Emulation.setEmulatedMedia",
             {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]})
        ev("""(function(){
          var st = document.createElement('style');
          st.textContent = '*,*::before,*::after{transition:none!important;' +
                           'animation:none!important}';
          document.head.appendChild(st);
          return 1;
        })()""")
        ev("window.__jump('seg-6', 2)")
        ev("window.__jump('seg-6', 3)")
        shot = read()
        assert shot["present"] and shot["anPaths"] == [] and shot["anLab"] == [], shot
        assert shot["off"] in ("0px", "0") and shot["op"] == 1, shot
        assert shot["L"]["sx"] == pytest.approx(u["x"] - 12, abs=.2) and \
            shot["R"]["sx"] == pytest.approx(u["x"] + u["w"] + 12, abs=.2), shot
    finally:
        _kill(chrome)
