#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Courseware Studio 字幕导出（可选交付）。

把 `audio/narration_timing.json`（唯一时间来源）序列化成标准字幕文件：
SRT（通用播放器 / 剪辑软件）与 WebVTT（浏览器 / 在线平台）。字幕文本
仍来自逐句旁白原文，不在页面之外维护第二份文案——导出只是同一份时间轴
的另一种序列化。

用途：
- 无障碍：听障学习者跟随字幕阅读；
- 后期：把字幕带进剪辑 / 压制工具，与导出的 mp4 对齐；
- 多语言 / 复习：SRT/VTT 是交换格式，可再翻译或导入笔记软件。

用法：
    python scripts/export_subtitles.py <页面目录或 index.html>
    python scripts/export_subtitles.py <页面目录> --format srt --speaker --hl-mark
    python scripts/export_subtitles.py <页面目录> --out subtitles.vtt
    python scripts/export_subtitles.py --timing audio/narration_timing.json --format srt

时间偏移 --offset 以秒为单位（可负），用于与外部音视频对齐（如片头片尾）。
默认拒收 synth_failed 降级句（静音占位会"有字幕没声音"）；确要导出降级
成片的字幕才加 --allow-degraded。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _contracts import (TIMING_EPS, check_degraded_status,  # noqa: E402
                        require_finite_number, require_schema_version)
from _script_utils import (guard_not_in_skill_dir, read_text,  # noqa: E402
                           setup_stdio, write_text_atomic)
from build_page import _timeline_json  # noqa: E402  # 与页面同一份时间轴解析


def _timeline_to_manifest(timeline: dict) -> dict:
    """把页面内联时间轴（scenes[i].runtime.narration）归一成 manifest 形状
    （scenes[i].sentences），复用 _cues 的同一道校验与抽取。

    内联形状与 manifest 的字段名不同（runtime.narration vs sentences），
    但 text / start / duration / speaker / hl / synth_failed 语义一致——
    页面组装时 build_page 已核对过两者对齐（_validate_timing_alignment）。
    """
    scenes = []
    for sc in timeline.get("scenes") or []:
        runtime = sc.get("runtime") or {}
        narration = runtime.get("narration") or []
        sentences = []
        for s in narration:
            item = {"start": s.get("start"), "duration": s.get("duration"),
                    "text": s.get("text")}
            if s.get("speaker"):
                item["speaker"] = s["speaker"]
            if s.get("synth_failed"):
                item["synth_failed"] = True
            if s.get("hl"):
                item["hl"] = True
            sentences.append(item)
        scenes.append({"step_id": sc.get("step_id"), "sentences": sentences})
    return {"schema_version": timeline.get("schema_version"), "scenes": scenes}


def _fmt_srt(seconds: float) -> str:
    """SRT 时间戳：HH:MM:SS,mmm（毫秒三位，小时补零可超 99 小时）。"""
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt(seconds: float) -> str:
    """WebVTT 时间戳：HH:MM:SS.mmm。"""
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _cues(manifest: dict, *, speaker: bool, hl_mark: bool, offset: float,
          allow_degraded: bool) -> list[tuple[float, float, str]]:
    """从 manifest 抽逐句字幕条目，返回 [(start, end, text), …]。

    校验口径与 build_timeline / check_gates 一致（有限数值、句内不重叠、
    空句拒收、synth_failed 默认拒收），让"导出成功"意味着"时间轴本身是
    一份可交付的旁白表"。
    """
    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("[error] narration_timing.json 必须包含非空 scenes 数组")
    # manifest 级降级闸在库函数里也生效（不只 CLI）：import 路径不能把
    # degraded 成片无声导出。--allow-degraded 只放行，仍由句级闸逐句拦截
    # synth_failed（静音占位句"有字幕没声音"）。
    try:
        check_degraded_status(manifest, allow_degraded, "narration_timing.json")
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    cues: list[tuple[float, float, str]] = []
    for si, sc in enumerate(scenes, 1):
        if not isinstance(sc, dict):
            raise SystemExit(f"[error] scenes[{si}] 必须是对象")
        sid = sc.get("step_id")
        if not isinstance(sid, str) or not sid.strip():
            raise SystemExit(f"[error] scenes[{si}] 缺少 step_id")
        sentences = sc.get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise SystemExit(f"[error] 场景 {sid} 缺少非空 sentences")
        last_end = None
        for ni, sent in enumerate(sentences, 1):
            if not isinstance(sent, dict):
                raise SystemExit(f"[error] {sid}#{ni}: 旁白条目必须是对象")
            if sent.get("synth_failed") and not allow_degraded:
                raise SystemExit(
                    f"[error] {sid}#{ni} 是 synth_failed 静音占位句：导出会得到"
                    "「有字幕没声音」的成片。修复 TTS 失败后重跑 narration.py，"
                    "或显式使用 --allow-degraded。")
            raw_text = sent.get("text")
            if not isinstance(raw_text, str):
                raise SystemExit(f"[error] {sid}#{ni}: text 必须是字符串")
            text = raw_text.strip()
            if not text:
                raise SystemExit(f"[error] {sid}#{ni}: text 为空")
            try:
                s_start = float(require_finite_number(
                    sent.get("start"), f"{sid}#{ni}.start", nonnegative=True))
                s_dur = float(require_finite_number(
                    sent.get("duration"), f"{sid}#{ni}.duration", positive=True))
            except ValueError as exc:
                raise SystemExit(f"[error] {exc}")
            if last_end is not None and s_start < last_end - TIMING_EPS:
                raise SystemExit(
                    f"[error] {sid}#{ni}: 与上一句旁白重叠 {last_end - s_start:.3f}s")
            last_end = s_start + s_dur
            if speaker and sent.get("speaker"):
                text = f"{sent['speaker']}：{text}"
            if hl_mark and sent.get("hl"):
                text = f"◆ {text}"
            start = s_start + offset
            end = last_end + offset
            if start < 0:
                raise SystemExit(
                    f"[error] {sid}#{ni}: --offset 使字幕起点为负（{start:.3f}s）。"
                    "字幕时间不能小于 0，请减小偏移量。")
            cues.append((start, end, text))
    return cues


def subtitles_srt(manifest: dict, *, speaker: bool = False, hl_mark: bool = False,
                  offset: float = 0.0, allow_degraded: bool = False) -> str:
    """manifest → SRT 全文。序号从 1 起，条目间空行分隔。"""
    lines: list[str] = []
    for i, (start, end, text) in enumerate(_cues(
            manifest, speaker=speaker, hl_mark=hl_mark, offset=offset,
            allow_degraded=allow_degraded), 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt(start)} --> {_fmt_srt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def subtitles_vtt(manifest: dict, *, speaker: bool = False, hl_mark: bool = False,
                  offset: float = 0.0, allow_degraded: bool = False) -> str:
    """manifest → WebVTT 全文（带 WEBVTT 头）。"""
    lines = ["WEBVTT", ""]
    for start, end, text in _cues(
            manifest, speaker=speaker, hl_mark=hl_mark, offset=offset,
            allow_degraded=allow_degraded):
        lines.append(f"{_fmt_vtt(start)} --> {_fmt_vtt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _load_timing(path: Path):
    try:
        return json.loads(read_text(path))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[error] 无法读取时间轴：{path}: {exc}")


def main() -> int:
    setup_stdio()   # 必须在 parse_args 之前：Windows 管道下 help/usage 也是中文
    p = argparse.ArgumentParser(
        description="把 narration_timing.json 导出为标准字幕 SRT / WebVTT")
    p.add_argument("page", nargs="?", default=None,
                   help="页面目录（含 index.html）或页面 .html；默认读其 audio/narration_timing.json")
    p.add_argument("--timing", default=None,
                   help="直接指定 narration_timing.json 路径（覆盖 page 推断）")
    p.add_argument("--format", choices=("srt", "vtt", "both"), default="both",
                   help="导出格式（默认 both：同目录一份 .srt 一份 .vtt）")
    p.add_argument("--speaker", action="store_true",
                   help="句子带 speaker 时在字幕文本前加「说话人：」前缀")
    p.add_argument("--hl-mark", action="store_true",
                   help="结论句（hl）在字幕文本前加 ◆ 标记（页面字幕不带，仅供导出）")
    p.add_argument("--offset", type=float, default=0.0,
                   help="整体时间偏移（秒，可负），用于与外部音视频对齐")
    p.add_argument("--out", default=None,
                   help="输出路径；只能配合单一格式（--format srt 或 vtt）")
    p.add_argument("--from-page", action="store_true",
                   help="直接读页面内联时间轴（无需 audio/narration_timing.json；"
                        "与 --timing 互斥）")
    p.add_argument("--allow-degraded", action="store_true",
                   help="允许导出 synth_failed 静音占位句（不建议用于最终交付）")
    args = p.parse_args()

    if args.page is None and args.timing is None:
        p.error("需要 <页面目录或 index.html> 位置参数，或 --timing 指定时间轴文件")
    if args.timing is not None and args.from_page:
        p.error("--timing 与 --from-page 互斥，只给一个")
    if args.out and args.format == "both":
        p.error("--out 只能配合单一格式（--format srt 或 --format vtt）")

    if args.page is not None:
        page = Path(args.page).resolve()
        page_dir = page if page.is_dir() else page.parent
        if not page.exists():
            raise SystemExit(f"[error] 找不到页面：{page}")
        if args.timing is None and not args.from_page:
            args.timing = page_dir / "audio" / "narration_timing.json"

    if args.from_page:
        if args.page is None:
            p.error("--from-page 需要 <页面目录或 index.html> 位置参数")
        index = page_dir / "index.html" if page.is_dir() else page
        if not index.is_file():
            raise SystemExit(f"[error] 找不到页面：{index}")
        timeline = _timeline_json(read_text(index), index)
        # 页面内联时间轴没有 status 字段：check_degraded_status 会按
        # "外部 timing"处理并给出 [warn]——语义正确，不要强加 status（强加
        # 非 ok 的值会误触降级闸）。
        manifest = _timeline_to_manifest(timeline)
        label = f"页面内联时间轴 {index.name}"
    else:
        timing_path = Path(args.timing).resolve()
        if not timing_path.is_file():
            raise SystemExit(
                f"[error] 找不到时间轴：{timing_path}\n"
                "默认读取 <页面目录>/audio/narration_timing.json；"
                "也可用 --timing 直接指定，或用 --from-page 读页面内联时间轴。")
        manifest = _load_timing(timing_path)
        if not isinstance(manifest, dict):
            raise SystemExit("[error] narration_timing.json 顶层必须是 JSON 对象")
        label = f"narration_timing.json {timing_path.name}"
    try:
        require_schema_version(manifest, label)
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    try:
        warn = check_degraded_status(manifest, args.allow_degraded, label)
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    if warn:
        print(f"[warn] {warn}", file=sys.stderr)

    if args.out:
        out_path = Path(args.out).resolve()
        fmt = args.format
    else:
        base = page_dir if args.page is not None else timing_path.parent
        stem = base.name if base.name else "lesson"
        out_path = None
        fmt = args.format

    outputs: list[tuple[str, str]] = []
    if fmt in ("srt", "both"):
        if out_path is not None:
            out = out_path
        else:
            out = base / f"{stem}.srt"
        outputs.append(("srt", str(out)))
    if fmt in ("vtt", "both"):
        if out_path is not None:
            out = out_path
        else:
            out = base / f"{stem}.vtt"
        outputs.append(("vtt", str(out)))

    guard_not_in_skill_dir(
        *[(label, Path(path)) for label, path in outputs],
        tip="请把字幕导出到技能目录之外的项目目录。",
    )

    text_fn = {"srt": subtitles_srt, "vtt": subtitles_vtt}
    cues = _cues(manifest, speaker=args.speaker, hl_mark=args.hl_mark,
                 offset=args.offset, allow_degraded=args.allow_degraded)
    for fmt_name, path in outputs:
        body = text_fn[fmt_name](manifest, speaker=args.speaker,
                                 hl_mark=args.hl_mark, offset=args.offset,
                                 allow_degraded=args.allow_degraded)
        write_text_atomic(path, body)
        print(f"[out] {path}  ({len(body)} chars)")
    total = cues[-1][1] if cues else 0.0
    source_name = index.name if args.from_page else timing_path.name
    print(f"[note] {len(cues)} 条字幕，覆盖到 {total:.2f}s（来源 {source_name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
