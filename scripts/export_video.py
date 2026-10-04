#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Courseware Studio 视频导出（可选交付）。

课件画面完全由 audio.currentTime 驱动、一条旁白句子 = 一个稳定视觉步，
所以线性视频不需要真实录屏：逐句在句末前一瞬定格截帧，按逐句时长拼接，
再混入旁白音轨，字幕与语音天然逐句对齐。

依赖：本机 Chrome/Edge（headless 截图）+ ffmpeg（经 `_audio.get_ffmpeg`
解析，含 imageio-ffmpeg 回退；不需要 ffprobe）。
前提：页面遵循标准骨架（#stage 舞台、.stage 布局类、#lesson-timeline 内联
时间轴、#main-audio 指向交付音频）。门禁在截图页里被显式抑制：截图页在
<head> 最前注入 window.__coursewareShotMode=true 并 CSS 藏起 #gate/#pregate；
页面见旗同时按桌面渲染（小屏字号放大/取景不进视频，成片与桌面页面同一张脸）。
页面门禁逻辑见旗即让路（契约见 references/runtime.md §7）。

用法：
    python scripts/export_video.py <页面目录或页面 .html>
    python scripts/export_video.py <页面目录> -o out.mp4 --crf 18 --keep
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audio import get_ffmpeg, measure_duration, quote_ffpath  # noqa: E402
from _contracts import TIMING_EPS  # noqa: E402
from _script_utils import (guard_not_in_skill_dir, normalize_meta_charset,  # noqa: E402
                           read_text, setup_stdio, write_text_atomic)
from check_gates import (  # noqa: E402
    ChromeLaunchError,
    _browser_preflight,
    _find_chrome,
    _html_comment_ranges,
    _rmtree_retry,
    _run_chrome,
    _timeline_from_html,
)

STAGE_PAD = 10          # 标准骨架 .stage 的 padding；窗口按它留边
EPS = 0.06              # 定格点相对句末的提前量
SHOT_MODE_FLAG = '<script>window.__coursewareShotMode = true;</script>'

SNIPPET = """
<script>
(function(){
  var t = parseFloat(new URLSearchParams(location.search).get('shot'));
  if (isNaN(t)) return;
  var st = document.createElement('style');
  st.textContent =
    '*,*::before,*::after{transition:none!important;animation:none!important}' +
    'body{margin:0;background:#181c25}' +
    '.shell{display:block;padding:0;max-width:none;gap:0}' +
    '.col-side,.chapter,.rack{display:none!important}' +
    '.stage{border:0!important;border-radius:0;padding:%(pad)dpx}' +
    '#gate,#pregate{display:none!important}';
  document.head.appendChild(st);
  var audio = document.getElementById('main-audio');
  var stage = document.getElementById('stage');
  var tries = 0;
  function seek(){
    if (!(audio && audio.duration > 0)){
      // 音频始终没就绪（duration=NaN/0）时 seek 无效，会产出 N 帧同一画面；
      // 有限重试后放弃，交给帧差异校验把失败显式化。重试上限 × 150ms 必须
      // 落在 --virtual-time-budget 之内，否则预算先掐断 seek（见 frame 循环）。
      if (++tries < 40) setTimeout(seek, 150);
      return;
    }
    audio.pause();
    audio.currentTime = Math.max(0.001, Math.min(t, audio.duration - 0.001));
    if (stage) stage.dataset.state = 'paused';
    audio.dispatchEvent(new Event('timeupdate'));
  }
  window.addEventListener('load', seek);
})();
</script>
</body>"""


def _fail(msg: str) -> SystemExit:
    return SystemExit(f'[error] {msg}')


def _timeline_sentences(src: str) -> list[tuple[float, float, int]]:
    # 与 check_gates 同一解析入口（含 html.unescape）：两套口径会产出
    # "过检页面炸导出"或反之心照不宣的漂移。
    data, err = _timeline_from_html(src)
    if err:
        raise _fail(f'{err}；导出要求时间轴已内联进页面')
    out = []
    for si, sc in enumerate(data['scenes']):
        # 非 dict 场景不拦就是 sc.get 裸 AttributeError——检查器会把这种页面
        # 拦下，但"先过检查再导出"不是导出的前提，这里自己站稳。
        if not isinstance(sc, dict):
            raise _fail(f'时间轴场景 {si + 1} 不是对象，无法导出')
        runtime = sc.get('runtime') or {}
        if not isinstance(runtime, dict):
            raise _fail(f'时间轴 {sc.get("step_id") or si + 1}: runtime 不是对象，无法导出')
        for ni, n in enumerate(runtime.get('narration') or []):
            try:
                start, dur = float(n['start']), float(n['duration'])
            except (KeyError, TypeError, ValueError):
                raise _fail(f'时间轴 {sc.get("step_id")}#{ni} 缺少合法 start/duration')
            # NaN/inf 能过 float()（"1e999" 就是 inf），一路喂到帧时长与
            # concat 列表里会让整条时间线变 NaN，帧全同却报"成功"。
            if not (math.isfinite(start) and math.isfinite(dur)):
                raise _fail(f'时间轴 {sc.get("step_id")}#{ni} 的 start/duration 不是有限数值')
            if start < 0 or dur <= 0:
                raise _fail(f'时间轴 {sc.get("step_id")}#{ni} 的 start 必须非负、duration 必须为正')
            out.append((start, dur, si))
    if not out:
        raise _fail('时间轴里没有任何旁白句子')
    return out


def _first_outside_comment(pattern: str, src: str, flags: int = re.I,
                           start: int = 0, end: int | None = None):
    # 与 check_gates 同一口径：整段被注释掉的旧结构残稿（ghost #main-audio /
    # ghost #stage）排得比真钩子靠前时，first-match 会撞上死标签——轻则读错
    # 音频路径报"文件不存在"，重则把 ghost src 改写进截图页而真 audio 原样
    # 走相对路径，N 帧截到同一张 idle 画面。start/end 把搜索限制在调用方
    # 认定的元素块内（真 audio 与它的真 </audio> 之间），注释里的假闭合
    # 不会再截短块、块内注释里的假 <source> 也不会抢先到命中。
    ranges = _html_comment_ranges(src)
    for m in re.finditer(pattern, src, flags):
        if m.start() < start:
            continue
        if end is not None and m.start() >= end:
            break
        if not any(a <= m.start() < b for a, b in ranges):
            return m
    return None


def _audio_block(src: str):
    """Return (opening_match, content_start, content_end) for the real #main-audio."""
    tag = _first_outside_comment(r'<audio\b[^>]*\bid=["\']main-audio["\'][^>]*>', src)
    if not tag:
        return None, 0, 0
    close = _first_outside_comment(r'</audio\s*>', src, re.I, tag.end())
    return tag, tag.end(), close.start() if close else tag.end()


def _audio_src(src: str) -> str:
    # 先锁定 #main-audio 的开标签，再在标签内找 src：属性顺序无关
    # （check_gates 的静态解析两种顺序都认，这里若只认 id 在前就会
    # "过检页面炸导出"）。
    tag, lo, hi = _audio_block(src)
    if tag:
        # (?<![-\w])：'-' 也是 \b 的边界，裸 \bsrc= 会先撞上 data-src= 拿错值
        m = re.search(r'(?<![-\w])src=["\']([^"\']+)["\']', tag.group(0), re.I)
        if m:
            return m.group(1)
        s = _first_outside_comment(r'<source\b[^>]*?(?<![-\w])src=["\']([^"\']+)["\']',
                                   src, re.I, lo, hi)
        if s:
            return s.group(1)
    raise _fail('找不到 #main-audio 的 src（src 属性或 <source> 子标签），请用 --audio 显式指定旁白音频')


def _view_box(src: str) -> tuple[float, float]:
    # 只认舞台自己的 svg：从 #stage 往后找。全篇第一个 <svg viewBox> 不行——
    # 页面若把 logo / 插画 svg 排在舞台前，窗口宽高比整个拿错，meet  letterbox
    # 出黑边，成片静默错框。main() 已保证 #stage 存在。
    # (?<![-\w])：与 src 守卫同一口径，裸 \b 在 '-' 与 'i' 之间也成立，
    # data-id="stage" 会先命中导致 viewBox 取错。
    anchor = _first_outside_comment(r'(?<![-\w])id=["\']stage["\']', src)
    tail = src[anchor.end():] if anchor else src
    m = re.search(
        r'<svg[^>]*viewBox=["\']\s*[-\d.]+\s*[ ,]+\s*[-\d.]+\s*[ ,]+\s*([\d.]+)\s*[ ,]+\s*([\d.]+)',
        tail, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    print('[note] #stage 之后未解析到舞台 viewBox，按 1000x460 兜底')
    return 1000.0, 460.0


def _media_dur(path: Path, ffmpeg: str) -> float:
    # 走 _audio.measure_duration：WAV 精确样本路径 + ffmpeg stderr 兜底，
    # 不依赖 ffprobe（imageio-ffmpeg 回退包里就没有它）。0.0 = 读失败。
    d = measure_duration(ffmpeg, str(path))
    if not d:
        raise _fail(f'读取时长失败（ffmpeg 没给出有效 Duration）：{path}')
    return d


def _replace_shot_audio(src: str, audio: Path) -> str:
    """Point the temporary screenshot page at the exact mux audio file."""
    uri = audio.resolve().as_uri()
    tag, _lo, end = _audio_block(src)
    if not tag:
        raise _fail('截图页缺少 #main-audio，无法让画面与 --audio 使用同一时钟')
    # 找不到 </audio> 就退到开标签末尾（不动块内）：退到全文末尾会把后文里
    # 每个 <source>（页面若挂了多档音源）都改写成旁白。闭合标签也认注释（真闭合前的
    # ghost `</audio>` 不会截短块）。
    block = src[tag.start():end]
    audio_tag_end = block.find('>')
    opening = block[:audio_tag_end + 1]
    # (?<![-\w])：\b 在 '-' 与 's' 之间也成立，裸 \bsrc= 会误改 data-src=
    # lambda 替换：re.sub 的替换串里反斜杠会被解释成转义，POSIX 文件名合法
    # 含 `\` 时直接拼 f'src="{uri}"' 会拆坏路径。
    if re.search(r'(?<![-\w])src=["\'][^"\']*["\']', opening, re.I):
        opening = re.sub(r'(?<![-\w])src=["\'][^"\']*["\']',
                         lambda _m: f'src="{uri}"', opening, count=1, flags=re.I)
    sources = re.sub(
        r'(<source\b[^>]*?(?<![-\w])src=["\'])[^"\']*(["\'])',
        lambda _m: _m.group(1) + uri + _m.group(2),
        block[audio_tag_end + 1:],
        flags=re.I,
    )
    if not re.search(r'(?<![-\w])src=["\'][^"\']*["\']', opening, re.I) and not re.search(
        r'<source\b[^>]*?(?<![-\w])src=["\']', sources, re.I
    ):
        opening = opening[:-1] + f' src="{uri}">'
    block = opening + sources
    return src[:tag.start()] + block + src[end:]


def _build_shot_page(src: str, out_path: Path, page_dir: Path, audio: Path) -> None:
    """截图页写在临时目录：交付目录不留副本。

    页面相对资源（音频/CSS/runtime）靠注入的 <base> 解析回真实页面目录，
    否则 audio.duration 取不到、每一帧都会截到同一张 idle 画面。
    """
    src = _replace_shot_audio(src, audio)
    # 与 #main-audio/#stage 同一防御：注释里的 ghost `</body>` 排在真闭合之前
    # 会把截图脚本注进注释——旗标虽在、seek 脚本却永不执行，N 帧全截成 idle。
    body_close = _first_outside_comment(r'</body\s*>', src)
    if not body_close:
        raise _fail('页面没有 </body>，无法在末尾挂截图脚本；请检查页面骨架')
    head = _first_outside_comment(r'<head\b[^>]*>', src)
    if not head:
        # 没有 <head> 时 <base> 与 flag 只能挂到 </body> 前——那时 <audio> 早已
        # 按临时目录解析、门禁逻辑也已跑完，产出的是静默错帧。硬失败，别赌。
        raise _fail('页面没有 <head>，截图模式无法在页面脚本之前生效；请补齐标准骨架（见 references/layout.md）')
    base = page_dir.resolve().as_uri().rstrip('/') + '/'
    insert = f'\n<base href="{base}">\n{SHOT_MODE_FLAG}\n'
    i = head.end()
    out = src[:i] + insert + src[i:body_close.start()] + \
        SNIPPET % {'pad': STAGE_PAD} + src[body_close.end():]
    out_path.write_text(normalize_meta_charset(out), encoding='utf-8')


def _frame_plan(sentences: list[tuple[float, float, int]], wav_dur: float) -> list[tuple[float, float, int]]:
    """Return (capture_time, display_duration, scene_index) entries.

    The first entry preserves leading silence instead of silently shifting the
    first visual to t=0. The final duration is measured against the real audio
    length, so the plan always covers [0, wav_dur].
    """
    if not math.isfinite(wav_dur) or wav_dur <= 0:
        raise ValueError('wav_dur 必须是正的有限数值')
    previous_start = -math.inf
    previous_end = -math.inf
    for start, dur, _ in sentences:
        # start/duration 的有限性与非负性由 _timeline_sentences 在页面边界统一拦下；
        # 这里只核句与句之间的关系——帧计划依赖单调、不重叠、不出音频时长。
        if start < previous_start:
            raise ValueError('句子时间必须按 start 递增')
        # 重叠容差与 build_timeline/check_gates 的句间校验共用 _contracts.TIMING_EPS：
        # 更严的 0.001 会让过了检查的页面在导出这一步硬失败。
        if start < previous_end - TIMING_EPS:
            raise ValueError('句子时间区间不能重叠')
        if start + dur > wav_dur + 0.05:
            raise ValueError('句子结束点超过旁白音频时长')
        previous_start = start
        previous_end = start + dur
    plan: list[tuple[float, float, int]] = []
    first_start = max(0.0, sentences[0][0])
    if first_start > 0.001:
        plan.append((0.01, first_start, -1))
    for i, (start, dur, scene_idx) in enumerate(sentences):
        end = start + dur
        # Capture just before the sentence ends, but keep very short sentences
        # inside their own interval rather than clamping into the previous one.
        lead = min(EPS, max(dur * 0.5, 0.001))
        t_cap = max(0.001, end - lead)
        nxt = sentences[i + 1][0] if i + 1 < len(sentences) else wav_dur
        show = max(nxt - start, 0.0)
        plan.append((t_cap, show, scene_idx))
    return plan


def main() -> int:
    setup_stdio()   # 必须在 parse_args 之前：Windows 管道下 help/usage 也是中文
    ap = argparse.ArgumentParser(description='课件 → 线性 MP4（逐句定格 + 按时长拼接 + 旁白混音）')
    ap.add_argument('page', help='课件目录或页面 .html 路径（与 check_gates.py 同一口径）')
    ap.add_argument('-o', '--output', help='默认 <课件目录名>.mp4，写在课件目录同级')
    ap.add_argument('--audio', help='旁白音频，默认取页面 #main-audio 的 src')
    ap.add_argument('--chrome', help='Chrome/Edge 可执行文件路径，默认自动探测')
    ap.add_argument('--width', type=int, default=1000, help='舞台 SVG Capture 宽度像素（默认 1000）')
    ap.add_argument('--scale', type=int, default=2, help='设备缩放系数，2 即输出约 2x 分辨率')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--crf', type=int, default=20, help='x264 质量，越小越清晰（默认 20）')
    ap.add_argument('--keep', action='store_true',
                    help='保留临时目录（逐帧 PNG + 截图页副本 _shot.html）供排查；默认全部清理')
    args = ap.parse_args()

    page = Path(args.page)
    # 传文件路径时 parent 若是相对的 '.'，默认输出名会取成 '.mp4'——
    # 先 resolve 再取 parent，默认名恒为 <课件目录名>.mp4；目录分支同样
    # 归一，否则默认成片会相对 CWD 落点而不是挂在课件目录旁边。
    # 与 check_gates 同一口径：显式传某个 .html 就以它为目标页面，
    # 静默换回同目录 index.html 会导出错页面。
    page = page.resolve()
    if page.is_dir():
        page_dir = page
        html_path = page_dir / 'index.html'
    else:
        html_path = page
        page_dir = page.parent
    if not html_path.exists():
        raise _fail(f'找不到页面：{html_path}')
    src = read_text(html_path)

    out = Path(args.output) if args.output else page_dir.parent / f'{page_dir.name}.mp4'
    guard_not_in_skill_dir(('-o/--output', os.path.abspath(out)),
                           tip='导出成片是制作产物，请用 -o 指定技能目录之外的绝对路径。')
    # 父目录不存在就早点建好：ffmpeg 不会代劳，等 33 帧截完再在混流一步失败太亏。
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise _fail(f'输出目录不可用：{out.parent}（{e}）')
    if out.exists():
        print(f'[warn] 输出已存在，将被覆盖：{out}', file=sys.stderr)

    chrome = args.chrome or _find_chrome()
    if not chrome:
        raise _fail('未找到 Chrome/Edge，无法逐句截图（可用 --chrome 指定路径）')
    ffmpeg = get_ffmpeg()
    if not (shutil.which(ffmpeg) or Path(ffmpeg).exists()):
        raise _fail('未找到可用的 ffmpeg（系统 PATH 与 imageio-ffmpeg 回退都没有），无法编码视频')
    # 沙箱优先，起不来才退回 --no-sandbox（与 check_gates 同一预检）；
    # 逐帧截图不传 --allow-file-access-from-files，成片不需要它。
    extra_flags = _browser_preflight(chrome)
    if extra_flags is None:
        raise _fail('headless Chrome 预检未通过（带沙箱与 --no-sandbox 退路均未成功）')

    # #stage 是导出的硬前提：缺 id 时截图 snippet 会静默停在 idle 画面，
    # 产出 N 帧全同的成片。先报错，别烧几十次 Chrome 再赌。
    if not _first_outside_comment(r'(?<![-\w])id=["\']stage["\']', src):
        raise _fail('页面缺少 #stage（舞台容器 id，data-state 挂在这里）；'
                    '导出与 QA 都依赖该钩子，见 references/runtime.md §7')
    # 截图模式的压平/隐藏 CSS 按模板结构类名逐条写死（见 SNIPPET）：id 钩子
    # 保证时间轴跑得动，但成片入镜什么由类名决定。类名缺失有两种可能——页面
    # 真的没有那块（无害）或换了别的名字（侧栏/播放器行整程入镜，且帧帧相同，
    # "所有帧完全相同"兜底拦不住）。静态分不清这两种，所以只 warn，
    # 换过结构的页面必须人工核对一帧成片。
    _cls_tokens = set()
    for _m in re.finditer(r'''class=(["'])(.*?)\1''', src, re.I):
        _cls_tokens.update(_m.group(2).split())
    _missing_cls = [c for c in ('shell', 'stage', 'rack', 'col-side', 'chapter')
                    if c not in _cls_tokens]
    if _missing_cls:
        print('[warn] 页面元素上未出现截图模式依赖的结构类名：'
              + '、'.join('.' + c for c in _missing_cls)
              + '；若这些块其实存在只是换了名，侧栏/播放器行会整程入镜——'
                '请人工核对一帧成片（依赖清单见 references/runtime.md §7）')

    audio = Path(args.audio) if args.audio else \
        page_dir / posixpath.normpath(
            # 与 check_gates 静态侧同构：src 可带 ?query 与 #fragment
            _audio_src(src).replace('\\', '/').split('?', 1)[0].split('#', 1)[0])
    if not audio.exists():
        raise _fail(f'旁白音频不存在：{audio}')
    wav_dur = _media_dur(audio, ffmpeg)

    sentences = _timeline_sentences(src)
    last_end = sentences[-1][0] + sentences[-1][1]
    if abs(last_end - wav_dur) > 0.5:
        print(f'[warn] 时间轴结束点 {last_end:.2f}s 与音频 {wav_dur:.2f}s 相差 '
              f'{abs(last_end - wav_dur):.2f}s：-shortest 对 concat demuxer 并不可靠，'
              '成片音画可能不齐（混流后有时长断言兜底，超 0.35s 会直接失败）')
    if last_end > wav_dur + 0.05:
        raise _fail(f'时间轴最后一句结束于 {last_end:.2f}s，但旁白只有 {wav_dur:.2f}s；拒绝导出截断音画')
    vw, vh = _view_box(src)
    win_w = int(args.width + STAGE_PAD * 2)
    win_h = int(round(args.width * vh / vw + STAGE_PAD * 2))

    tmp = Path(tempfile.mkdtemp(prefix='courseware-video-'))
    try:
        # mkdir 也放进 try：建目录失败（磁盘/权限）不能让临时目录逃出 finally。
        frames = tmp / 'frames'
        frames.mkdir()
        shot_html = tmp / '_shot.html'
        digests: list[str] = []
        frame_scenes: list[int] = []
        _build_shot_page(src, shot_html, page_dir, audio)
        lines = []
        try:
            plan = _frame_plan(sentences, wav_dur)
        except ValueError as exc:
            raise _fail(f'无法生成逐句帧计划：{exc}')
        for i, (t_cap, show, scene_idx) in enumerate(plan):
            png = frames / f'shot_{i:03d}.png'
            url = shot_html.resolve().as_uri() + f'?shot={t_cap:.3f}'
            # 每帧一个全新 user-data-dir：共用一份 profile 时，上一帧的 Chrome
            # 若还没退干净，下一帧会撞上 "profile in use"（或连上濒死实例），
            # 表现为整轮导出偶发的 rc≠0 / 陈旧截图。
            cmd = [
                chrome, '--headless=new', '--disable-gpu', '--no-first-run',
                '--no-default-browser-check', '--disable-dev-shm-usage',
                '--hide-scrollbars', f'--force-device-scale-factor={args.scale}',
                f'--window-size={win_w},{win_h}',
                f'--user-data-dir={tmp / f"profile_{i:03d}"}',
                # 预算必须罩得住 SNIPPET 里 40×150ms 的音频就绪重试，否则慢磁盘 /
                # 大 WAV 时 seek 被掐断，开头几帧全是未定位的 idle 画面。
                '--virtual-time-budget=7000', f'--screenshot={png}',
            ] + extra_flags + [url]
            try:
                rc_chrome = _run_chrome(cmd, tmp / 'shot-stdout.log', tmp / 'shot-stderr.log',
                                        timeout=120)
            except subprocess.TimeoutExpired:
                raise _fail(f'第 {i + 1} 帧截图超过 120s，Chrome 进程树已清理')
            except ChromeLaunchError as exc:
                raise _fail(f'第 {i + 1} 帧截图无法启动浏览器：{exc}')
            if rc_chrome != 0 or not png.exists():
                err = ''
                log = tmp / 'shot-stderr.log'
                if log.exists():
                    err = log.read_text(encoding='utf-8', errors='replace')[-400:]
                raise _fail(f'第 {i + 1} 帧截图失败 rc={rc_chrome} {err}')
            digests.append(hashlib.md5(png.read_bytes()).hexdigest())
            frame_scenes.append(scene_idx)
            lines.append(f"file '{quote_ffpath(png.as_posix())}'\nduration {show:.3f}")
            label = 'pre-roll' if scene_idx < 0 else str(scene_idx)
            print(f'[frame {i + 1:2d}/{len(plan)}] t={t_cap:7.2f} show={show:6.3f}s scene={label}',
                  flush=True)
        if len(sentences) >= 2 and len(set(digests)) < 2:
            raise _fail('所有帧完全相同：页面很可能没有响应 currentTime（检查 #stage/#main-audio '
                        '契约与 tick 逻辑），已中止以免产出静默错误的成片')
        # 开头几帧专项体检：音频比虚拟时间预算加载得慢时，seek 的 40×150ms
        # 重试会被预算掐断，成片开头几帧可能全是同一张 idle 画面——全帧查重
        # 拦不住它（后面的帧是正常的）。跨场景仍与首帧逐像素相同 = 基本必是坏帧。
        try:
            first_other = next(i for i, sc in enumerate(frame_scenes[1:], 1)
                               if sc != frame_scenes[0])
        except StopIteration:
            first_other = -1
        if first_other > 0 and digests[first_other] == digests[0]:
            print(f'[warn] 第 {first_other + 1} 帧（已换场）与第 1 帧完全相同：'
                  '页面可能没等到音频就绪（seek 未生效），开头几秒会是静止画面；'
                  '用 --keep 复查帧，必要时放慢音频或预加载', file=sys.stderr)
        # 帧列表必须覆盖整条音轨，且成片时长精确等于音频时长。concat demuxer 对
        # 末尾条目的时长处理不可靠（末项 duration 被忽略、末帧按内部规则续播，
        # 实测随条目数/前序时长漂移），因此：
        #   1) 循环已给每帧写了 duration，这里再补一行"末帧无 duration"的重复项，
        #      让视频流**只多不少**地覆盖到音轨末尾——缺它时末帧会被 demuxer 截短，
        #      成片尾部音画错位（音频被一起截短是静默的，比多出静帧更难发现）；
        #   2) 时长控制在下方"混流 + 流拷贝二次裁剪"两步完成（见该处注释），
        #      这里不再用 -t / -shortest 参与混流。
        lines.append(f"file '{quote_ffpath((frames / f'shot_{len(plan) - 1:03d}.png').as_posix())}'")
        listf = tmp / 'list.txt'
        # 恒 LF：Windows 裸写会翻译 CRLF，concat demuxer 对行尾 \r 的容忍是
        # "实测恰好没事"级别的；与 _audio.concat_audio 同一约定。
        write_text_atomic(str(listf), '\n'.join(lines))

        # 混流先落隐藏临时文件、验证通过再原子替换：成片若带尾帧缺陷（视频比
        # 音轨长出一截或短了一截）不能留在交付位置。
        #   1) 帧列表结构：循环给每帧写了 duration，末尾再补一行"末帧无 duration"
        #      的重复项——concat demuxer 会忽略**列表最后一条**的 duration（实测按
        #      条目数/前序时长给末帧续播一个不确定的时长），补重复项能让"真正的
        #      末帧"退居倒数第二条，其 duration 被 demuxer 用作重复项的起始时间，
        #      从而保证视频流 ≥ 音轨长度（只多不少）；
        #   2) 混流不要带 -t / -shortest：-shortest 对 concat demuxer 实测不裁尾巴
        #      （"最短输入"判定拿不到可靠 EOF）；-t 会让 concat 视频流提前结束
        #      （115.2s 的合成被截成 112.73s）；二者同用触发提前截断更严重
        #      （112.77s）。正确做法是先无约束混流，再用流拷贝二次裁剪；
        #   3) 二次裁剪：ffmpeg -t <wav_dur> -c copy（只重封装、秒级完成），
        #      把视频精确压到音频长度（末尾最多多一帧 0.03s 的容差）；
        #   4) 裁剪后断言 |成片时长 − 音频时长| ≤ 0.35s：若 concat 把视频流压得
        #      比音频还短，裁剪救不了（会把音频一起截短），必须显式失败而非静默交付。
        mux_tmp = out.with_name(out.name + '.mux.tmp.mp4')
        trim_tmp = out.with_name(out.name + '.trim.tmp.mp4')

        def _cleanup_tmp():
            for p in (mux_tmp, trim_tmp):
                try:
                    p.unlink()
                except OSError:
                    pass

        try:
            r = subprocess.run([
                ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', str(listf),
                '-i', str(audio),
                '-c:v', 'libx264', '-crf', str(args.crf), '-preset', 'medium',
                '-r', str(args.fps), '-pix_fmt', 'yuv420p',
                '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
                str(mux_tmp)], capture_output=True, timeout=1800)
        except subprocess.TimeoutExpired:
            _cleanup_tmp()
            raise _fail('最终编码超过 1800s 未归，中止（--keep 复查帧与 list.txt）')
        if r.returncode != 0:
            _cleanup_tmp()
            err = (r.stderr or b'').decode('utf-8', 'ignore')[-1200:]
            raise _fail(f'ffmpeg 编码失败：{err}')
        try:
            r2 = subprocess.run([
                ffmpeg, '-y', '-i', str(mux_tmp), '-t', f'{wav_dur:.3f}',
                '-c', 'copy', str(trim_tmp)], capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            _cleanup_tmp()
            raise _fail('成片时长裁剪超过 600s 未归，中止（--keep 复查帧与 list.txt）')
        if r2.returncode != 0:
            _cleanup_tmp()
            err = (r2.stderr or b'').decode('utf-8', 'ignore')[-1200:]
            raise _fail(f'ffmpeg 时长裁剪失败：{err}')
        out_dur = _media_dur(trim_tmp, ffmpeg)
        if abs(out_dur - wav_dur) > 0.35:
            _cleanup_tmp()
            direction = '比旁白音频长' if out_dur > wav_dur else '比旁白音频短'
            raise _fail(f'成片时长 {out_dur:.2f}s {direction} {wav_dur:.2f}s '
                        f'（相差 {abs(out_dur - wav_dur):.2f}s）：画面与音轨不同步。'
                        '请用 --keep 复查 concat 列表（list.txt）与帧计划——'
                        '若视频比音频短，说明 concat demuxer 截短了末帧，'
                        '成片尾部旁白会被一起截掉，必须修复后重导')
        os.replace(trim_tmp, out)
        try:
            mux_tmp.unlink()
        except OSError:
            pass
        print(f'[done] {out}  {out_dur:.1f}s  '
              f'{out.stat().st_size / 1e6:.1f} MB')
    finally:
        if args.keep:
            print(f'[keep] 帧、截图页副本与日志保留在 {tmp}')
        else:
            # Windows 刚杀掉 Chrome 时 profile_* 目录可能仍被短暂占用，
            # 单次 ignore_errors rmtree 会把删不掉的静默留成 %TEMP% 残留。
            _rmtree_retry(tmp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
