"""export_subtitles：时间轴 → SRT/WebVTT 字幕导出。

字幕是时间轴（narration_timing.json）的另一种序列化：文本来源、时间来源都
只有一份，测试锁定"导出成功 ⇒ 时间轴本身可交付"的两道闸（句内不重叠、
synth_failed 拒收）与三种格式细节（时间戳、说话人前缀、结论句标记）。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import export_subtitles as es
from conftest import minimal_manifest


def manifest_with_speakers():
    m = minimal_manifest()
    m["scenes"][0]["sentences"][1]["speaker"] = "小明"
    m["scenes"][1]["sentences"][0]["speaker"] = "小钢"
    return m


class TestTimeFormats:
    def test_srt_zero(self):
        assert es._fmt_srt(0.0) == "00:00:00,000"

    def test_srt_over_hour(self):
        # 超过 1 小时：小时位保持两位数宽度、可超 99
        assert es._fmt_srt(3600 + 61.25) == "01:01:01,250"

    def test_srt_rounds_ms(self):
        # 1.9995s → 2000ms，进位到下一秒；int() 截断会得到 1.999 的假值
        assert es._fmt_srt(1.9995) == "00:00:02,000"

    def test_vtt_uses_dot(self):
        assert es._fmt_vtt(61.25) == "00:01:01.250"


class TestCueExtraction:
    def test_basic_cues(self):
        cues = es._cues(minimal_manifest(), speaker=False, hl_mark=False,
                        offset=0.0, allow_degraded=False)
        assert [t for _, _, t in cues] == ["第一句。", "第二句。", "第三句。",
                                           "第四句。", "第五句。",
                                           "第六句。", "第七句。"]
        assert cues[0] == (0.0, 1.0, "第一句。")
        assert cues[-1] == (10.8, 12.0, "第七句。")

    def test_speaker_prefix(self):
        m = manifest_with_speakers()
        cues = es._cues(m, speaker=True, hl_mark=False, offset=0.0,
                        allow_degraded=False)
        assert cues[1][2] == "小明：第二句。"
        assert cues[3][2] == "小钢：第四句。"
        assert cues[0][2] == "第一句。"          # 无 speaker 的句子不加前缀

    def test_speaker_off_by_default(self):
        m = manifest_with_speakers()
        cues = es._cues(m, speaker=False, hl_mark=False, offset=0.0,
                        allow_degraded=False)
        assert cues[1][2] == "第二句。"

    def test_hl_mark(self):
        m = minimal_manifest()
        m["scenes"][0]["sentences"][2]["hl"] = True
        cues = es._cues(m, speaker=False, hl_mark=True, offset=0.0,
                        allow_degraded=False)
        assert cues[2][2] == "◆ 第三句。"
        assert cues[0][2] == "第一句。"          # 非结论句不加标记

    def test_offset(self):
        cues = es._cues(minimal_manifest(), speaker=False, hl_mark=False,
                        offset=1.5, allow_degraded=False)
        assert cues[0] == (1.5, 2.5, "第一句。")
        assert cues[-1] == (12.3, 13.5, "第七句。")

    def test_negative_offset_error(self):
        with pytest.raises(SystemExit, match="字幕起点为负"):
            es._cues(minimal_manifest(), speaker=False, hl_mark=False,
                     offset=-0.5, allow_degraded=False)

    def test_overlap_rejected(self):
        m = minimal_manifest()
        m["scenes"][0]["sentences"][1]["start"] = 0.2   # 与第一句 [0,1) 重叠
        with pytest.raises(SystemExit, match="重叠"):
            es._cues(m, speaker=False, hl_mark=False, offset=0.0,
                     allow_degraded=False)

    def test_empty_text_rejected(self):
        m = minimal_manifest()
        m["scenes"][0]["sentences"][0]["text"] = "   "
        with pytest.raises(SystemExit, match="text 为空"):
            es._cues(m, speaker=False, hl_mark=False, offset=0.0,
                     allow_degraded=False)

    def test_missing_scenes_rejected(self):
        m = minimal_manifest()
        m["scenes"] = []
        with pytest.raises(SystemExit, match="非空 scenes"):
            es._cues(m, speaker=False, hl_mark=False, offset=0.0,
                     allow_degraded=False)


class TestDegradedGate:
    def _with_synth_failed(self):
        m = minimal_manifest()
        m["scenes"][1]["sentences"][0]["synth_failed"] = True
        return m

    def test_synth_failed_rejected_by_default(self):
        with pytest.raises(SystemExit, match="synth_failed"):
            es._cues(self._with_synth_failed(), speaker=False, hl_mark=False,
                     offset=0.0, allow_degraded=False)

    def test_synth_failed_allowed(self):
        m = self._with_synth_failed()
        cues = es._cues(m, speaker=False, hl_mark=False, offset=0.0,
                        allow_degraded=True)
        assert len(cues) == 7

    def test_manifest_status_gate(self):
        m = minimal_manifest(status="degraded",
                             degraded={"tts_silence_fallback_count": 1,
                                       "dropped_sentence_count": 0})
        with pytest.raises(SystemExit, match="状态为 degraded"):
            es.subtitles_srt(m)
        # --allow-degraded 放行（静音占位句仍按句级闸逐句拦截）
        es.subtitles_srt(m, allow_degraded=True)


class TestSrt:
    def test_full_srt(self):
        body = es.subtitles_srt(minimal_manifest())
        lines = body.splitlines()
        assert lines[0] == "1"
        assert lines[1] == "00:00:00,000 --> 00:00:01,000"
        assert lines[2] == "第一句。"
        assert lines[3] == ""
        assert lines[4] == "2"
        # 最后一条：第六句在 8.5，第七句在 10.8–12.0
        assert "00:00:10,800 --> 00:00:12,000" in lines
        assert body.count("\n\n") >= 6      # 条目间空行分隔

    def test_srt_indexes_are_sequential(self):
        body = es.subtitles_srt(minimal_manifest())
        nums = [int(line) for line in body.splitlines() if line.isdigit()]
        assert nums == list(range(1, 8))


class TestVtt:
    def test_full_vtt(self):
        body = es.subtitles_vtt(minimal_manifest())
        assert body.startswith("WEBVTT\n\n")
        assert "00:00:00.000 --> 00:00:01.000" in body
        assert "00:00:10.800 --> 00:00:12.000" in body
        assert "第一句。" in body

    def test_vtt_has_no_auto_index(self):
        body = es.subtitles_vtt(minimal_manifest())
        # WebVTT 不带序号：只有 WEBVTT 头、时间行、文本与空行
        assert not any(line.strip().isdigit() for line in body.splitlines())


class TestCli:
    def test_page_dir_defaults(self, tmp_path):
        page_dir = tmp_path / "lesson"
        (page_dir / "audio").mkdir(parents=True)
        (page_dir / "audio" / "narration_timing.json").write_text(
            json.dumps(minimal_manifest()), encoding="utf-8")
        # 走真实子进程，贴近用户调用
        proc = subprocess.run(
            [sys.executable, str(Path(es.__file__).resolve()), str(page_dir)],
            capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        srt = page_dir / "lesson.srt"
        vtt = page_dir / "lesson.vtt"
        assert srt.is_file() and vtt.is_file()
        assert "第一句。" in srt.read_text(encoding="utf-8")
        assert vtt.read_text(encoding="utf-8").startswith("WEBVTT")

    def test_timing_flag_and_single_format(self, tmp_path):
        timing = tmp_path / "audio" / "narration_timing.json"
        timing.parent.mkdir()
        timing.write_text(json.dumps(minimal_manifest()), encoding="utf-8")
        out = tmp_path / "subs.srt"
        proc = subprocess.run(
            [sys.executable, str(Path(es.__file__).resolve()),
             "--timing", str(timing), "--format", "srt", "--out", str(out)],
            capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()
        assert "00:00:00,000 --> 00:00:01,000" in out.read_text(encoding="utf-8")

    def test_out_with_both_is_rejected(self, tmp_path):
        timing = tmp_path / "t.json"
        timing.write_text(json.dumps(minimal_manifest()), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(Path(es.__file__).resolve()),
             "--timing", str(timing), "--out", str(tmp_path / "x.srt")],
            capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode != 0
        assert "--out 只能配合单一格式" in proc.stderr

    def test_speaker_and_hl_via_cli(self, tmp_path):
        m = manifest_with_speakers()
        m["scenes"][0]["sentences"][2]["hl"] = True
        timing = tmp_path / "audio" / "narration_timing.json"
        timing.parent.mkdir()
        timing.write_text(json.dumps(m), encoding="utf-8")
        out = tmp_path / "subs.vtt"
        proc = subprocess.run(
            [sys.executable, str(Path(es.__file__).resolve()),
             "--timing", str(timing), "--format", "vtt", "--out", str(out),
             "--speaker", "--hl-mark"],
            capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        body = out.read_text(encoding="utf-8")
        assert "小明：第二句。" in body
        assert "◆ 第三句。" in body
