"""_audio：atempo 链、gap 归一、ffmpeg 时长解析、路径转义。"""
import shutil
import subprocess

import pytest

import _audio as au
import _contracts as c

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="需要 ffmpeg")


class TestBuildAtempoFilter:
    def test_identity(self):
        assert au.build_atempo_filter(1.0) == "atempo=1.0"

    def test_single(self):
        assert au.build_atempo_filter(1.5) == "atempo=1.5"

    def test_chain_wide(self):
        # 3.0x = 2.0 * 1.5
        assert au.build_atempo_filter(3.0) == "atempo=2.0,atempo=1.5"

    def test_chain_slow(self):
        # 0.25x = 0.5 * 0.5
        assert au.build_atempo_filter(0.25) == "atempo=0.5,atempo=0.5"

    def test_chain_deep(self):
        parts = au.build_atempo_filter(8.0).split(",")
        assert parts[0:3] == ["atempo=2.0", "atempo=2.0", "atempo=2.0"]

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            au.build_atempo_filter(-1)

    def test_zero_rejected(self):
        with pytest.raises(ValueError):
            au.build_atempo_filter(0)

    def test_nan_rejected(self):
        with pytest.raises(ValueError):
            au.build_atempo_filter(float("nan"))

    def test_product_reaches_speed(self):
        # 任意合法倍率的链式乘积应收敛到原值
        for speed in (0.3, 0.75, 1.7, 2.4, 5.0, 9.9):
            factors = [float(f.split("=")[1]) for f in au.build_atempo_filter(speed).split(",")]
            prod = 1.0
            for f in factors:
                prod *= f
            assert prod == pytest.approx(speed, abs=1e-3)


class TestNormalizeGaps:
    def test_scalar(self):
        assert au._normalize_gaps(0.4, 4) == [0.4, 0.4, 0.4]

    def test_sequence(self):
        assert au._normalize_gaps([0.1, 0.5], 3) == [0.1, 0.5]

    def test_wrong_length(self):
        with pytest.raises(ValueError, match="逐边界静音需要 2 个值"):
            au._normalize_gaps([0.1], 3)

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            au._normalize_gaps(-0.1, 3)

    def test_n_too_small(self):
        with pytest.raises(ValueError):
            au._normalize_gaps(0.4, 0)


class TestParseDuration:
    def test_seconds(self):
        assert au.parse_duration("Duration: 00:00:03.50") == 3.5

    def test_minutes_hours(self):
        assert au.parse_duration("Duration: 01:02:03.25") == 3723.25

    def test_missing(self):
        assert au.parse_duration("no duration here") is None

    def test_empty(self):
        assert au.parse_duration("") is None


class TestQuoteFfpath:
    def test_no_quote(self):
        assert au.quote_ffpath("/a/b.png") == "/a/b.png"

    def test_with_quote(self):
        assert au.quote_ffpath("/a/b'c.png") == "/a/b'\\''c.png"


class TestWavDuration:
    def test_generated_sine(self, tmp_path):
        out = tmp_path / "t.wav"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2.5",
                            "-ar", "24000", "-ac", "1", str(out)],
                           capture_output=True)
        assert r.returncode == 0
        assert au._wav_duration(out) == pytest.approx(2.5, abs=0.1)

    def test_str_path(self, tmp_path):
        out = tmp_path / "t.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1", "-ar", "24000",
                        "-ac", "1", str(out)], check=True)
        assert au._wav_duration(str(out)) == pytest.approx(1.0, abs=0.05)

    def test_non_wav_returns_none(self, tmp_path):
        p = tmp_path / "x.mp3"
        p.write_bytes(b"not a wav")
        assert au._wav_duration(p) is None

    def test_missing_returns_none(self, tmp_path):
        assert au._wav_duration(tmp_path / "nope.wav") is None

    def test_generate_silence(self, tmp_path):
        out = tmp_path / "s.wav"
        au.generate_silence(au.get_ffmpeg(), 1.25, out)
        assert au._wav_duration(out) == pytest.approx(1.25, abs=0.05)


class TestApplySpeed:
    def test_faster_shorter(self, tmp_path):
        src = tmp_path / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=4", "-ar", "24000",
                        "-ac", "1", str(src)], check=True)
        assert au.apply_speed(au.get_ffmpeg(), src, 2.0)
        assert au._wav_duration(src) == pytest.approx(2.0, abs=0.1)

    def test_slower_longer(self, tmp_path):
        src = tmp_path / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=2", "-ar", "24000",
                        "-ac", "1", str(src)], check=True)
        assert au.apply_speed(au.get_ffmpeg(), src, 0.5)
        assert au._wav_duration(src) == pytest.approx(4.0, abs=0.1)


def _ffmpeg_wav(path, dur, rate, ch):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={dur}",
                    "-ar", str(rate), "-ac", str(ch), str(path)], check=True)


class TestConcatMixedFormat:
    """concat 的格式归一路径（真实生产分支，此前零覆盖）：

    TTS 原速产物常是 48kHz 立体声，静音占位/变速产物是 24kHz 单声道——混列时
    _audio 必须把偏离段就地转码到管线约定再拼接，否则 concat demuxer 按首段
    格式探测会产出时长错乱的废片。本测试钉死：混格式拼接时长必须等于
    各片段 + 逐边界静音之和，且产物可按 wave 直接读（格式已统一）。
    """

    def test_tts_48k_stereo_plus_silence_24k_mono(self, tmp_path):
        a = tmp_path / "tts.wav"        # 48k 立体声（TTS 原速形态）
        b = tmp_path / "placeholder.wav"  # 24k 单声道（静音占位形态）
        out = tmp_path / "combined.wav"
        _ffmpeg_wav(a, 1.0, 48000, 2)
        _ffmpeg_wav(b, 1.5, 24000, 1)
        assert au.concat_audio(au.get_ffmpeg(), [str(a), str(b)], 0.4, out)
        # 1.0 + 0.4 + 1.5 = 2.9s
        assert au._wav_duration(out) == pytest.approx(2.9, abs=0.1)
        # 产物格式已统一：wave 模块能直接读（首段格式不会污染时长解析）
        import wave
        with wave.open(str(out), "rb") as w:
            assert w.getframerate() == 24000
            assert w.getnchannels() == 1

    def test_silence_first_then_tts(self, tmp_path):
        # 静音占位排前、TTS 排后：归一逻辑不能只认"第一个是 TTS"
        a = tmp_path / "placeholder.wav"
        b = tmp_path / "tts.wav"
        out = tmp_path / "combined.wav"
        _ffmpeg_wav(a, 0.5, 24000, 1)
        _ffmpeg_wav(b, 2.0, 48000, 2)
        assert au.concat_audio(au.get_ffmpeg(), [str(a), str(b)], 0.2, out)
        assert au._wav_duration(out) == pytest.approx(2.7, abs=0.1)

    def test_per_boundary_gaps_mixed_formats(self, tmp_path):
        # 逐边界静音 + 混格式：两种机制叠加不能互相抵消
        a = tmp_path / "tts.wav"
        b = tmp_path / "s.wav"
        c = tmp_path / "tts2.wav"
        out = tmp_path / "combined.wav"
        _ffmpeg_wav(a, 1.0, 48000, 2)
        _ffmpeg_wav(b, 1.0, 24000, 1)
        _ffmpeg_wav(c, 1.0, 48000, 2)
        assert au.concat_audio(au.get_ffmpeg(), [str(a), str(b), str(c)],
                               [0.3, 0.7], out)
        # 三条 1.0s 片段 + 0.3/0.7 两段静音 = 4.0s
        assert au._wav_duration(out) == pytest.approx(4.0, abs=0.1)

    def test_uniform_format_copy_path(self, tmp_path):
        # 全列同格式时走流拷贝路径：时长仍是逐边界累加
        a = tmp_path / "a.wav"
        b = tmp_path / "b.wav"
        out = tmp_path / "combined.wav"
        _ffmpeg_wav(a, 2.0, 24000, 1)
        _ffmpeg_wav(b, 1.0, 24000, 1)
        assert au.concat_audio(au.get_ffmpeg(), [str(a), str(b)], 0.5, out)
        assert au._wav_duration(out) == pytest.approx(3.5, abs=0.1)
