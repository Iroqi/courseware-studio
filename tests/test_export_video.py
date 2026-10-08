"""export_video：句表解析、帧计划、viewBox、audio src。"""
import json

import pytest

import export_video as ev
from conftest import TEMPLATE_HTML


def _page_with_timeline(scenes):
    data = {"schema_version": 1, "scenes": scenes}
    return ('<html><body><script id="lesson-timeline">'
            + json.dumps(data).replace("<", "\\u003c")
            + "</script></body></html>")


def _scene(step_id, narration):
    return {
        "step_id": step_id,
        "runtime": {"start": narration[0]["start"], "duration": 1.0,
                    "end": narration[-1]["start"] + narration[-1]["duration"],
                    "narration": narration},
    }


class TestTimelineSentences:
    def test_basic(self):
        src = _page_with_timeline([
            _scene("seg-1", [{"start": 0.0, "duration": 1.0, "text": "a"},
                             {"start": 1.5, "duration": 1.0, "text": "b"}]),
        ])
        out = ev._timeline_sentences(src)
        assert out == [(0.0, 1.0, 0), (1.5, 1.0, 0)]

    def test_scene_index_carried(self):
        src = _page_with_timeline([
            _scene("seg-1", [{"start": 0.0, "duration": 1.0, "text": "a"}]),
            _scene("seg-2", [{"start": 2.0, "duration": 1.0, "text": "b"}]),
        ])
        out = ev._timeline_sentences(src)
        assert [s[2] for s in out] == [0, 1]

    def test_invalid_json_raises(self):
        with pytest.raises(SystemExit):
            ev._timeline_sentences("<html>{bad</html>")

    def test_missing_timeline_raises(self):
        with pytest.raises(SystemExit, match="没有内联时间轴|时间轴已内联"):
            ev._timeline_sentences("<html><body>no timeline</body></html>")

    def test_non_finite_start_raises(self):
        src = _page_with_timeline([
            _scene("seg-1", [{"start": float("nan"), "duration": 1.0, "text": "a"}]),
        ])
        with pytest.raises(SystemExit, match="有限数值"):
            ev._timeline_sentences(src)

    def test_negative_start_raises(self):
        src = _page_with_timeline([
            _scene("seg-1", [{"start": -1.0, "duration": 1.0, "text": "a"}]),
        ])
        with pytest.raises(SystemExit, match="非负"):
            ev._timeline_sentences(src)


class TestFramePlan:
    def test_leading_silence_preserved(self):
        plan = ev._frame_plan([(1.0, 1.0, 0), (2.5, 1.0, 0)], 4.0)
        assert plan[0] == (0.01, 1.0, -1)  # 前置静音帧
        assert plan[1][2] == 0
        assert plan[-1][1] == pytest.approx(1.5)  # 末帧 = 4.0 - 2.5

    def test_no_leading_silence_when_zero(self):
        plan = ev._frame_plan([(0.0, 1.0, 0)], 2.0)
        assert len(plan) == 1

    def test_monotonic_required(self):
        with pytest.raises(ValueError, match="递增"):
            ev._frame_plan([(2.0, 1.0, 0), (1.0, 1.0, 0)], 5.0)

    def test_overlap_rejected(self):
        with pytest.raises(ValueError, match="重叠"):
            ev._frame_plan([(0.0, 1.5, 0), (1.4, 1.0, 0)], 5.0)

    def test_beyond_audio_rejected(self):
        with pytest.raises(ValueError, match="超过旁白音频时长"):
            ev._frame_plan([(0.0, 6.0, 0)], 5.0)

    def test_covers_full_audio(self):
        # 末句结束点 < wav_dur 时，末帧 show 补到 wav_dur
        plan = ev._frame_plan([(0.0, 1.0, 0), (1.5, 1.0, 0)], 4.0)
        assert plan[-1][1] == pytest.approx(2.5)

    def test_wav_dur_invalid(self):
        with pytest.raises(ValueError):
            ev._frame_plan([(0.0, 1.0, 0)], 0)
        with pytest.raises(ValueError):
            ev._frame_plan([(0.0, 1.0, 0)], float("inf"))

    def test_short_sentence_capture_inside(self):
        # 0.05s 超短句：capture 时间不能钳进上一句区间
        plan = ev._frame_plan([(0.0, 0.05, 0)], 1.0)
        assert 0 < plan[0][0] < 0.05


class TestViewBox:
    def test_finds_stage_svg(self):
        # #stage 是容器，viewBox 的 svg 在其内部/之后（导出从 #stage 往后找）
        src = '<div id="stage"><svg viewBox="0 0 100 200"></svg></div>'
        assert ev._view_box(src) == (100.0, 200.0)

    def test_ignores_svg_before_stage(self):
        src = ('<svg viewBox="0 0 10 10"></svg>'
               '<div id="stage"></div><svg viewBox="0 0 100 200"></svg>')
        assert ev._view_box(src) == (100.0, 200.0)

    def test_fallback_when_missing(self):
        assert ev._view_box("<html></html>") == (1000.0, 460.0)


class TestAudioSrc:
    def test_attr_src(self):
        src = '<audio id="main-audio" src="audio/combined.wav"></audio>'
        assert ev._audio_src(src) == "audio/combined.wav"

    def test_src_before_id(self):
        # 属性顺序无关
        src = '<audio src="audio/combined.wav" id="main-audio"></audio>'
        assert ev._audio_src(src) == "audio/combined.wav"

    def test_source_child(self):
        src = '<audio id="main-audio"><source src="audio/combined.wav"></audio>'
        assert ev._audio_src(src) == "audio/combined.wav"

    def test_commented_out_not_seen(self):
        src = '<!-- <audio id="main-audio" src="audio/ghost.wav"></audio> -->' \
              '<audio id="main-audio" src="audio/combined.wav"></audio>'
        assert ev._audio_src(src) == "audio/combined.wav"

    def test_missing_raises(self):
        with pytest.raises(SystemExit, match="找不到 #main-audio"):
            ev._audio_src("<html></html>")

    def test_data_src_ignored(self):
        src = '<audio id="main-audio" data-src="audio/old.wav" src="audio/combined.wav"></audio>'
        assert ev._audio_src(src) == "audio/combined.wav"

    def test_real_template(self):
        src = TEMPLATE_HTML.read_text(encoding="utf-8")
        assert ev._audio_src(src) == "audio/combined.wav"
