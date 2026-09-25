#!/usr/bin/env python3
"""Courseware Studio — 把实测时间轴压成页面能直接读的 `<script id="lesson-timeline">`。

职责只有一件：**把 narration.py 产出的时间轴改写成页面可直接内联的形状**，
避免手抄几十个数字出错（手抄是本流水线里最容易出错、也最难发现的一步）。

输入：
    --timing  audio/narration_timing.json   ← 唯一的时间来源（实测值）
    --source  narration-source.json         ← 可选：带出每段的 title / tagline

输出（-o，不给则打到 stdout）：
    <script type="application/json" id="lesson-timeline">{"scenes":[…]}</script>

形状：
    scenes[i] = {"step_id", "content":{"title","tagline"},
                 "runtime":{"start","duration","end",
                            "narration":[{start,duration,text,hl?,speaker?,synth_failed?}]}}

`narration[i].text` 是逐句口播原文，同时也是**画布字幕的唯一来源**——页面不留第二份文案，
所以字幕与旁白在结构上不可能对不上。`--source` 给的 `hl`（结论句序号，从 1 数起）只加一个
`hl:true`，让那一句在字幕带里用主色。

旁白时间是**全局秒**（不做场景内换算）——页面按全局时间切片，直接比 t >= start。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import setup_stdio, guard_not_in_skill_dir, write_text_atomic  # noqa: E402
from _contracts import (SCHEMA_VERSION, check_degraded_status, closing_title,  # noqa: E402
                        inline_json, opening_title, require_finite_number,
                        require_schema_version, segment_step_id)


def _load(path, what):
    try:
        # utf-8-sig：PowerShell `Set-Content -Encoding UTF8` 写出的带 BOM 文件
        # 用 utf-8 读会在第 1 列炸 json 解析。
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"[error] 无法读取{what}: {e}")


def _content_map(source):
    """从旁白脚本抽出 step_id → {title, tagline, hl}（章节头、目录、字幕强调色要用）。"""
    if source is None:
        return {}
    if not isinstance(source, dict):
        raise SystemExit("[error] --source 顶层必须是 JSON 对象，不能是数组、字符串或数字")
    if not source:
        return {}
    out = {}
    if source.get("opening"):
        out["opening"] = {"title": opening_title(source),
                          "tagline": source.get("opening_tagline") or ""}
    # 编号与兜底规则走 _contracts 的共享实现（segment_step_id / *_title），
    # 不再与 narration.py 各写一份。先滤掉空段落再数：讲稿里混着一个空段时，
    # 这里的 seg-N 若与音频那边错开一位，该段的 title / tagline / hl 会全部静默丢失。
    def _has_content(segment):
        if not isinstance(segment, dict):
            return False
        raw_text = segment.get("text")
        text_ok = isinstance(raw_text, str) and bool(raw_text.strip())
        dialogue = segment.get("dialogue")
        dialogue_ok = isinstance(dialogue, list) and bool(dialogue)
        return text_ok or dialogue_ok

    segs = [s for s in (source.get("segments") or []) if _has_content(s)]
    for i, seg in enumerate(segs, 1):
        sid = segment_step_id(seg, i)
        out[sid] = {"title": seg.get("title") or sid,
                    "tagline": seg.get("tagline") or "",
                    "hl": seg.get("hl")}
    if source.get("closing"):
        out["closing"] = {"title": closing_title(source),
                          "tagline": source.get("closing_tagline") or ""}
    return out


def _hl_indices(raw, sid, n):
    """`hl` = 这一段里**结论句的句序号，从 1 数起**（作者写"第 3 句是结论"最自然）。

    越界 / 非数字**只告警，不报错**：改稿后句序会变，而一个颜色标错不该挡住整条流水线——
    它只影响那一句字幕的强调色，与文案、时长、画面步数都无关。
    """
    out = set()
    if raw is None:
        return out
    if not isinstance(raw, list):
        print(f"[warn] {sid} 的 hl 必须是数组（收到 {raw!r}），已忽略", file=sys.stderr)
        return out
    for v in raw:
        # bool 是 int 子类（true→1）、float 会被 int() 静默截断（2.7→2）：
        # 两种都会把强调色标到错误的句子上且无声。
        try:
            if isinstance(v, bool) or not float(v).is_integer():
                raise ValueError
            i = int(v)
        except (TypeError, ValueError):
            print(f"[warn] {sid} 的 hl 里 {v!r} 不是整数句序号，已忽略", file=sys.stderr)
            continue
        if 1 <= i <= n:
            out.add(i)
        else:
            print(f"[warn] {sid} 的 hl 指向第 {i} 句，但这一段只有 {n} 句（已忽略）",
                  file=sys.stderr)
    return out


def build(timing, content):
    if not isinstance(timing, dict):
        raise SystemExit("[error] 时间轴顶层必须是对象")
    try:
        require_schema_version(timing, "时间轴")
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    raw = timing.get("scenes") or []
    if not isinstance(raw, list) or not raw:
        raise SystemExit("[error] 时间轴里没有任何场景——检查 narration.py 是否成功产出音频")

    scenes = []
    seen_ids = set()
    prev_start = None
    prev_end = None
    for index, sc in enumerate(raw, 1):
        if not isinstance(sc, dict):
            raise SystemExit(f"[error] scenes[{index}] 必须是对象")
        sid = str(sc.get("step_id") or sc.get("scene_id") or "").strip()
        if not sid:
            raise SystemExit(f"[error] scenes[{index}] 缺少 step_id")
        if sid in seen_ids:
            raise SystemExit(f"[error] 重复 step_id：{sid}")
        seen_ids.add(sid)

        sentences = sc.get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise SystemExit(f"[error] 场景 {sid} 没有句子")
        try:
            start = float(require_finite_number(sc.get("start"), f"{sid}.start", nonnegative=True))
            duration = float(require_finite_number(sc.get("duration"), f"{sid}.duration", positive=True))
            end = float(require_finite_number(sc.get("end"), f"{sid}.end", nonnegative=True))
        except ValueError as exc:
            raise SystemExit(f"[error] {exc}")
        if abs((start + duration) - end) > 0.12:
            raise SystemExit(f"[error] {sid}: start + duration 与 end 相差 {abs(start + duration - end):.3f}s")
        if prev_start is not None and start < prev_start - 0.02:
            raise SystemExit(f"[error] {sid}: 场景起点没有按时间递增")
        if prev_end is not None and start < prev_end - 0.02:
            raise SystemExit(f"[error] {sid}: 场景与上一场重叠 {prev_end - start:.3f}s")

        meta = content.get(sid) or {}
        hl = _hl_indices(meta.get("hl"), sid, len(sentences))
        narration = []
        last_sent_end = None
        for ni, sent in enumerate(sentences, 1):
            if not isinstance(sent, dict):
                raise SystemExit(f"[error] {sid}#{ni}: 旁白条目必须是对象")
            raw_text = sent.get("text")
            if not isinstance(raw_text, str):
                raise SystemExit(f"[error] {sid}#{ni}: text 必须是字符串")
            text = raw_text.strip()
            if not text:
                raise SystemExit(f"[error] {sid}#{ni}: text 为空")
            try:
                s_start = float(require_finite_number(sent.get("start"), f"{sid}#{ni}.start", nonnegative=True))
                s_dur = float(require_finite_number(sent.get("duration"), f"{sid}#{ni}.duration", positive=True))
            except ValueError as exc:
                raise SystemExit(f"[error] {exc}")
            s_end = s_start + s_dur
            if s_start < start - 0.12 or s_end > end + 0.12:
                raise SystemExit(f"[error] {sid}#{ni}: 旁白区间超出场景 [{start:.3f}, {end:.3f}]")
            if last_sent_end is not None and s_start < last_sent_end - 0.02:
                raise SystemExit(f"[error] {sid}#{ni}: 与上一句旁白重叠 {last_sent_end - s_start:.3f}s")
            last_sent_end = s_end
            item = {"start": round(s_start, 3), "duration": round(s_dur, 3), "text": text}
            if sent.get("speaker"):
                item["speaker"] = str(sent["speaker"])
            if sent.get("synth_failed"):
                item["synth_failed"] = True
            if ni in hl:
                item["hl"] = True
            narration.append(item)

        sentences_end = last_sent_end
        if sentences_end is not None and abs(sentences_end - end) > 0.25:
            raise SystemExit(f"[error] {sid}: 最后一句旁白结束点与 scene.end 相差 {abs(sentences_end-end):.3f}s")

        scenes.append({
            "step_id": sid,
            "content": {"title": meta.get("title") or sc.get("title") or sid,
                        "tagline": meta.get("tagline") or sc.get("tagline") or ""},
            "runtime": {
                "start": round(start, 3),
                "duration": round(duration, 3),
                "end": round(end, 3),
                "narration": narration,
            },
        })
        prev_start, prev_end = start, end

    return scenes


def report(scenes, total, stream=sys.stdout):
    """输出构建诊断；--bare 时走 stderr，保证 stdout 是纯 JSON。"""
    print(f"[scenes] {len(scenes)} 个场景，总时长 {total:.2f}s", file=stream)
    print(f"  {'id':<10} {'start':>8} {'end':>8} {'句数':>4}  标题", file=stream)
    for sc in scenes:
        r = sc["runtime"]
        print(f"  {sc['step_id']:<10} {r['start']:>8.3f} {r['end']:>8.3f} "
              f"{len(r['narration']):>4}  {sc['content']['title']}", file=stream)
    print("  ↑ 「句数」= 对应渲染器的分支数，逐句对一遍（对不上后半段画面会静止）", file=stream)
    marks = []
    for sc in scenes:
        k = sum(1 for s in sc["runtime"]["narration"] if s.get("hl"))
        if k:
            marks.append(f"{sc['step_id']}×{k}")
    print("  字幕强调句（hl）：" + ("、".join(marks) if marks else "无"), file=stream)


def main():
    p = argparse.ArgumentParser(description="把 narration_timing.json 压成可内联的时间轴脚本块")
    p.add_argument("--timing", required=True, help="narration.py 产出的 narration_timing.json")
    p.add_argument("--source", default=None, help="可选：旁白脚本，用来带出 title / tagline")
    p.add_argument("-o", "--output", default=None, help="输出文件（不给则打到 stdout）")
    p.add_argument("--bare", action="store_true",
                   help="只输出裸 JSON，不套 <script> 标签")
    p.add_argument("--allow-degraded", action="store_true",
                   help="允许 narration_timing.json 处于 degraded 状态（默认拒绝，避免降级痕迹在此断链）")
    args = p.parse_args()
    setup_stdio()   # Windows 重定向下 stdout 非 UTF-8：下面要打中文章节标题
    # 产物守卫：-o 是相对 CWD 解析的，从技能目录照抄示例命令会把时间轴块
    # 直接写进技能仓库（其余写盘入口都有同一道拦截）。
    if args.output:
        guard_not_in_skill_dir(("-o/--output", os.path.abspath(args.output)),
                               tip="请用 -o/--output 指定技能目录之外的绝对路径，"
                                   "或去掉 -o 直接把结果打到 stdout。")

    timing = _load(args.timing, "时间轴")
    # 降级契约要在这一环接住：synth_failed 占位句会随逐句数据带进页面（check_gates
    # 认得），但**整句被丢弃**的句子不留任何痕迹——不在这里拦，交付就查无此病。
    try:
        warn = check_degraded_status(timing, args.allow_degraded, "narration_timing.json")
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    if warn:
        print(f"[warn] {warn}", file=sys.stderr)
    source = _load(args.source, "旁白脚本") if args.source else None
    if source is None:
        print("[note] 未提供 --source：title/tagline 取时间轴自带，hl 强调一律缺失",
              file=sys.stderr)
    scenes = build(timing, _content_map(source))

    payload = inline_json({"schema_version": SCHEMA_VERSION, "scenes": scenes})
    text = payload if args.bare else \
        f'<script type="application/json" id="lesson-timeline">{payload}</script>\n'

    # total_duration 走与其它字段同一道守卫：外部/手写 timing JSON 里
    # "total_duration": "300" 这类字符串会在 float() 直接炸出裸 traceback。
    td = timing.get("total_duration")
    if td is None or str(td).strip() == "":
        td = scenes[-1]["runtime"]["end"]
    try:
        total = float(require_finite_number(td, "total_duration", nonnegative=True))
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}")
    # --bare 是机器接口：stdout 必须只有 JSON。诊断信息统一走 stderr。
    if args.bare:
        report(scenes, total, stream=sys.stderr)
        if args.output:
            write_text_atomic(args.output, text)   # 原子写：半截时间轴块比没有更坏
            print(f"[out] {args.output}  ({len(text)} chars)", file=sys.stderr)
        else:
            print(text)
        return

    report(scenes, total)
    if args.output:
        write_text_atomic(args.output, text)
        print(f"[out] {args.output}  ({len(text)} chars)")
    else:
        print(text)


if __name__ == "__main__":
    main()
