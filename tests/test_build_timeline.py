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

    def test_non_dict_scene(self):
        timing = conftest_minimal()
        timing["scenes"][1] = "seg-2"
        with pytest.raises(SystemExit, match="必须是对象"):
            bt.build(timing, content_map())


def conftest_minimal(**kw):
    from conftest import minimal_manifest
    return minimal_manifest(**kw)
