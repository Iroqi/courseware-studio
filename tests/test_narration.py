"""narration.py（TTS 编排核心）单元测试——全部走 fake client / 本地 wav，零网络零密钥。

覆盖 build_parts、_load_script_source、_spread_segment_overrides、
_accumulate_start_times、_resume_decision、_finalize_audio（monkeypatch concat）、
synth_sentence（fake OpenAI client）、_synthesize_pending（含静音兜底与致命短路）。
"""
import base64
import io
import json
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import narration


# ── 构件 ───────────────────────────────────────────────────────────
def wav_bytes(dur=0.1, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * dur))
    return buf.getvalue()


def write_wav(path, dur=0.1):
    Path(path).write_bytes(wav_bytes(dur))
    return path


class FakeHTTPError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class FakeAudio:
    def __init__(self, data):  # data: base64 字符串
        self.data = data


class FakeMessage:
    def __init__(self, data=None):
        self.audio = FakeAudio(data) if data is not None else None


class FakeChoice:
    def __init__(self, data=None):
        self.message = FakeMessage(data)


class FakeCompletions:
    """factory(kwargs) → completion 或抛异常；计数供断言。"""

    def __init__(self, factory):
        self.factory = factory
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self.factory(kwargs)


class FakeClient:
    def __init__(self, factory):
        self.chat = SimpleNamespace(completions=FakeCompletions(factory))


def ok_factory(kwargs):
    return SimpleNamespace(choices=[FakeChoice(base64.b64encode(wav_bytes()).decode())])


def base_source(**over):
    data = {"title": "测试课", "segments": [
        {"id": "seg-1", "title": "开场", "text": "第一句。第二句。"}]}
    data.update(over)
    return data


# ── build_parts ────────────────────────────────────────────────────
class TestBuildParts:
    def test_basic_blocks(self):
        src = base_source(opening="开场白。",
                          closing="收尾。",
                          segments=[
                              {"id": "seg-1", "title": "A", "text": "甲句。乙句。"},
                              {"id": "seg-2", "title": "B", "text": "丙句。"},
                          ])
        sents, segs = narration.build_parts(src, default_speed=1.0)
        assert sents == ["开场白。", "甲句。", "乙句。", "丙句。", "收尾。"]
        assert [s["start"] for s in segs] == [0, 1, 3, 4]
        assert [s["end"] for s in segs] == [1, 3, 4, 5]
        assert segs[0]["id"] == "opening"
        assert segs[1]["id"] == "seg-1"

    def test_opening_closing_speed_inherits_default(self):
        src = base_source(opening="开场白。", closing="收尾。")
        _, segs = narration.build_parts(src, default_speed=1.5)
        assert segs[0]["speed"] == 1.5
        assert segs[-1]["speed"] == 1.5

    def test_segment_speed_validation(self):
        src = base_source(segments=[{"id": "s1", "title": "A", "text": "甲。",
                                     "speed": "1.2"}])
        with pytest.raises(ValueError, match="speed"):
            narration.build_parts(src, default_speed=1.0)

    def test_segment_speed_zero_rejected(self):
        src = base_source(segments=[{"id": "s1", "title": "A", "text": "甲。",
                                     "speed": 0}])
        with pytest.raises(ValueError, match="speed"):
            narration.build_parts(src, default_speed=1.0)

    def test_dialogue_expands_with_turns(self):
        # 音色唯一真源是 speakers 配置（turn 级自写 voice_id 会被 _load_script_source 拒收）
        src = {"title": "t",
               "speakers": {"a": {"label": "讲师", "voice_id": "冰糖", "voice_style": "沉稳"},
                            "b": {"label": "学生"}},
               "segments": [{"id": "seg-1", "title": "D", "dialogue": [
                   {"speaker": "a", "text": "你听。"},
                   {"speaker": "b", "text": "听到了。"}]}]}
        sents, segs = narration.build_parts(src, default_speed=1.0)
        assert sents == ["你听。", "听到了。"]
        turns = segs[0]["turns"]
        assert turns[0]["start"] == 0 and turns[0]["end"] == 1
        assert turns[0]["speaker"] == "a" and turns[0]["label"] == "讲师"
        assert turns[1]["start"] == 1 and turns[1]["end"] == 2
        assert turns[1]["speaker"] == "b"
        assert turns[0]["voice_id"] == "冰糖" and turns[0]["voice_style"] == "沉稳"
        assert turns[1].get("voice_id") is None

    def test_beats_shift_to_global_index(self):
        src = base_source(opening="开场白。",
                          segments=[{"id": "seg-1", "title": "A", "text": "甲句。乙句。",
                                     "beat": {"2": {"pause": 0.5}}}])
        _, segs = narration.build_parts(src, default_speed=1.0)
        # 开场占了全局 0 号，seg-1 第二句 → 全局 2 号
        assert segs[1]["beat"] == {2: {"pause": 0.5}}

    def test_beat_pause_on_first_sentence_rejected(self):
        src = base_source(segments=[{"id": "seg-1", "title": "A", "text": "甲句。",
                                     "beat": {"1": {"pause": 0.3}}}])
        with pytest.raises(ValueError, match="第 1 句写了 pause"):
            narration.build_parts(src, default_speed=1.0)

    def test_beat_out_of_range_rejected(self):
        src = base_source(segments=[{"id": "seg-1", "title": "A", "text": "甲句。",
                                     "beat": {"3": {"pause": 0.3}}}])
        with pytest.raises(ValueError, match="只有 1 句"):
            narration.build_parts(src, default_speed=1.0)

    def test_beat_unknown_key_rejected(self):
        src = base_source(segments=[{"id": "seg-1", "title": "A", "text": "甲句。",
                                     "beat": {"1": {"hold": 3}}}])
        with pytest.raises(ValueError, match="未知节拍键"):
            narration.build_parts(src, default_speed=1.0)

    def test_empty_segments_rejected(self):
        with pytest.raises(ValueError, match="segments"):
            narration.build_parts({"title": "t", "segments": []}, 1.0)

    def test_empty_sentence_in_segment_rejected(self):
        src = base_source(segments=[{"id": "seg-1", "title": "A", "text": " "}])
        with pytest.raises(ValueError, match="分句后为空|text.*为空"):
            narration.build_parts(src, default_speed=1.0)


# ── _load_script_source ────────────────────────────────────────────
class TestLoadScriptSource:
    def test_valid(self, tmp_path):
        p = tmp_path / "src.json"
        p.write_text(json.dumps(base_source(), ensure_ascii=False), encoding="utf-8")
        data = narration._load_script_source(str(p))
        assert data["segments"][0]["id"] == "seg-1"

    def test_speakers_type_rejected(self, tmp_path):
        p = tmp_path / "src.json"
        p.write_text(json.dumps({"title": "t", "speakers": ["a"],
                                 "segments": [{"id": "s1", "text": "甲。"}]},
                                ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="speakers"):
            narration._load_script_source(str(p))

    def test_turn_string_rejected(self, tmp_path):
        p = tmp_path / "src.json"
        p.write_text(json.dumps({"title": "t", "speakers": {"a": {}},
                                 "segments": [{"id": "s1", "dialogue": ["甲：你好"]}]},
                                ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="dialogue\\[1\\] 必须是对象"):
            narration._load_script_source(str(p))

    def test_turn_self_voice_id_rejected(self, tmp_path):
        p = tmp_path / "src.json"
        p.write_text(json.dumps({"title": "t", "speakers": {"a": {"label": "甲"}},
                                 "segments": [{"id": "s1", "dialogue": [
                                     {"speaker": "a", "text": "你好。",
                                      "voice_id": "冰糖"}]}]},
                                ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="turn 级音色只取 speakers"):
            narration._load_script_source(str(p))

    def test_unknown_voice_id_rejected(self, tmp_path):
        p = tmp_path / "src.json"
        p.write_text(json.dumps({"title": "t",
                                 "segments": [{"id": "s1", "text": "甲。",
                                               "voice_id": "不存在"}]},
                                ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="不在可用音色里"):
            narration._load_script_source(str(p))

    def test_duplicate_seg_id_rejected(self, tmp_path):
        p = tmp_path / "src.json"
        p.write_text(json.dumps({"title": "t", "segments": [
            {"id": "seg-1", "text": "甲。"}, {"id": "seg-1", "text": "乙。"}]},
            ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="重复"):
            narration._load_script_source(str(p))


# ── _spread_segment_overrides ──────────────────────────────────────
class TestSpreadSegmentOverrides:
    def make(self, seg_config, sentences):
        speeds, voices, labels, pauses = {}, {}, {}, {}
        narration._spread_segment_overrides(
            seg_config, SimpleNamespace(voice_id="冰糖", voice_style="默认"),
            sentences, speeds, voices, labels, pauses)
        return speeds, voices, labels, pauses

    def test_seg_speed_fills_range(self):
        seg = {"id": "s", "start": 0, "end": 3, "speed": 1.2}
        speeds, *_ = self.make([seg], ["a", "b", "c"])
        assert speeds == {0: 1.2, 1: 1.2, 2: 1.2}

    def test_beat_speed_overrides_seg_speed(self):
        seg = {"id": "s", "start": 0, "end": 3, "speed": 1.2,
               "beat": {1: {"speed": 0.9}}}
        speeds, *_ = self.make([seg], ["a", "b", "c"])
        assert speeds[0] == 1.2 and speeds[1] == 0.9

    def test_beat_pause_and_speed(self):
        # 进入 seg_config 的 beat 键已是全局索引（build_parts 已从段内序号换算）
        seg = {"id": "s", "start": 2, "end": 5, "beat": {3: {"pause": 1.0}}}
        *_, pauses = self.make([seg], ["a", "b", "c", "d", "e"])
        assert pauses == {3: 1.0}

    def test_turn_voice_and_label(self):
        seg = {"id": "s", "start": 1, "end": 3,
               "turns": [{"start": 1, "end": 2, "speaker": "a", "label": "甲",
                          "voice_id": "茉莉", "voice_style": "活泼"}]}
        _, voices, labels, _ = self.make([seg], ["a", "b"])
        assert voices[1] == ("茉莉", "活泼")
        assert labels[1] == "甲"
        assert 2 not in voices

    def test_out_of_range_beat_skipped(self):
        seg = {"id": "s", "start": 0, "end": 1, "beat": {99: {"speed": 1.5}}}
        speeds, *_ = self.make([seg], ["a"])
        assert speeds == {}


# ── _accumulate_start_times ────────────────────────────────────────
class TestAccumulateStartTimes:
    def test_plain_gaps(self):
        data = [{"duration": 1.0}, {"duration": 2.0}, {"duration": 0.5}]
        assert narration._accumulate_start_times(data, [0.4, 0.4]) == [0.0, 1.4, 3.8]

    def test_custom_boundary_gaps(self):
        data = [{"duration": 1.0}, {"duration": 1.0}]
        assert narration._accumulate_start_times(data, [2.5]) == [0.0, 3.5]

    def test_rounding_to_3dp(self):
        data = [{"duration": 1.0001}, {"duration": 1.0}]
        starts = narration._accumulate_start_times(data, [0.0])
        assert starts == [0.0, 1.0]

    def test_gaps_shorter_than_data(self):
        data = [{"duration": 1.0}, {"duration": 1.0}, {"duration": 1.0}]
        starts = narration._accumulate_start_times(data, [0.5])
        assert starts == [0.0, 1.5, 2.5]  # 超出长度的 gap 不读


# ── _resume_decision ───────────────────────────────────────────────
class TestResumeDecision:
    def hash_for(self, text="甲句。", speed=1.0, **kw):
        return narration._sentence_hash(text, kw.get("voice_id", "冰糖"),
                                        kw.get("voice_style", ""), "m", speed, "u")

    def test_no_cache_regen(self, tmp_path):
        out = tmp_path / "tts.wav"
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u") == ("regen", 0.0)

    def test_valid_cache_skip(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        (tmp_path / "tts.wav.sha").write_text(self.hash_for(), encoding="utf-8")
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")[0] == "skip"

    def test_sha_mismatch_regen(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        (tmp_path / "tts.wav.sha").write_text("stale-hash", encoding="utf-8")
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")[0] == "regen"

    def test_missing_sha_regen(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")[0] == "regen"

    def test_corrupt_wav_regen(self, tmp_path):
        out = tmp_path / "tts.wav"
        out.write_bytes(b"not a wav at all")
        (tmp_path / "tts.wav.sha").write_text(self.hash_for(), encoding="utf-8")
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")[0] == "regen"

    def test_needs_speed_speed_only(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        # 指纹含 speed：决策用 speed=1.2，.sha 必须同口径否则 regen
        (tmp_path / "tts.wav.sha").write_text(
            self.hash_for(speed=1.2), encoding="utf-8")
        (tmp_path / "tts.wav.needs-speed").write_text("1.2", encoding="utf-8")
        action, dur = narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.2, "u")
        assert action == "speed_only" and dur == pytest.approx(0.1, abs=0.01)

    def test_failed_marker_skip_failed(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        (tmp_path / "tts.wav.sha").write_text(self.hash_for(), encoding="utf-8")
        (tmp_path / "tts.wav.failed").write_text("", encoding="utf-8")
        action, dur = narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")
        assert action == "skip_failed" and dur > 0

    def test_failed_marker_sha_changed_regen(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        (tmp_path / "tts.wav.sha").write_text("old", encoding="utf-8")
        (tmp_path / "tts.wav.failed").write_text("", encoding="utf-8")
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")[0] == "regen"

    def test_failed_marker_corrupt_sha_regen(self, tmp_path):
        out = tmp_path / "tts.wav"
        write_wav(out)
        (tmp_path / "tts.wav.failed").write_text("", encoding="utf-8")
        assert narration._resume_decision(
            str(out), None, "甲句。", "冰糖", "", "m", 1.0, "u")[0] == "regen"


# ── _finalize_audio ────────────────────────────────────────────────
class TestFinalizeAudio:
    def run_finalize(self, tmp_path, sentence_data, seg_config, total_sentences,
                     gaps=None, monkeypatch=None, **over):
        out = tmp_path / "out"
        out.mkdir()
        args = SimpleNamespace(output=str(out), gap=0.4, voice_id="冰糖")
        real_concat = narration.concat_audio

        def fake_concat(ffmpeg, files, gap_sec, out_path):
            Path(out_path).write_bytes(wav_bytes(0.05))
            return True

        monkeypatch.setattr(narration, "concat_audio", fake_concat)
        try:
            narration._finalize_audio(
                args, None, sentence_data, {"title": "t"}, seg_config,
                over.get("silence_fallback_count", 0), total_sentences,
                over.get("cached_count", 0), gaps or {})
        finally:
            monkeypatch.setattr(narration, "concat_audio", real_concat)
        return json.loads((out / "narration_timing.json").read_text(encoding="utf-8"))

    def make_data(self, indices, texts=None):
        out = []
        for i in indices:
            out.append({"index": i, "text": (texts or {}).get(i, f"第{i}句。"),
                        "file": f"/tmp/x{i}.wav", "duration": 1.0})
        return out

    def test_basic_manifest(self, tmp_path, monkeypatch):
        data = self.make_data([0, 1, 2])
        segs = [{"id": "seg-1", "title": "A", "start": 0, "end": 3}]
        m = self.run_finalize(tmp_path, data, segs, 3, monkeypatch=monkeypatch)
        assert m["schema_version"] == 1
        assert m["status"] == "ok"
        assert m["audio"] == "combined.wav"
        assert len(m["scenes"]) == 1
        sc = m["scenes"][0]
        assert sc["step_id"] == "seg-1"
        # gap 0.4：三句 1.0s → starts 0.0 / 1.4 / 2.8
        assert [s["start"] for s in sc["sentences"]] == [0.0, 1.4, 2.8]
        assert sc["end"] == pytest.approx(3.8)

    def test_sentence_pauses_override(self, tmp_path, monkeypatch):
        data = self.make_data([0, 1, 2])
        segs = [{"id": "seg-1", "title": "A", "start": 0, "end": 3}]
        m = self.run_finalize(tmp_path, data, segs, 3, gaps={2: 1.0},
                              monkeypatch=monkeypatch)
        starts = [s["start"] for s in m["scenes"][0]["sentences"]]
        assert starts == [0.0, 1.4, 3.4]  # 第 2 句（全局 2 号）前停 1.0s

    def test_dropped_first_sentence_scenes_by_original_index(self, tmp_path, monkeypatch):
        # 丢弃全局 0 号句（静音兜底也没落成）：场景必须仍按原 index 切，
        # 否则后面段落整体前移、句子被静默切进错误段落。
        data = self.make_data([1, 2, 3])
        segs = [{"id": "seg-1", "start": 0, "end": 2},
                {"id": "seg-2", "start": 2, "end": 4}]
        m = self.run_finalize(tmp_path, data, segs, 4, monkeypatch=monkeypatch)
        assert len(m["scenes"]) == 2
        assert [s["text"] for s in m["scenes"][0]["sentences"]] == ["第1句。"]
        assert [s["text"] for s in m["scenes"][1]["sentences"]] == ["第2句。", "第3句。"]

    def test_degraded_status_silence(self, tmp_path, monkeypatch):
        data = self.make_data([0, 1])
        data[1]["synth_failed"] = True
        segs = [{"id": "seg-1", "start": 0, "end": 2}]
        m = self.run_finalize(tmp_path, data, segs, 2,
                              silence_fallback_count=1, monkeypatch=monkeypatch)
        assert m["status"] == "degraded"
        assert m["degraded"]["tts_silence_fallback_count"] == 1
        assert m["scenes"][0]["sentences"][1]["synth_failed"] is True

    def test_degraded_status_dropped(self, tmp_path, monkeypatch):
        data = self.make_data([0, 2])  # 全局 1 号被整句丢弃
        segs = [{"id": "seg-1", "start": 0, "end": 3}]
        m = self.run_finalize(tmp_path, data, segs, 3, monkeypatch=monkeypatch)
        assert m["status"] == "degraded"
        assert m["degraded"]["dropped_sentence_count"] == 1

    def test_empty_scene_skipped_with_warning(self, tmp_path, monkeypatch):
        # 段落内所有句子都被丢弃：该场景从时间轴剔除，不写空场景
        data = self.make_data([3])  # seg-1 的 0..2 全丢
        segs = [{"id": "seg-1", "start": 0, "end": 3},
                {"id": "seg-2", "start": 3, "end": 4}]
        m = self.run_finalize(tmp_path, data, segs, 4, monkeypatch=monkeypatch)
        assert [s["step_id"] for s in m["scenes"]] == ["seg-2"]


# ── synth_sentence（fake OpenAI client）────────────────────────────
class TestSynthSentence:
    def synth(self, client, speed=1.0, out_path=None, fatal_event=None):
        return narration.synth_sentence(
            client, "你好。", "冰糖", "", str(out_path), None, speed,
            "s001/002", "m", 30.0, fatal_event)

    def test_success_writes_wav(self, tmp_path):
        ev = __import__("threading").Event()
        ok, sp = self.synth(FakeClient(ok_factory), out_path=tmp_path / "a.wav", fatal_event=ev)
        assert ok is True and sp is True
        assert (tmp_path / "a.wav").read_bytes()[:4] == b"RIFF"
        assert not ev.is_set()

    def test_non_riff_response_fatal(self, tmp_path):
        ev = __import__("threading").Event()
        def factory(kw):
            return SimpleNamespace(choices=[FakeChoice(
                base64.b64encode(b"NOTWAV").decode())])
        ok, _ = self.synth(FakeClient(factory), out_path=tmp_path / "b.wav", fatal_event=ev)
        assert ok is False
        assert ev.is_set()  # 网关返回非 WAV = 配置类失败，短路整池

    def test_401_fatal_no_retry(self, tmp_path):
        ev = __import__("threading").Event()
        client = FakeClient(lambda kw: (_ for _ in ()).throw(FakeHTTPError(401)))
        ok, _ = self.synth(client, out_path=tmp_path / "c.wav", fatal_event=ev)
        assert ok is False
        assert ev.is_set()
        assert client.chat.completions.calls == 1

    def test_400_reject_not_fatal(self, tmp_path):
        ev = __import__("threading").Event()
        client = FakeClient(lambda kw: (_ for _ in ()).throw(FakeHTTPError(400)))
        ok, _ = self.synth(client, out_path=tmp_path / "d.wav", fatal_event=ev)
        assert ok is False
        assert not ev.is_set()  # 逐句拒答不炸整稿
        assert client.chat.completions.calls == 1

    def test_transient_error_retries_then_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        ev = __import__("threading").Event()
        client = FakeClient(lambda kw: (_ for _ in ()).throw(RuntimeError("boom")))
        ok, _ = self.synth(client, out_path=tmp_path / "e.wav", fatal_event=ev)
        assert ok is False and not ev.is_set()
        assert client.chat.completions.calls == 3  # MAX_RETRIES

    def test_speed_applied_flag(self, tmp_path):
        ev = __import__("threading").Event()
        # speed=1.0 短路 atempo → (True, True)；无 ffmpeg 也不降级
        ok, sp = self.synth(FakeClient(ok_factory), speed=1.0,
                            out_path=tmp_path / "f.wav", fatal_event=ev)
        assert ok and sp


# ── _synthesize_pending ────────────────────────────────────────────
class TestSynthesizePending:
    def tasks(self, tmp_path, texts=("甲句。", "乙句。")):
        return [{"index": i, "text_tts": t, "out_path": str(tmp_path / f"s{i}.wav"),
                 "label": f"s{i:03d}", "speed": 1.0, "voice_id": "冰糖",
                 "voice_style": ""} for i, t in enumerate(texts)]

    def run(self, tmp_path, factory, on_fail="abort", workers=1):
        args = SimpleNamespace(workers=workers, on_fail=on_fail, api_timeout=30.0)
        client = FakeClient(factory)
        return narration._synthesize_pending(
            args, client, None, "m", "u", self.tasks(tmp_path), {}), client

    def test_all_success(self, tmp_path):
        (res, failed, fatal), client = self.run(tmp_path, ok_factory)
        assert len(res) == 2 and not failed and not fatal
        assert client.chat.completions.calls == 2

    def test_abort_on_reject(self, tmp_path):
        def factory(kw):
            if kw["messages"][-1]["content"] == "甲句。":
                raise FakeHTTPError(400)
            return ok_factory(kw)
        (res, failed, fatal), client = self.run(tmp_path, factory, on_fail="abort")
        assert failed == [0] and fatal is False
        assert [r["index"] for r in res] == [1]

    def test_silence_fallback_writes_placeholder(self, tmp_path):
        def factory(kw):
            if kw["messages"][-1]["content"] == "甲句。":
                raise FakeHTTPError(400)
            return ok_factory(kw)
        (res, failed, fatal), _ = self.run(tmp_path, factory, on_fail="silence")
        assert failed == [] and fatal is False
        assert len(res) == 2
        assert res[0]["synth_failed"] is True and res[1].get("synth_failed") is None
        # 占位三件套：静音 wav + .failed + .sha
        assert (tmp_path / "s0.wav").exists()
        assert (tmp_path / "s0.wav.failed").exists()
        assert (tmp_path / "s0.wav.sha").exists()

    def test_fatal_shortcircuits_remaining(self, tmp_path):
        client = FakeClient(lambda kw: (_ for _ in ()).throw(FakeHTTPError(401)))
        (res, failed, fatal) = narration._synthesize_pending(
            SimpleNamespace(workers=1, on_fail="silence", api_timeout=30.0),
            client, None, "m", "u", self.tasks(tmp_path), {})
        assert fatal is True
        assert failed == [0, 1]
        # 致命失败后第二句被短路，不再发调用
        assert client.chat.completions.calls == 1

    def test_success_clears_stale_failed_marker(self, tmp_path):
        # 上一轮失败留下的 .failed 标记：这句重试成功后必须清掉，
        # 否则下轮 resume 会把它误判成"静音占位"整句跳过
        (tmp_path / "s0.wav.failed").write_text("", encoding="utf-8")
        (res, _, _), _ = self.run(tmp_path, ok_factory)
        assert not (tmp_path / "s0.wav.failed").exists()
        assert not res[0].get("synth_failed")
