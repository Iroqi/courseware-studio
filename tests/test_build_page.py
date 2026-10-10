"""build_page：时间轴解析、manifest 对齐门、模板替换。"""
import json
from pathlib import Path

import pytest

import build_page as bp
from conftest import minimal_manifest, TEMPLATE_HTML


def minimal_timeline():
    m = minimal_manifest()
    scenes = []
    for sc in m["scenes"]:
        scenes.append({
            "step_id": sc["step_id"],
            "content": {"title": sc["title"], "tagline": sc["tagline"]},
            "runtime": {
                "start": sc["start"],
                "duration": sc["duration"],
                "end": sc["end"],
                "narration": [
                    {"start": s["start"], "duration": s["duration"], "text": s["text"]}
                    for s in sc["sentences"]
                ],
            },
        })
    return {"schema_version": 1, "scenes": scenes}


class TestTimelineJson:
    def test_from_script_block(self):
        data = minimal_timeline()
        raw = '<html><script id="lesson-timeline">' + json.dumps(data) + "</script></html>"
        assert bp._timeline_json(raw, Path("x.html"))["scenes"][0]["step_id"] == "seg-1"

    def test_bare_json(self):
        data = minimal_timeline()
        assert bp._timeline_json(json.dumps(data), Path("x.html"))["scenes"]

    def test_escaped_entities_fallback(self):
        data = minimal_timeline()
        payload = json.dumps(data).replace("<", "\\u003c")
        assert bp._timeline_json(payload, Path("x.html"))["scenes"]

    def test_invalid_json(self):
        with pytest.raises(SystemExit, match="不是合法 JSON"):
            bp._timeline_json("{oops", Path("x.html"))

    def test_no_scenes(self):
        with pytest.raises(SystemExit, match="scenes"):
            bp._timeline_json('{"schema_version": 1, "scenes": []}', Path("x.html"))

    def test_missing_schema_version(self):
        with pytest.raises(SystemExit, match="schema_version"):
            bp._timeline_json('{"scenes": [{}]}', Path("x.html"))


class TestValidateTimingAlignment:
    def test_matching(self):
        bp._validate_timing_alignment(minimal_timeline(), minimal_manifest())

    def test_scene_count_mismatch(self):
        tl = minimal_timeline()
        mf = minimal_manifest()
        mf["scenes"] = mf["scenes"][:2]
        with pytest.raises(SystemExit, match="场景数不一致"):
            bp._validate_timing_alignment(tl, mf)

    def test_step_id_mismatch(self):
        tl = minimal_timeline()
        mf = minimal_manifest()
        mf["scenes"][0]["step_id"] = "seg-x"
        with pytest.raises(SystemExit, match="step_id 不一致"):
            bp._validate_timing_alignment(tl, mf)

    def test_runtime_field_mismatch(self):
        tl = minimal_timeline()
        mf = minimal_manifest()
        mf["scenes"][0]["start"] = 9.9
        with pytest.raises(SystemExit, match="start 与 manifest 不一致"):
            bp._validate_timing_alignment(tl, mf)

    def test_sentence_count_mismatch(self):
        tl = minimal_timeline()
        mf = minimal_manifest()
        mf["scenes"][0]["sentences"] = mf["scenes"][0]["sentences"][:2]
        with pytest.raises(SystemExit, match="句子数与 manifest 不一致"):
            bp._validate_timing_alignment(tl, mf)

    def test_text_mismatch_whitespace_normalized(self):
        tl = minimal_timeline()
        mf = minimal_manifest()
        mf["scenes"][0]["sentences"][0]["text"] = "第一句。 "
        # 空白归一下仍一致，不应报错
        bp._validate_timing_alignment(tl, mf)

    def test_text_different(self):
        tl = minimal_timeline()
        mf = minimal_manifest()
        mf["scenes"][0]["sentences"][0]["text"] = "另一句。"
        with pytest.raises(SystemExit, match="文本与 manifest 不一致"):
            bp._validate_timing_alignment(tl, mf)


class TestEmbedAndReplace:
    def test_embed_timeline_roundtrip(self):
        template = '<script id="lesson-timeline">old</script>'
        out = bp._embed_timeline(template, minimal_timeline())
        assert "old" not in out
        # 嵌回后应可解析
        import re
        m = re.search(r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>(.*?)</script>',
                      out, re.S)
        assert json.loads(m.group(1))["scenes"]

    def test_embed_missing_hook(self):
        with pytest.raises(SystemExit, match="缺少 <script id=\"lesson-timeline\">"):
            bp._embed_timeline("<html></html>", minimal_timeline())

    def test_replace_audio_src(self):
        template = '<audio id="main-audio" src="audio/old.wav"></audio>'
        assert bp._replace_audio_src(template) == \
            '<audio id="main-audio" src="audio/combined.wav"></audio>'

    def test_replace_audio_missing_hook(self):
        with pytest.raises(SystemExit, match="缺少 #main-audio"):
            bp._replace_audio_src("<html></html>")

    def test_replace_title(self):
        page = "<html><head><title>旧标题</title></head><body></body></html>"
        out = bp._replace_title(page, "新课：排序算法")
        assert "<title>新课：排序算法</title>" in out
        assert "旧标题" not in out

    def test_replace_title_escapes(self):
        page = "<html><head><title>x</title></head></html>"
        out = bp._replace_title(page, "1 < 2 的真相")
        assert "1 &lt; 2 的真相" in out

    def test_replace_title_missing_hook(self):
        with pytest.raises(SystemExit, match="缺少 <title>"):
            bp._replace_title("<html></html>", "x")

    def test_replace_lang_present(self):
        page = '<html lang="zh-CN"><head></head></html>'
        out = bp._replace_lang(page, "en")
        assert '<html lang="en">' in out

    def test_replace_lang_absent(self):
        page = "<html><head></head></html>"
        out = bp._replace_lang(page, "en")
        assert '<html lang="en">' in out

    def test_replace_lang_missing_hook(self):
        with pytest.raises(SystemExit, match="缺少 <html>"):
            bp._replace_lang("<body></body>", "en")

    def test_meta_injection_roundtrip(self):
        # 官方范本：注入 title/lang 后页面结构完好、时间轴仍可解析
        src = TEMPLATE_HTML.read_text(encoding="utf-8")
        out = bp._replace_title(bp._replace_lang(src, "zh-Hans"), "测试标题")
        assert "<title>测试标题</title>" in out
        assert '<html lang="zh-Hans">' in out
        import re
        m = re.search(r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>(.*?)</script>',
                      out, re.S)
        assert json.loads(m.group(1))["scenes"]

    def test_real_template_roundtrip(self):
        # 官方范本：替换后时间轴可解析、audio src 被改写
        src = TEMPLATE_HTML.read_text(encoding="utf-8")
        out = bp._embed_timeline(src, minimal_timeline())
        import re
        assert json.loads(re.search(
            r'<script\b[^>]*\bid=["\']lesson-timeline["\'][^>]*>(.*?)</script>',
            out, re.S).group(1))["scenes"]
        assert 'src="audio/combined.wav"' in bp._replace_audio_src(out)
