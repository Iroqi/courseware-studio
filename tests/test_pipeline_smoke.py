"""全链路冒烟：真实 CLI 跑通 时间轴 → 组装 → 检查 → 导出，并断言时长。

这是"亲自吃梨子"的自动化版：用真实的范本页面 + 合成音频 + 最小讲稿，
走完各脚本的 main() 入口（subprocess），任何一个契约断裂都会在这里失败。
浏览器冒烟与视频导出按环境能力跳过（无 chrome 时静态链路仍必须通过）。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, SCRIPTS, TEMPLATE_HTML

HAVE_CHROME = shutil.which("chromium") or shutil.which("google-chrome") \
    or shutil.which("chromium-browser") or shutil.which("chrome") \
    or shutil.which("microsoft-edge")
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_FFPROBE = shutil.which("ffprobe") is not None

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
