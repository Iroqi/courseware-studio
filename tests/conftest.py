"""共享 fixture：把 scripts/ 挂到 sys.path，提供最小时间轴/讲稿构件。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 全链路冒烟用的范本页面（真实契约实现的唯一官方 fixture）。
TEMPLATE_HTML = REPO / "references" / "template.html"
TEMPLATE_NARRATION = REPO / "references" / "template-narration.json"


def minimal_manifest(scenes=None, **over):
    """构造一个 schema_version=1 的 narration_timing.json 形状对象。"""
    data = {
        "schema_version": 1,
        "status": "ok",
        "title": "测试课",
        "voice_id": "冰糖",
        "audio": "combined.wav",
        "total_duration": 12.0,
        "degraded": {"tts_silence_fallback_count": 0, "dropped_sentence_count": 0},
        "scenes": scenes if scenes is not None else [
            {
                "step_id": "seg-1",
                "title": "开场",
                "tagline": "",
                "start": 0.0,
                "duration": 4.0,
                "end": 4.0,
                "sentences": [
                    {"start": 0.0, "duration": 1.0, "text": "第一句。"},
                    {"start": 1.3, "duration": 1.2, "text": "第二句。"},
                    {"start": 2.8, "duration": 1.2, "text": "第三句。"},
                ],
            },
            {
                "step_id": "seg-2",
                "title": "正文",
                "tagline": "",
                "start": 4.5,
                "duration": 3.5,
                "end": 8.0,
                "sentences": [
                    {"start": 4.5, "duration": 1.5, "text": "第四句。"},
                    {"start": 6.2, "duration": 1.8, "text": "第五句。"},
                ],
            },
            {
                "step_id": "closing",
                "title": "小结",
                "tagline": "",
                "start": 8.5,
                "duration": 3.5,
                "end": 12.0,
                "sentences": [
                    {"start": 8.5, "duration": 2.0, "text": "第六句。"},
                    {"start": 10.8, "duration": 1.2, "text": "第七句。"},
                ],
            },
        ],
    }
    data.update(over)
    return data
