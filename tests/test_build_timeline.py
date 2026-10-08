"""build_timeline.build：时间轴构建的接受/拒绝门。"""
import copy

import pytest

import build_timeline as bt


def content_map(**over):
    content = {
        "seg-1": {"title": "开场", "tagline": "", "hl": [2]},
        "seg-2": {"title": "正文", "tagline": "", "hl": None},
        "closing": {"title": "小结", "tagline": "", "hl": None},
    }
    content.update(over)
    return content


class TestBuild:
    def test_valid(self):
        scenes = bt.build(conftest_minimal(), content_map())
        assert len(scenes) == 3
        assert scenes[0]["step_id"] == "seg-1"
        # hl 标记落进对应句
        assert scenes[0]["runtime"]["narration"][1]["hl"] is True
        assert "hl" not in scenes[0]["runtime"]["narration"][0]
        # 内部字段都 round 到 3 位
        assert scenes[0]["runtime"]["start"] == 0.0

    def test_missing_schema_version(self):
        timing = conftest_minimal()
        del timing["schema_version"]
        with pytest.raises(SystemExit, match="schema_version"):
            bt.build(timing, content_map())

    def test_no_scenes(self):
        timing = conftest_minimal(scenes=[])
        with pytest.raises(SystemExit, match="没有任何场景"):
            bt.build(timing, content_map())

    def test_duplicate_step_id(self):
        timing = conftest_minimal()
        timing["scenes"].append(copy.deepcopy(timing["scenes"][0]))
        with pytest.raises(SystemExit, match="重复 step_id"):
            bt.build(timing, content_map())

    def test_missing_step_id(self):
        timing = conftest_minimal()
        del timing["scenes"][0]["step_id"]
        with pytest.raises(SystemExit, match="缺少 step_id"):
            bt.build(timing, content_map())

    def test_scene_without_sentences(self):
        timing = conftest_minimal()
        timing["scenes"][1]["sentences"] = []
        with pytest.raises(SystemExit, match="没有句子"):
            bt.build(timing, content_map())

    def test_scene_align_mismatch(self):
        timing = conftest_minimal()
        timing["scenes"][0]["end"] = 4.5  # start+duration=4.0, 差 0.5 > 0.12
        with pytest.raises(SystemExit, match="start \\+ duration 与 end 相差"):
            bt.build(timing, content_map())

    def test_scene_not_increasing(self):
        timing = conftest_minimal()
        # seg-3 起点早于上一场 end=8.0，但保持 start+duration==end（否则先撞对齐门）
        timing["scenes"][2]["start"] = 3.0
        timing["scenes"][2]["duration"] = 9.0  # end 保持 12.0
        with pytest.raises(SystemExit, match="没有按时间递增"):
            bt.build(timing, content_map())

    def test_scene_overlap(self):
        timing = conftest_minimal()
        # seg-2 起点 3.9 与 seg-1 end=4.0 重叠；duration 同步改为 4.1 保持对齐
        timing["scenes"][1]["start"] = 3.9
        timing["scenes"][1]["duration"] = 4.1  # end 保持 8.0
        with pytest.raises(SystemExit, match="重叠"):
            bt.build(timing, content_map())

    def test_sentence_overlap(self):
        timing = conftest_minimal()
        timing["scenes"][0]["sentences"][1]["start"] = 0.5  # 与第 1 句 [0,1] 重叠
        with pytest.raises(SystemExit, match="重叠"):
            bt.build(timing, content_map())

    def test_sentence_outside_scene(self):
        timing = conftest_minimal()
        timing["scenes"][0]["sentences"][0]["start"] = 3.9  # 出场景 [0,4]
        with pytest.raises(SystemExit, match="超出场景"):
            bt.build(timing, content_map())

    def test_tail_drift(self):
        timing = conftest_minimal()
        # 最后一句 [10.8, 12.0]，end=12.0：把末句 duration 改小让尾巴漂移
        timing["scenes"][2]["sentences"][1]["duration"] = 0.5
        with pytest.raises(SystemExit, match="最后一句旁白结束点"):
            bt.build(timing, content_map())

    def test_negative_start(self):
        timing = conftest_minimal()
        timing["scenes"][0]["start"] = -1
        with pytest.raises(SystemExit, match="非负"):
            bt.build(timing, content_map())

    def test_nan_duration(self):
        timing = conftest_minimal()
        timing["scenes"][0]["duration"] = float("nan")
        with pytest.raises(SystemExit, match="有限数字"):
            bt.build(timing, content_map())

    def test_empty_text(self):
        timing = conftest_minimal()
        timing["scenes"][0]["sentences"][0]["text"] = "   "
        with pytest.raises(SystemExit, match="text 为空"):
            bt.build(timing, content_map())

    def test_content_map_filters_empty_segments(self):
        """回归：空段必须被滤掉再编号，否则 seg-N 与音频侧错位，
        title/tagline/hl 会全部静默丢失（mutation 曾全绿放行）。"""
        source = {
            "schema_version": 1, "title": "T", "opening": True,
            "opening_title": "开场", "opening_text": "开场白",
            "segments": [
                {"title": "第一节", "text": "内容一"},
                {"title": "空段", "text": "   "},      # 空段：滤掉
                {"title": "第三节", "text": "内容三", "hl": [1]},
                {"title": "只讲不讲", "dialogue": []},  # 空对话段：同样滤掉
            ],
            "closing": True, "closing_title": "小结", "closing_text": "收尾",
        }
        cm = bt._content_map(source)
        # 编号按**内容段**数：第一节=seg-1，第三节=seg-2，空段不入列
        assert list(cm.keys()) == ["opening", "seg-1", "seg-2", "closing"]
        assert cm["seg-1"]["title"] == "第一节"
        # 第三节被编号为 seg-2，且 hl 没有因错位丢失
        assert cm["seg-2"]["title"] == "第三节"
        assert cm["seg-2"]["hl"] == [1]

    def test_content_map_dialogue_only_segment_kept(self):
        """只有 dialogue 没有 text 的段是有内容段（不能被滤掉）。"""
        source = {
            "schema_version": 1, "title": "T",
            "segments": [
                {"title": "对话段", "dialogue": [{"speaker": "A", "text": "你好"}]},
            ],
        }
        cm = bt._content_map(source)
        assert list(cm.keys()) == ["seg-1"]
        assert cm["seg-1"]["title"] == "对话段"

    def test_content_map_non_dict_segment_skipped(self):
        """坏形状段（非对象）按空段处理，不参与编号。"""
        source = {
            "schema_version": 1, "title": "T",
            "segments": [
                {"title": "正常段", "text": "内容"},
                "垃圾段",
                {"title": "末段", "text": "收尾"},
            ],
        }
        cm = bt._content_map(source)
        assert list(cm.keys()) == ["seg-1", "seg-2"]
        assert cm["seg-1"]["title"] == "正常段"
        assert cm["seg-2"]["title"] == "末段"

    def test_non_dict_scene(self):
        timing = conftest_minimal()
        timing["scenes"][1] = "seg-2"
        with pytest.raises(SystemExit, match="必须是对象"):
            bt.build(timing, content_map())


def conftest_minimal(**kw):
    from conftest import minimal_manifest
    return minimal_manifest(**kw)
