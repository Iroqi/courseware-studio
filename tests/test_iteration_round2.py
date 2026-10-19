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
    那层 transform 属性顶掉）、系数只从令牌读一处、位移取 camTo 回传的 tx/ty
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
    assert "#depth{transition:transform var(--dur-cam) var(--ease-out)}" in html
    # 位移量取自 camTo 的回传值：钳位公式不留第二套
    assert "return {cx:f[0] + f[2] / 2, cy:f[1] + f[3] / 2, k:k, tx:tx, ty:ty};" in html
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
        # 先等一切落定（--dur-cam .6s / .nt .5s 还在跑时 bbox 本来就在变，
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
              · **先把擦除 pause() 再去等镜头**：换场时 `#cam` 还在补间（--dur-cam .6s），
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
    assert "capSent = null; capProgWrite(0); return;" in html, "收起必须把细线归零"
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

    # ⑩ 本轮实测否决的四条，别被下一轮再请回来
    assert "<animate" not in html and "animateMotion" not in html, \
        "SMIL 不吃 animation:none，getAnimations() 也冻不住——可及性与导出两侧都管不了它"
    assert "offset-path" not in html and "offset-distance" not in html, \
        "offset-path 的移动体不进 getBBox()，且有实体：切掉动画后落在起点多出一个小点"


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
