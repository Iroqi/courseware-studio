#!/usr/bin/env python3
"""Courseware Studio — 无 TTS key 构建样例课件（开发 / 演示工具）。

把范本讲稿 + 范本页面跑成一份可以双击打开、能通过交付检查的成品课件，
全程不调 TTS、不需要 MiMo key、不碰网络：旁白音频用 ffmpeg 合成的占位音轨
（正弦波），只保证时长与时间轴一致，听感不真实——它的用途是"本地看成品结构"。

用法（任意目录均可，脚本用绝对路径调用）：

    python <仓库>/scripts/demo_lesson.py                    # 默认输出到系统临时目录
    python <仓库>/scripts/demo_lesson.py --out ./demo       # 输出到指定目录
    python <仓库>/scripts/demo_lesson.py --out ./demo --export   # 额外导出 mp4

流程（与 tests/test_pipeline_smoke.py 同一路径，全部走真实 CLI，不绕过任何契约）：

    1. 读 references/template-narration.json（范本讲稿，兼作 --source）；
    2. 按同一套分句逻辑切句，合成逐句时间轴与占位音轨；
    3. build_timeline.py → build_page.py（以 references/template.html 为页面骨架）；
    4. check_gates.py 交付检查（有 Chrome 时自动做浏览器冒烟，没有则静态通过）；
    5. --export 时 export_video.py 导出线性 mp4（需要 Chrome + ffmpeg）。

设计纪律：
    · 输出目录默认在系统临时目录；显式 --out 也不得落在本技能目录内
      （与各写盘入口共用 guard_not_in_skill_dir 同一道拦截）；
    · 不产生 narration.py 依赖（无 key、无网络）；占位音轨不冒充真实旁白。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import (guard_not_in_skill_dir, setup_stdio,  # noqa: E402
                           split_sentences)

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TEMPLATE_HTML = REPO / "references" / "template.html"
TEMPLATE_NARRATION = REPO / "references" / "template-narration.json"

# 占位音轨的合成节奏（秒）：每句 1.0s + 句间 0.4s + 场景间 0.5s。
# 这是"看结构"用的合成占位，不是真实朗读时长。
SENT_DUR = 1.0
GAP_SENTENCE = 0.4
GAP_SCENE = 0.5


def _run(args, cwd, timeout=600):
    r = subprocess.run([sys.executable, *args], capture_output=True,
                       text=True, cwd=str(cwd), timeout=timeout)
    return r


def build_timing(source):
    """范本讲稿 → 与范本页面场景对齐的 narration_timing.json 形状。"""
    scene_texts = [("opening", source.get("opening") or "开场。")]
    for seg in source.get("segments") or []:
        if isinstance(seg, dict) and (seg.get("text") or "").strip():
            scene_texts.append((seg["id"], seg["text"]))
    scene_texts.append(("closing", source.get("closing") or "小结。"))

    t = 0.0
    scenes = []
    for sid, text in scene_texts:
        sentences = []
        for s in split_sentences(text):
            sentences.append({"start": round(t, 3), "duration": SENT_DUR,
                              "text": s})
            t = round(t + SENT_DUR + GAP_SENTENCE, 3)
        if not sentences:
            continue
        start = sentences[0]["start"]
        end = round(sentences[-1]["start"] + SENT_DUR, 3)
        scenes.append({
            "step_id": sid,
            "title": source.get(f"{sid}_title")
            if sid in ("opening", "closing") else seg_title(source, sid),
            "tagline": "",
            "start": start,
            "duration": round(end - start, 3),
            "end": end,
            "sentences": sentences,
        })
        t = round(end + GAP_SCENE, 3)

    return {
        "schema_version": 1,
        "status": "ok",
        "title": source.get("title") or "样例课件",
        "voice_id": "冰糖",
        "audio": "combined.wav",
        "total_duration": round(scenes[-1]["end"], 3),
        "degraded": {"tts_silence_fallback_count": 0, "dropped_sentence_count": 0},
        "scenes": scenes,
    }


def seg_title(source, sid):
    for seg in source.get("segments") or []:
        if isinstance(seg, dict) and seg.get("id") == sid:
            return seg.get("title") or sid
    return sid


def synth_wav(path, duration):
    """ffmpeg 正弦波占位音轨：只保证时长，不冒充真实旁白。"""
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={duration}",
         "-ar", "24000", "-ac", "1", str(path)],
        capture_output=True, text=True)
    return r


def main():
    setup_stdio()
    p = argparse.ArgumentParser(
        description="无 TTS key 构建样例课件：范本讲稿 → 成品目录（可双击打开）")
    p.add_argument("--out", default=None,
                   help="输出目录（默认系统临时目录；不得落在本技能目录内）")
    p.add_argument("--export", action="store_true",
                   help="额外导出线性 mp4（需要本机 Chrome/Edge 与 ffmpeg）")
    args = p.parse_args()

    if shutil.which("ffmpeg") is None:
        raise SystemExit("[error] 需要 ffmpeg 在 PATH 上（用于合成占位音轨与导出）")

    out = Path(args.out).resolve() if args.out else \
        Path(tempfile.mkdtemp(prefix="courseware-demo-")).resolve()
    guard_not_in_skill_dir(("-o/--out", str(out)),
                           tip="请用 --out 指定本技能目录之外的目录。")

    source = json.loads(
        (TEMPLATE_NARRATION).read_text(encoding="utf-8"))
    timing = build_timing(source)

    proj = out / "proj"
    audio_dir = proj / "audio"
    audio_dir.mkdir(parents=True)
    (proj / "source.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8")
    timing_path = audio_dir / "narration_timing.json"
    timing_path.write_text(
        json.dumps(timing, ensure_ascii=False), encoding="utf-8")

    wav = audio_dir / "combined.wav"
    r = synth_wav(wav, timing["total_duration"])
    if r.returncode != 0:
        raise SystemExit(f"[error] ffmpeg 合成占位音轨失败：{r.stderr}")

    # 1) 时间轴
    r = _run([str(SCRIPTS / "build_timeline.py"),
              "--timing", str(timing_path),
              "--source", str(proj / "source.json"),
              "-o", str(proj / "timeline.html")], cwd=out)
    if r.returncode != 0:
        raise SystemExit(f"[error] build_timeline 失败：\n{r.stderr}")

    # 2) 组装（范本页面作骨架：build_page 只换时间轴 / 音频 / runtime）
    draft = proj / "page-draft.html"
    draft.write_text(TEMPLATE_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    page_dir = out / "lesson"
    r = _run([str(SCRIPTS / "build_page.py"),
              "--template", str(draft),
              "--timeline", str(proj / "timeline.html"),
              "--audio", str(wav),
              "--timing", str(timing_path),
              "-o", str(page_dir / "index.html")], cwd=out)
    if r.returncode != 0:
        raise SystemExit(f"[error] build_page 失败：\n{r.stderr}")

    # 3) 交付检查（有 Chrome 自动做浏览器冒烟，没有则静态通过）
    r = _run([str(SCRIPTS / "check_gates.py"), str(page_dir)], cwd=out)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    if r.returncode != 0:
        raise SystemExit(f"[error] check_gates 未通过（退出码 {r.returncode}）")

    # 4) 可选导出
    if args.export:
        r = _run([str(SCRIPTS / "export_video.py"), str(page_dir),
                  "-o", str(out / "lesson.mp4")], cwd=out, timeout=1800)
        print(r.stdout)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
        if r.returncode != 0:
            raise SystemExit(f"[error] export_video 失败（退出码 {r.returncode}）")

    print(f"[demo] 完成：{page_dir}")
    print(f"[demo] 打开 {page_dir / 'index.html'} 即可播放（占位音轨无真实朗读）。")


if __name__ == "__main__":
    main()
