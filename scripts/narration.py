#!/usr/bin/env python3
"""Courseware Studio — TTS 能力适配器：旁白脚本 → 音频 + 时间轴。

职责只有三件：**把文本念出来、拼成一条音轨、给出每句的起止时间。**
页面结构、配色、布局、是否播放、怎么播放都由 Agent 在自己的 HTML 里决定——
本脚本不规定页面长什么样，也不持有任何"页面应该长这样"的假设。

输入（旁白脚本，由 Agent 手写这一份即可）：

    普通段落：
    {
      "title": "主题",
      "opening": "开场白。",
      "segments": [
        {"id": "seg-1", "title": "小节名", "text": "这一节要念的话。",
         "hl": [2],
         "voice_id": "冰糖", "speed": 1.2}
      ]
    }

    `text` 分句后就地成为字幕与时间轴；`hl` 是结论句的句序号（从 1 数起），
    只由 build_timeline 透传给页面用作强调色。`speed` 默认 1.0（原速），逐段可覆盖。

    多人对话：顶层提供 speakers，段落上用 dialogue（每一轮单独分句，各自用各自音色）。

输出（写到 -o 目录）：

    combined.wav            整段音轨
    narration_timing.json   逐句起止时间（内联进 HTML 用；file:// 下不能 fetch）
"""
import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _audio import (apply_speed, concat_audio, generate_silence,  # noqa: E402
                    get_ffmpeg, measure_duration, _remove_quiet)
from _contracts import (DEFAULT_CHARS_PER_SEC, DEFAULT_GAP, DEFAULT_SPEED,  # noqa: E402
                        SCHEMA_VERSION, VOICE_IDS, closing_title, opening_title,
                        require_finite_number, segment_step_id, validate_speed)
from _env import get_key, resolve_model_config  # noqa: E402
from _script_utils import (guard_not_in_skill_dir, is_inside, read_text,  # noqa: E402
                           setup_stdio, split_sentences, write_text_atomic)

# 超长句提醒阈值：写稿时一句一口气念得完最好；超过只 warn 不拦截。
LONG_SENTENCE_CHARS = 45

# 单句 TTS 的应用层重试次数（合成器内所有失败路径共用，不给 CLI 开口径）。
MAX_RETRIES = 3

# 句间静音上限：gap 会乘在整稿所有句间切换上（200 句 × 10s 就是 30+ 分钟纯静音），
# 给得更大几乎一定是参数写错，而不是作者真想留白。
MAX_GAP_SECONDS = 10.0

# 分句预览最多打印多少句（长稿件逐句打印会刷屏，淹没真正的错误信息）。
_PREVIEW_LIMIT = 10


# ===================================================================
# 一、旁白脚本 → 句子列表 + 段落分组
# ===================================================================
@dataclass
class Block:
    """一个待合成段落：sentences 是它分好的句子；turns 非空表示多人对话段落。"""
    id: str
    title: str
    tagline: str
    sentences: List[str]
    extra: Dict = field(default_factory=dict)
    turns: List[Dict] = field(default_factory=list)


def _collect_dialogue_sentences(dialogue, speakers, seg_index, seg_title):
    """把段落的 dialogue（多轮对话）拆成扁平句子 + 段内局部 turns。

    每一轮独立分句（避免"短句被并入下一轮"这类跨轮错位）；turns 记录每轮在本段内
    的句子区间 + 说话人信息（voice_id/voice_style 就近解析，下游不必再查 speakers）。
    """
    sents, turns = [], []
    for j, turn in enumerate(dialogue, 1):
        spk = turn.get("speaker")
        raw_text = turn.get("text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise ValueError(f"第 {seg_index} 段（title={seg_title!r}）"
                             f"dialogue[{j}].text 必须是字符串")
        t_text = (raw_text or "").strip()
        if not t_text:
            raise ValueError(f"第 {seg_index} 段（title={seg_title!r}）"
                             f"dialogue[{j}]（speaker={spk!r}）的 'text' 为空")
        t_sents = split_sentences(t_text)
        if not t_sents:
            raise ValueError(f"第 {seg_index} 段（title={seg_title!r}）"
                             f"dialogue[{j}]（speaker={spk!r}）分句后为空，"
                             "请检查文本是否以终止标点（。！？）结尾")
        spk_cfg = (speakers or {}).get(spk, {})
        start = len(sents)
        sents.extend(t_sents)
        turns.append({
            "start": start, "end": len(sents), "speaker": spk,
            "label": spk_cfg.get("label") or spk,
            "voice_id": spk_cfg.get("voice_id"),
            "voice_style": spk_cfg.get("voice_style"),
        })
    return sents, turns


def _collect_blocks(source, default_speed):
    """把结构化 source 组装成 Block 列表（opening / segments / closing）。"""
    blocks: List[Block] = []
    # opening/closing 不传显式覆盖时跟随全局 --speed（default_speed），而不是钉死
    # 1.0：整稿调语速时开场/收尾不该掉队。

    def _extra(seg, fallback_speed):
        extra = {}
        speed = seg.get("speed", fallback_speed)
        if speed is not None:
            extra["speed"] = speed
        for key in ("voice_id", "voice_style"):
            if seg.get(key) is not None:
                extra[key] = seg[key]
        return extra

    opening_text = (source.get("opening") or "").strip()
    if opening_text:
        blocks.append(Block(
            id="opening",
            title=opening_title(source),
            tagline=(source.get("opening_tagline") or "").strip(),
            sentences=split_sentences(opening_text),
            extra={"speed": source.get("opening_speed", default_speed)},
        ))

    raw_segments = source.get("segments", [])
    if not raw_segments:
        raise ValueError("source 中 'segments' 为空，至少需要一条内容段落")

    speakers = source.get("speakers") or {}
    for i, seg in enumerate(raw_segments, 1):
        title = seg.get("title", "")
        dialogue = seg.get("dialogue")
        turns = []
        if dialogue:
            sents, turns = _collect_dialogue_sentences(dialogue, speakers, i, title)
        else:
            text = (seg.get("text") or "").strip()
            if not text:
                raise ValueError(f"第 {i} 段（title={title!r}）的 'text' 字段为空")
            sents = split_sentences(text)
            if not sents:
                raise ValueError(f"第 {i} 段（title={title!r}）分句后为空，"
                                 "请检查文本是否以终止标点（。！？）结尾")
        blocks.append(Block(
            id=segment_step_id(seg, i),
            title=title,
            tagline=seg.get("tagline", ""),
            sentences=sents,
            extra=_extra(seg, default_speed),
            turns=turns,
        ))

    closing_text = (source.get("closing") or "").strip()
    if closing_text:
        blocks.append(Block(
            id="closing",
            title=closing_title(source),
            tagline=(source.get("closing_tagline") or "").strip(),
            sentences=split_sentences(closing_text),
            extra={"speed": source.get("closing_speed", default_speed)},
        ))
    return blocks


def build_parts(source, default_speed):
    """把结构化 source 转成 (sentences, segments)，供 main 使用。

    每段**独立**分句（不拼接成整篇再重分句）——结构化输入下每段是独立字符串，
    跨段短句合并结构上不可能发生。

    Returns:
        sentences: list[str]，按段落顺序排列的全部句子
        segments: 段落分组（id/title/tagline/start/end + 可选 speed/voice_*/turns）
    """
    blocks = _collect_blocks(source, default_speed)
    # 逐段 / opening / closing 的 speed 也要过同一道校验：写进 JSON 的值是手敲的，
    # 类型或取值非法（0 / 负数 / "1.2"）会在 synth_sentence 里炸出 TypeError，
    # 而那一行在 try 之外。在这里拦下，报错直接指到出错的段落。
    for blk in blocks:
        sp = blk.extra.get("speed")
        if sp is None:
            continue
        try:
            blk.extra["speed"] = validate_speed(sp)
        except ValueError as e:
            raise ValueError(f"段落 {blk.id}（title={blk.title!r}）的 speed 非法：{e}")
    sentences = [s for blk in blocks for s in blk.sentences]

    segments, cursor = [], 0
    for blk in blocks:
        start, end = cursor, cursor + len(blk.sentences)
        cursor = end
        seg = {"id": blk.id, "title": blk.title, "tagline": blk.tagline,
               "start": start, "end": end}
        seg.update(blk.extra)
        if blk.turns:
            # 段内局部区间 → 全局区间，供按句覆盖音色 + 记录说话人标签。
            seg["turns"] = [
                {"start": start + t["start"], "end": start + t["end"],
                 "speaker": t["speaker"], "label": t["label"],
                 **({"voice_id": t["voice_id"]} if t["voice_id"] else {}),
                 **({"voice_style": t["voice_style"]} if t["voice_style"] else {})}
                for t in blk.turns
            ]
        segments.append(seg)

    for idx, s in enumerate(sentences, 1):
        if len(s) > LONG_SENTENCE_CHARS:
            print(f"[warn] 第 {idx} 句长达 {len(s)} 字（>{LONG_SENTENCE_CHARS}），"
                  f"念出来偏喘不过气：{s[:24]}…建议写稿时在逗号处拆成两句",
                  file=sys.stderr)
    return sentences, segments


# ===================================================================
# 二、MiMo TTS 单句合成
# ===================================================================
class BadAudioResponseError(Exception):
    """TTS 响应里没有音频（chat.completions 返回了纯文本）。

    几乎总是 --base-url/--model 指向了不支持 audio 参数的网关或模型，重试 N 次
    结果完全一样。必须整句放弃并让调用方尽早终止。
    """


def _is_non_retryable(exc):
    """重试也不会好的确定性失败：400/401/403/404/422 与"响应不含音频"。
    注意这只决定**单句不重试**；要不要短路整池看 _is_config_fatal。"""
    if isinstance(exc, BadAudioResponseError):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in (400, 401, 403, 404, 422)


def _is_config_fatal(exc):
    """配置类的确定性失败（鉴权 / 模型 / 地址口径，script.md §3）：这一句、
    其余每一句都必然同样输，才值得短路整池、立即中止。
    400 不在名单里：内容审核 / 超长拒答是**逐句**拒绝——最常见的一档失败，
    --on-fail silence 应该给它落静音占位，而不是让一句稿子炸掉整稿。"""
    if isinstance(exc, BadAudioResponseError):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in (401, 403, 404, 422)


def _retry_backoff_seconds(exc, attempt):
    """重试等待：服务端给了可解析的 Retry-After（429 限流窗口）就照它的数，
    否则线性退避 2*(attempt+1)s。

    只采纳 [0, 120] 秒内的提示：再长的 Retry-After 意味着这一轮必然跑不完，
    让重试耗尽走 abort 路径比让 worker 静默睡几分钟更容易诊断。
    """
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            raw = headers.get("retry-after")
            if raw is not None:
                secs = float(raw)
                if 0.0 <= secs <= 120.0:
                    return secs
        except (AttributeError, TypeError, ValueError):
            pass
    return 2 * (attempt + 1)


def synth_sentence(client, text, voice_id, voice_style, out_path,
                   ffmpeg_path, speed,
                   sentence_label, model, api_timeout,
                   fatal_event):
    """合成一句话到 out_path，并按 speed 做确定性变速。返回 (ok, speed_applied)。

    ok：音频是否成功落盘；speed_applied：atempo 是否落上（ok=True 而
    speed_applied=False 时音频有效但仍是原速）。

    fatal_event：线程池共享的 threading.Event。任一请求命中确定性失败
    （key 不对、模型不含音频……）就置位，其余任务在发起 API 调用前检查并
    短路返回——4 个 worker 各自烧完 3 次重试纯属浪费。

    MiMo TTS 走 chat completions 格式：user 角色放音色风格描述（可选），
    assistant 角色放要念的文本，audio 参数指定格式与音色，音频以 base64 WAV
    形式返回在 choices[0].message.audio.data。
    """
    messages = []
    if voice_style:
        messages.append({"role": "user", "content": voice_style})
    messages.append({"role": "assistant", "content": text})

    audio_params = {"format": "wav"}
    if voice_id:
        audio_params["voice"] = voice_id

    for attempt in range(MAX_RETRIES):
        if fatal_event.is_set():
            return False, False
        try:
            completion = client.chat.completions.create(
                model=model, messages=messages, audio=audio_params,
                timeout=api_timeout,
            )
            # 显式检查响应结构而不是直接下钻 .data：端点/模型配错时 message.audio
            # 为 None，AttributeError 不带 status_code 会被归为可重试，白烧额度。
            # choices 缺失/为空同理——网关回了空结果，重试必然还是一样。
            choices = getattr(completion, "choices", None)
            if not choices:
                raise BadAudioResponseError(
                    "TTS 响应没有 choices（网关返回空结果）——"
                    "检查 --model/--base-url 是否指向可用的 TTS 模型")
            # message 缺失/形状不对同样不是可重试的网络抖动：getattr 防住
            # AttributeError 被归为可重试、白烧满 MAX_RETRIES。
            audio_obj = getattr(getattr(choices[0], "message", None), "audio", None)
            audio_data = getattr(audio_obj, "data", None) if audio_obj else None
            if not audio_data:
                raise BadAudioResponseError(
                    "TTS 响应不含音频（chat.completions 返回了纯文本）——"
                    "检查 --model/--base-url 是否指向支持 audio 参数的 TTS 模型"
                    "（默认 mimo-v2.5-tts），不要指向普通对话模型")
            wav_bytes = base64.b64decode(audio_data)
            # 只认 WAV 容器：非 RIFF 的响应（网关回了别的编码）写进缓存后，
            # resume 轮次靠 measure_duration 才拦得住，坏文件已污染缓存。
            if wav_bytes[:4] != b"RIFF":
                raise BadAudioResponseError(
                    "TTS 返回的音频不是 WAV 容器（首 4 字节非 RIFF）——"
                    "检查 --base-url/--model 返回的 audio.format")
            try:
                with open(out_path, "wb") as f:
                    f.write(wav_bytes)
            except OSError as e:
                # 本地写盘失败（磁盘满/权限）不是可重试的网络抖动：留在下面的
                # 宽泛 except 里会重发整次 TTS 请求，单句白烧满 MAX_RETRIES 额度。
                print(f"    [{sentence_label}][io] 写入缓存失败，不重试以免重复烧额度：{e}",
                      flush=True)
                return False, False
            break
        except Exception as e:  # noqa: BLE001 — 下面按异常类型分流
            if _is_non_retryable(e):
                # 两级分流：配置类（401/403/404/422、模型不含音频）短路整池；
                # 逐句拒答（典型 400 内容审核）只弃这一句，让 --on-fail silence 兜底。
                config_fatal = _is_config_fatal(e)
                print(f"    [{label}][{'fatal' if config_fatal else 'reject'}] {e}"
                      f"（{'配置错误，短路后续全部调用' if config_fatal else '该句确定性失败，不重试'}）",
                      flush=True)
                if config_fatal:
                    fatal_event.set()
                return False, False
            print(f"    [{sentence_label}][retry {attempt+1}/{MAX_RETRIES}] {e}", flush=True)
            if attempt < MAX_RETRIES - 1:
                # Retry-After 优先（429 限流窗口），否则线性退避；都再加抖动：
                # 多 worker 同步休眠同步唤醒会一起撞限流窗口。
                time.sleep(_retry_backoff_seconds(e, attempt) + random.uniform(0.0, 1.0))
    else:
        return False, False

    # speed≈1 时根本不需要 atempo，音频就是所请求的语速：先判这条，
    # 否则"ffmpeg 缺失 + 原速"会被记成变速未落上，指纹永远不写、每轮白重烧。
    if abs(speed - 1.0) <= 0.01:
        return True, True
    if not ffmpeg_path:
        print(f"    [{sentence_label}][speed-skip] "
              f"ffmpeg 不可用，跳过变速（音频保持原速）", file=sys.stderr, flush=True)
        return True, False
    # 变速失败保留原速音频即可（时长由实测决定，时间轴仍然准确）；重调 TTS 只会白烧额度。
    try:
        return True, apply_speed(ffmpeg_path, out_path, speed)
    except Exception as e:  # noqa: BLE001
        print(f"    [{sentence_label}][speed-skip] "
              f"atempo 变速失败，保留原始语速: {e}", flush=True)
        return True, False


def _sentence_hash(text, voice_id, voice_style, model, speed, base_url):
    """一句话 TTS 输入的指纹（文本 + 音色 + 风格 + 模型 + 网关 + 语速），resume 用。

    缓存文件名按这个指纹命名（见 main 的 out_path），sidecar 再存一份用于核对。
    换网关（--base-url）几乎必然伴随音色实现/韵律的差别，旧缓存跨网关复用
    会把两个声音混进同一条音轨，所以 base_url 也进指纹。
    """
    payload = "\x1f".join([text, voice_id, voice_style,
                           model, base_url, f"{float(speed):.4f}"])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _write_sidecar(path, content):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        print(f"  [sidecar][warn] 无法写入 {os.path.basename(path)}（{e}），"
              f"下次 --resume 会重新合成该句", file=sys.stderr)


def _drop_sentence_cache(out_path):
    """清掉某句的音频与全部 sidecar（重新合成前调用）。"""
    for suffix in ("", ".sha", ".failed", ".needs-speed"):
        _remove_quiet(out_path + suffix)


def _resume_decision(out_path, ffmpeg_path, text, voice_id, voice_style, model,
                     speed, base_url):
    """--resume 时判断某句能不能跳过。返回 (action, duration)。

        "skip"        缓存可用（输入指纹一致且时长有效）
        "skip_failed" 上次已判定 TTS 失败并降级为静音——输入指纹未变则不再重试
        "speed_only"  缓存音频有效但上次 atempo 没落上（.needs-speed 标记在）
                      ——只需重施变速，不必重烧 TTS
        "regen"       没缓存 / 指纹不符 / 时长无效 → 重新合成
    """
    if os.path.exists(out_path + ".failed"):
        # 失败占位也要核对指纹：改了这句文案/音色/语速后，旧的"失败"结论
        # 对新输入不成立，必须 regen 重试，而不是永远 skip_failed 锁死静音。
        try:
            with open(out_path + ".sha", encoding="utf-8") as f:
                cached_failed_sha = f.read().strip()
        except (OSError, UnicodeDecodeError):
            cached_failed_sha = ""
        if cached_failed_sha != _sentence_hash(text, voice_id, voice_style,
                                               model, speed, base_url):
            return "regen", 0.0
        # WAV 缓存走精确样本测时，不需要 ffmpeg 二进制；若因缺失就记 0，
        # 有效缓存会永远走 regen。非 WAV 的回退路径在 measure_duration 内部。
        dur = measure_duration(ffmpeg_path, out_path)
        return ("skip_failed", dur) if dur and dur > 0 else ("regen", 0.0)

    if not os.path.exists(out_path):
        return "regen", 0.0
    try:
        with open(out_path + ".sha", encoding="utf-8") as f:
            cached = f.read().strip()
    except (OSError, UnicodeDecodeError):
        return "regen", 0.0  # 无指纹（或读不了）→ 无法确认内容是否已变，重合成
    if cached != _sentence_hash(text, voice_id, voice_style, model, speed,
                                base_url):
        return "regen", 0.0
    # 同上：WAV 精确路径不依赖 ffmpeg 二进制。
    dur = measure_duration(ffmpeg_path, out_path)
    if not (dur and dur > 0):
        return "regen", 0.0
    if os.path.exists(out_path + ".needs-speed"):
        return "speed_only", dur
    return "skip", dur


# ===================================================================
# 三、CLI
# ===================================================================
def _build_parser():
    parser = argparse.ArgumentParser(description="courseware-studio TTS 能力（旁白 → 音频 + 时间轴）")
    parser.add_argument("--source", required=True,
                        help="旁白脚本 JSON（{title, segments:[{id,title,text}]}）。"
                             "逐段独立分句，直接产出带段落分组的时间轴，Agent 手写即可。")
    parser.add_argument("-o", "--output", default=None, help="输出目录")
    parser.add_argument("--api-key", default=None,
                        help="MiMo TTS API key。建议不传、由 .env 提供 MIMO_API_KEY："
                             "命令行参数会出现在进程列表与 shell 历史里，等于泄密")
    parser.add_argument("--voice-id", default="冰糖", choices=VOICE_IDS,
                        help="音色（默认 冰糖）")
    parser.add_argument("--voice-style",
                        default="专业新闻播报，语速适中，语气沉稳自信，中英文表达流畅自然",
                        help="音色风格描述")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP,
                        help=f"句间静音秒数（默认取 _contracts.DEFAULT_GAP，"
                             f"上限 {MAX_GAP_SECONDS:g}s）")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                        help="语速倍率（ffmpeg atempo，1.0=原速=默认，1.5=快一半）")
    parser.add_argument("--resume", action="store_true",
                        help="复用 cache-dir 中输入未变的句子音频")
    parser.add_argument("--clean-output", action="store_true",
                        help="生成前清空 -o 目录（仅在确认该目录专用于本课件时使用）")
    parser.add_argument("--cache-dir", default=None,
                        help="--resume 的句子缓存目录（默认在输出目录同级的 .courseware-cache/ 下）")
    parser.add_argument("--model", default=None,
                        help="TTS 模型（默认 MIMO_TTS_MODEL 或 'mimo-v2.5-tts'）")
    parser.add_argument("--base-url", default=None,
                        help="MiMo API base URL（默认 MIMO_BASE_URL 或官方地址）")
    parser.add_argument("--api-timeout", type=float, default=30.0,
                        help="单次 TTS 调用超时（默认 30s）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只分句 + 预览，不调 TTS、不写音频")
    parser.add_argument("--workers", type=int, default=4, help="并行 TTS 调用数（默认 4）")
    parser.add_argument("--on-fail", choices=["abort", "silence"], default="abort",
                        help="单句反复失败后：abort（默认）阻断管线；silence 该句降级为"
                             "静音占位（时长按字数/语速估算），保留在时间轴与字幕位置，"
                             "时间轴对应句子带 \"synth_failed\": true")
    return parser


def _validate_args(parser, args):
    try:
        validate_speed(args.speed)
    except ValueError as e:
        parser.error(str(e))
    # 有限数值校验只写一份（_contracts.require_finite_number），CLI 侧只负责补标签；
    # 各入口手写 math.isfinite 必然与它漂移。
    for label, val, kw in (("--gap（句间静音秒数）", args.gap, {"nonnegative": True}),
                           ("--api-timeout（单次 TTS 超时秒数）", args.api_timeout, {})):
        try:
            require_finite_number(val, label, **kw)
        except ValueError as e:
            parser.error(str(e))
    # inf 能过 float()（argparse 认 "inf"），timeout=inf 会让挂死的请求永远吊住线程池。
    if args.api_timeout <= 0:
        parser.error(f"--api-timeout 必须大于 0（收到 {args.api_timeout:g}）")
    if args.workers < 1:
        parser.error(f"--workers 至少为 1（收到 {args.workers}）")
    # 上限护栏：几百并发对 TTS 网关只是 429 风暴，还会让 fatal 短路的
    # "在途请求"变成几百个白烧的窗口。
    if args.workers > 32:
        parser.error(f"--workers 最多 32（收到 {args.workers}）：并发过高只会触发限流风暴")
    # gap 乘在整稿所有句间切换上：上限拦截基本是笔误的超大值（TTS 已烧完
    # 才在 concat 处产出半小时静音的话，浪费的是真金白银）。
    if args.gap > MAX_GAP_SECONDS:
        parser.error(f"--gap 过大（{args.gap:g}s > {MAX_GAP_SECONDS:g}s）："
                     "句间静音会按整稿句数累积，长稿下几十秒的 gap 直接毁掉节奏")
    if args.cache_dir and not args.resume:
        parser.error("--cache-dir 只能与 --resume 一起使用")


def _load_script_source(path):
    """读取旁白脚本 JSON，规整成内部结构。

    唯一格式：`{title, segments:[{id,title,text,...}]}`，可选顶层 opening/closing/
    opening_title/closing_title/opening_tagline/closing_tagline/opening_speed/
    closing_speed/speakers。不做隐式兼容——格式不对就报错，不猜。
    """
    try:
        # read_text：全仓唯一一份编码探测链（utf-8-sig / utf-16 / gb18030），
        # 与 build_timeline 读同一份 narration-source.json 的判定一致。
        data = json.loads(read_text(path))
    except ValueError as e:
        raise ValueError(f"无法读取旁白脚本: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("旁白脚本必须是 {title, segments:[...]}")
    # speakers 若是数组/字符串，下游 speakers.get(spk) 会裸 AttributeError；
    # 格式错误在入口就指出来，不烧完分句再崩。判型不能被 `or {}` 吞掉：
    # [] / "" 是假值，or 之后既骗过类型守卫又静默走"未配置说话人"路径。
    speakers_raw = data.get("speakers")
    if speakers_raw is not None and not isinstance(speakers_raw, dict):
        raise ValueError("顶层 'speakers' 必须是对象（说话人 → {label,voice_id,...}）")
    # 段落循环里的未配置说话人校验要读它——必须在此之前绑定，否则 Python 按
    # 局部变量处理，dialogue 一出现就 UnboundLocalError。
    speakers = speakers_raw or {}
    for key in ("opening", "closing", "title", "opening_title", "closing_title",
                "opening_tagline", "closing_tagline"):
        if data.get(key) is not None and not isinstance(data.get(key), str):
            raise ValueError(f"顶层 '{key}' 必须是字符串")

    segs = []
    seen_ids = set()
    # --voice-id 有 argparse choices 兜底，但 JSON 里的 voice_id 绕过了它：
    # 写错一个音色名会被原样发给 TTS（400 或静默换声），在这里对着同一份
    # 名单校验，报错直接指到出错的段落。
    valid_voices = set(VOICE_IDS)

    def _check_voice(value, where):
        if value is not None and value not in valid_voices:
            raise ValueError(f"{where} 的 voice_id {value!r} 不在可用音色里："
                             f"{'、'.join(sorted(valid_voices))}")

    for index, seg in enumerate(data["segments"], 1):
        if not isinstance(seg, dict):
            raise ValueError(f"segments[{index}] 必须是对象，不能静默跳过")
        _check_voice(seg.get("voice_id"), f"segments[{index}]")
        dialogue = seg.get("dialogue")          # 多人对话：保留下来交给 _collect_blocks 展开
        raw_text = seg.get("text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise ValueError(f"segments[{index}].text 必须是字符串")
        text = (raw_text or "").strip()
        if not text and not dialogue:
            raise ValueError(f"segments[{index}] 既没有 text 也没有 dialogue，不能是空段落")
        if dialogue is not None and not isinstance(dialogue, list):
            raise ValueError(f"segments[{index}].dialogue 必须是数组")
        if text and dialogue:
            raise ValueError(f"segments[{index}] 不能同时提供 text 与 dialogue")
        if dialogue:
            # 每一轮必须是对象：字符串轮（"甲：你好"）会让 turn.get 裸崩，
            # 报错要指得到是第几段的第几轮。
            for j, turn in enumerate(dialogue, 1):
                if not isinstance(turn, dict):
                    raise ValueError(f"segments[{index}].dialogue[{j}] 必须是对象"
                                     f"（{turn!r}），说话人写 speaker 字段")
                speaker = turn.get("speaker")
                if not isinstance(speaker, str) or not speaker.strip():
                    raise ValueError(f"segments[{index}].dialogue[{j}].speaker 必须是非空字符串")
                if speakers and speaker not in speakers:
                    raise ValueError(f"segments[{index}].dialogue[{j}] 引用了未配置的说话人：{speaker!r}")
                # turn 上的音色键会被展开层静默丢弃，但键名与 speakers 里的完全
                # 同款——放行了就是"以为换了声、成品逐句 voice_id 却记着默认音色"，
                # 只能靠耳朵重听整稿才发现。当场拒收，指回唯一真源 speakers。
                for key in ("voice_id", "voice_style"):
                    if turn.get(key) is not None:
                        raise ValueError(
                            f"segments[{index}].dialogue[{j}] 自写了 {key}——"
                            f"turn 级音色只取 speakers[{speaker!r}]，要换声改那里的配置")
        sid = segment_step_id(seg, len(segs) + 1).strip()
        if not sid:
            raise ValueError(f"segments[{index}].id 不能为空")
        if sid in seen_ids:
            raise ValueError(f"segments[{index}].id 重复：{sid}")
        seen_ids.add(sid)
        for key in ("title", "tagline", "voice_style"):
            if seg.get(key) is not None and not isinstance(seg.get(key), str):
                raise ValueError(f"segments[{index}].{key} 必须是字符串")
        out = {"id": sid,
               "title": seg.get("title") or sid,
               "text": text}
        for k in ("voice_id", "voice_style", "speed", "tagline"):
            if seg.get(k) is not None:
                out[k] = seg[k]
        if dialogue is not None:
            out["dialogue"] = dialogue
        segs.append(out)
    if not segs:
        raise ValueError("旁白脚本没有可朗读的段落内容（segments[].text 全为空）")

    result = {"title": data.get("title") or data.get("opening_title") or "",
              "segments": segs}
    for k in ("opening", "closing", "opening_title", "closing_title",
              "opening_tagline", "closing_tagline",
              "opening_speed", "closing_speed", "speakers"):
        if data.get(k) is not None:
            result[k] = data[k]
    # 多人对话的音色走 speakers：同样过一遍名单（dialogue 轮次不直接带
    # voice_id，错在 speakers 一处即可拦下）。入口已把过形状关
    # （speakers_raw 要么 dict 要么 None），这里不必再判 isinstance。
    if speakers_raw is not None:
        for name, cfg in speakers_raw.items():
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"speakers[{name!r}] 必须是对象（如 {{'label': '小明', "
                    f"'voice_id': '冰糖'}}），当前是 {type(cfg).__name__}——"
                    "下游读 label/voice_id 会直接崩")
            for key in ("label", "voice_style"):
                if cfg.get(key) is not None and not isinstance(cfg.get(key), str):
                    raise ValueError(f"speakers[{name!r}].{key} 必须是字符串")
            _check_voice(cfg.get("voice_id"), f"speakers[{name!r}]")
    return result


# ===================================================================
# 四、主管线
# ===================================================================
def _mk_hidden_wav(directory, prefix):
    """建一个隐藏 .wav 临时文件路径（与交付物同目录：os.replace 原子、不跨设备）。"""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".wav", dir=directory)
    os.close(fd)
    return path


def _finalize_audio(args, ffmpeg_path, sentence_data, source_data, seg_config,
                    silence_fallback_count, total_sentences, cached_count):
    """拼接 → 写 narration_timing.json。"""
    print(f"\n[concat] {len(sentence_data)} clips (gap {args.gap}s)...", flush=True)
    combined_path = os.path.join(args.output, "combined.wav")
    # 最终交付契约永远只有 audio/combined.wav：拼接落在同目录的隐藏临时文件上
    # （同目录保证 os.replace 原子、不跨设备），成功才原子替换。
    raw_path = _mk_hidden_wav(args.output, ".combined-raw-")
    try:
        if not concat_audio(ffmpeg_path, [s["file"] for s in sentence_data], args.gap,
                            raw_path):
            print("[error] 音频拼接失败", file=sys.stderr)
            sys.exit(1)
        # concat 落在隐藏临时文件上，成功才原子替换：Ctrl-C 掐在拼接中途时
        # combined.wav 要么完整（上一版）要么不存在，不会留下半截 wav 被下游
        # （build_timeline / export_video）当成有效音频读走。
        os.replace(raw_path, combined_path)
    finally:
        # 任何异常（含 SystemExit / KeyboardInterrupt）都不把临时件留进交付目录：
        # replace 之后路径已不存在，这里是 no-op。
        _remove_quiet(raw_path)

    total_dur = measure_duration(ffmpeg_path, combined_path)
    if not total_dur or total_dur <= 0:
        # 0 时长会产出"合法但废掉"的时间轴（渲染出无声空片），在这里拦下。
        print("[error] combined.wav 时长测量失败（0.0s）——检查磁盘空间与 ffmpeg 可用性",
              file=sys.stderr)
        sys.exit(1)
    print(f"[done] 总时长 {total_dur:.2f}s", flush=True)

    # 每句起始时间：拼接实测时长 + 句间 gap 累加（时间轴的唯一来源）。
    cumulative = 0.0
    for i, sd in enumerate(sentence_data):
        sd["start_time"] = round(cumulative, 3)
        cumulative += sd["duration"]
        if i < len(sentence_data) - 1:
            cumulative += args.gap

    # 时间轴按各句实测时长累加，combined.wav 由 concat 产出（_audio 探测到格式
    # 不统一会自动重编码）。两者对不上说明拼接或测时有问题——逐句时间会整体漂移。
    if abs(total_dur - cumulative) > max(0.5, 0.02 * cumulative):
        print(f"[warn] combined.wav 实测 {total_dur:.2f}s 与时间轴累计 {cumulative:.2f}s "
              f"相差较大——逐句时间轴可能整体漂移，请检查各句音频与 ffmpeg 拼接是否正常",
              file=sys.stderr)

    # 时间轴：一句 = 一条 {start, duration, text}；一段 = 一个 scene。
    sentences_out = []
    for s in sentence_data:
        entry = {"start": float(s["start_time"]), "duration": round(float(s["duration"]), 3),
                 "text": s["text"]}
        if s.get("voice_id"):
            entry["voice_id"] = s["voice_id"]
        if s.get("speaker"):
            entry["speaker"] = s["speaker"]
        if s.get("synth_failed"):
            entry["synth_failed"] = True
        sentences_out.append(entry)

    scenes = []
    # 按句子的原始 index 切场，不按列表位置：整句被丢弃（静音兜底也失败）时
    # 位置会整体前移，用 enumerate 会把后面的句子静默切进错误段落。
    for seg in seg_config or []:
        start_idx, end_idx = seg["start"], seg["end"]   # 0-based / exclusive
        seg_sentences = [e for e, src_s in zip(sentences_out, sentence_data)
                         if start_idx <= src_s["index"] < end_idx]
        if not seg_sentences:
            # 该段所有句子都没产出音频（--on-fail silence 下连静音兜底也没落成，
            # 或段落本身被上游丢空；abort 模式在更早处就已 exit，走不到这里）。
            # 报出来，而不是写一份下游读不懂的空场景。
            print(f"\n[warn] 段落 {seg.get('id')} 没有任何可用音频，已从时间轴剔除"
                  f"（常见原因：这几段 TTS 失败且 --on-fail silence 下静音兜底也没落成，"
                  f"或该段本身为空）",
                  file=sys.stderr, flush=True)
            continue
        start = seg_sentences[0]["start"]
        end = max(e["start"] + e["duration"] for e in seg_sentences)
        sid = str(seg.get("id"))
        scenes.append({
            "step_id": sid,          # 与 interactive_runtime.js 的场景键一致
            "title": seg.get("title", ""),
            "tagline": seg.get("tagline", ""),
            "start": round(start, 3),
            "duration": round(max(0.0, end - start), 3),
            "end": round(end, 3),
            "sentences": seg_sentences,
        })

    # 整句丢弃（TTS 失败且静音兜底也没落成）同样是降级：这几句在成片里既没
    # 配音也没字幕，status 不能因为"没有占位"就报 ok。
    dropped_count = max(0, total_sentences - len(sentence_data))
    timing = {
        "schema_version": SCHEMA_VERSION,
        "status": "degraded" if (silence_fallback_count or dropped_count) else "ok",
        "title": source_data.get("title") or "",
        "total_duration": round(total_dur, 3),
        "voice_id": args.voice_id,
        # 只保留文件名：消费方按约定在同一目录下查找。
        "audio": os.path.basename(str(combined_path)),
        "scenes": scenes,
        "degraded": {"tts_silence_fallback_count": silence_fallback_count,
                     "dropped_sentence_count": dropped_count},
    }
    # 原子写：narration_timing.json 是页面内联时间轴的唯一数据源，写到一半被
    # Ctrl-C 打断会留下截断 JSON——要么完整要么不存在。
    manifest_path = os.path.join(args.output, "narration_timing.json")
    write_text_atomic(manifest_path, json.dumps(timing, ensure_ascii=False, indent=2))

    print(f"\n[manifest] {manifest_path}", flush=True)
    ok_count = len(sentence_data) - silence_fallback_count
    degraded_note = f"，{silence_fallback_count} 句静音占位" if silence_fallback_count else ""
    print(f"[stats] {ok_count}/{total_sentences} 句成功{degraded_note}"
          f"（{cached_count} 句复用缓存）", flush=True)
    print(f"[duration] {total_dur:.2f}s", flush=True)


def _clean_output_dir(path):
    """Explicitly remove a previous delivery directory before regeneration."""
    for entry in os.scandir(path):
        target = entry.path
        try:
            if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                shutil.rmtree(target)
            else:
                os.remove(target)
        except OSError as exc:
            raise SystemExit(f"[error] --clean-output 无法移除 {target}: {exc}")


def _spread_segment_overrides(seg_config, args, sentences,
                              sentence_speeds, sentence_voices, speaker_labels):
    """按段落与 turns 展开每句的语速 / 音色 / 说话人标签。"""
    for seg in seg_config:
        start_idx, end_idx = seg["start"], seg["end"]
        seg_speed = seg.get("speed")
        if seg_speed is not None:
            for si in range(start_idx, min(end_idx, len(sentences))):
                sentence_speeds[si] = seg_speed
        seg_voice_id, seg_voice_style = seg.get("voice_id"), seg.get("voice_style")
        if seg_voice_id or seg_voice_style:
            for si in range(start_idx, min(end_idx, len(sentences))):
                sentence_voices[si] = (
                    seg_voice_id or args.voice_id,
                    seg_voice_style if seg_voice_style is not None else args.voice_style,
                )
        # turns 是比段落更细的子区间：同一段里 A/B 交替发言，各自用各自音色。
        for turn in seg.get("turns", []):
            t_start, t_end = turn["start"], turn["end"]
            t_voice_id, t_voice_style = turn.get("voice_id"), turn.get("voice_style")
            t_label = turn.get("label") or turn.get("speaker")
            for si in range(t_start, min(t_end, len(sentences))):
                if t_voice_id or t_voice_style:
                    base_id, base_style = sentence_voices.get(
                        si, (args.voice_id, args.voice_style))
                    sentence_voices[si] = (
                        t_voice_id or base_id,
                        t_voice_style if t_voice_style is not None else base_style,
                    )
                if t_label:
                    speaker_labels[si] = t_label


def _make_sentence_entry(task, duration, speaker_labels, synth_failed=False):
    sd = {"index": task["index"], "text": task["text_tts"], "file": task["out_path"],
          # duration 存精确值、只在写 manifest 时舍入：起始时间按它逐句累加，
          # 这里先 round 会把亚毫秒量化误差累积成整课可见的漂移，还刚好
          # 躲在下面那道 max(0.5, 2%) 拼接偏差容差之下。
          "duration": float(duration)}
    # 逐句记录实际使用的音色：多说话人场景下顶层 voice_id 只是 CLI 默认值，
    # 事后"这句是谁的声音"否则只能拿 wav 文件名 hash 反推。
    if task.get("voice_id"):
        sd["voice_id"] = task["voice_id"]
    if task["index"] in speaker_labels:
        sd["speaker"] = speaker_labels[task["index"]]
    if synth_failed:
        sd["synth_failed"] = True
    return sd


def _synthesize_pending(args, client, ffmpeg_path, model, base_url, pending_tasks,
                        sentence_speaker_labels):
    """并行合成待处理句子。返回 (new_results, failed_indices, fatal)。

    fatal=True 表示命中"重试也不会好"的确定性失败（401 key 不对、模型不含
    音频……）：共享事件一旦置位，还没发起的任务全部短路返回，主流程据此
    立即中止，不再往一个注定失败的配置上烧额度。
    """
    new_results, failed = [], []
    total = len(pending_tasks)
    if not total:
        return new_results, failed, False

    fatal_event = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    try:
        future_to_task = {
            executor.submit(synth_sentence, client, t["text_tts"], t["voice_id"],
                            t["voice_style"], t["out_path"], ffmpeg_path,
                            t["speed"], t["label"], model, args.api_timeout,
                            fatal_event): t
            for t in pending_tasks
        }
        done = 0
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                ok, speed_applied = future.result()
            except Exception as e:  # noqa: BLE001 — worker 未预期异常不能让整轮裸崩
                # synth_sentence 只兜住了网络/解码路径；写 sidecar、apply_speed
                # 的 os.replace 等偶发 OSError 会从这里掀出来，绕过 --on-fail
                # 分流与错误文案。记成单句失败，其余句子照常收尾。
                print(f"    [{task['label']}][crash] worker 未预期异常，按该句失败处理：{e}",
                      file=sys.stderr, flush=True)
                ok, speed_applied = False, False
            done += 1
            label, out_path = task["label"], task["out_path"]
            preview = task["text_tts"][:30]

            if ok and os.path.exists(out_path):
                dur = measure_duration(ffmpeg_path, out_path)
                if dur > 0:
                    # 这句这次真成功了：**先**清掉上一轮留下的失败标记（否则下轮会
                    # 被误判为"已失败的静音占位"而跳过），**再**写指纹与变速标记。
                    _remove_quiet(out_path + ".failed")
                    # 指纹 sidecar：resume 时用它判断这句是不是同一份输入。
                    _write_sidecar(out_path + ".sha", _sentence_hash(
                        task["text_tts"], task["voice_id"], task["voice_style"],
                        model, task["speed"], base_url))
                    if speed_applied:
                        _remove_quiet(out_path + ".needs-speed")
                    else:
                        # atempo 没落上：文件是有效的原速音频。写 .needs-speed 标记
                        # （连同指纹一起存好），下轮 --resume 只重施 atempo，
                        # 不再重烧整句 TTS。
                        _write_sidecar(out_path + ".needs-speed",
                                       f"{task['speed']:.4f}")
                        print(f"    [{label}][warn] 该句仍为原速（atempo 未落上），"
                              f"下次 --resume 只重试变速", file=sys.stderr)
                    new_results.append(_make_sentence_entry(
                        task, dur, sentence_speaker_labels))
                    print(f"[TTS {done}/{total}] {label} {preview} -> {dur:.2f}s", flush=True)
                    continue

            if fatal_event.is_set():
                # 确定性失败后其余任务被短路：整条管线即将中止，
                # 不要再给这些句子造静音占位。
                failed.append(task["index"])
                print(f"[TTS {done}/{total}] {label} {preview} "
                      f"[确定性失败，后续调用已跳过]", flush=True)
                continue

            if args.on_fail == "silence":
                # 降级：该句反复失败（如触发内容审核）时不丢弃，改为静音占位；
                # 时长只能按字数/语速估算（没有真实语速可测），估算已除过 speed。
                fallback_dur = max(
                    len(task["text_tts"]) / DEFAULT_CHARS_PER_SEC / task["speed"], 0.3)
                try:
                    generate_silence(ffmpeg_path, fallback_dur, out_path)
                    # .failed 标记 + 指纹：下次 --resume 认得出这句是"已失败的静音
                    # 占位"，既不重试也不丢标记。**先写失败标记再写指纹**——
                    # 两步之间被打断时，宁可"标记了失败"（下轮保守重来），
                    # 也不要"有指纹却没标记"（静音被当成成功配音，status 变 ok）。
                    _write_sidecar(out_path + ".failed", "")
                    _write_sidecar(out_path + ".sha", _sentence_hash(
                        task["text_tts"], task["voice_id"], task["voice_style"],
                        model, task["speed"], base_url))
                    new_results.append(_make_sentence_entry(
                        task, fallback_dur, sentence_speaker_labels, synth_failed=True))
                    print(f"[TTS {done}/{total}] {label} {preview} "
                          f"[失败 → 静音兜底 ~{fallback_dur:.2f}s，建议事后补录]", flush=True)
                    continue
                except Exception as e:  # noqa: BLE001
                    print(f"[TTS {done}/{total}] {label} {preview} "
                          f"[失败，且静音兜底也失败: {e}]", flush=True)

            failed.append(task["index"])
            print(f"[TTS {done}/{total}] {label} {preview} [失败]", flush=True)
    except BaseException:
        # Ctrl-C / 意外异常：置位事件让在途任务尽快短路，cancel_futures 丢掉
        # **排队未启动**的任务——否则退出前要等整条队列跑完（继续烧 TTS 额度）
        # 异常才能放出来。
        fatal_event.set()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        # 在途的少量请求等它收尾（线程杀不掉），队列已被上面的 cancel 清空。
        executor.shutdown(wait=True)
    return new_results, failed, fatal_event.is_set()


def main():
    setup_stdio()
    parser = _build_parser()
    args = parser.parse_args()
    _validate_args(parser, args)

    if not args.output and not args.dry_run:
        parser.error("缺少 -o/--output（--dry-run 不需要）")
    # 产物路径守卫：--dry-run 承诺不写文件，不需要拦
    if not args.dry_run:
        guard_not_in_skill_dir(("-o/--output", os.path.abspath(args.output)))
        if args.cache_dir:
            cache_abs = os.path.abspath(args.cache_dir)
            guard_not_in_skill_dir(("--cache-dir", cache_abs))
            # 逐句缓存塞进交付目录 = 违约：audio/ 只留 combined.wav 与
            # narration_timing.json，残渣会被交付前检查当事故清掉。
            # （--cache-dir 必须配 --resume，validate_args 已拦，这里不必再判。）
            if is_inside(cache_abs, os.path.abspath(args.output)):
                parser.error("--cache-dir 不能落在 -o 交付目录内部：成品目录只保留 "
                             "combined.wav 与 narration_timing.json。默认缓存在同级的 "
                             ".courseware-cache/ 下，或显式指定交付目录之外的路径。")

    api_key = get_key("MIMO_API_KEY", args.api_key, source_path=args.source)
    if not api_key and not args.dry_run:
        print("[error] 没有 API key。用 --api-key 或设置 .env 的 MIMO_API_KEY",
              file=sys.stderr)
        sys.exit(1)
    # source_path 不能省：key 与 模型/base_url 都走带 source_path 的解析，
    # 讲稿旁同目录的 .env 才能整套生效——早年只给 key 传 source_path，同一份
    # .env 里 key 生效、MIMO_TTS_MODEL 静默不生效，是最难查的半套配置。
    model, base_url = resolve_model_config(
        args.model, args.base_url, "MIMO_TTS_MODEL", "mimo-v2.5-tts",
        source_path=args.source)

    try:
        source_data = _load_script_source(args.source)
        sentences, seg_config = build_parts(source_data, default_speed=args.speed)
    except ValueError as e:
        print(f"[error] 旁白脚本无效：{e}", file=sys.stderr)
        sys.exit(1)

    print(f"[script] {sum(len(s) for s in sentences)} chars", flush=True)
    print(f"[split] {len(sentences)} sentences", flush=True)
    for i, s in enumerate(sentences[:_PREVIEW_LIMIT]):
        print(f"  {i+1}. {s[:35] + '...' if len(s) > 35 else s}", flush=True)
    if len(sentences) > _PREVIEW_LIMIT:
        print(f"  ...（其余 {len(sentences) - _PREVIEW_LIMIT} 句已省略）", flush=True)

    if args.dry_run:
        print("\n[dry-run] 分句与段落识别完成：未调 TTS、未写音频。", flush=True)
        return

    # -o 指到已存在的同名文件（手滑把文件路径当目录传）时提前拦下
    if os.path.isfile(args.output):
        print(f"[error] 输出路径 {args.output} 是一个已存在的文件，--output 需要目录路径",
              file=sys.stderr)
        sys.exit(1)
    os.makedirs(args.output, exist_ok=True)
    # 成品目录只放 combined.wav 与 narration_timing.json。可复用缓存移到同级的
    # 隐藏工作目录；不开 --resume 时用 TemporaryDirectory，不会污染交付目录。
    temp_cache = None
    if args.resume:
        cache_root = args.cache_dir or os.path.join(
            os.path.dirname(os.path.abspath(args.output)), ".courseware-cache",
            os.path.basename(os.path.abspath(args.output)))
        sentences_dir = os.path.join(cache_root, "sentences")
        print(f"[cache] {sentences_dir}", flush=True)
    else:
        temp_cache = tempfile.TemporaryDirectory(
            prefix=".courseware-tts-",
            dir=os.path.dirname(os.path.abspath(args.output)))
        # 后面还有六条 sys.exit 路径（ffmpeg 缺失、缺 SDK、abort 阻断……）：
        # TemporaryDirectory 自带的解释器退出 finalizer 兜住这些路径，
        # 成功路径再显式 cleanup() 一次提前归还。
        sentences_dir = temp_cache.name
    os.makedirs(sentences_dir, exist_ok=True)

    ffmpeg_path = get_ffmpeg()
    if not (ffmpeg_path and os.path.isfile(ffmpeg_path)):
        # get_ffmpeg 在"系统没有 + imageio 也没装"时返回字面量 "ffmpeg"：
        # 不管它，后面 concat_audio 会抛 FileNotFoundError，用户吃一段跟 ffmpeg
        # 毫无关系的裸栈。在这里 fail-fast，直接说清要装什么。
        print("[error] 没找到可用的 ffmpeg（拼接 / 变速都依赖它）。\n"
              "        装一个 ffmpeg 再跑；只想看分句结果可以加 --dry-run。",
              file=sys.stderr)
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        # MiMo TTS 客户端走 OpenAI 兼容协议；没装 SDK 时不是一句 ImportError 能
        # 自解释的，直接把装法说清。
        print("[error] 缺少 openai SDK（MiMo TTS 客户端依赖）：pip install openai\n"
              "        只想看分句结果可以加 --dry-run（不需要 SDK 与 key）。",
              file=sys.stderr)
        sys.exit(1)
    # max_retries=0：SDK 内部默认还会静默重试 2 次，叠加本模块自己的 3 次应用层
    # 重试 = 单句最多 6 次请求。重试策略统一收口到 synth_sentence。
    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    # base_url 可能带代理凭据（https://user:token@host）——原样打印会把密钥
    # 送进终端/CI 日志，展示前抹掉 userinfo。
    shown_base = str(base_url)
    i = shown_base.find("//")
    auth = shown_base[i + 2:].split("/", 1)[0] if i >= 0 else ""
    if "@" in auth:
        # rsplit：host 是**最后一个** @ 之后的部分——密码里带 @ 时 split 会把
        # "secret@host" 这样的尾巴留在打印结果里，等于半个密钥进 CI 日志。
        shown_base = shown_base[:i + 2] + "***@" + auth.rsplit("@", 1)[1] + \
            shown_base[i + 2 + len(auth):]
    print(f"[api] model={model} base_url={shown_base}", flush=True)

    sentence_speeds, sentence_voices, sentence_speaker_labels = {}, {}, {}
    _spread_segment_overrides(seg_config, args, sentences, sentence_speeds,
                              sentence_voices, sentence_speaker_labels)

    sentence_data, pending_tasks, cached_count = [], [], 0
    cached_failed_labels = []
    occ_seen = {}
    for i, sent_text in enumerate(sentences):
        voice_id, voice_style = sentence_voices.get(i, (args.voice_id, args.voice_style))
        speed = sentence_speeds.get(i, args.speed)
        # 缓存文件名内容寻址（指纹前 16 位）：句子增删导致整体重新编号时，
        # 未变的句子仍命中旧缓存；occurrence 区分同一份输入的重复句——同名
        # 并发写同一个文件才会真正炸缓存。sidecar .sha 再核对一次指纹。
        digest = _sentence_hash(sent_text, voice_id, voice_style, model, speed,
                                base_url)
        occurrence = occ_seen.get(digest, 0)
        occ_seen[digest] = occurrence + 1
        out_path = os.path.join(sentences_dir,
                                f"tts-{digest[:16]}-{occurrence:02d}.wav")
        task = {"index": i, "text_tts": sent_text, "out_path": out_path,
                "label": f"s{i+1:03d}/{len(sentences):03d}",
                "speed": speed, "voice_id": voice_id, "voice_style": voice_style}

        if args.resume and os.path.exists(out_path):
            action, dur = _resume_decision(out_path, ffmpeg_path, sent_text,
                                           voice_id, voice_style, model, speed,
                                           base_url)
            if action == "skip_failed" and args.on_fail == "abort":
                # abort 模式的契约是"交付里没有静音占位"。缓存里的失败标记
                # 若不拦下，--resume 会带着占位一路跑到 exit 0。在这里攒名单，
                # 循环后立刻退出——在任何一次 TTS 调用之前，不白烧额度。
                cached_failed_labels.append((task["label"], out_path))
                continue
            if action == "speed_only":
                # 上次 atempo 没落上的原速缓存：只重施变速，不重烧 TTS。
                # 这次仍失败就按原速交付本轮（时长实测，时间轴依旧准确）。
                if apply_speed(ffmpeg_path, out_path, speed):
                    _remove_quiet(out_path + ".needs-speed")
                    print(f"  [{task['label']}][speed] 缓存命中，已补施 atempo ×{speed}",
                          flush=True)
                else:
                    print(f"    [{task['label']}][warn] atempo 仍未落上，本轮按原速交付："
                          "该句听感偏慢（时间轴仍按实测时长，不漂移），"
                          ".needs-speed 标记保留，下轮 --resume 会自动再试",
                          file=sys.stderr, flush=True)
                dur = measure_duration(ffmpeg_path, out_path)
                action = "skip" if dur and dur > 0 else "regen"
            if action != "regen":
                sentence_data.append(_make_sentence_entry(
                    task, dur, sentence_speaker_labels,
                    synth_failed=(action == "skip_failed")))
                cached_count += 1
                print(f"  [{task['label']}][skip] {dur:.2f}s (cached)", flush=True)
                continue
            print(f"  [{task['label']}] 稿件/音色/语速已变或缓存不完整，重新合成", flush=True)
            _drop_sentence_cache(out_path)

        pending_tasks.append(task)

    if cached_failed_labels:
        # 缓存文件名是内容寻址的，句序号对不上文件名——把完整 .failed 路径列出来，
        # 删哪个文件不用猜。
        labels = "、".join(lbl for lbl, _ in cached_failed_labels)
        paths_txt = "\n".join(f"  · {p}" for _, p in cached_failed_labels)
        print(f"\n[error] --resume 命中 {len(cached_failed_labels)} 个「失败静音占位」句"
              f"（{labels}）：\n{paths_txt}\n"
              "--on-fail abort（默认）不接受无声占位。"
              "把上面每个文件连同它的 .sha / .failed  sidecar 一起删掉再重跑"
              "（这三件是一组；只删 .failed 不够——静音 wav 与 .sha 还在，"
              "--resume 会把它当成功配音直接跳过，静音照发且 status 变 ok）。"
              "重跑只会重新合成这几句，其余缓存照常复用；"
              "或显式改用 --on-fail silence 保留占位。", file=sys.stderr, flush=True)
        sys.exit(1)

    # --clean-output 挪到所有前置硬失败（ffmpeg 缺失、缺 SDK、缺 key、
    # resume 命中占位句……）之后：放在最前面会让这些失败路径把上一版好产物
    # 清掉、这一版又什么都没有。此刻交付目录尚未写入任何文件，清理是安全的。
    if args.clean_output:
        _clean_output_dir(args.output)

    pending_count = len(pending_tasks)
    if pending_count:
        mean_chars = sum(len(t["text_tts"]) for t in pending_tasks) / pending_count
        est = mean_chars / DEFAULT_CHARS_PER_SEC * pending_count * 1.2 / args.workers
        print(f"[est] 待合成 {pending_count} 句，约 {est:.0f}s"
              f"（≤{args.workers} 并发，句均 {mean_chars:.1f} 字）", flush=True)

    new_results, failed, fatal = _synthesize_pending(
        args, client, ffmpeg_path, model, base_url, pending_tasks,
        sentence_speaker_labels)

    sentence_data.extend(new_results)
    sentence_data.sort(key=lambda s: s["index"])

    if fatal:
        # 确定性失败（key / 模型 / 网关配错）：即便 --on-fail silence 也不该继续——
        # 剩下的句子全都会变成静音占位，产出一条"看似成功"的废片。
        print("\n[error] 命中确定性 TTS 失败（如 401 key 不对、模型不含音频），"
              "后续调用已跳过。核对 MIMO_API_KEY / --model / --base-url 后重跑；"
              "已合成好的句子带 --resume 会直接复用。", file=sys.stderr, flush=True)
        sys.exit(1)
    if not sentence_data:
        print("[error] 所有句子都失败了", file=sys.stderr)
        sys.exit(1)
    if failed and args.on_fail == "abort":
        print(f"\n[error] {len(failed)} 句 TTS 失败：{[f + 1 for f in failed]}。"
              "默认 --on-fail abort 阻断管线，避免静默丢失内容；"
              "如需保留时间轴并显式进入 degraded 状态，请改用 --on-fail silence。",
              file=sys.stderr, flush=True)
        sys.exit(1)
    if failed:
        # 走到这里说明 --on-fail silence 下连静音兜底也失败了：这些句**不在**
        # 时间轴上（不是占位），文案会整句消失，必须显式报出。
        print(f"\n[warn] {len(failed)} 句 TTS 失败且静音占位也没落成，已从时间轴整句丢弃："
              f"{[f + 1 for f in failed]}（成片这几处无配音也无字幕，交付时间轴为 degraded）",
              file=sys.stderr, flush=True)

    silence_fallback_count = sum(1 for s in sentence_data if s.get("synth_failed"))
    if silence_fallback_count:
        print(f"\n[warn] {silence_fallback_count} 句无有效配音（静音占位，含缓存复用），"
              f"成片对应位置为静音；可核对 narration_timing.json 中 "
              f"\"synth_failed\": true 的句子并考虑补录", file=sys.stderr, flush=True)

    _finalize_audio(args, ffmpeg_path, sentence_data, source_data, seg_config,
                    silence_fallback_count, len(sentences), cached_count)
    if temp_cache is not None:
        temp_cache.cleanup()


if __name__ == "__main__":
    main()
