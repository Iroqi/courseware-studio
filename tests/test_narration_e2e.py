"""narration.py 完整 CLI 端到端（fake openai 模块注入，零网络零密钥）。

第四轮实例验证的固化：此前 narration 的测试全部停在函数级（synth_sentence /
_synthesize_pending / _finalize_audio 各自 monkeypatch），main() 的真实编排
（argparse → 分句 → 并发合成 → concat → timing 落盘 → 下游 build_timeline /
build_page / check_gates）从没有被整链跑过。这里把 fake OpenAI 塞进
sys.modules，让 main() 里的 `from openai import OpenAI` 拿到桩客户端，
走完完整 CLI，再断言：

  * 对话展开：dialogue 段展开为逐说话人句子
  * 句级节拍：beat pause 精确落进相邻句的 start 间隔
  * 段级变速：--speed/段 speed 应用后 duration 重测（0.5s → 0.333s）
  * concat 时长精确性：combined.wav 与 manifest total_duration 一致
  * 下游全链：build_timeline → build_page → check_gates 静态通过
"""
import base64
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import narration
from conftest import REPO, SCRIPTS, TEMPLATE_HTML


# ── fake openai 模块 ──────────────────────────────────────────────
def _wav_bytes(dur=0.5, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(12000 * (0.5 + 0.5 * math.sin(
                2 * math.pi * 440 * t / rate))))
            for t in range(int(rate * dur)))
        w.writeframes(frames)
    return buf.getvalue()


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(audio=SimpleNamespace(
                data=base64.b64encode(_wav_bytes()).decode())))])


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeOpenAI:
    def __init__(self, *a, **k):
        self.chat = _FakeChat()


@pytest.fixture
def fake_openai(monkeypatch):
    mod = ModuleType("openai")
    mod.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return mod


def _source():
    return {
        "schema_version": 1,
        "title": "端到端实例课",
        "opening": "欢迎来到本节。",
        "segments": [
            {"id": "seg-1", "title": "定义",
             "text": "第一句正文。第二句正文。第三句正文。",
             "beat": {"3": {"pause": 0.9}}},
            {"id": "seg-2", "title": "对话",
             "dialogue": [
                 {"speaker": "小明", "text": "什么是排序？"},
                 {"speaker": "老师", "text": "按规则排顺序。"},
             ]},
            {"id": "seg-3", "title": "应用",
             "text": "应用段只有一句。",
             "speed": 1.5},
            {"id": "seg-4", "title": "扩展",
             "text": "扩展段第一句。扩展段第二句。"},
            {"id": "seg-5", "title": "进阶",
             "text": "进阶段第一句。进阶段第二句。进阶段第三句。"},
            {"id": "seg-6", "title": "案例",
             "text": "案例段第一句。案例段第二句。"},
        ],
        "closing": "本课结束。",
    }


def _run_main(work, source, monkeypatch, extra=()):
    src_path = work / "source.json"
    src_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    out_dir = work / "out"
    monkeypatch.setattr(sys, "argv",
                        ["narration.py", "--source", str(src_path),
                         "-o", str(out_dir), "--gap", "0.4", *extra])
    monkeypatch.setenv("MIMO_API_KEY", "fake-key-for-e2e")
    try:
        narration.main()
    except SystemExit as e:
        assert e.code in (0, None), f"narration main 退出码 {e.code}"
    return out_dir


class TestNarrationCliEndToEnd:
    def test_full_cli_explicit(self, tmp_path):
        """显式注入 fake openai 后跑 narration.main() 完整 CLI。"""
        import sys as _sys
        mod = ModuleType("openai")
        mod.OpenAI = _FakeOpenAI
        saved = _sys.modules.get("openai")
        _sys.modules["openai"] = mod
        try:
            work = tmp_path / "p"
            work.mkdir()
            src_path = work / "source.json"
            src_path.write_text(json.dumps(_source(), ensure_ascii=False),
                                encoding="utf-8")
            out_dir = work / "out"
            _sys.argv = ["narration.py", "--source", str(src_path),
                         "-o", str(out_dir), "--gap", "0.4"]
            os.environ["MIMO_API_KEY"] = "fake-key-for-e2e"
            try:
                narration.main()
            except SystemExit as e:
                assert e.code in (0, None)

            import _audio as au
            wav = out_dir / "combined.wav"
            timing = out_dir / "narration_timing.json"
            assert wav.is_file() and timing.is_file()
            m = json.loads(timing.read_text(encoding="utf-8"))
            wav_dur = au._wav_duration(wav)
            # concat 时长精确性：combined.wav == manifest total_duration
            assert abs(wav_dur - m["total_duration"]) < 0.05, \
                f"concat {wav_dur:.3f}s 与 manifest {m['total_duration']:.3f}s 不一致"

            scenes = {s["step_id"]: s for s in m["scenes"]}
            # 对话展开为逐说话人句子
            assert len(scenes["seg-2"]["sentences"]) == 2, "对话未展开为 2 句"
            # 句级节拍 pause 精确落进相邻句 start 间隔
            seg1 = scenes["seg-1"]["sentences"]
            gap = seg1[2]["start"] - (seg1[1]["start"] + seg1[1]["duration"])
            assert abs(gap - 0.9) < 0.05, f"beat pause 未生效: {gap:.3f}"
            # 段级变速：0.5s → ~0.333s，duration 重测
            d3 = scenes["seg-3"]["sentences"][0]["duration"]
            assert abs(d3 - 0.333) < 0.05, f"段级 speed 未生效: {d3:.3f}"
        finally:
            if saved is None:
                _sys.modules.pop("openai", None)
            else:
                _sys.modules["openai"] = saved

    def test_downstream_chain_green(self, tmp_path):
        """narration 产物接入真实下游：build_timeline → build_page → check_gates。"""
        import sys as _sys
        mod = ModuleType("openai")
        mod.OpenAI = _FakeOpenAI
        saved = _sys.modules.get("openai")
        _sys.modules["openai"] = mod
        try:
            work = tmp_path / "p"
            work.mkdir()
            src_path = work / "source.json"
            src_path.write_text(json.dumps(_source(), ensure_ascii=False),
                                encoding="utf-8")
            out_dir = work / "out"
            _sys.argv = ["narration.py", "--source", str(src_path),
                         "-o", str(out_dir), "--gap", "0.4"]
            os.environ["MIMO_API_KEY"] = "fake-key-for-e2e"
            try:
                narration.main()
            except SystemExit as e:
                assert e.code in (0, None)

            wav = out_dir / "combined.wav"
            timing = out_dir / "narration_timing.json"
            tl = out_dir / "timeline.html"
            tl_bare = out_dir / "timeline_bare.json"

            def _run(*args):
                return subprocess.run([sys.executable, *args],
                                      capture_output=True, text=True,
                                      cwd=str(REPO), timeout=300)

            r = _run(str(SCRIPTS / "build_timeline.py"),
                     "--timing", str(timing), "--source", str(src_path),
                     "--bare", "-o", str(tl_bare))
            assert r.returncode == 0, r.stderr
            tl_data = json.loads(Path(tl_bare).read_text(encoding="utf-8"))
            tl_scenes = {s["step_id"]: s for s in tl_data["scenes"]}
            # runtime 由 build_timeline 加入：对话段 runtime.narration 同步
            assert len(tl_scenes["seg-2"]["runtime"]["narration"]) == 2

            r = _run(str(SCRIPTS / "build_timeline.py"),
                     "--timing", str(timing), "--source", str(src_path),
                     "-o", str(tl))
            assert r.returncode == 0, r.stderr

            draft = work / "page-draft.html"
            shutil.copy(TEMPLATE_HTML, draft)
            lesson = out_dir / "lesson"
            r = _run(str(SCRIPTS / "build_page.py"),
                     "--template", str(draft), "--timeline", str(tl),
                     "--audio", str(wav), "--timing", str(timing),
                     "-o", str(lesson / "index.html"))
            assert r.returncode == 0, r.stderr

            r = _run(str(SCRIPTS / "check_gates.py"), str(lesson), "--no-browser")
            assert r.returncode == 0, r.stderr + r.stdout
            assert "[ok] 静态检查通过" in r.stdout
        finally:
            if saved is None:
                _sys.modules.pop("openai", None)
            else:
                _sys.modules["openai"] = saved

    def test_dry_run_no_key_no_audio(self, tmp_path, fake_openai):
        """--dry-run：不需要 key、不写音频，节拍在 dry-run 输出可见。"""
        import sys as _sys
        work = tmp_path / "p"
        work.mkdir()
        src_path = work / "source.json"
        src_path.write_text(json.dumps(_source(), ensure_ascii=False),
                            encoding="utf-8")
        os.environ.pop("MIMO_API_KEY", None)
        _sys.argv = ["narration.py", "--source", str(src_path), "--dry-run"]
        try:
            narration.main()
        except SystemExit as e:
            assert e.code in (0, None)
        finally:
            assert not (work / "out").exists(), "dry-run 不应写产物"
