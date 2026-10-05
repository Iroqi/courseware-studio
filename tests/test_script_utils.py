"""_script_utils：分句 / 编码探测 / 原子写 / 越界守卫。"""
import os
import sys

import pytest

import _script_utils as su


class TestSplitSentences:
    def test_cn_only(self):
        assert su.split_sentences("你好。世界！真的吗？行。") == \
            ["你好。", "世界！", "真的吗？", "行。"]

    def test_ascii_period_not_split_when_followed_by_digit(self):
        # 小数点 / 版本号 / 域名不切
        assert su.split_sentences("Pi 是 3.14。结束。") == ["Pi 是 3.14。", "结束。"]

    def test_ellipsis_not_split(self):
        assert su.split_sentences("等等……他说完了。") == ["等等……他说完了。"]

    def test_abbreviation_not_split(self):
        assert su.split_sentences("Dr. Smith arrived. Then we left.") == \
            ["Dr. Smith arrived.", "Then we left."]

    def test_initials_not_split(self):
        assert su.split_sentences("J. Smith came in. We talked.") == \
            ["J. Smith came in.", "We talked."]

    def test_english_sentence_split_with_whitespace(self):
        assert su.split_sentences("Hello world. Next line.") == \
            ["Hello world.", "Next line."]

    def test_exclamation_question_require_trailing_space(self):
        # !? 需后随空白/文末才切；句子中间的 ! 不切
        assert su.split_sentences("真的!假的。") == ["真的!假的。"]

    def test_newline_splits(self):
        assert su.split_sentences("第一句\n第二句") == ["第一句", "第二句"]

    def test_lone_punctuation_merged_into_previous(self):
        # 成串「！！」的第二个终止符并入上一句，不丢字
        assert su.split_sentences("真的吗？！！") == ["真的吗？！！"]

    def test_empty(self):
        assert su.split_sentences("") == []
        assert su.split_sentences("   ") == []

    def test_short_sentence_warns_but_kept(self, capsys):
        sentences = su.split_sentences("短。完整的一句话在这里。")
        assert "少于 5 字" in capsys.readouterr().err
        assert sentences[0] == "短。"

    def test_quote_terminal_does_not_leave_dangling_closing_quote(self):
        # 引号里的句号照切，但闭引号必须归回引号内那句，不能留下
        # 以孤立引号开头的残句（口播/字幕会带上裸引号）
        assert su.split_sentences('他说"你好。"然后走了。') == \
            ['他说"你好。"', "然后走了。"]

    def test_quote_terminal_with_sentence_initial_quote(self):
        # 开引号起句 + 引号内句号：两句各自完整，不吞并
        assert su.split_sentences('"第一句。"然后第二句。') == \
            ['"第一句。"', "然后第二句。"]

    def test_quote_terminal_double_quote_pair(self):
        # 连续两处引号内句号：闭引号逐对归位
        assert su.split_sentences('他说"好。"我说"行。"') == \
            ['他说"好。"', '我说"行。"']

    def test_quote_terminal_nested(self):
        # 嵌套引号：按最近未闭合配对收敛
        assert su.split_sentences("他说'我说\"你好。\"'" ) == \
            ["他说'我说\"你好。\"'"]


class TestDecodeTextBlob:
    def test_utf8(self):
        assert su.decode_text_blob("你好".encode("utf-8")) == "你好"

    def test_utf8_bom(self):
        assert su.decode_text_blob(b"\xef\xbb\xbf" + "你好".encode("utf-8")) == "你好"

    def test_utf16_with_bom(self):
        # 探测链只在带 BOM 时尝试 utf-16（无 BOM 的偶数长 GBK 会"成功"解成乱码）
        assert su.decode_text_blob("你好".encode("utf-16")) == "你好"

    def test_gb18030(self):
        assert su.decode_text_blob("中文".encode("gb18030")) == "中文"

    def test_nul_bomless_utf16_rejected(self):
        # 含 NUL 的无 BOM UTF-16 判为不可识别
        assert su.decode_text_blob("AB".encode("utf-16-le")) is None


class TestWriteTextAtomic:
    def test_write_and_read(self, tmp_path):
        p = tmp_path / "a.txt"
        su.write_text_atomic(str(p), "内容\n")
        assert p.read_text(encoding="utf-8") == "内容\n"

    def test_overwrites(self, tmp_path):
        p = tmp_path / "a.txt"
        su.write_text_atomic(str(p), "one")
        su.write_text_atomic(str(p), "two")
        assert p.read_text(encoding="utf-8") == "two"


class TestGuardNotInSkillDir:
    def test_rejects_inside(self, tmp_path, monkeypatch):
        monkeypatch.setattr(su, "SKILL_DIR", str(tmp_path))
        bad = tmp_path / "scripts" / "x.mp4"
        bad.parent.mkdir()
        with pytest.raises(SystemExit):
            su.guard_not_in_skill_dir(("x", str(bad)))

    def test_allows_outside(self, tmp_path, monkeypatch):
        monkeypatch.setattr(su, "SKILL_DIR", str(tmp_path))
        su.guard_not_in_skill_dir(("x", str(tmp_path.parent / "out.mp4")))
