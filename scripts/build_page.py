#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble a Courseware Studio page from the template and generated assets.

The page remains ordinary HTML: this helper only performs the repetitive,
high-risk wiring step. It embeds the generated timeline, copies the final
audio to ``audio/combined.wav`` next to ``index.html``, and copies the
interactive runtime beside the page.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import (guard_not_in_skill_dir, read_text,  # noqa: E402
                           setup_stdio, write_text_atomic)
from _contracts import (TIMING_EPS, check_degraded_status, inline_json,  # noqa: E402
                        require_schema_version)

# runtime 恒取 skill 自带的一份（runtime.md §6：复制到输出目录、references/ 不放
# 副本），页面只依赖统一的 data-locked 契约，不提供替换内核的入口。
RUNTIME_SRC = Path(__file__).resolve().parent / "interactive_runtime.js"


def _timeline_json(raw: str, path: Path) -> dict:
    match = re.search(
        r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>(.*?)</script>',
        raw,
        re.S | re.I,
    )
    payload = match.group(1) if match else raw
    try:
        data = json.loads(payload.strip())
    except json.JSONDecodeError as first_exc:
        # 仅当裸 JSON 解析失败（时间轴被 HTML 实体转义过）才 unescape 兜底。
        # 无条件 unescape 会把旁白里合法的 "&lt;" / "&amp;" 字面串反转义，
        # 与 manifest 对不上，拒收合法的配对。
        try:
            data = json.loads(html.unescape(payload).strip())
        except json.JSONDecodeError:
            raise SystemExit(f"[error] 时间轴不是合法 JSON：{path}: {first_exc}")
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list) or not data["scenes"]:
        raise SystemExit("[error] 时间轴必须是含非空 scenes 数组的对象")
    # schema_version 必须显式存在：与 _timing_manifest / build_timeline 同一道门。
    try:
        require_schema_version(data, f"时间轴 {path.name}")
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    return data


def _timing_manifest(raw: str, path: Path) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[error] narration_timing.json 不是合法 JSON：{path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit("[error] narration_timing.json 必须是含 schema_version 的 JSON 对象")
    try:
        require_schema_version(data, "narration_timing.json")
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("[error] narration_timing.json 必须包含非空 scenes 数组")
    for i, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise SystemExit(f"[error] narration_timing.json scenes[{i}] 必须是对象")
        sid = scene.get("step_id")
        if not isinstance(sid, str) or not sid.strip():
            raise SystemExit(f"[error] narration_timing.json scenes[{i}] 缺少 step_id")
        sentences = scene.get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise SystemExit(f"[error] narration_timing.json 场景 {sid} 缺少非空 sentences")
    return data


def _close_number(a, b) -> bool:
    # bool 守卫与 _contracts.require_finite_number / check_gates._finite_number
    # 同一口径：float(True)==1.0 会让 builder 组装过、checker 必红——口径分裂。
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    try:
        return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a) - float(b)) <= TIMING_EPS
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_timing_alignment(timeline: dict, manifest: dict) -> None:
    """Reject mixed artifacts whose inline timeline and manifest describe different audio."""
    timeline_scenes = timeline.get("scenes") or []
    manifest_scenes = manifest.get("scenes") or []
    if len(timeline_scenes) != len(manifest_scenes):
        raise SystemExit(
            f"[error] 时间轴与 narration_timing.json 场景数不一致："
            f"{len(timeline_scenes)} != {len(manifest_scenes)}"
        )
    for i, (timeline_scene, manifest_scene) in enumerate(zip(timeline_scenes, manifest_scenes), 1):
        if not isinstance(timeline_scene, dict):
            raise SystemExit(f"[error] 时间轴 scenes[{i}] 必须是对象")
        timeline_id = timeline_scene.get("step_id")
        manifest_id = manifest_scene.get("step_id")
        if timeline_id != manifest_id:
            raise SystemExit(
                f"[error] 第 {i} 个场景的 step_id 不一致："
                f"时间轴={timeline_id!r}，manifest={manifest_id!r}"
            )
        runtime = timeline_scene.get("runtime")
        if not isinstance(runtime, dict):
            raise SystemExit(f"[error] 时间轴场景 {timeline_id} 缺少 runtime")
        for field in ("start", "duration", "end"):
            if not _close_number(runtime.get(field), manifest_scene.get(field)):
                raise SystemExit(f"[error] 场景 {timeline_id} 的 {field} 与 manifest 不一致")
        timeline_sentences = runtime.get("narration")
        manifest_sentences = manifest_scene.get("sentences")
        if not isinstance(timeline_sentences, list) or not isinstance(manifest_sentences, list):
            raise SystemExit(f"[error] 场景 {timeline_id} 的 sentences/narration 必须是数组")
        if len(timeline_sentences) != len(manifest_sentences):
            raise SystemExit(f"[error] 场景 {timeline_id} 的句子数与 manifest 不一致")
        for j, (timeline_sentence, manifest_sentence) in enumerate(
            zip(timeline_sentences, manifest_sentences), 1
        ):
            if not isinstance(timeline_sentence, dict) or not isinstance(manifest_sentence, dict):
                raise SystemExit(f"[error] 场景 {timeline_id} 第 {j} 句必须是对象")
            # 逐字比较前把两侧空白归一：内部换行/连续空格在页面渲染时会被折叠，
            # 逐字相等会制造"排版不同而已"的假不一致。
            tl_text = " ".join(str(timeline_sentence.get("text") or "").split())
            mf_text = " ".join(str(manifest_sentence.get("text") or "").split())
            if tl_text != mf_text:
                raise SystemExit(f"[error] 场景 {timeline_id} 第 {j} 句文本与 manifest 不一致")
            for field in ("start", "duration"):
                if not _close_number(timeline_sentence.get(field), manifest_sentence.get(field)):
                    raise SystemExit(f"[error] 场景 {timeline_id} 第 {j} 句的 {field} 与 manifest 不一致")


def _embed_timeline(template: str, timeline: dict) -> str:
    pattern = re.compile(
        r'(<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>)(.*?)(</script>)',
        re.S | re.I,
    )
    if not pattern.search(template):
        raise SystemExit('[error] 模板缺少 <script id="lesson-timeline">')
    payload = inline_json(timeline)
    return pattern.sub(lambda m: m.group(1) + payload + m.group(3), template, count=1)


def _replace_audio_src(template: str) -> str:
    pattern = re.compile(r'(<audio\b[^>]*\bid=["\']main-audio["\'][^>]*)(>)', re.I)
    match = pattern.search(template)
    if not match:
        raise SystemExit('[error] 模板缺少 #main-audio')
    tag = match.group(1)
    # (?<![-\w])：'-' 也是 \b 边界，裸 \bsrc= 会误改 data-src=
    if re.search(r'(?<![-\w])src=["\'][^"\']*["\']', tag, re.I):
        tag = re.sub(r'(?<![-\w])src=["\'][^"\']*["\']', 'src="audio/combined.wav"', tag, count=1, flags=re.I)
    else:
        if tag.rstrip().endswith('/'):      # 自闭合 <audio …/>：先剥掉斜杠再挂 src
            tag = tag.rstrip()[:-1]
        tag += ' src="audio/combined.wav"'
    return template[:match.start(1)] + tag + match.group(2) + template[match.end(2):]


def _copy_atomic(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise SystemExit(f"[error] 找不到输入文件：{src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp", dir=dst.parent)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp_name)
        os.replace(tmp_name, dst)
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass


def main() -> int:
    setup_stdio()   # 必须在 parse_args 之前：Windows 管道下 help/usage 也是中文
    parser = argparse.ArgumentParser(
        description="从页面范本、内联时间轴和音频组装可交付的 Courseware Studio 页面"
    )
    parser.add_argument("--template", required=True,
                        help="本课写好的页面骨架（范本的工作副本）：只被换入时间轴与音频 src，"
                             "场景 / RENDER / GATES 原样保留")
    parser.add_argument("--timeline", required=True, help="build_timeline.py 生成的 HTML 或裸 JSON")
    parser.add_argument("--audio", required=True, help="最终旁白 WAV；会复制为 output/../audio/combined.wav")
    parser.add_argument("--timing", required=True, help="narration_timing.json；会复制到 output/../audio/")
    parser.add_argument("-o", "--output", required=True, help="输出 index.html 路径")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有 index.html/runtime/audio")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="允许 narration_timing.json 处于 degraded 状态（默认拒绝，与 build_timeline.py 同一道门）")
    args = parser.parse_args()

    template = Path(args.template).resolve()
    timeline_path = Path(args.timeline).resolve()
    audio = Path(args.audio).resolve()
    timing_manifest = Path(args.timing).resolve()
    output = Path(args.output).resolve()
    page_dir = output.parent
    audio_out = page_dir / "audio" / "combined.wav"
    timing_out = page_dir / "audio" / "narration_timing.json"
    runtime_out = page_dir / "interactive_runtime.js"

    guard_not_in_skill_dir(
        ("-o/--output", output),
        ("audio output", audio_out),
        ("timing output", timing_out),
        ("runtime output", runtime_out),
        tip="请把课件输出到技能目录之外的项目目录。",
    )
    for p, label in ((template, "模板"), (timeline_path, "时间轴"), (audio, "音频"),
                     (timing_manifest, "narration_timing.json"), (RUNTIME_SRC, "runtime")):
        if not p.is_file():
            raise SystemExit(f"[error] {label}不存在：{p}")
    if not args.force:
        existing = [p for p in (output, audio_out, timing_out, runtime_out) if p.exists()]
        if existing:
            raise SystemExit("[error] 输出已存在；如确认覆盖请加 --force：\n" + "\n".join(f"  · {p}" for p in existing))

    timeline = _timeline_json(read_text(timeline_path), timeline_path)
    manifest = _timing_manifest(read_text(timing_manifest), timing_manifest)
    # 组装是"静音占位页面"出厂前的最后一道关口：build_timeline 拒收的 degraded
    # manifest 若绕开它直接送到这里，页面会带着无声句子照常交付。
    try:
        warn = check_degraded_status(manifest, args.allow_degraded, "narration_timing.json")
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    if warn:
        print(f"[warn] {warn}", file=sys.stderr)
    _validate_timing_alignment(timeline, manifest)
    page = _replace_audio_src(_embed_timeline(read_text(template), timeline))
    # 资源先落盘、页面最后写：页面是 audio/runtime 的"总清单"，反过来写时
    # 任何一步复制失败都会留下一份引用缺失资源的坏页面。
    _copy_atomic(audio, audio_out)
    _copy_atomic(timing_manifest, timing_out)
    _copy_atomic(RUNTIME_SRC, runtime_out)
    write_text_atomic(str(output), page)
    print(f"[done] page     {output}")
    print(f"[done] audio    {audio_out}")
    print(f"[done] timing   {timing_out}")
    print(f"[done] runtime  {runtime_out}")
    print("[next] python scripts/check_gates.py " + str(page_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
