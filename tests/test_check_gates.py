"""check_gates：解析器单测 + 基于范本页面的 static_check 集成。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import check_gates as cg
import build_page as bp
import build_timeline as bt
from conftest import TEMPLATE_HTML


# ── _js_strip ────────────────────────────────────────────────────
class TestJsStrip:
    def test_string_blanked(self):
        out = cg._js_strip("var a = 'hello';")
        assert "hello" not in out
        assert out.count("'") == 2  # 引号保留、内容变空格

    def test_line_comment_blanked(self):
        out = cg._js_strip("var a = 1; // comment")
        assert "comment" not in out

    def test_block_comment_blanked(self):
        out = cg._js_strip("/* multi\nline */ var a = 1;")
        assert "multi" not in out

    def test_regex_blanked(self):
        out = cg._js_strip("var re = /ab+c/gi;")
        assert "ab+c" not in out

    def test_regex_after_operator(self):
        # 除法不是正则：`a / b` 里的 b 保留
        out = cg._js_strip("var x = a / b;")
        assert "b" in out

    def test_offsets_preserved(self):
        src = "var a = 'x'; var b = 'y';"
        out = cg._js_strip(src)
        assert len(out) == len(src)
        assert out[0:7] == "var a ="


# ── _html_scan ───────────────────────────────────────────────────
class TestHtmlScan:
    def test_real_script_body(self):
        comments, bodies = cg._html_scan('<script id="x">var a = 1;</script>')
        assert len(bodies) == 1
        assert bodies[0][0] > 0

    def test_comment_with_script_ignored(self):
        comments, bodies = cg._html_scan('<!-- <script id="x">ghost</script> -->')
        assert not bodies
        assert len(comments) == 1

    def test_comment_before_real_script(self):
        src = '<!-- <script>ghost</script> --><script id="lesson-timeline">real</script>'
        comments, bodies = cg._html_scan(src)
        assert len(bodies) == 1
        assert "real" in src[bodies[0][0]:bodies[0][1]]

    def test_unclosed_comment(self):
        comments, bodies = cg._html_scan('<!-- never closed')
        assert comments


# ── _cap_literals ────────────────────────────────────────────────
class TestCapLiterals:
    def test_extracts_strings(self):
        # 契约 helper 是 txt / badge（stage.md 口径），cap 不是
        body = "txt('第一句'); badge('第二句');"
        out = cg._cap_literals(body)
        assert "第一句" in out
        assert "第二句" in out

    def test_ignores_color_literals(self):
        body = "txt(attrs, '#ff8800');"
        assert cg._cap_literals(body) == []

    def test_ignores_substring_named_funcs(self):
        body = "hotbadge('x'); plotTxt('y');"
        assert cg._cap_literals(body) == []

    def test_nested_args(self):
        body = "txt(attrs, `模板串${x}`);"
        out = cg._cap_literals(body)
        assert any("模板串" in s for s in out)


# ── _render_map ──────────────────────────────────────────────────
class TestRenderMap:
    def test_named_and_inline(self):
        # RENDER 键带 '-'（seg-1）时必须用引号包裹才能被 vm_re 解析（与范本一致）
        src = (
            "var RENDER = {"
            '"seg-1": renderSeg1,'
            '"seg-2": function(step) { return 1; },'
            '"seg-3": (step) => { return 2; }'
            "};"
        )
        mapping, inline = cg._render_map(src)
        assert mapping.get("seg-1") == "renderSeg1"
        assert "seg-2#inline" in inline
        assert "seg-3#inline" in inline
        assert "return 1" in inline["seg-2#inline"]

    def test_no_render(self):
        mapping, inline = cg._render_map("var x = 1;")
        assert mapping == {} and inline == {}

    def test_quoted_keys(self):
        src = 'var RENDER = {"seg-1": renderSeg1};'
        mapping, _ = cg._render_map(src)
        assert mapping.get("seg-1") == "renderSeg1"


# ── _gate_scene_counts ───────────────────────────────────────────
class TestGateSceneCounts:
    def test_counts_quoted_scenes(self):
        src = "var GATES = [{scene:'seg-1', at:0},{scene:'seg-1', at:2},{scene:'seg-2', at:1}];"
        counts = cg._gate_scene_counts(src, cg._js_strip_html(src))
        assert counts == {"seg-1": 2, "seg-2": 1}

    def test_expr_scene_sentinel(self):
        src = "var GATES = [{scene: dynamicId, at:0}];"
        counts = cg._gate_scene_counts(src, cg._js_strip_html(src))
        assert counts.get(cg.EXPR_SCENE_KEY) == 1

    def test_no_gates(self):
        assert cg._gate_scene_counts("var x=1;", "var x=1;") == {}

    def test_commented_gates_ignored(self):
        # 注释必须在真 <script> 体内才会被 _js_strip_html 空白化
        src = "<script>// var GATES = [{scene:'seg-1'}];</script>"
        counts = cg._gate_scene_counts(src, cg._js_strip_html(src))
        assert counts == {}


# ── _timeline_from_html ──────────────────────────────────────────
class TestTimelineFromHtml:
    def test_script_block(self):
        data = {"schema_version": 1, "scenes": [{"step_id": "seg-1"}]}
        payload = json.dumps(data)
        src = '<script id="lesson-timeline">' + payload + "</script>"
        parsed, err = cg._timeline_from_html(src)
        assert err is None
        assert parsed["scenes"][0]["step_id"] == "seg-1"

    def test_escaped(self):
        data = {"schema_version": 1, "scenes": [{"step_id": "seg-1"}]}
        payload = json.dumps(data).replace("<", "\\u003c")
        src = '<script id="lesson-timeline">' + payload + "</script>"
        parsed, err = cg._timeline_from_html(src)
        assert err is None
        assert parsed["scenes"][0]["step_id"] == "seg-1"

    def test_invalid(self):
        parsed, err = cg._timeline_from_html("{not json")
        assert parsed is None
        assert err

    def test_script_block_present_but_broken_json(self):
        """回归：script 块存在但内容 JSON 解析失败时必须返回错误，
        而不是静默返回 (None, None)（mutation 曾全绿放行）。"""
        src = '<script id="lesson-timeline">{not json</script>'
        parsed, err = cg._timeline_from_html(src)
        assert parsed is None
        assert err
        assert "不是合法 JSON" in err

    def test_commented_block_then_broken_block(self):
        """注释掉的旧时间轴 + 新时间轴内容损坏：错误必须来自真实块。"""
        src = (
            '<!-- <script id="lesson-timeline">{"schema_version":1}</script> -->\n'
            '<script id="lesson-timeline">{broken</script>'
        )
        parsed, err = cg._timeline_from_html(src)
        assert parsed is None
        assert err
        assert "不是合法 JSON" in err


# ── static_check 集成（范本页面 + 最小时间轴）──────────────────────
def _template_aligned_manifest():
    scenes = []
    t = 0.0
    ids = ["opening", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5", "seg-6", "closing"]
    titles = {"opening": "开场", "closing": "小结"}
    for i, sid in enumerate(ids):
        title = titles.get(sid, f"第{i}节")
        sentences = []
        for k in range(1, 4):
            sentences.append({"start": round(t, 3), "duration": 1.0,
                              "text": f"{sid}第{k}句。"})
            t += 1.4
        end = round(sentences[-1]["start"] + 1.0, 3)
        scenes.append({
            "step_id": sid, "title": title, "tagline": "",
            "start": sentences[0]["start"],
            "duration": round(end - sentences[0]["start"], 3),
            "end": end,
            "sentences": sentences,
        })
        t = round(end + 0.5, 3)
    return {
        "schema_version": 1, "status": "ok", "title": "测试课",
        "voice_id": "冰糖", "audio": "combined.wav",
        "total_duration": round(t - 0.5, 3),
        "degraded": {"tts_silence_fallback_count": 0, "dropped_sentence_count": 0},
        "scenes": scenes,
    }


@pytest.fixture()
def built_lesson(tmp_path):
    manifest = _template_aligned_manifest()
    content = {sc["step_id"]: {"title": sc["title"], "tagline": "", "hl": None}
               for sc in manifest["scenes"]}
    timeline = {"schema_version": 1, "scenes": bt.build(manifest, content)}
    template = TEMPLATE_HTML.read_text(encoding="utf-8")
    page = bp._embed_timeline(template, timeline)
    page = bp._replace_audio_src(page)
    page_dir = tmp_path / "lesson"
    (page_dir / "audio").mkdir(parents=True)
    (page_dir / "index.html").write_text(page, encoding="utf-8")
    (page_dir / "audio" / "narration_timing.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=1", "-ar", "24000",
                    "-ac", "1", str(page_dir / "audio" / "combined.wav")],
                   check=True)
    shutil.copy(Path(__file__).resolve().parents[1] / "scripts" / "interactive_runtime.js",
                page_dir / "interactive_runtime.js")
    return page_dir


class TestStaticCheckIntegration:
    def test_built_lesson_passes(self, built_lesson):
        result = cg.static_check(
            (built_lesson / "index.html").read_text(encoding="utf-8"),
            allow_degraded=False, page_dir=built_lesson)
        assert result["errors"] == []
        assert result["stats"]["scenes"] == 8
        assert result["stats"]["sentences"] == 24

    def test_tampered_text_fails(self, built_lesson):
        page = (built_lesson / "index.html").read_text(encoding="utf-8")
        # 把 manifest 某句文本改掉 → 装配对齐检查应报文本不一致
        manifest = json.loads(
            (built_lesson / "audio" / "narration_timing.json").read_text(encoding="utf-8"))
        manifest["scenes"][0]["sentences"][0]["text"] = "被篡改的句子。"
        (built_lesson / "audio" / "narration_timing.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        result = cg.static_check(page, allow_degraded=False, page_dir=built_lesson)
        assert any("文本与 manifest 不一致" in e for e in result["errors"])

    def test_missing_audio_fails(self, built_lesson):
        (built_lesson / "audio" / "combined.wav").unlink()
        page = (built_lesson / "index.html").read_text(encoding="utf-8")
        result = cg.static_check(page, allow_degraded=False, page_dir=built_lesson)
        assert any("主音频文件不存在" in e for e in result["errors"])

    def test_audio_dir_extra_file_fails(self, built_lesson):
        (built_lesson / "audio" / "leftover.wav").write_bytes(b"x")
        page = (built_lesson / "index.html").read_text(encoding="utf-8")
        result = cg.static_check(page, allow_degraded=False, page_dir=built_lesson)
        assert any("交付残留" in e for e in result["errors"])
