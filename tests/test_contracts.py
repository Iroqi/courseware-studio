"""_contracts 契约原语测试。"""
import pytest

import _contracts as c


class TestRequireSchemaVersion:
    def test_ok(self):
        c.require_schema_version({"schema_version": 1}, "x")

    def test_missing_key(self):
        with pytest.raises(ValueError, match="schema_version"):
            c.require_schema_version({"scenes": []}, "x")

    def test_non_dict(self):
        with pytest.raises(ValueError):
            c.require_schema_version([], "x")

    def test_bool_true_rejected(self):
        # bool 是 int 子类，True == 1 会混过，必须显式拒绝
        with pytest.raises(ValueError):
            c.require_schema_version({"schema_version": True}, "x")

    def test_wrong_version(self):
        with pytest.raises(ValueError):
            c.require_schema_version({"schema_version": 2}, "x")


class TestCheckDegradedStatus:
    def test_ok_passes(self):
        assert c.check_degraded_status({"status": "ok"}, False, "t") is None

    def test_missing_status_returns_warning(self):
        assert "status" in c.check_degraded_status({}, False, "t")

    def test_degraded_not_allowed_raises(self):
        with pytest.raises(ValueError, match="修复 TTS"):
            c.check_degraded_status({"status": "degraded", "degraded": {}}, False, "t")

    def test_degraded_allowed(self):
        assert c.check_degraded_status(
            {"status": "degraded", "degraded": {}}, True, "t") is None


class TestSegmentStepId:
    def test_explicit_id(self):
        assert c.segment_step_id({"id": "seg-9"}, 3) == "seg-9"

    def test_fallback_counts_from_one_after_filtering(self):
        # kept_index 是滤掉空段后的序号，不是原始下标
        assert c.segment_step_id({}, 1) == "seg-1"

    def test_empty_id_falls_back(self):
        assert c.segment_step_id({"id": ""}, 4) == "seg-4"


class TestTitles:
    def test_opening_fallback_chain(self):
        assert c.opening_title({"title": "T"}) == "T"
        assert c.opening_title({"opening_title": "O", "title": "T"}) == "O"
        assert c.opening_title({}) == "开场"

    def test_closing_fallback(self):
        assert c.closing_title({"closing_title": "C"}) == "C"
        assert c.closing_title({}) == "小结"


class TestInlineJson:
    def test_lt_escaped(self):
        out = c.inline_json({"text": "a<b>c</script><!--"})
        assert "<" not in out
        assert "\\u003c" in out

    def test_roundtrip(self):
        import json
        payload = {"a": [1, 2.5, "中文", None]}
        assert json.loads(c.inline_json(payload)) == payload


class TestRequireFiniteNumber:
    def test_ok(self):
        assert c.require_finite_number(3.5, "x") == 3.5

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            c.require_finite_number(True, "x")

    def test_nan_inf_rejected(self):
        with pytest.raises(ValueError):
            c.require_finite_number(float("nan"), "x")
        with pytest.raises(ValueError):
            c.require_finite_number(float("inf"), "x")

    def test_huge_int_rejected(self):
        # 400 位大整数 float() 溢出，必须按校验失败处理
        with pytest.raises(ValueError):
            c.require_finite_number(10 ** 400, "x")

    def test_positive_nonnegative(self):
        with pytest.raises(ValueError):
            c.require_finite_number(0, "x", positive=True)
        assert c.require_finite_number(0, "x", nonnegative=True) == 0
        with pytest.raises(ValueError):
            c.require_finite_number(-1, "x", nonnegative=True)

    def test_str_rejected(self):
        with pytest.raises(ValueError):
            c.require_finite_number("1.5", "x")


class TestValidateSpeed:
    def test_ok(self):
        assert c.validate_speed(1.0) == 1.0

    def test_zero_and_negative(self):
        with pytest.raises(ValueError):
            c.validate_speed(0)
        with pytest.raises(ValueError):
            c.validate_speed(-0.5)

    def test_non_number(self):
        with pytest.raises(ValueError):
            c.validate_speed("fast")
        with pytest.raises(ValueError):
            c.validate_speed(None)


class TestNormalizeBeat:
    def test_none_returns_none_list(self):
        assert c.normalize_beat(None, "x", 3) == [None, None, None]

    def test_pause_and_speed(self):
        beats = c.normalize_beat({"2": {"pause": 0.5}, "3": {"speed": 1.2}}, "x", 3)
        assert beats == [None, {"pause": 0.5}, {"speed": 1.2}]

    def test_out_of_range(self):
        with pytest.raises(ValueError, match="只有 3 句"):
            c.normalize_beat({"4": {"pause": 0.5}}, "x", 3)

    def test_non_numeric_key(self):
        with pytest.raises(ValueError):
            c.normalize_beat({"two": {"pause": 0.5}}, "x", 3)

    def test_unknown_key(self):
        with pytest.raises(ValueError, match="未知节拍键"):
            c.normalize_beat({"1": {"pause": 0.5, "bogus": 1}}, "x", 3)

    def test_pause_over_limit(self):
        with pytest.raises(ValueError, match="上限"):
            c.normalize_beat({"1": {"pause": 6.0}}, "x", 3)

    def test_non_dict_value(self):
        with pytest.raises(ValueError):
            c.normalize_beat({"1": 0.5}, "x", 3)

    def test_non_dict_raw(self):
        with pytest.raises(ValueError):
            c.normalize_beat([1, 2], "x", 3)

    def test_zero_index(self):
        with pytest.raises(ValueError):
            c.normalize_beat({"0": {"pause": 0.5}}, "x", 3)
