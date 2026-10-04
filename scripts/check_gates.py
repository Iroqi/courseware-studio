#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Courseware Studio 交付检查器。

它保留两类真正有价值的校验：

1. 静态检查：时间轴、逐句旁白、字幕挂点、场景边界、重复字幕风险。
2. 浏览器冒烟：真实 DOM 中逐句核对字幕，并自动走一遍门禁错误/正确路径，
   同时捕获 JS error / unhandled rejection。

它不是 SelfTest，也不维护第二套播放引擎。页面自己的 audio.currentTime / tick()
仍是唯一时间源；本脚本只观察页面并主动推进 currentTime。

用法（详见 references/runtime.md §8）：
    python scripts/check_gates.py <页面目录或 index.html>
    python scripts/check_gates.py <页面目录或 index.html> --no-browser
    python scripts/check_gates.py <页面目录或 index.html> --require-browser   # CI 严格模式
    python scripts/check_gates.py <页面目录或 index.html> --keep               # 保留探针副本排查
    # 默认拒收 synth_failed 降级句；确要交付降级成片才加 --allow-degraded
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _contracts import (SCHEMA_VERSION, SCENE_ALIGN_MAX, TAIL_DRIFT_MAX,  # noqa: E402
                        TIMING_EPS, require_finite_number)
from _script_utils import (normalize_meta_charset, read_text,  # noqa: E402
                           setup_stdio)


_CAP_LEN_MIN = 10
_CAP_SIM_MIN = 0.75
_CAP_PUNCT = set("，。！？、：；“”‘’「」『』·→—…（）() .,!?;:\"'…")


def _cap_norm(text: str) -> str:
    for c in _CAP_PUNCT:
        text = text.replace(c, "")
    return text.strip()


def _js_strip(src: str) -> str:
    """剥掉 JS 源码里的字符串/模板串/正则字面量/注释（内容替换为空格，保持偏移）。

    renderer 体提取与计时检查先过这一层，避免 var brace='{' 或注释里的
    括号把配对扫描带偏、也避免注释里的 setTimeout 误报。
    """
    out = list(src)
    n = len(src)
    i = 0
    prev_sig = ''  # 上一个有意义的非空白字符，用于区分除法与正则

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if src[k] != '\n':
                out[k] = ' '

    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in ('"', "'", '`'):
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == c:
                    j += 1
                    break
                j += 1
            blank(i + 1, j - 1)
            prev_sig = c
            i = j
        elif c == '/':
            # 正则字面量：仅当上一有意义字符不能构成表达式结尾时
            if prev_sig and prev_sig in '=([{,;:&|!?+-*%<>~^' or prev_sig == '':
                j = i + 1
                in_cls = False
                while j < n:
                    if src[j] == '\\':
                        j += 2
                        continue
                    if src[j] == '\n':
                        break
                    if src[j] == '[':
                        in_cls = True
                    elif src[j] == ']':
                        in_cls = False
                    elif src[j] == '/' and not in_cls:
                        break
                    j += 1
                if j < n and src[j] == '/' and not in_cls:
                    blank(i + 1, j)
                    prev_sig = '/'
                    i = j + 1
                    continue
            prev_sig = c
            i += 1
        else:
            if not c.isspace():
                prev_sig = c
            i += 1
    return ''.join(out)


_SCRIPT_OPEN_RE = re.compile(r'<script(?![\w-])', re.I)
_SCRIPT_CLOSE_RE = re.compile(r'</script', re.I)


def _html_scan(src: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """浏览器分词口径的线性扫描：返回 (HTML 注释区间, 真 <script> 体区间)。

    注释掉的契约代码（ghost GATES、假钩子）此前在 stripped/src 两个口径上都
    算实现：双向误判（好页面被假 scene 拒、空页面靠注释骗过"唯一防线"）。
    不用"注释与 script 正则区间重叠即丢弃"的启发式，也不用正则配对找 script
    体：残稿 `<!-- … <script> -->` 会让假开标签与真 `</script>` 错配，把真体
    整个跳过 strip。线性扫描按状态走：normal 态见 `<!--` 跳到闭合（注释里的
    `<script` 永远不开新状态；`<!-->` / `<!--!>` 突兀闭合与 `--!>` 结尾都按
    HTML5 认），真 `<script>` 的体只找下一个 `</script`（大小写不敏感）。
    """
    comments: list[tuple[int, int]] = []
    bodies: list[tuple[int, int]] = []
    n = len(src)
    i = 0
    while i < n:
        c = src.find('<!--', i)
        s = _SCRIPT_OPEN_RE.search(src, i)
        si = s.start() if s else n
        if c < 0 and si >= n:
            break
        if 0 <= c <= si:
            j0 = c + 4
            ends: list[int] = []
            if src.startswith('>', j0):
                ends.append(j0 + 1)          # <!--> 突兀闭合
            if src.startswith('!>', j0):
                ends.append(j0 + 2)          # <!--!> 突兀闭合
            a = src.find('-->', c + 3)
            if a >= 0:
                ends.append(a + 3)
            b = src.find('--!>', j0)
            if b >= 0:
                ends.append(b + 4)
            e = min(ends) if ends else n
            comments.append((c, e))
            i = e
            continue
        k = src.find('>', si + 7)
        if k < 0:
            break
        jc = _SCRIPT_CLOSE_RE.search(src, k + 1)
        if not jc:
            break                            # 未闭合的 script：余文全是 script data
        bodies.append((k + 1, jc.start()))
        k2 = src.find('>', jc.end())
        i = k2 + 1 if k2 >= 0 else n
    return comments, bodies


def _html_comment_ranges(src: str) -> list[tuple[int, int]]:
    return _html_scan(src)[0]


def _js_strip_html(src: str) -> str:
    """只对真 <script> 体跑 _js_strip，保持全局偏移；先空白化 script 外的 HTML 注释。

    整份 HTML 一把剥会被页面文本里的一个孤立引号（`<p>It's…`、HTML 注释、
    无引号属性）开一条幽灵字符串，把后面的代码全部清空：RENDER 映射变空 →
    静态检查对每个场景假报"缺少 renderer"。偏移不变，原文同区间切片不受影响。
    """
    out = list(src)
    comments, bodies = _html_scan(src)
    for a, b in comments:
        for k in range(a, b):
            if out[k] not in '\r\n':
                out[k] = ' '
    for a, b in bodies:
        if a >= b:
            continue
        stripped = _js_strip(src[a:b])
        out[a:b] = list(stripped)
    return ''.join(out)


def _match_brace(stripped: str, open_idx: int) -> int:
    """从 '{' 起做配对，返回右括号位置；找不到返回 -1（在剥离后的文本上跑）。"""
    depth = 0
    for k in range(open_idx, len(stripped)):
        ch = stripped[k]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return k
    return -1


def _cap_extract_bodies(src: str) -> dict[str, str]:
    """提取所有 renderer 形态的函数体：function NAME(...){...} 与
    NAME = (...)=>{...} / NAME = function(...){...}。返回 {名字: 体}，
    两种形态都按函数名 / 变量名登记（RENDER 映射表本身除外，它不是 renderer）。

    参数表允许一层嵌套括号（默认参数 `done = () => {}` 很常见），并认 async
    前缀——裸 `[^)]*` 遇默认参数里的 ')' 直接断配，好页面被假报"没找到实现"。
    """
    params = r'\((?:[^()]|\([^()]*\))*\)'
    stripped = _js_strip_html(src)
    bodies: dict[str, str] = {}
    for m in re.finditer(rf"(?:async\s+)?function\s+(\w+)\s*{params}\s*\{{", stripped):
        end = _match_brace(stripped, m.end() - 1)
        if end > 0:
            bodies[m.group(1)] = src[m.end() : end]
    for m in re.finditer(rf"(?:const|let|var)\s+(\w+)\s*=\s*(?:(?:async\s+)?function\s*\w*\s*{params}|(?:async\s*)?{params}\s*=>|(?:async\s*)?[\w$]+\s*=>)\s*\{{", stripped):
        if m.group(1).upper() == 'RENDER':
            continue
        end = _match_brace(stripped, m.end() - 1)
        if end > 0:
            bodies[m.group(1)] = src[m.end() : end]
    return bodies


_COLOR_LITERAL = re.compile(r"#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def _string_literals(call: str) -> list[str]:
    """扫出调用片段里的字符串字面量（单引号 / 双引号 / 反引号都算）。

    旧实现只认 ['\"]，`txt(attrs, \\`模板串\\`)` 整个漏检——正好是"画面文字复述
    旁白"最容易用 backtick 的场景。转义符按 \\x 两字符跳过；插值 ${…} 当正文看待，
    相似度检查宁可粗一点。
    """
    out: list[str] = []
    i, n = 0, len(call)
    while i < n:
        c = call[i]
        if c in ('"', "'", '`'):
            j = i + 1
            while j < n:
                if call[j] == '\\':
                    j += 2
                    continue
                if call[j] == c:
                    break
                j += 1
            out.append(call[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


def _cap_literals(body: str) -> list[str]:
    out: list[str] = []
    # \b 挡住 hotbadge( / plotTxt( 这类"名字里含 txt/badge"的他函数：
    # stage.md 口径是按**函数名**匹配自造 helper，子串命中会送去假"双字幕"比对。
    for m in re.finditer(r"(?<!function )\b(txt|badge)\s*\(", body):
        i = m.end() - 1
        depth = 0
        j = i
        while j < len(body):
            c = body[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    call = body[m.end() : j]
                    strs = _string_literals(call)
                    # 全部非颜色字面量都送去比相似度。旧实现只取**最后一个**：
                    # `txt(attrs, v)`（文案走变量）时最后字面量是 attrs 里的
                    # 字体串——真正画出来的字比不到，比了的又是无关噪声。
                    # 属性串混进比对无妨：判据是与旁白句的相似度，够不着阈值。
                    for s in strs:
                        if not _COLOR_LITERAL.fullmatch(s.strip()):
                            out.append(s)
                    break
            j += 1
    # Direct DOM/SVG text assignments are common in hand-authored renderers
    # (``node.textContent = '...'``). They are just as capable of creating a
    # second subtitle track as txt()/badge(), so include their literal RHS.
    for m in re.finditer(r"\b(?:textContent|innerHTML)\s*=\s*(['\"`])", body):
        quote = m.group(1)
        j = m.end()
        while j < len(body):
            if body[j] == "\\":
                j += 2
                continue
            if body[j] == quote:
                out.append(body[m.end():j])
                break
            j += 1
    for m in re.finditer(r"\b(?:createTextNode)\s*\(", body):
        j = m.end()
        while j < len(body) and body[j].isspace():
            j += 1
        if j < len(body) and body[j] in "'\"`":
            quote = body[j]
            k = j + 1
            while k < len(body):
                if body[k] == "\\":
                    k += 2
                    continue
                if body[k] == quote:
                    out.append(body[j + 1:k])
                    break
                k += 1
    return [x.strip() for x in out if x.strip()]


def _stage_static_literals(src: str) -> list[str]:
    """Return literal SVG <text> content outside the caption hook.

    This is deliberately a warning heuristic, not a DOM parser. It catches
    static duplicate captions while leaving layout/semantic text decisions to
    the author.
    """
    out: list[str] = []
    for m in re.finditer(r'<text\b[^>]*>(.*?)</text>', src, re.S | re.I):
        if re.search(r'\bid=["\']cap-text["\']', m.group(0), re.I):
            continue
        value = html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if value:
            out.append(value)
    return out


def _timeline_from_html(src: str) -> tuple[dict[str, Any] | None, str | None]:
    # 跳过落在 HTML 注释里的残稿；多个真实匹配取第一个"解析成功"的，
    # 而不是第一个正则命中（注释掉的旧时间轴会截走口径）。
    comments = _html_comment_ranges(src)
    matches = [m for m in re.finditer(
        r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>([\s\S]*?)</script>',
        src, re.I)
        if not any(a <= m.start() < b for a, b in comments)]
    if not matches:
        return None, '找不到 #lesson-timeline'
    first_err: str | None = None
    for m in matches:
        # 先按裸 JSON 解析，失败才 html.unescape 兜底：旁白里合法的 "&lt;" /
        # "&amp;" 字面串不该被无条件反转义（与 build_page._timeline_json 同一口径）。
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(html.unescape(raw))
            except json.JSONDecodeError as exc:
                if first_err is None:
                    first_err = f'#lesson-timeline 不是合法 JSON：{exc}'
                continue
        if not isinstance(data, dict):
            if first_err is None:
                first_err = '#lesson-timeline 顶层必须是对象'
            continue
        # 缺 key 不算 v1：与 build_page._timeline_json 同一口径（那边也拒收缺
        # schema_version 的时间轴），宽松一侧会造出"过检页面炸组装"的口径分裂。
        # bool 是 int 子类：JSON true 会因 True == 1 混过版本门。
        version = data.get('schema_version')
        if isinstance(version, bool) or version != SCHEMA_VERSION:
            if first_err is None:
                first_err = f'#lesson-timeline 必须带显式 schema_version: {SCHEMA_VERSION}（请用 build_timeline.py 重新生成）'
            continue
        if not isinstance(data.get('scenes'), list) or not data['scenes']:
            if first_err is None:
                first_err = '#lesson-timeline.scenes 必须是非空数组'
            continue
        return data, None
    return None, first_err


def _finite_number(value: Any) -> bool:
    # 复用 _contracts 的"唯一实现"；超大整数 float() 会抛 OverflowError，
    # 在这里必须按"非有限"处理而不是掀掉整轮检查。
    try:
        require_finite_number(value, 'value')
        return True
    except ValueError:
        return False


def _timing_manifest_errors(page_dir: Path, timeline: dict[str, Any]) -> list[str]:
    """Validate the on-disk timing manifest against the page's inline timeline."""
    path = page_dir / 'audio' / 'narration_timing.json'
    if not path.is_file():
        return [f'缺少 narration_timing.json：{path.as_posix()}']
    try:
        # 与 build_page 共用 _script_utils.read_text 的探测链：UTF-16/GBK 的
        # manifest 能过组装却在这里被判"不是合法 JSON"= 口径分裂的假失败。
        data = json.loads(read_text(path))
    except (SystemExit, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f'narration_timing.json 无法读取或不是合法 JSON：{exc}']
    errors: list[str] = []
    # bool 是 int 子类：JSON true 会因 True == 1 混过版本门。与
    # build_page._timing_manifest / _timeline_from_html 同一口径。
    version = data.get('schema_version') if isinstance(data, dict) else None
    if not isinstance(data, dict) or isinstance(version, bool) or version != SCHEMA_VERSION:
        return [f'narration_timing.json 必须是 schema_version={SCHEMA_VERSION} 的对象']
    manifest_scenes = data.get('scenes')
    timeline_scenes = timeline.get('scenes')
    if not isinstance(manifest_scenes, list) or not manifest_scenes:
        return ['narration_timing.json 必须包含非空 scenes 数组']
    if not isinstance(timeline_scenes, list) or len(manifest_scenes) != len(timeline_scenes):
        return [f'narration_timing.json 与内联时间轴场景数不一致：'
                f'{len(manifest_scenes)} != {len(timeline_scenes or [])}']
    for i, (manifest_scene, timeline_scene) in enumerate(zip(manifest_scenes, timeline_scenes), 1):
        if not isinstance(manifest_scene, dict) or not isinstance(timeline_scene, dict):
            errors.append(f'第 {i} 个场景在 manifest/时间轴中必须是对象')
            continue
        msid = manifest_scene.get('step_id')
        tsid = timeline_scene.get('step_id')
        if msid != tsid:
            errors.append(f'第 {i} 个场景 step_id 不一致：manifest={msid!r}，时间轴={tsid!r}')
            continue
        runtime = timeline_scene.get('runtime')
        if not isinstance(runtime, dict):
            errors.append(f'{tsid}: 时间轴缺少 runtime')
            continue
        for field in ('start', 'duration', 'end'):
            mv, tv = manifest_scene.get(field), runtime.get(field)
            if not (_finite_number(mv) and _finite_number(tv)) or abs(float(mv) - float(tv)) > TIMING_EPS:
                errors.append(f'{tsid}: {field} 与 manifest 不一致')
        mn = manifest_scene.get('sentences')
        tn = runtime.get('narration')
        if not isinstance(mn, list) or not isinstance(tn, list) or len(mn) != len(tn):
            errors.append(f'{tsid}: manifest 与时间轴句子数不一致')
            continue
        for j, (ms, ts) in enumerate(zip(mn, tn), 1):
            if not isinstance(ms, dict) or not isinstance(ts, dict):
                errors.append(f'{tsid}#{j}: manifest/时间轴句子必须是对象')
                continue
            # 与 build_page.py 同口径：逐字比较前把两侧空白归一，内部换行/连续
            # 空格在页面渲染时会被折叠，否则"排版不同"会在装配通过后又误报不一致。
            mtext = " ".join(str(ms.get('text') or '').split())
            ttext = " ".join(str(ts.get('text') or '').split())
            if mtext != ttext:
                errors.append(f'{tsid}#{j}: 文本与 manifest 不一致')
            for field in ('start', 'duration'):
                mv, tv = ms.get(field), ts.get(field)
                if not (_finite_number(mv) and _finite_number(tv)) or abs(float(mv) - float(tv)) > TIMING_EPS:
                    errors.append(f'{tsid}#{j}: {field} 与 manifest 不一致')
    return errors


def _render_map(src: str) -> tuple[dict[str, str], dict[str, str]]:
    """读取模板约定的 RENDER = {sceneId: renderer} 映射。

    在剥离字符串/注释后的文本上做括号配对，返回两样东西：
    - mapping：{sceneId: 具名 renderer 函数名}（匿名内联 renderer 不在其中）
    - inline_bodies：{sceneId#inline: 匿名内联 renderer 的函数体}
    匿名 `key: function(step){…}` / `key: (step)=>{…}` 同样能被拿到，
    不再只认具名 render\\w+。
    """
    stripped = _js_strip_html(src)
    m = re.search(r"\bRENDER\s*=\s*\{", stripped)
    if not m:
        return {}, {}
    brace = stripped.index('{', m.start())
    end = _match_brace(stripped, brace)
    if end < 0:
        return {}, {}
    lit_s = stripped[brace:end + 1]
    lit_r = src[brace:end + 1]
    mapping: dict[str, str] = {}
    inline_bodies: dict[str, str] = {}
    # 注意：剥离后引号内容变空格（键文案丢失但偏移保持），quoted 键要从原文同区间取。
    # 手动推进扫描位置：抓到一段内联体后直接跳过它，渲染函数体内部的
    # 对象字面量（`{n: function(){}}` 之类）才不会被误当成下一层 RENDER 键。
    vm_re = re.compile(r"(?:(['\"])([^'\"]*)\1|([A-Za-z_$][\w$]*))\s*:\s*")
    pos = 1
    while pos < len(lit_s):
        vm = vm_re.search(lit_s, pos)
        if not vm:
            break
        if vm.group(1):
            sid = lit_r[vm.start(2):vm.end(2)]
        else:
            sid = vm.group(3)
        rest = lit_s[vm.end():]
        _p = r'\((?:[^()]|\([^()]*\))*\)'   # 与 _cap_extract_bodies 同口径：一层嵌套 + async
        fm = re.match(rf"(?:(?:async\s+)?function\s*\w*\s*{_p}|(?:async\s*)?{_p}\s*=>|(?:async\s*)?[\w$]+\s*=>)\s*\{{", rest)
        if fm:
            close = _match_brace(lit_s, vm.end() + fm.end() - 1)
            if close > 0:
                inline_bodies[f"{sid}#inline"] = lit_r[vm.end() + fm.end(): close]
                pos = close + 1
            else:
                pos = vm.end()
            continue
        nm = re.match(r"[\w$]+(?:\.[\w$]+)*", rest)
        if nm:
            mapping[sid] = nm.group(0)
            pos = vm.end() + nm.end()
        else:
            pos = vm.end()
    return mapping, inline_bodies


# _gate_scene_counts 的哨兵桶：`scene:` 值为标识符/模板串等静态读不出的表达式时，
# 只计入门禁总数，不参与"同 scene 重复"与"scene 存在性"核对。
EXPR_SCENE_KEY = '<expr>'


def _gate_scene_counts(src: str, stripped: str) -> dict[str, int]:
    """在剥离字符串/注释后的文本上定位 `GATES = [...]` 并做括号配对，
    场景 id 再从原文同偏移读出。

    旧实现直接在原文上跑非贪婪 `\\[...\\];`：注释掉的 gate、题面里的 `[1];`
    都会污染或截断扫描。注释/字符串在 stripped 上是空格，括号配对因此只
    认真实代码结构。
    """
    m = re.search(r"(?:var|let|const)\s+GATES\s*=\s*\[", stripped)
    if not m:
        return {}
    depth = 0
    end = -1
    for k in range(m.end() - 1, len(stripped)):
        ch = stripped[k]
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = k
                break
    if end < 0:
        return {}
    counts: dict[str, int] = {}
    for km in re.finditer(r"\bscene\s*:\s*(['\"])", stripped[m.end():end]):
        quote = km.group(1)
        v0 = m.end() + km.end()
        v1 = src.find(quote, v0, end)
        if v1 < 0:
            continue
        sid = src[v0:v1]
        counts[sid] = counts.get(sid, 0) + 1
    # scene: 后面不是引号（标识符 / 模板串 / 三元）：值静态读不出。旧实现直接
    # 不计数——混合写法会静默关掉"同 scene 重复"与 ">3 道"检查，还因 counts 非空
    # 骗过调用方的"GATES 无法解析"兜底 warning。这里计入哨兵桶保总数口径。
    expr_n = len(re.findall(r"\bscene\s*:\s*(?!['\"])[\w$`]", stripped[m.end():end]))
    if expr_n:
        counts[EXPR_SCENE_KEY] = counts.get(EXPR_SCENE_KEY, 0) + expr_n
    return counts


# runtime 引用 tag 的唯一正则（static_check 与 _build_probe 共用，防止两处口径漂移）：
# `>\s*</script\s*>`——静态严格贴着的 `></script>` 会漏掉换行闭合的真引用，
# 假报"缺少 runtime 引用"。只在 _js_strip_html 产物上匹配：注释残稿里的同款 tag
# 排得比真引用靠前时，裸 src 会先撞上死标签——文件不存在也假报"缺少引用"，存在就假通过。
RUNTIME_REF_RE = re.compile(
    r'<script\b[^>]*src=["\']([^"\']*interactive_runtime\.js(?:\?[^"\']*)?)["\'][^>]*>\s*</script\s*>',
    re.I,
)


def static_check(src: str, *, allow_degraded: bool,
                 page_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"scenes": 0, "sentences": 0}
    stripped_html = _js_strip_html(src)

    timeline, timeline_err = _timeline_from_html(src)
    if timeline_err:
        errors.append(timeline_err)
        return {"errors": errors, "warnings": warnings, "stats": stats}

    scenes = timeline["scenes"]
    stats["scenes"] = len(scenes)
    # 契约存在性检查跑在 stripped_html 上：HTML 注释（本轮起被空白化）里的
    # 假钩子 / 残稿 audio 标签不再算实现，也不再被误当成正品解析。
    # 一次搜索两用：既判 #main-audio 在不在，也拿整段标签给下面解析 src。
    audio_tag = re.search(r'<audio\b[^>]*\bid=["\']main-audio["\'][^>]*>', stripped_html, re.I)
    if not audio_tag:
        errors.append('找不到 #main-audio')
    if not re.search(r'(?:id=["\']cap-text["\'][^>]*data-courseware-caption(?![\w-])|data-courseware-caption(?![\w-])[^>]*id=["\']cap-text["\'])', stripped_html, re.I):
        errors.append('找不到带 data-courseware-caption 的 #cap-text')
    # #stage 在 runtime.md §7 的钩子表里：探针要写它的 data-state、导出把它当
    # 硬前提——静态侧此前只靠"存在时顺手用"，缺了会静默通过，这里补上强制。
    # (?<![-\w]) 与 export_video 的 #stage 守卫同口径：裸 \b 在 '-' 与 'i' 之间
    # 也成立，data-id="stage" 这类假钩子会骗过检查。
    if not re.search(r'(?<![-\w])id=["\']stage["\']', stripped_html, re.I):
        errors.append('找不到 #stage（QA 与导出定位舞台的钩子，runtime.md §7）')
    # 形状匹配而不是裸子串：注释 / 字符串里提一句钩子名不能算实现了钩子
    # （--no-browser 模式下这是唯一防线；浏览器模式另有逐句断言兜底）。
    if not re.search(r'window\.__coursewareRenderTrace\s*(?:=|\.push\(|\.length)', stripped_html):
        errors.append('页面缺少 __coursewareRenderTrace：无法验证每个句子是否真正驱动了 renderer')
    if not re.search(r'window\.__coursewareResetRenderTrace\s*=\s*(?:function|\(|(?!(?:null|undefined|true|false)\b)[\w$])', stripped_html):
        errors.append('页面缺少 __coursewareResetRenderTrace：无法安全重置逐句 renderer QA 状态')
    if audio_tag:
        # src 允许写在 <audio> 属性上或内嵌 <source> 子标签上，两种形态归一处理。
        ref: str | None = None
        # (?<![-\w])：'-' 也是 \b 的边界，裸 \bsrc= 会先撞上 data-src= 拿错值
        m_src = re.search(r'(?<![-\w])src=["\']([^"\']*)["\']', audio_tag.group(0), re.I)
        if m_src:
            ref = m_src.group(1)
        else:
            close_m = re.compile(r'</audio\s*>', re.I).search(stripped_html, audio_tag.end())
            inner = stripped_html[audio_tag.end(): close_m.start() if close_m else audio_tag.end()]
            m_s = re.search(r'<source\b[^>]*?(?<![-\w])src=["\']([^"\']+)["\']', inner, re.I)
            if m_s:
                ref = m_s.group(1)
        if ref is None:
            errors.append('#main-audio 缺少 src（src 属性与 <source> 子标签都没有）')
        else:
            norm = ref.replace('\\', '/')
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', norm) or norm.startswith('data:'):
                errors.append('主音频必须是本地文件引用，不要引用外部 URL 或 data: URI')
            else:
                path_part = norm.split('?', 1)[0].split('#', 1)[0]
                norm_path = path_part if path_part.startswith('/') else posixpath.normpath(path_part)
                if norm_path.startswith('/') or norm_path.startswith('../'):
                    errors.append('主音频必须相对页面目录引用 audio/combined.wav；不接受绝对路径或越出页面目录')
                elif norm_path != 'audio/combined.wav':
                    errors.append('最终音频必须精确引用 audio/combined.wav（可带 ./ 前缀）；不要引用其它文件或同名文件')
                elif not (page_dir / 'audio' / 'combined.wav').is_file():
                    errors.append(f'主音频文件不存在：{(page_dir / "audio" / "combined.wav").as_posix()}')
                else:
                    audio_dir = page_dir / 'audio'
                    allowed = {'combined.wav', 'narration_timing.json'}
                    extras = sorted(
                        p.name for p in audio_dir.iterdir()
                        if p.name not in allowed
                    ) if audio_dir.is_dir() else []
                    if extras:
                        errors.append('audio/ 目录含交付残留（只允许 combined.wav 与 narration_timing.json）：'
                                      + '、'.join(extras))

    errors.extend(_timing_manifest_errors(page_dir, timeline))
    runtime_ref = RUNTIME_REF_RE.search(stripped_html)
    if not runtime_ref:
        errors.append('页面缺少 interactive_runtime.js 引用')
    else:
        ref = runtime_ref.group(1).split('?', 1)[0]
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', ref) or ref.startswith('//'):
            errors.append('interactive_runtime.js 必须是页面目录内的本地相对文件')
        elif not (page_dir / ref).resolve().is_file():
            errors.append(f'页面引用的 runtime 不存在：{(page_dir / ref).resolve()}')

    seen_ids: set[str] = set()
    prev_start: float | None = None
    prev_end: float | None = None
    all_sentences: list[tuple[str, int, dict[str, Any]]] = []

    for si, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f'场景 {si + 1} 不是对象')
            continue
        sid = str(scene.get('step_id', '')).strip()
        runtime = scene.get('runtime')
        if not sid:
            errors.append(f'场景 {si + 1} 缺少 step_id')
        elif sid in seen_ids:
            errors.append(f'重复 step_id：{sid}')
        seen_ids.add(sid)
        if not isinstance(runtime, dict):
            # 坏输入要进错误列表，不能在这里 .get 裸抛 AttributeError 掀掉整轮检查
            errors.append(f'{sid or si + 1}: runtime 必须是对象')
            continue

        start, duration, end = runtime.get('start'), runtime.get('duration'), runtime.get('end')
        if not all(_finite_number(v) for v in (start, duration, end)):
            errors.append(f'{sid or si + 1}: runtime.start/duration/end 必须是有限数字')
            continue
        start, duration, end = float(start), float(duration), float(end)
        if start < 0 or end < 0:
            errors.append(f'{sid}: runtime.start/end 必须是非负数字')
        if duration <= 0:
            errors.append(f'{sid}: runtime.duration 必须 > 0')
        if end < start:
            errors.append(f'{sid}: runtime.end 小于 start')
        if abs((start + duration) - end) > SCENE_ALIGN_MAX:
            warnings.append(f'{sid}: start + duration 与 end 相差 {abs(start + duration - end):.3f}s')
        if prev_start is not None and start < prev_start - TIMING_EPS:
            # TIMING_EPS 与 build_timeline.build 共用一把尺（_contracts）：裸比较
            # 会拦下 builder 亲手放行的浮点抖动，造出"过组装必红检查"。
            errors.append(f'{sid}: 场景起点没有按时间递增')
        if prev_end is not None and start < prev_end - TIMING_EPS:
            errors.append(f'{sid}: 场景与上一场重叠 {prev_end - start:.3f}s')
        prev_start, prev_end = start, end

        narration = runtime.get('narration') or []
        if not isinstance(narration, list) or not narration:
            errors.append(f'{sid}: 缺少 narration')
            continue
        last_sent_end: float | None = None
        for ni, sentence in enumerate(narration):
            if not isinstance(sentence, dict):
                errors.append(f'{sid}#{ni}: 旁白条目不是对象')
                continue
            raw_text = sentence.get('text')
            if raw_text is not None and not isinstance(raw_text, str):
                errors.append(f'{sid}#{ni}: text 必须是字符串')
                text = ''
            else:
                text = (raw_text or '').strip()
            s_start, s_dur = sentence.get('start'), sentence.get('duration')
            if not text:
                errors.append(f'{sid}#{ni}: text 为空')
            if not (_finite_number(s_start) and _finite_number(s_dur)):
                errors.append(f'{sid}#{ni}: start/duration 必须是有限数字')
                continue
            s_start, s_dur = float(s_start), float(s_dur)
            s_end = s_start + s_dur
            if s_start < 0:
                errors.append(f'{sid}#{ni}: start 必须是非负数字')
            if s_dur <= 0:
                errors.append(f'{sid}#{ni}: duration 必须 > 0')
            if s_start < start - SCENE_ALIGN_MAX or s_end > end + SCENE_ALIGN_MAX:
                errors.append(f'{sid}#{ni}: 旁白区间超出场景 [{start:.3f}, {end:.3f}]')
            if last_sent_end is not None and s_start < last_sent_end - TIMING_EPS:
                errors.append(f'{sid}#{ni}: 与上一句旁白重叠 {last_sent_end - s_start:.3f}s')
            last_sent_end = s_end
            stats["sentences"] += 1
            all_sentences.append((sid, ni, {**sentence, "start": s_start, "duration": s_dur, "end": s_end}))

        # 空 narration 在上面的守卫处已 continue；末句可能正是那里 continue 掉的
        # 坏条目：这里再 float() 就是二次崩溃，先验后用。
        last = narration[-1]
        if (isinstance(last, dict) and _finite_number(last.get('start'))
                and _finite_number(last.get('duration'))
                and abs((float(last['start']) + float(last['duration'])) - end) > TAIL_DRIFT_MAX):
            warnings.append(f'{sid}: 最后一句旁白结束点与 scene.end 相差较大')

    # 降级痕迹认"解析后的时间轴句子"，不扫整页源码：页面 JS/注释里出现同名字符串
    # 不该把一页好课件报成降级。
    if not allow_degraded and any(bool(s.get('synth_failed')) for _, _, s in all_sentences):
        errors.append('时间轴包含 synth_failed=true；默认不接受降级成片，修复 TTS 失败或显式使用 --allow-degraded')

    # 每个 scene 必须有 renderer；缺失时页面会出现“音频/字幕继续、画面静止”的真失败。
    # 非 dict 场景在第一轮已记"场景不是对象"，这里跳过（.get 二次崩溃会把整轮检查掀掉）。
    valid_scenes = [s for s in scenes if isinstance(s, dict)]
    render_map, inline_bodies = _render_map(src)
    bodies = _cap_extract_bodies(src)
    static_literals = _stage_static_literals(src)
    for scene in valid_scenes:
        sid = str(scene.get('step_id', '')).strip()
        fn = render_map.get(sid)
        if not fn and f"{sid}#inline" not in inline_bodies:
            errors.append(f'{sid}: RENDER 中缺少对应 renderer')
        elif fn and fn not in bodies:
            if '.' in fn:
                errors.append(f'{sid}: RENDER 的值必须是裸函数名或内联函数，不支持成员引用（{fn}）')
            else:
                errors.append(f'{sid}: renderer {fn} 没有找到 function 实现')

    # renderer 计时纪律：句序号是唯一视觉时钟（stage.md 纪律 1）。
    # 检查对象 = RENDER 引用的具名函数 + 内联匿名体；在剥离字符串/注释后的文本上匹配，
    # 不再全页面扫描（避免把 preGateOpen 这类页面函数误判成 renderer）。
    for label in sorted(set(render_map.values()) | set(inline_bodies)):
        body = bodies.get(label) or inline_bodies.get(label, '')
        if not body:
            continue
        bs = _js_strip(body)
        sched = sorted(set(re.findall(r'\b(setTimeout|setInterval)\s*\(', bs)))
        if sched:
            errors.append(f'renderer {label} 用 {" / ".join(sched)} 排程后续视觉状态')
        if re.search(r'\brequestAnimationFrame\s*\(', bs):
            warnings.append(f'renderer {label} 使用 requestAnimationFrame：声明式动效（stage.md §3.4）'
                            f'已覆盖这类补间，改回写目标状态；确要保留则人工确认它不推进句序号')

    # 一个 scene 最多挂一个 gate；页面自己的门禁控制器是单槽位，不允许静默覆盖。
    gate_counts = _gate_scene_counts(src, stripped_html)
    scene_set = {str(scene.get('step_id', '')).strip() for scene in valid_scenes}
    for sid, count in gate_counts.items():
        if sid == EXPR_SCENE_KEY:
            continue   # 值静态读不出：只进总数，不配做重复/存在性断言
        if count > 1:
            errors.append(f'{sid}: 同一 scene 配了 {count} 个 gate，但页面门禁控制器只支持一个')
        if sid not in scene_set:
            errors.append(f'{sid}: GATES 引用了时间轴不存在的 scene')
    total_gates = sum(gate_counts.values())
    if not gate_counts and re.search(r'\bGATES\b', stripped_html):
        warnings.append('检测到 GATES 标识但静态无法解析为字面量数组：门禁场景映射/数量检查已跳过，'
                        '浏览器冒烟仍按活动驱动检测门禁')
    if total_gates > 3:
        warnings.append(f'页面配了 {total_gates} 道门禁；建议默认 1–2 道、长课最多 3 道（interactions.md §1）')

    # Renderer literals vs sentence text: warning only.
    for scene_id, _, sentence in all_sentences:
        body = bodies.get(render_map.get(scene_id, '')) or inline_bodies.get(f"{scene_id}#inline")
        cap = _cap_norm(str(sentence.get('text', '')))
        if len(cap) < _CAP_LEN_MIN:
            continue
        literals = (_cap_literals(body) if body else []) + static_literals
        for literal in literals:
            lit = _cap_norm(literal)
            if len(lit) < _CAP_LEN_MIN:
                continue
            ratio = difflib.SequenceMatcher(None, cap, lit).ratio()
            if ratio >= _CAP_SIM_MIN:
                warnings.append(f'{scene_id}: 画面文字与旁白高度相似（{ratio:.0%}）：“{literal}”')
                break

    return {"errors": errors, "warnings": warnings, "stats": stats}


PROBE_STUB = r'''
<script>
(() => {
  // 错误钩子必须早于一切（包括下面的 stub 安装本身）：桩若在某台机器上装挂
  // （原型被冻结 / defineProperty 冲突），不装错误钩子的话这条失败自己就丢了，
  // 整轮检查还会拿着"零错误"的假绿灯继续。
  window.__coursewareCheckErrors = [];
  window.addEventListener('error', e => {
    const msg = e && e.message ? e.message : (e && e.error ? String(e.error) : 'unknown');
    window.__coursewareCheckErrors.push('JSERR ' + msg + ' @' + (e && e.filename || '') + ':' + (e && e.lineno || '?'));
  });
  window.addEventListener('unhandledrejection', e => {
    window.__coursewareCheckErrors.push('UNHANDLED ' + (e.reason && e.reason.message ? e.reason.message : String(e.reason)));
  });
  try {
    let t = 0;
    window.__coursewareCheckSetTime = v => { t = Number(v) || 0; };
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
      configurable: true,
      get(){ return t; },
      set(v){ t = Number(v) || 0; }
    });
    HTMLMediaElement.prototype.play = function(){ return Promise.resolve(); };
    HTMLMediaElement.prototype.pause = function(){};
    // 探针不走真实播放：只被 play/loadedmetadata 触发的页面若等不到这些事件，
    // 会把整轮字幕核对刷成一屏假"字幕不一致"。DOMContentLoaded 后补发一轮。
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('audio,video').forEach(el => {
        el.dispatchEvent(new Event('loadedmetadata'));
        el.dispatchEvent(new Event('canplay'));
        el.dispatchEvent(new Event('play'));
      });
    });
    Object.defineProperty(document, 'visibilityState', {configurable:true, get(){return 'visible';}});
    Object.defineProperty(document, 'hidden', {configurable:true, get(){return false;}});
  } catch(e) {
    window.__coursewareCheckErrors.push('PROBESTUB 探针桩安装失败: ' + (e && e.message || e));
  }
})();
</script>
'''


PROBE_DRIVER = r'''
<pre id="courseware-check-report">running</pre>
<script>
(() => {
  const report = {ok:false, errors:[], failures:[], warnings:[], gates:[], captionChecks:0};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // 同步一律用"轮询到条件成立 + 截止期限"，不用固定毫秒数：页面过渡时长
  // （CSS transition / preGate）超过旧的硬编码 sleep 时会产出假失败。
  const until = async (fn, ms = 1500, step = 50) => {
    const t0 = Date.now();
    for (;;) {
      if (fn()) return true;
      if (Date.now() - t0 > ms) return false;
      await sleep(step);
    }
  };
  const q = s => document.querySelector(s);
  const qa = s => Array.from(document.querySelectorAll(s));
  // 字幕比对把两侧空白折叠成单个空格再比：与静态侧 manifest 比对、build_page
  // 对齐校验同一口径。字幕按 tspan/多节点渲染时 textContent 带换行缩进，
  // 逐字比较会把好页面假报成"字幕不一致"。
  const capNorm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const out = q('#courseware-check-report');
  const timeline = JSON.parse(q('#lesson-timeline').textContent);
  const scenes = timeline.scenes || [];
  const gateScenes = new Set(__GATE_SCENES_JSON__);
  const audio = q('#main-audio');
  const gate = q('#gate');
  const pregate = q('#pregate');

  function captionShown(n){
    if (!n) return false;
    try {
      // opacity/visibility 不沿 computed style 继承：字幕 <text> 自己算出来永远是 1，
      // 上层 #cap 的淡出/隐藏只能沿祖先链逐级查。
      for (let el = n; el; el = el.parentElement) {
        if (el.hidden) return false;
        const cs = getComputedStyle(el);
        if (!cs) continue;
        if (cs.display === 'none') return false;
        if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
        if (Number(cs.opacity) < 0.05) return false;
      }
      // 零尺寸/未参与布局（藏在未渲染子树里）算不可见；遮挡关系不判。
      if (typeof n.getClientRects === 'function' && n.getClientRects().length === 0) return false;
      return true;
    } catch(e) { return false; }   // 取样式抛错按"不可见"处理：本文件其余守卫
  }                                  // 都是 fail-closed，放行会掩盖真故障

  function fire(t){
    window.__coursewareCheckSetTime(t);
    audio.dispatchEvent(new Event('timeupdate'));
  }
  function currentCard(){
    const n = gate.querySelector('[data-interaction]:not([hidden])');
    if (!n) return null;
    return {el:n, kind:n.dataset.interactionType || 'choice'};
  }
  function lockOf(card){ return card && card.el.dataset.locked === '1'; }
  function feedback(card){
    const n = card && card.el.querySelector('.interaction-feedback');
    return n ? n.textContent.trim() : '';
  }
  function tap(el){ if(el) el.click(); }
  function gestureTap(el){
    if(!el) return;
    const r=el.getBoundingClientRect(), x=r.left+r.width/2, y=r.top+r.height/2;
    if(window.PointerEvent){
      el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0,buttons:1,pointerId:19}));
      document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0,buttons:0,pointerId:19}));
    }else{
      el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0}));
      document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0}));
    }
  }
  function gestureDrag(item, target){
    if(!item || !target) return false;
    const a=item.getBoundingClientRect(), b=target.getBoundingClientRect();
    // 落在目标条目的下半部，触发 runtime 的“插到末尾”路径；落在正中会被
    // 解释成“插到目标前”，两项列表的顺序不会变化，产生假失败。
    // 目标因容器滚动而部分不可见时把点钳回容器可视区，避免打空（A9）。
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const holder = target.parentElement;
    let ex=clamp(b.left+b.width/2, window.innerWidth*0.02, window.innerWidth*0.98),
        ey=clamp(b.bottom-Math.min(1, b.height/4), 0, window.innerHeight-1);
    if(holder){
      const hr=holder.getBoundingClientRect();
      if(hr.width>0 && hr.height>0){
        ex=clamp(ex, hr.left+2, hr.right-2);
        ey=clamp(ey, hr.top+2, hr.bottom-2);
      }
    }
    const sx=a.left+a.width/2, sy=a.top+a.height/2;
    if(window.PointerEvent){
      item.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,clientX:sx,clientY:sy,button:0,buttons:1,pointerId:23}));
      document.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,cancelable:true,clientX:ex,clientY:ey,button:0,buttons:1,pointerId:23}));
      document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true,clientX:ex,clientY:ey,button:0,buttons:0,pointerId:23}));
    }else{
      item.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:sx,clientY:sy,button:0}));
      document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,cancelable:true,clientX:ex,clientY:ey,button:0}));
      document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:ex,clientY:ey,button:0}));
    }
    return true;
  }
  function cfg(card){ try{return JSON.parse(card.el.dataset.interaction || '{}');}catch(e){return {};} }

  function attempt(card, correct){
    if (!card) return false;
    const kind = card.kind, c = cfg(card);
    if (kind === 'choice' || kind === 'hotspot'){
      const opts = c.options || c.choices || c.spots || [];
      const pick = opts.find(o => correct ? o.correct === true : o.correct !== true);
      if (!pick) return false;
      const sel = kind === 'hotspot' ? `[data-hotspot-id="${CSS.escape(String(pick.id))}"]` : `[data-choice-id="${CSS.escape(String(pick.id))}"]`;
      const node = card.el.querySelector(sel);
      if (!node) return false;
      tap(node); return true;
    }
    if (kind === 'sequence'){
      const list = card.el.querySelector('.sequence-list');
      const order = c.correct_order || c.answer || [];
      if (!list || !order.length) return false;
      // 单项排序反转后仍是正序："错答"构造出来就是正答案，会假报"错答后仍锁定"
      if (!correct && order.length < 2) return false;
      const ids = correct ? order.slice() : order.slice().reverse();
      const map = {};
      Array.from(list.children).forEach(li => { map[li.dataset.sequenceId] = li; });
      ids.forEach(id => { if(map[id]) list.appendChild(map[id]); });
      const submit = card.el.querySelector('[data-sequence-submit]');
      if (!submit) return false;
      tap(submit); return true;
    }
    if (kind === 'bucket'){
      const answer = c.answer || {}, ids = Object.keys(answer);
      if (!ids.length) return false;
      // 没有任何可用的错误目标时，不能把正确答案伪装成“错答”提交。
      const allBuckets = [...card.el.querySelectorAll('[data-drop][data-bucket-id]')]
        .map(n => String(n.dataset.bucketId));
      const wrongBucket = allBuckets.find(id => id !== String(answer[ids[0]]));
      if (!correct && !wrongBucket) return false;
      ids.forEach((id, i) => {
        let bucket = String(answer[id]);
        if (!correct && i === 0) bucket = wrongBucket;
        const item = card.el.querySelector(`.bucket-item[data-bucket-item="${CSS.escape(id)}"]`);
        const box = card.el.querySelector(`[data-drop][data-bucket-id="${CSS.escape(bucket)}"]`);
        if(item && box){ gestureTap(item); tap(box); }
      });
      const submit = card.el.querySelector('[data-bucket-submit]');
      if (!submit) return false;
      tap(submit); return true;
    }
    if (kind === 'recall'){
      // recall 不判定：只有"对照后放行"一条路，wrong 分支按契约返回 false。
      if (!correct) return false;
      const reveal = card.el.querySelector('[data-recall-reveal]');
      if (!reveal) return false;
      tap(reveal); return true;
    }
    return false;
  }

  async function testGate(time, sentence){
    const card = currentCard();
    if (!card){
      report.failures.push('门禁已打开，但卡内没有可见的 [data-interaction] 题目');
      return false;
    }
    const host = q('#gate-host');
    const sceneId = (host && host.dataset.stepId) || '';
    if (!sceneId) report.failures.push('门禁缺少 gate-host.dataset.stepId');
    // 门禁期间播放器行与画布播放层必须 inert（layout.md §5）：只设
    // pointer-events 挡不住 Tab/Enter。closest 让祖先级 inert 也豁免（inert
    // 沿树级联生效），不可见（display:none/hidden → offsetParent 为 null）
    // 同样豁免：键盘本来就够不到。元素不存在则跳过，别替页面发明 id。
    for (const pid of ['rack', 'veilplay']) {
      const n = q('#' + pid);
      if (n && !n.closest('[inert]') && n.offsetParent !== null)
        report.failures.push(`门禁期间 #${pid} 未 inert（键盘可绕过）：${sceneId}`);
    }
    if (sentence){
      const frac = (time - sentence.start) / sentence.duration;
      if (frac > 0.08 && frac < 0.92) report.failures.push(`门禁落在句中间：${sceneId} ${Math.round(frac*100)}%`);
    }
    const kind = card.kind;
    // sequence / bucket 的 QA 不能只直接改 DOM：这会绕过 runtime 的真实手势接线。
    // runtime 在每个可拖拽条目上写 data-gesture="1"，以此确认动态生成的题目已重新接线。
    if (kind === 'sequence' || kind === 'bucket'){
      const items = qa(kind === 'sequence' ? '.sequence-item' : '.bucket-item')
        .filter(n => card.el.contains(n));
      if (!items.length) report.failures.push(`${kind} 缺少可交互条目`);
      items.forEach((item, i) => {
        if (item.dataset.gesture !== '1') report.failures.push(`${kind} 条目未接入手势：${i + 1}`);
      });
    }
    if (kind === 'sequence'){
      const list = card.el.querySelector('.sequence-list');
      const items = list ? [...list.querySelectorAll('.sequence-item')] : [];
      if (items.length > 1){
        const before = items.map(item => item.dataset.sequenceId).join(',');
        gestureDrag(items[0], items[items.length - 1]);
        const after = [...list.querySelectorAll('.sequence-item')].map(item => item.dataset.sequenceId).join(',');
        if (before === after) report.failures.push('sequence 拖拽手势没有改变顺序');
      }
    }
    const nextBtn = q('#gate-next');
    if (!nextBtn) report.failures.push('门禁缺少 #gate-next 继续按钮');
    const wrongDid = attempt(card, false);
    if (wrongDid){
      // 错答若错误地解锁/锁定，先给它最多 300ms 落定再断言，不猜固定时长。
      await until(() => lockOf(card) || (nextBtn && !nextBtn.hidden), 300);
      if (lockOf(card)) report.failures.push(`错答后仍锁定：${kind}`);
      if (nextBtn && !nextBtn.hidden) report.failures.push(`错答后出现继续按钮：${kind}`);
      // runtime.md §2 的标准骨架含 .interaction-feedback 节点，错答也走
      // finish(correct:false) 写反馈；这里把它真正断言掉（此前 feedback()
      // 探针定义了却从未调用，"错答后反馈不显示"会漏报）。
      if (!feedback(card)) report.failures.push(`错答后反馈区没有任何提示：${kind}`);
    } else if (kind !== 'recall') {
      // recall 没有错答路径是契约属性（不判定），不是探针能力缺口，不该报警。
      report.warnings.push(`门禁 ${kind} 未能构造错误路径，仅检查正确路径`);
    }
    const rightDid = attempt(card, true);
    if (!rightDid){ report.failures.push(`无法构造正确路径：${kind}`); return true; }
    await until(() => lockOf(card), 1000);
    if (!lockOf(card)) report.failures.push(`答对后未锁定：${kind}`);
    if (nextBtn){
      await until(() => !nextBtn.hidden, 1000);
      if (nextBtn.hidden) report.failures.push(`答对后没有继续按钮：${kind}`);
    }
    report.gates.push({scene:sceneId, kind, time, wrongTested:wrongDid, correctTested:rightDid});
    return true;
  }

  async function run(){
    if(!audio || !q('#lesson-timeline') || !q('#cap-text')){
      report.failures.push('缺少 audio / timeline / caption 节点');
      out.textContent = JSON.stringify(report);
      document.title = 'courseware-check FAIL';
      return;
    }
    const caption = q('#cap-text');
    // 探针靠 dispatchEvent('timeupdate') 驱动时间轴，不走真实 play，页面会停在
    // data-state="idle"；模板 CSS 在 idle 下强制 #cap 透明。先进入播放态，
    // 否则下面的可见性断言会对任何模板恒误报。
    const stage = q('#stage'); if (stage) stage.dataset.state = 'playing';

    // 先把所有门禁走完。这样后面的字幕逐句核对不会被门禁的 preGate 回退干扰。
    // candidates 标注该 scene 是否配了 gate：配了的要等 preGate 过渡结束再判断，
    // 不能 fire 后 15ms 看没开就跳过（那会把全部门禁静默漏检）。
    const candidates = [];
    scenes.forEach(scene => {
      const sid = String(scene.step_id);
      const hasGate = gateScenes.has(sid);
      candidates.push({time:Number(scene.runtime.start), hasGate:hasGate});
      const ns = scene.runtime.narration || [];
      if(ns.length) candidates.push({time:Number(ns[ns.length-1].start) + Number(ns[ns.length-1].duration), hasGate:hasGate});
    });
    const seenGateScenes = new Set();
    const revealedScenes = new Set();
    const gateActive = () => gate && (!gate.hidden || (pregate && !pregate.hidden));
    if (gateScenes.size && !gate) report.failures.push('检测到 GATES 配置，但页面没有 #gate 浮层');
    if (gate){
      for(const cand of candidates){
        fire(cand.time);
        await sleep(30);
        // 活动驱动：gate 已开或 preGate 过渡中——即使静态解析没认出这个场景配了
        // gate（GATES.push / 运行时拼装），也要等它开完并测试，堵住字面量依赖逃逸（A1）。
        if (gate.hidden && !(cand.hasGate || gateActive())) continue;
        await until(() => !gate.hidden, 2500);   // 等 preGate 过渡（≤2.5s）
        if (gate.hidden) continue;   // 配了却没开：统一在循环后报
        const host = q('#gate-host');
        const sid = (host && host.dataset.stepId) || '';
        revealedScenes.add(sid);
        if (seenGateScenes.has(sid)) continue;
        seenGateScenes.add(sid);
        const scene = scenes.find(x => String(x.step_id) === sid);
        const nlist = scene ? scene.runtime.narration || [] : [];
        let probeSentence = null;
        for(const n of nlist){ if(cand.time >= Number(n.start) && cand.time <= Number(n.start) + Number(n.duration)){ probeSentence = n; break; } }
        await testGate(cand.time, probeSentence);
        if (gateScenes.size && !gateScenes.has(sid)) report.warnings.push(`弹出的门禁场景 ${sid} 不在静态解析的 GATES 里，请核对门禁配置`);
        const go = q('#gate-go'); if(go && !go.disabled) go.click();
        await until(() => gate.hidden && (!pregate || pregate.hidden), 1500);
      }
      for(const sid of gateScenes){
        if(!revealedScenes.has(sid)) report.failures.push(`GATES 配置了 ${sid}，但门禁从未弹出`);
      }
    }

    // Start a clean trace for the sentence sweep. Gate handling above can
    // legitimately render the anchor sentence before the caption assertions.
    if (typeof window.__coursewareResetRenderTrace === 'function') {
      window.__coursewareResetRenderTrace();
    } else if (Array.isArray(window.__coursewareRenderTrace)) {
      window.__coursewareRenderTrace.length = 0;
    } else {
      report.failures.push('页面没有可用的 __coursewareRenderTrace');
    }

    // 字幕核对：每句取中点，要求画布字幕逐字等于时间轴原文，且真的可见。
    // 扫描途中若有门禁在句中点位打开（锚点没落在句子边界上），单独报出（A4）。
    const sweepGateReported = new Set();
    for(const scene of scenes){
      const ns = scene.runtime && scene.runtime.narration || [];
      for(let i=0;i<ns.length;i++){
        const n = ns[i];
        // 中点必须落在本句区间内：duration < 0.1s 的短句若两侧各夹 0.05，
        // mid 会越过句末进入下一句区间，产生系统性假"字幕不一致"。
        const mid = n.start + Math.min(n.duration * 0.5,
                                       Math.max(n.duration - 0.02, 0.001));
        fire(mid);
        await sleep(0);
        const renderKey = `${scene.step_id}#${i}`;
        if (Array.isArray(window.__coursewareRenderTrace)
            && !window.__coursewareRenderTrace.includes(renderKey)) {
          report.failures.push(`句子没有驱动 renderer：${renderKey}`);
        }
        if (gateActive()){
          const key = String(scene.step_id);
          if(!sweepGateReported.has(key)){
            report.failures.push(`门禁在句中标位置打开：${key} t=${mid.toFixed(2)}`);
            sweepGateReported.add(key);
          }
          const go = q('#gate-go'); if(go && !go.disabled) go.click();
          await until(() => !gateActive(), 1500);
        }
        const got = capNorm(caption.textContent);
        if(got !== capNorm(n.text)){
          report.failures.push(`字幕不一致：${scene.step_id}#${i} 期望“${n.text}” 实际“${got}”`);
        } else if(!captionShown(caption)){
          // 淡入过渡（模板 .38s）可能还没走完，等它结束再判不可见，避免假失败。
          await sleep(420);
          if(!captionShown(caption))
            report.failures.push(`字幕文字一致但不可见：${scene.step_id}#${i}`);
        }
        report.captionChecks += 1;
      }
    }

    // 空档不清字幕：换场空档与场景内句间空档共用同一条契约（停在上一句上，
    // 不清空、不闪白），也共用这一个探针——先把字幕定标回上一句，再进空档比对。
    // 探测前必须重新定标：直接 fire 空档点时，画面上残留的是上一轮扫描的字幕，
    // 比较对象错位会产生系统性误报。
    async function gapHold(tag, prev, nextStart){
      const end = Number(prev.start) + Number(prev.duration);
      const gap = Number(nextStart) - end;
      if (gap <= 0.08) return;
      fire(Number(prev.start) + Math.min(0.05, Number(prev.duration) * 0.5));
      await sleep(0);
      fire(end + Math.min(0.05, gap / 2));
      await sleep(0);
      if (capNorm(caption.textContent) !== capNorm(prev.text)) {
        report.failures.push(`空档字幕被清掉（${tag}）`);
      } else if (!captionShown(caption)) {
        // 字幕淡出过渡（模板 .38s）可能还没定形，给它 420ms 再判隐藏；
        // 只比文字不比可见性会放过"空档把字幕层整体藏起来"的闪白。
        await until(() => captionShown(caption), 420);
        if (!captionShown(caption)) report.failures.push(`空档字幕被隐藏（${tag}）`);
      }
    }
    for(let i=0;i<scenes.length-1;i++){
      const a=scenes[i], b=scenes[i+1];
      const ns=a.runtime.narration || [];
      if(!ns.length) continue;
      await gapHold(`换场空档 ${a.step_id}`, ns[ns.length-1], b.runtime.start);
    }
    // 逐句拼接的 gap 同样存在于场景内部，这里补上同一探针，页面契约不是一句空话。
    for(const scene of scenes){
      const ns = scene.runtime && scene.runtime.narration || [];
      for(let i=0;i+1<ns.length;i++){
        await gapHold(`场景内句间空档 ${scene.step_id}#${i}`, ns[i], ns[i+1].start);
      }
    }

    // 加载期（探针挂上之前）抛出的错误由 PROBE_STUB 记录，在这里并入报告。
    report.errors = report.errors.concat(window.__coursewareCheckErrors || []);
    report.ok = report.errors.length===0 && report.failures.length===0;
    out.textContent = JSON.stringify(report);
    document.title = `courseware-check ${report.ok ? 'PASS' : 'FAIL'}`;
  }
  run().catch(e => {
    report.errors.push('probe exception: ' + (e && e.stack ? e.stack : String(e)));
    report.errors = report.errors.concat(window.__coursewareCheckErrors || []);
    report.ok = false;
    out.textContent = JSON.stringify(report);
    document.title = 'courseware-check FAIL';
  });
})();
</script>
'''


def _find_chrome() -> str | None:
    cands = [
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge",
    ]
    # 本用户目录下的 Chrome/Edge（per-user 安装不进 Program Files）
    local = os.environ.get('LOCALAPPDATA')
    if local:
        cands += [
            os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(local, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
            os.path.join(local, 'Chromium', 'Application', 'chrome.exe'),
        ]
    for c in cands:
        if os.path.exists(c):
            return c
    for name in ('chrome', 'google-chrome', 'chromium', 'msedge', 'microsoft-edge'):
        found = shutil.which(name)
        if found:
            return found
    return None


def _build_probe(page: Path, out: Path) -> None:
    src = read_text(page)
    # 探针副本按 UTF-8 落盘：源页若是 GB18030/UTF-16 读进来的，不重写声明的话
    # 浏览器仍按原编码解 UTF-8 字节——中文全成乱码，冒烟整轮失真。
    src = normalize_meta_charset(src)
    # 与 static_check 用同一个解析入口：自造精确串匹配会造成"单引号 id 静态通过、
    # 浏览器阶段报'找不到'"的口径分裂。
    _, timeline_err = _timeline_from_html(src)
    if timeline_err:
        raise SystemExit(f'[error] {timeline_err}')
    # 门禁统计取"作者写下的页面"：inline 进来的 runtime 自带 GATES/scene 字样的
    # 注释与标识符，在它之上做字面量正则会污染计数（A6）。
    # 作者页面先 strip 一次：门禁统计与探针落点都在它上面定位（runtime 内联
    # 那处要等探针注入完再另算，见下面的 RUNTIME_REF_RE）。
    stripped_html = _js_strip_html(src)
    gate_scenes_json = json.dumps(sorted(k for k in
                                         _gate_scene_counts(src, stripped_html)
                                         if k != EXPR_SCENE_KEY),
                                  ensure_ascii=False).replace('</', '<\\/')
    # probe 落在临时目录：不给页面相对资源（styles.css / 图片 / 音频）留一个
    # 指向页面目录的 <base>，它们会全部 404——外部 CSS 一失效，祖先链可见性
    # 检查就全体空洞通过，无样式布局还会产出假的手势失败。
    base = page.parent.resolve().as_uri().rstrip('/') + '/'
    # 与 runtime 引用同一口径在 stripped 上定位（偏移一致）：注释残稿里排在
    # 真 <head> / 真 <script> 前面的 ghost tag 会把探针注进注释——浏览器里是
    # 死代码，桩不住 currentTime，整轮冒烟静默失真。
    head = re.search(r'<head\b[^>]*>', stripped_html, re.I)
    first_script = re.search(r'<script\b', stripped_html, re.I)
    if head:
        i = head.end()
    else:
        # timeline 已通过解析，页面必然有 <script>；没有 <head> 时贴到首个脚本前。
        i = first_script.start()
    src = src[:i] + '\n' + f'<base href="{base}">\n' + PROBE_STUB + src[i:]
    # 与 static_check 同一正则（RUNTIME_REF_RE）、同一 stripped_html 口径：
    # 注释残稿里的同款 tag 排得比真引用靠前时，裸 src + 字符串 replace 会把
    # runtime 内联进注释（浏览器里是死代码），真 tag 反而 404，冒烟整轮失真。
    # 命中区间只含标签 markup（正则要 `>\s*</script`），stripped 与原文逐字相同，
    # span 可直接用于替换 src。
    runtime_ref = RUNTIME_REF_RE.search(_js_strip_html(src))
    if runtime_ref:
        rel = runtime_ref.group(1).split('?', 1)[0]
        candidate = (page.parent / rel).resolve()
        if not candidate.exists():
            raise FileNotFoundError(f'页面引用的 runtime 不存在：{candidate}')
        runtime = read_text(candidate)
        # probe 永远 inline 页面实际使用的 runtime，避免临时目录改变相对 src 后悄悄变成 404。
        # 与 gate_scenes_json 同一口径防 `</script`：runtime 源码里（哪怕字符串/注释里）
        # 出现字面 `</script` 会截断 inline 块，浏览器冒烟整轮失真。JS 字符串/正则里
        # `<\/script` 与 `</script` 同值，替换对语义无损。
        runtime = runtime.replace('</script', '<\\/script')
        src = src[:runtime_ref.start()] + '<script>\n' + runtime + '\n</script>' + src[runtime_ref.end():]
    driver = PROBE_DRIVER.replace('__GATE_SCENES_JSON__', gate_scenes_json)
    # 注入点跳过注释里的 ghost `</body>`：残稿假闭合排在真闭合之前时，driver
    # 注进注释 = 探针永不执行，冒烟只剩"浏览器没有返回检查报告"的假失败。
    # 与 _audio_src/_view_box 的 _first_outside_comment 同一防御。
    _comment_ranges = _html_comment_ranges(src)
    _inject_at = None
    for _m in re.finditer(r'</body\s*>', src, re.I):
        if not any(a <= _m.start() < b for a, b in _comment_ranges):
            _inject_at = _m.start()
            break
    if _inject_at is not None:
        src = src[:_inject_at] + driver + src[_inject_at:]
    else:
        src += driver
    out.write_text(src, encoding='utf-8')



class ChromeLaunchError(RuntimeError):
    """Chrome/Edge 二进制存在但 exec 失败（损坏、被安全软件拦截、32 位已移除等）。
    与"找不到浏览器"不同：_find_chrome 已返回路径，Popen 却抛 OSError；不接住
    就会以裸 traceback 崩掉，绕开所有"未找到/预检未通过"的友好退路。"""


def _run_chrome(cmd: list[str], stdout_path: Path, stderr_path: Path, timeout: float) -> int:
    """启动浏览器并提供硬超时；超时后杀整组进程，避免留下 zygote/renderer。"""
    with stdout_path.open('w', encoding='utf-8', errors='replace') as fo, stderr_path.open('w', encoding='utf-8', errors='replace') as fe:
        kwargs = {}
        if os.name == 'posix':
            kwargs['start_new_session'] = True
        try:
            proc = subprocess.Popen(cmd, stdout=fo, stderr=fe, **kwargs)
        except OSError as exc:
            raise ChromeLaunchError(f'无法启动浏览器进程（{cmd[0]}：{exc}）') from exc
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if os.name == 'posix':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    # proc.kill() 只杀父进程，会留下 renderer/zygote 孤儿；
                    # taskkill /T 沿进程树整棵收掉，失败再退回单杀父进程。
                    # TimeoutExpired 不是 OSError：taskkill 自己卡住也要落进兜底。
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                                   capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            raise


def _rmtree_retry(root: Path) -> None:
    """Windows 刚 taskkill 掉 Chrome 时 profile 文件可能仍被短暂占用，
    单次 ignore_errors rmtree 会把删不掉静默吞成 %TEMP% 残留。两处清理共用。"""
    for _ in range(4):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            return
        time.sleep(0.5)


def _browser_preflight(chrome: str) -> list[str] | None:
    """确认本机 headless Chrome 真能完成一个最小 dump-dom，返回可用附加 flag。

    先带沙箱跑；只有沙箱起不来的容器/CI 环境才退回 --no-sandbox。
    绝不传 --allow-file-access-from-files：检查对象是不可信页面，那个 flag
    等于给页面开任意本地文件读取通道（含 .env）；runtime 已 inline、媒体被
    桩接管，本来就不需要它。
    """
    for extra in ([], ['--no-sandbox']):
        root = Path(tempfile.mkdtemp(prefix='courseware-browser-preflight-'))
        try:
            page = root / 'probe.html'
            out = root / 'dom.html'
            err = root / 'chrome.log'
            page.write_text('<!doctype html><html><body>courseware-preflight-ok</body></html>', encoding='utf-8')
            cmd = [chrome, '--headless=new', '--disable-gpu', '--no-first-run',
                   '--no-default-browser-check', '--disable-dev-shm-usage',
                   f'--user-data-dir={root / "profile"}', '--virtual-time-budget=500', '--dump-dom'] + extra
            cmd.append(page.as_uri())
            try:
                rc = _run_chrome(cmd, out, err, timeout=10)
            except (subprocess.TimeoutExpired, ChromeLaunchError):
                continue
            if rc != 0 or not out.exists():
                continue
            if 'courseware-preflight-ok' in out.read_text(encoding='utf-8', errors='replace'):
                return extra
        finally:
            _rmtree_retry(root)
    return None

def browser_check(page: Path, keep: bool,
                  scene_count: int) -> tuple[dict[str, Any] | None, str]:
    chrome = _find_chrome()
    if not chrome:
        return None, '未找到可用 Chrome/Edge'
    extra_flags = _browser_preflight(chrome)
    if extra_flags is None:
        return None, 'headless Chrome 预检未通过（带沙箱与 --no-sandbox 退路均未成功）'
    # 虚拟时间预算要覆盖全部探针等待（门禁 preGate、逐句字幕、换场空档）：
    # 固定 40s 对短课够用，长课会烧光预算——报告停在 "running"，被当成
    # "没有返回检查报告"的假故障。按场景数扩容（A8）。每场按 7s 计：场景内
    # 若挂着从未弹出的门禁，候选会被试错两轮（各 2.5s 等待），4.5s 罩不住，
    # 真故障会先烧光预算、退化成语焉不详的"没有返回检查报告"。
    budget = max(40000, scene_count * 7000 + 20000)
    temp_root = Path(tempfile.mkdtemp(prefix='courseware-check-'))
    probe = temp_root / 'probe.html'
    dom = temp_root / 'dom.html'
    log = temp_root / 'chrome.log'
    profile = temp_root / 'profile'
    try:
        _build_probe(page, probe)
    except (OSError, UnicodeError) as exc:
        if keep:
            print(f'[note] 检查页构造失败，产物目录已保留：{temp_root}')
        else:
            shutil.rmtree(temp_root, ignore_errors=True)
        return {"ok": False, "errors": [f"无法构造浏览器检查页：{exc}"], "failures": [], "warnings": [], "stats": {}}, ''
    except SystemExit as exc:
        # 构造页失败也要走报告通道：直接让 SystemExit 穿透会跳过 temp_root 清理
        if keep:
            print(f'[note] 检查页构造失败，产物目录已保留：{temp_root}')
        else:
            shutil.rmtree(temp_root, ignore_errors=True)
        return {"ok": False, "errors": [str(exc)], "failures": [], "warnings": [], "stats": {}}, ''
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome, '--headless=new', '--disable-gpu', '--no-first-run',
        '--no-default-browser-check', '--disable-dev-shm-usage',
        '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding', f'--user-data-dir={profile}',
        '--run-all-compositor-stages-before-draw', f'--virtual-time-budget={budget}', '--dump-dom',
    ] + extra_flags + [probe.resolve().as_uri()]
    try:
        _run_chrome(cmd, dom, log, timeout=max(60, budget // 1000))
        raw = dom.read_text(encoding='utf-8', errors='replace') if dom.exists() else ''
        m = re.search(r'<pre id="courseware-check-report">(.*?)</pre>', raw, re.S)
        if not m:
            tail = log.read_text(encoding='utf-8', errors='replace')[-1000:] if log.exists() else ''
            return {"ok": False, "errors":["浏览器没有返回检查报告"], "failures":[], "warnings":[tail], "stats":{}}, ''
        report_text = html.unescape(m.group(1))
        try:
            report = json.loads(report_text)
        except json.JSONDecodeError:
            return {"ok":False,"errors":["浏览器返回的检查报告不是 JSON"],"failures":[],"warnings":[report_text[:1000]],"stats":{}}, ''
        return report, ''
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": [f"浏览器冒烟检查超过 {max(60, budget // 1000)}s，已中止"], "failures": [], "warnings": [], "stats": {}}, ''
    except ChromeLaunchError as exc:
        return {"ok": False, "errors": [str(exc)], "failures": [], "warnings": [], "stats": {}}, ''
    finally:
        if not keep:
            _rmtree_retry(temp_root)
        else:
            print(f'[note] 已保留检查产物目录：{temp_root}（含 probe.html / dom.html / Chrome profile，用完请自行删除）')


def main() -> int:
    setup_stdio()   # 中文 Windows 管道重定向下 stdout 默认 gbk，中文章节标题会乱码
    ap = argparse.ArgumentParser(description='Courseware Studio 交付检查：字幕 + 时间轴 + 门禁 + JS')
    ap.add_argument('page', help='页面目录（含 index.html）或某个 .html 路径')
    ap.add_argument('--no-browser', action='store_true', help='只做静态检查，不启动 Chrome/Edge')
    ap.add_argument('--keep', action='store_true', help='保留浏览器冒烟副本')
    ap.add_argument('--require-browser', action='store_true', help='浏览器冒烟未执行时返回失败（适合 CI）')
    ap.add_argument('--allow-degraded', action='store_true', help='允许时间轴含 synth_failed=true（不建议用于最终交付）')
    args = ap.parse_args()
    if args.no_browser and args.require_browser:
        # 两个开关同时给会在"跳过浏览器"分支直接返回 0：CI 拿到绿勾却零浏览器覆盖。
        ap.error('--no-browser 与 --require-browser 冲突：CI 要求浏览器冒烟时请去掉 --no-browser')

    page = Path(args.page)
    if page.is_dir():
        page = page / 'index.html'
    page = page.resolve()
    if not page.exists():
        print(f'[error] 找不到页面：{page}')
        return 2

    src = read_text(page)
    # 主音频引用合法性与文件存在性都在 static_check 里报（含页面目录）：
    # 早退会吞掉同页其它静态错误，存在性检查也不能当任意路径探针用。
    static = static_check(src, allow_degraded=args.allow_degraded, page_dir=page.parent)
    print(f"[static] scenes={static['stats'].get('scenes',0)} sentences={static['stats'].get('sentences',0)}")
    for w in static['warnings']:
        print('[warn] ' + w)
    for e in static['errors']:
        print('[error] ' + e)
    if static['errors']:
        return 1

    if args.no_browser:
        print('[ok] 静态检查通过（已跳过浏览器字幕/门禁冒烟）')
        return 0

    report, note = browser_check(page, keep=args.keep,
                                 scene_count=static['stats'].get('scenes', 0))
    if report is None:
        print(f'[note] 浏览器冒烟未执行：{note}。静态检查已通过。')
        return 1 if args.require_browser else 0
    for e in report.get('errors', []):
        print('[error] ' + str(e))
    for f in report.get('failures', []):
        print('[fail] ' + str(f))
    for w in report.get('warnings', []):
        if w:
            print('[warn] ' + str(w))
    print(f"[browser] captions={report.get('captionChecks',0)} gates={len(report.get('gates',[]))}")
    if report.get('ok'):
        print('[ok] 字幕 / 时间轴 / 门禁 / JS 浏览器冒烟通过')
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
