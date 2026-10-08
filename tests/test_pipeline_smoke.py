"""全链路冒烟：真实 CLI 跑通 时间轴 → 组装 → 检查 → 导出，并断言时长。

这是"亲自吃梨子"的自动化版：用真实的范本页面 + 合成音频 + 最小讲稿，
走完各脚本的 main() 入口（subprocess），任何一个契约断裂都会在这里失败。
浏览器冒烟与视频导出按环境能力跳过（无 chrome 时静态链路仍必须通过）。
"""
import json
import os
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
HAVE_FFPROBE = shutil.which("ffprobe") is not None
try:
    import websocket  # noqa: F401
    HAVE_WEBSOCKET = True
except ImportError:
    HAVE_WEBSOCKET = False

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")


def _run(args, cwd=None, timeout=300):
    r = subprocess.run([sys.executable, *args], capture_output=True,
                       text=True, cwd=cwd, timeout=timeout)
    return r


def _template_aligned_manifest():
    """与范本页面的门禁场景（seg-2..seg-6）对齐的 8 场景 manifest。"""
    scenes = []
    t = 0.0
    ids = ["opening", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5", "seg-6", "closing"]
    titles = {"opening": "开场", "closing": "小结"}
    for i, sid in enumerate(ids):
        sentences = []
        for k in range(1, 4):
            sentences.append({"start": round(t, 3), "duration": 1.0,
                              "text": f"{sid}第{k}句。"})
            t += 1.4
        end = round(sentences[-1]["start"] + 1.0, 3)
        scenes.append({
            "step_id": sid,
            "title": titles.get(sid, f"第{i}节"),
            "tagline": "",
            "start": sentences[0]["start"],
            "duration": round(end - sentences[0]["start"], 3),
            "end": end,
            "sentences": sentences,
        })
        t = round(end + 0.5, 3)
    return {
        "schema_version": 1, "status": "ok", "title": "冒烟测试课",
        "voice_id": "冰糖", "audio": "combined.wav",
        "total_duration": round(t - 0.5, 3),
        "degraded": {"tts_silence_fallback_count": 0, "dropped_sentence_count": 0},
        "scenes": scenes,
    }


def _make_source(manifest):
    segments = []
    for sc in manifest["scenes"]:
        if sc["step_id"] == "opening":
            continue
        if sc["step_id"] == "closing":
            continue
        segments.append({
            "id": sc["step_id"],
            "title": sc["title"],
            "text": "".join(s["text"] for s in sc["sentences"]),
        })
    return {
        "schema_version": 1,
        "title": manifest["title"],
        "opening_title": "开场",
        "opening": True,
        "opening_text": "欢迎来到本课。",
        "segments": segments,
        "closing": True,
        "closing_title": "小结",
        "closing_text": "我们下次再见。",
    }


@pytest.fixture(scope="module")
def lesson_project(tmp_path_factory):
    """跑完整装配链路，返回 (page_dir, manifest, wav_dur)。"""
    root = tmp_path_factory.mktemp("lesson-smoke")
    proj = root / "proj"
    (proj / "audio").mkdir(parents=True)
    manifest = _template_aligned_manifest()
    wav_dur = manifest["total_duration"]

    # 1) 讲稿与音频
    (proj / "source.json").write_text(
        json.dumps(_make_source(manifest), ensure_ascii=False), encoding="utf-8")
    (proj / "audio" / "narration_timing.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={wav_dur}",
         "-ar", "24000", "-ac", "1", str(proj / "audio" / "combined.wav")],
        capture_output=True)
    assert r.returncode == 0

    # 2) 时间轴
    r = _run([str(SCRIPTS / "build_timeline.py"),
              "--timing", str(proj / "audio" / "narration_timing.json"),
              "--source", str(proj / "source.json"),
              "-o", str(proj / "timeline.html")], cwd=str(root))
    assert r.returncode == 0, r.stderr
    assert (proj / "timeline.html").is_file()

    # 3) 组装（范本作页面骨架）
    draft = root / "page-draft.html"
    draft.write_text(TEMPLATE_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    page_dir = root / "lesson"
    r = _run([str(SCRIPTS / "build_page.py"),
              "--template", str(draft),
              "--timeline", str(proj / "timeline.html"),
              "--audio", str(proj / "audio" / "combined.wav"),
              "--timing", str(proj / "audio" / "narration_timing.json"),
              "-o", str(page_dir / "index.html")], cwd=str(root))
    assert r.returncode == 0, r.stderr
    assert (page_dir / "index.html").is_file()
    assert (page_dir / "audio" / "combined.wav").is_file()
    assert (page_dir / "audio" / "narration_timing.json").is_file()
    assert (page_dir / "interactive_runtime.js").is_file()
    return page_dir, manifest, wav_dur


class TestPipelineStatic:
    def test_build_chain(self, lesson_project):
        page_dir, manifest, _ = lesson_project
        r = _run([str(SCRIPTS / "check_gates.py"), str(page_dir), "--no-browser"])
        assert r.returncode == 0, r.stderr
        assert "[ok] 静态检查通过" in r.stdout
        assert "[static] scenes=8" in r.stdout

    def test_built_page_roundtrip_consistency(self, lesson_project):
        # 组装产物自洽：内联时间轴与 manifest 逐值一致（build_page 的对齐门已保证，
        # 这里通过 check_gates 的 manifest 比对再确认一次）
        page_dir, _, _ = lesson_project
        r = _run([str(SCRIPTS / "check_gates.py"), str(page_dir), "--no-browser"])
        assert "与 manifest 不一致" not in r.stdout


@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
class TestPipelineBrowser:
    def test_browser_smoke(self, lesson_project):
        page_dir, _, _ = lesson_project
        r = _run([str(SCRIPTS / "check_gates.py"), str(page_dir)], timeout=600)
        assert r.returncode == 0, r.stderr + r.stdout
        assert "浏览器冒烟通过" in r.stdout

    def test_export_duration_contract(self, lesson_project):
        """导出成片必须与旁白音频等长（回归：concat 尾帧缺陷曾让视频长出 4.6s）。"""
        page_dir, _, wav_dur = lesson_project
        out = page_dir.parent / "lesson.mp4"
        r = _run([str(SCRIPTS / "export_video.py"), str(page_dir),
                  "-o", str(out)], timeout=900)
        assert r.returncode == 0, r.stderr + r.stdout
        assert out.is_file()
        assert "mux.tmp" not in out.name
        if HAVE_FFPROBE:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,duration", "-of", "compact", str(out)],
                capture_output=True, text=True)
            assert probe.returncode == 0, probe.stderr
            video_dur = None
            audio_dur = None
            for line in probe.stdout.splitlines():
                if "video" in line and "duration=" in line:
                    video_dur = float(line.split("duration=")[1])
                if "audio" in line and "duration=" in line:
                    audio_dur = float(line.split("duration=")[1])
            assert audio_dur is not None
            assert video_dur is not None
            assert abs(video_dur - audio_dur) <= 0.35, \
                f"视频流 {video_dur:.2f}s 与音轨 {audio_dur:.2f}s 不同步"
            assert abs(audio_dur - wav_dur) <= 0.1, \
                f"音轨 {audio_dur:.2f}s 与旁白 {wav_dur:.2f}s 不符"
        else:
            # 无 ffprobe 时退化为脚本自带的时长断言（脚本内 |out_dur−wav_dur|≤0.35）
            assert "[done]" in r.stdout


# ── 真实时钟浏览器回归（可选）──────────────────────────────────────
# 第三轮实例验证的固化：check_gates 的浏览器冒烟用确定性时钟桩（从不真实播放
# 音频）；这里用无头 Chrome + CDP + 真实 audio.currentTime 推进，验证：
#   * 真实播放下字幕翻句延迟有界（rAF 驱动，<= 200ms 墙钟）
#   * 任意采样点字幕文本与时间轴逐句一致（20ms 采样）
#   * 门禁真实开/锁/继续，答对后音频真实恢复（首道门禁与下一门禁锚点过近时
#     允许被下一门禁再次暂停——范本 seg-2 末锚点与 seg-3 首锚点仅隔 0.5s）
# 因依赖真实媒体时钟与无头环境，默认跳过：REALCLOCK=1 时显式开启。
_REALCLOCK_PROBE = r"""
(() => {
  const report = {ok:false, errors:[], flipLatency:[], gates:[], rafHz:0,
                  finalT:0, captionMismatch:0};
  window.addEventListener('error', e =>
    report.errors.push('JSERR ' + (e && e.message || e)));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const q = s => document.querySelector(s);
  const capNorm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const audio = q('#main-audio');
  const caption = q('#cap-text');
  const timeline = JSON.parse(q('#lesson-timeline').textContent);
  const scenes = timeline.scenes || [];
  const idx = [];
  scenes.forEach((sc, si) => (sc.runtime.narration || []).forEach((n, ni) =>
    idx.push({si, sii:ni, t0:n.start, t1:n.start + n.duration, text:n.text})));
  let rafCount = 0;
  (function rafLoop(){ rafCount++; requestAnimationFrame(rafLoop); })();
  setInterval(() => { report.rafHz = Math.round(rafCount / 3); rafCount = 0; }, 3000);
  let lastTxt = '';
  setInterval(() => {
    const t = audio.currentTime, txt = capNorm(caption.textContent);
    if (txt !== lastTxt){
      lastTxt = txt;
      const cur = idx.find(e => txt === capNorm(e.text));
      if (cur && t >= cur.t0) report.flipLatency.push(+((t - cur.t0) * 1000).toFixed(0));
    }
    const cur = idx.find(e => t >= e.t0 + 0.15 && t < e.t1 - 0.02);
    if (cur && txt !== capNorm(cur.text)) report.captionMismatch++;
  }, 20);
  const gate = q('#gate'), go = q('#gate-go');
  const gateActive = () => gate && !gate.hidden;
  async function driveGate(){
    const card = gate.querySelector('[data-interaction]:not([hidden])');
    if (!card){ report.errors.push('no visible card'); return; }
    const kind = card.dataset.interactionType;
    const cfg = JSON.parse(card.dataset.interaction || '{}');
    const scene = (q('#gate-host') && q('#gate-host').dataset.stepId) || '';
    report.gates.push({kind, scene});
    if (kind === 'choice'){
      const right = (cfg.options || []).find(o => o.correct === true);
      const b = card.querySelector('[data-choice-id="' + CSS.escape(String(right.id)) + '"]');
      if (b) b.click();
    } else if (kind === 'recall'){
      const rv = card.querySelector('[data-recall-reveal]');
      if (rv) rv.click();
    } else if (kind === 'sequence'){
      const list = card.querySelector('.sequence-list');
      const order = (cfg.correct_order || []).map(String);
      const map = {}; Array.from(list.children).forEach(li => map[li.dataset.sequenceId] = li);
      order.forEach(id => { if (map[id]) list.appendChild(map[id]); });
      const sb = card.querySelector('[data-sequence-submit]');
      if (sb) sb.click();
    } else if (kind === 'hotspot'){
      const right = (cfg.options || cfg.spots || []).find(o => o.correct === true);
      const sp = card.querySelector('[data-hotspot-id="' + CSS.escape(String(right.id)) + '"]');
      if (sp) sp.click();
    } else if (kind === 'bucket'){
      // 条目选中走 pointerdown 手势通路（位移<=3px 算点选），click() 不触发——
      // 派发真实指针手势模拟"点条目 → 点筐"（runtime 一等手势，模板注释明确支持）
      const answer = cfg.answer || {};
      Object.keys(answer).forEach(id => {
        const item = card.querySelector('.bucket-item[data-bucket-item="' + CSS.escape(id) + '"]');
        const box = card.querySelector('[data-drop][data-bucket-id="' + CSS.escape(String(answer[id])) + '"]');
        if (item && box){
          const r = item.getBoundingClientRect();
          const tapOpts = {bubbles:true, cancelable:true, pointerId: 41, isPrimary:true,
                           clientX: r.left + r.width/2, clientY: r.top + r.height/2,
                           button: 0, pointerType: 'mouse'};
          item.dispatchEvent(new PointerEvent('pointerdown', tapOpts));
          item.dispatchEvent(new PointerEvent('pointerup', tapOpts));
          box.click();
        }
      });
      const sb = card.querySelector('[data-bucket-submit]');
      if (sb) sb.click();
    }
    let lockedMs = null;
    for (let i=0;i<40;i++){ if (card.dataset.locked === '1'){ lockedMs = i*50; break; } await sleep(50); }
    const g = report.gates[report.gates.length - 1];
    g.lockedMs = lockedMs;
    let waitMs = 0;
    while (waitMs < 1000 && go.disabled){ await sleep(50); waitMs += 50; }
    g.goEnabledMs = lockedMs === null ? -1 : waitMs;
    if (go.disabled){ report.errors.push('gate-go never enabled: ' + kind + '@' + scene); return; }
    const pausedBefore = audio.paused;
    go.click();
    await sleep(500);
    g.resumed = pausedBefore && !audio.paused;
    g.audioPausedAfter = audio.paused;
  }
  async function run(){
    audio.playbackRate = 4;
    audio.currentTime = 0;
    await audio.play().catch(()=>{});
    const t0 = Date.now();
    while (Date.now() - t0 < 18000){
      if (gateActive()) await driveGate();
      await sleep(60);
    }
    audio.pause();
    report.finalT = +audio.currentTime.toFixed(2);
    report.ok = report.errors.length === 0 && report.captionMismatch === 0
        && report.gates.length >= 1;
    window.__rlReport = report;
    document.title = 'realclock ' + (report.ok ? 'PASS' : 'FAIL');
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
"""


@pytest.mark.skipif(os.environ.get("REALCLOCK") != "1",
                    reason="真实时钟冒烟需 REALCLOCK=1 显式开启")
@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
class TestRealClockBrowser:
    def test_real_audio_clock_sync_and_gates(self, lesson_project):
        import websocket

        page_dir, _, _ = lesson_project
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        chrome = subprocess.Popen(
            [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
             "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
             f"--remote-debugging-port={port}", "--user-data-dir=" + str(page_dir / "_rlprof"),
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

            # 注入探针并等它跑完（自限 18s 真实时间）
            assert ev("document.querySelector('#lesson-timeline') !== null"), \
                "页面未就绪"
            ev(_REALCLOCK_PROBE)
            time.sleep(22)
            rep = ev("window.__rlReport")
            assert rep is not None, "探针未产出报告"
            assert rep["ok"], f"真实时钟回归失败: {json.dumps(rep['errors'], ensure_ascii=False)}"
            assert rep["captionMismatch"] == 0, "存在字幕与时间轴不一致的采样点"
            assert rep["finalT"] > 10, f"真实播放未推进（finalT={rep['finalT']}）"
            assert len(rep["gates"]) >= 1, "未遇到任何门禁"
            lats = rep["flipLatency"]
            assert lats, "未采集到翻句延迟样本"
            assert max(lats) <= 200, f"翻句延迟超限: max={max(lats)}ms"
            # 首道门禁与下一道锚点过近（范本 seg-2 末/seg-3 首仅隔 0.5s），
            # 答对后音频恢复可能被下一门禁立即暂停——恢复通路只要**有任一
            # 门禁**真实恢复播放即证明成立（实例实测 5 道中 3 道恢复、2 道
            # 被下一门禁 500ms 内再次暂停，均符合门禁锚点间距）
            assert any(g["resumed"] for g in rep["gates"]), \
                "没有任何门禁答对后恢复播放"
            for g in rep["gates"]:
                assert g["lockedMs"] is not None, f"门禁 {g['kind']}@{g['scene']} 未锁定"
                assert g["goEnabledMs"] is not None, \
                    f"门禁 {g['kind']}@{g['scene']} 继续按钮未启用"
        finally:
            chrome.kill()

# ══ 第六轮：seek / 进度条 × 门禁语义 + 导出门禁抑制（真实时钟回归）══
# 契约见 interactions.md §4「seek / 进度条与门禁的交互」。核心断言：
#   越过锚点的 seek 不追溯打断；未答门禁被 seek 撤销后重问；答对后不复出；
#   rew 重置清空状态；__coursewareShotMode 抑制门禁且不冻结音频。
_SEEK_PROBE = r"""
(() => {
  const report = {ok:false, errors:[], checks:[]};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const audio = document.getElementById('main-audio');
  const gate = document.getElementById('gate');
  const card = () => document.querySelector('#gate-host [data-interaction]:not([hidden])');
  const gateOpen = () => !gate.hidden && !!card();
  const gateClosed = () => gate.hidden;
  const goBtn = document.getElementById('gate-go');
  const kindOf = () => (card() && card().dataset) ? card().dataset.interactionType : 'none';
  const step = (name, pass) => report.checks.push({name, pass:!!pass});
  async function waitGate(ms){
    const t0 = Date.now();
    while (Date.now() - t0 < ms){ if (gateOpen()) return true; await sleep(120); }
    return gateOpen();
  }
  async function ans(kind){
    const k = kindOf();
    if (k === 'recall'){ card().querySelector('[data-recall-reveal]').click(); }
    else if (k === 'choice'){ card().querySelector('[data-choice-id="b"]').click(); }
    else if (k === 'hotspot'){ card().querySelector('[data-hotspot-id="step3"]').click(); }
    else if (k === 'bucket'){
      const cfg = JSON.parse(card().dataset.interaction || '{}');
      const answer = cfg.answer || {};
      Object.keys(answer).forEach(id => {
        const item = card().querySelector('.bucket-item[data-bucket-item="'+CSS.escape(id)+'"]');
        const box = card().querySelector('[data-drop][data-bucket-id="'+CSS.escape(String(answer[id]))+'"]');
        if (item && box){
          const r = item.getBoundingClientRect();
          const tap = {bubbles:true, cancelable:true, pointerId:41, isPrimary:true,
                       clientX:r.left+r.width/2, clientY:r.top+r.height/2, button:0, pointerType:'mouse'};
          item.dispatchEvent(new PointerEvent('pointerdown', tap));
          item.dispatchEvent(new PointerEvent('pointerup', tap));
          box.click();
        }
      });
      const sb = card().querySelector('[data-bucket-submit]');
      if (sb) sb.click();
    }
    else { report.errors.push('ans: unexpected '+k); return false; }
    await sleep(300);
    if (card().dataset.locked !== '1'){ report.errors.push('not locked: '+k); return false; }
    goBtn.click(); await sleep(450);
    return gateClosed();
  }
  async function run(){
    audio.playbackRate = 4;
    audio.currentTime = 0; await audio.play().catch(()=>{});
    audio.currentTime = 12.5; await sleep(150);                 // recall@seg-2 end
    if (!(await waitGate(1800) && kindOf()==='recall')){ report.errors.push('recall 未弹出'); }
    else if (!(await ans('recall'))){ report.errors.push('recall 未答对'); }
    step('choice 弹出', await waitGate(1800) && kindOf()==='choice');
    if (!(await ans('choice'))){ report.errors.push('choice 未答对'); }
    if (!gateClosed()){ report.errors.push('前菜后门禁未关'); }
    // 主验证：hotspot@seg-4 end
    audio.currentTime = 21.5; await sleep(900);
    step('seek 越过锚点不弹', gateClosed());                     // a
    audio.currentTime = 20.9; await sleep(150);
    step('回锚点前播放过锚点弹 hotspot', await waitGate(1800) && kindOf()==='hotspot');  // b
    if (!(await ans('hotspot'))){ report.errors.push('hotspot 未答对'); }
    audio.currentTime = 5; await sleep(700);
    step('seek 走收起门禁', gateClosed());                       // c
    audio.currentTime = 20.8; await sleep(900);
    step('答对后 seek 回锚点前不复出', gateClosed());            // e
    // 未答撤销重问：bucket@seg-5 end
    audio.currentTime = 25.1; await sleep(150);
    step('bucket 弹出', await waitGate(1800) && kindOf()==='bucket');  // f
    audio.currentTime = 5; await sleep(800);                    // 不答 seek 走
    step('未答 bucket seek 走收起', gateClosed());               // g
    audio.currentTime = 25.1; await audio.play().catch(()=>{}); await sleep(150);
    step('撤销后 bucket 重问', await waitGate(1800) && kindOf()==='bucket');  // h
    if (!(await ans('bucket'))){ report.errors.push('bucket 重问后未答对'); }
    step('答对后 bucket 收起', gateClosed());
    // 导出门禁抑制。模拟导出截图页语义：页面加载前注入 __coursewareShotMode
    // （export_video.py SHOT_MODE_FLAG），整页从头到尾无门禁。探针此前若在
    // 播放中过过 sequence 锚点，preGate 的 setTimeout 已排队——先 rew 复位：
    // resetGates() 把 gOpen 置 -1，排队回调的 `gOpen === i` 检查随之作废。
    document.getElementById('rew').click();
    window.__coursewareShotMode = true;
    audio.pause();
    await sleep(1300);                                           // 让任何排队回调过期
    audio.currentTime = 21.2; await sleep(900);
    step('shotMode 抑制 hotspot', gateClosed());                 // m
    audio.currentTime = 25.5; await sleep(900);
    step('shotMode 抑制 bucket', gateClosed());                  // n
    audio.currentTime = 26.0; await sleep(900);
    step('shotMode 抑制 sequence', gateClosed());                // o
    audio.play().catch(()=>{}); await sleep(700);
    step('shotMode 下音频不冻结', !audio.paused);                // p
    audio.pause();
    report.ok = report.errors.length === 0 && report.checks.every(c => c.pass);
    window.__rlSeekReport = report;
    document.title = 'seek ' + (report.ok ? 'PASS' : 'FAIL');
  }
  run().catch(e => { report.errors.push('PROBE ' + (e && e.message || String(e)));
    report.ok = false; window.__rlSeekReport = report; document.title = 'seek FAIL'; });
})();
"""


@pytest.mark.skipif(os.environ.get("REALCLOCK") != "1",
                    reason="seek 门禁语义回归需 REALCLOCK=1 显式开启")
@pytest.mark.skipif(not HAVE_CHROME, reason="需要 Chrome/Edge")
@pytest.mark.skipif(not HAVE_WEBSOCKET, reason="需要 websocket-client")
class TestSeekGateSemantics:
    """真实时钟下验证 seek/进度条与门禁的交互契约（interactions.md §4）。"""

    def test_seek_gate_semantics_and_shot_mode(self, lesson_project):
        import websocket

        page_dir, _, _ = lesson_project
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        chrome = subprocess.Popen(
            [str(HAVE_CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
             "--remote-allow-origins=*", "--autoplay-policy=no-user-gesture-required",
             f"--remote-debugging-port={port}", "--user-data-dir=" + str(page_dir / "_rlprof2"),
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

            assert ev("document.querySelector('#lesson-timeline') !== null"), "页面未就绪"
            ev(_SEEK_PROBE)
            time.sleep(32)
            rep = ev("window.__rlSeekReport")
            assert rep is not None, "探针未产出报告"
            assert rep["ok"], \
                f"seek 门禁语义回归失败: {json.dumps(rep['errors'], ensure_ascii=False)} " \
                f"{json.dumps([c for c in rep['checks'] if not c['pass']], ensure_ascii=False)}"
        finally:
            chrome.kill()
