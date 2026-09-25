#!/usr/bin/env python3
"""跨脚本共享的契约原语：数值/语速校验、默认值、严格 JSON。

各脚本通过文件（旁白脚本 / narration_timing.json / timeline JSON）通信，
字段缺失或类型错误会变成难以定位的报错或静默降级。把领域规则收口成少量共享
函数，避免漂移。

运行时共享规则；不承担制品审计或 self-test。
（check_gates.py 只复用 require_finite_number 这类通用原语，不自建第二份；
时间轴门与降级门则有意各写一遍——QA 不复用生产方的闸门判断，要用自己的眼睛
重新核一遍制品。）
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── 时间轴制品契约（单一来源）──────────────────────────────────────
SCHEMA_VERSION = 1


def require_schema_version(payload, label):
    """时间轴类 JSON 必须显式带 schema_version == SCHEMA_VERSION 的唯一实现。

    缺 key 不默认按 1 处理：SKILL.md §2 要求外部产出的 timing 先归一并标版本号。
    bool 是 int 子类，JSON true 会因 True == 1 混过版本门，必须显式排除。
    违规抛 ValueError（label 进错误消息），由调用方决定怎么报。
    """
    if not isinstance(payload, dict) or "schema_version" not in payload:
        raise ValueError(f"{label} 缺少显式 schema_version"
                         f"（外部产出的 timing 请先归一并标 {SCHEMA_VERSION}）")
    version = payload["schema_version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValueError(f"{label} 不支持的 schema_version：{version!r}"
                         f"（需要 {SCHEMA_VERSION}）")


def check_degraded_status(manifest, allow_degraded, label):
    """degraded manifest 拒收门的唯一实现（narration_timing.json / timeline）。

    返回需要打出的 [warn] 文本（外部 timing 没有 status 字段），无需告警返回 None。
    降级且未放行时抛 ValueError，错误消息由调用方加前缀报出。
    """
    status = manifest.get("status")
    if status is None:
        # 外部归一成的 timing（SKILL.md §2）按契约没有 status 字段——认它继续；
        # narration.py 的内部 manifest 恒写 status，缺了要出声。
        return (f"{label} 没有 status 字段：按外部 timing 处理；"
                "若它来自 narration.py，请核对是否被截断或改写")
    if str(status) != "ok" and not allow_degraded:
        degraded = manifest.get("degraded") or {}
        raise ValueError(
            f"{label} 状态为 {status}"
            f"（静音占位 {degraded.get('tts_silence_fallback_count', 0)} 句、"
            f"整句丢弃 {degraded.get('dropped_sentence_count', 0)} 句）。"
            "修复 TTS 失败后重跑 narration.py，或显式使用 --allow-degraded。")
    return None


def segment_step_id(seg, kept_index):
    """段落 step_id 的兜底编号规则（从 1 数起，narration 与 build_timeline 共用）。

    两处各自实现时一旦漂移，讲稿里的 seg-N 会与音频侧错开一位，
    title / tagline / hl 全部静默丢失。kept_index 是**滤掉空段落之后**的序号。
    """
    return str(seg.get("id") or f"seg-{kept_index}")


def opening_title(source):
    """开场标题回退链（narration 与 build_timeline 同一实现）。"""
    return source.get("opening_title") or source.get("title") or "开场"


def closing_title(source):
    """收尾标题回退链（narration 与 build_timeline 同一实现）。"""
    return source.get("closing_title") or "小结"


def inline_json(payload):
    """序列化成可嵌进 <script type="application/json"> 的 JSON 文本。

    所有 "<" 一律转义成 \\u003c：旧的 `</`→`<\\/` 挡不住 `<!--` + `<script`
    组合——HTML tokenizer 会进入 script data double escaped 状态，块的
    </script> 不再闭合元素，整页后半段被吞进时间轴。对 JSON.parse / json.loads
    均透明。
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def require_finite_number(value, label, *, positive=False, nonnegative=False):
    """校验「有限数值」的唯一实现。

    label 形如 "narration_timing.json scenes[0].start"，直接进错误消息。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} 必须是有限数字")
    try:
        fvalue = float(value)
    except OverflowError:
        # 超大整数（JSON 里恶意/手滑写出的 400 位 start）float() 直接溢出——
        # 它不是可用的有限时间值，按校验失败处理而不是裸栈穿透调用方。
        raise ValueError(f"{label} 必须是有限数字")
    if not math.isfinite(fvalue):
        raise ValueError(f"{label} 必须是有限数字")
    if positive and value <= 0:
        raise ValueError(f"{label} 必须是正数")
    if nonnegative and value < 0:
        raise ValueError(f"{label} 必须是非负数字")
    return value


def validate_speed(speed):
    """校验语速倍率必须是 >0 的有限数值，非法时抛 ValueError。

    CLI 的 --speed 入口与 _audio.build_atempo_filter 的入口守卫共用这一份判断，
    避免多处各写一份而漂移（速度 <=0 会让 atempo 链不收敛）。
    """
    if not isinstance(speed, (int, float)) or isinstance(speed, bool):
        raise ValueError(f"speed 必须是数值（收到 {speed!r}）")
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(f"speed 必须是大于 0 的有限数值（收到 {speed!r}）")
    return speed


# ── 跨脚本默认值（单一来源）────────────────────────────────────────
# 默认**原速**。变速是逐段可选的调味（`segments[].speed`），不是全局基调：
# 默认值一旦不是 1.0，"这段就是原速"这个最朴素的预期就没了，改回来还得重烧一遍 TTS 额度。
DEFAULT_SPEED = 1.0
# opening/closing 不另设常量：不传 opening_speed/closing_speed 时跟随全局 --speed
# （见 narration._collect_blocks）。单独调速时记得**小于** DEFAULT_SPEED 才是"略慢"。

# 静音兜底时长估算用的启发式语速（字/秒）。
DEFAULT_CHARS_PER_SEC = 4.3
# 与 narration.py 的 --gap 默认值保持一致（不一致会让估算相对实测系统性偏移）。
DEFAULT_GAP = 0.4


def estimate_sentence_seconds(sentence, chars_per_sec, speed):
    """单句预计时长（秒）：字数 / 语速 / 倍速（TTS 失败时估静音占位时长用）。"""
    return len(sentence) / chars_per_sec / max(speed, 0.01)


# ── Voice registry ───────────────────────────────────────────────
VOICE_IDS = ["冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]


def list_voice_ids():
    return list(VOICE_IDS)
