# Courseware Studio

把一段讲稿变成**一页会讲话的课件**：旁白音频是唯一时钟，画面、字幕、章节、门禁全部跟着
`audio.currentTime` 走，也可以顺手导出一条线性视频。

```
讲稿 ──► 逐句朗读 ──► 音频 + 逐句时间轴 ──► 一个 HTML 页面
```

不需要服务器、不需要构建、不需要账号——成品是一个目录，双击 `index.html` 就能放。

![CI](https://github.com/Iroqi/courseware-studio/actions/workflows/ci.yml/badge.svg)

这是一个 Claude Code / Agent 环境的 **skill**：`SKILL.md` 是给模型读的契约，本 README 是
给人的。你不需要理解它内部怎么拼页面，只需要知道**该给它什么信息**。

---

## 做出来是什么

一个左右两栏的单页课件。左边是"会跟着旁白变的舞台 + 播放条"，右边是可查的参考资料：

```
┌───────────────────────────────────────────┬────────────────────┐
│     A counter-intuitive result            │  ▪ reference       │
│      string order, not numeric order      │   · key point      │
│                                           │   · term           │
│   ┌───────────────────────────────────┐   │   · hands-on demo  │
│   │             SVG stage             │   │                    │
│   │       one step per sentence       │   │  (sidebar is not   │
│   │                                   │   │   played or scored)│
│   ├───────────────────────────────────┤   │                    │
│   │  ▪ [10,9,1].sort() → [1,10,9]     │   │                    │
│   │  the subtitle line                │   │                    │
│   └───────────────────────────────────┘   │                    │
│   ▸  ◂  ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬  3/8 00:12  1× □ │                    │
└───────────────────────────────────────────┴────────────────────┘
```

图里：左栏上块是章节头；中间框是 stage（内联 SVG 场景，一句一个视觉步，框底那行是画布内字幕）；
最下面是 rack 播放条（播放 / 倒回 / 进度条兼章节刻度 / 第几句 / 时间 / 倍速 / 全屏）；
右栏是参考资料（要点 · 术语 · 手感实验），不参与播放与评分。

**它给你的东西：**

- **逐句同步的幻灯片**：一句话一个画面步，走到哪句播到哪句，空档停在上一句不闪白；
- **画布内字幕**：字幕长在画面里，位置和字号固定，文案不用你维护第二份；
  多人对话（问答 / 情景剧）时左侧显示说话人标签；
- **进度条兼章节地图**：每节起点一根刻度，悬停报段名，可点可拖；
- **0.75× 到 2× 倍速、全屏**：画面字幕门禁全是时钟的纯函数，变速只是时钟走快些；
- **可选门禁**：在真正的认知转折点拦一下，答对才放行继续（选择题 / 图上点选 /
  归类拖放 / 排序 / 复述对照五种）；
- **一条 mp4**：不需要录屏，逐句截帧拼成线性视频，旁白同步；
- **标准字幕（SRT / WebVTT）**：把逐句时间轴序列化成字幕文件——无障碍跟随阅读、
  剪辑 / 压制软件后期、多语言与复习的交换格式，一行命令导出。

**它不管的事：** 掌握度、学习进度、复习排期、题库系统——那些是平台层的事。门禁放行
只代表"这堂课继续往下"，不代表"已掌握"。

---

## 你要给 agent 什么

用一句话提需求，skill 会在这些场景自动接管：把讲稿/文稿/演讲稿做成带旁白音频的网页课件、
生成 TTS 与字幕时间轴、或把成品课件导出成 mp4。

**四种典型输入：**

| 你说 | 你给 | 结果 |
|---|---|---|
| "把这份讲稿做成课件" | 一份讲稿（粘贴 / 文件） | 完整课件目录 |
| "用这份材料做一节课" | 文档 / 网页 / 粘贴文本 | agent 自己写讲稿，再做课件 |
| "把这个主题讲成一节课" | 一个主题 + 你希望讲到的点 | 同上 |
| "我有现成音频，帮我做成课件" | 音频 + 每句文本 | 不调 TTS，直接做页面 |

**可以顺带指定（不说就按默认来）：**

- **音色 / 语速**：默认「冰糖」1.0×，八种音色可选，不同段落可以不同语速
  （想先看全表：`python scripts/narration.py --list-voices`，无需 key）；
- **门禁**：默认按内容在认知转折点加 1–2 道。想多加就说"多出几道题"，不想要就说"不要门禁"；
- **导不导出视频**：默认只给网页，要 mp4 需要显式说一句；
- **讲稿结构**：分几段、每段讲什么、开场抛什么问题——这部分决定课件质量。你可以全权交给
  agent，也可以自己逐段写好再交给它。

**最值得你自己判断的一条：开场抛什么问题。** 它应该是学习者大概率会答错的那个，不是讲师
自己关心的那个——如果学习者一开口就答对，这场就没有信息增量。这个判断 Agent 替不了你。

---

## agent 会替你做的几步

了解即可，你不需要操作任何一步。如果你给的只有主题或素材、还没有讲稿，agent 会先把讲稿
写出来跟你确认，再往下走。

```
讲稿 → 逐句旁白 → 音频 + 逐句时间轴 → HTML 页面 → 交付检查 →（可选）mp4
```

1. **分句**：一条作者句子 = 一个视觉步 = 一个字幕步，先给你看分句结果再往下走；
2. **合成语音**：每句独立合成后拼成一条音轨，同时记下每句的起止时间；
3. **生成时间轴**：把逐句时间压成页面内联的数据，页面不再 fetch 外部 JSON；
4. **做页面**：以内联 SVG 逐句画场景、配字幕、在转折点放门禁、排侧栏参考资料；
5. **交付检查**：跑通用检查器验证字幕同步、门禁放行、页面无 JS 错误，有问题会自己修到过；
6. **导出视频**（可选）：逐句截帧拼接，旁白同步混入。

步骤 2–3 只在用内置 TTS 时发生；你提供现成音频时从第 4 步开始。

---

## 需要准备的东西

| 需要 | 用途 |
|---|---|
| 一个 MiMo TTS API key | 把讲稿合成语音（**用现成音频则不需要**） |
| Python 3.10+ | agent 跑脚本 |
| `ffmpeg` 在 PATH 上 | 合成音频、导出 mp4 |

key 只有一个是必需的：

```text
MIMO_API_KEY
```

推荐写成用户级配置，一次配好所有课件通用：

```ini
# Windows：C:\Users\<你>\.config\courseware-studio\.env
# macOS / Linux：~/.config/courseware-studio/.env

MIMO_API_KEY=sk-你的key
```

也可以放在课件工程目录下的 `.env`（agent 从讲稿所在目录往上找，走到当前工作目录为止），
或者设成系统环境变量。优先级：命令行参数 > 环境变量 > 工程 `.env` > 用户 `.env`。

```ini
# 可选，不配就用默认值
# MIMO_TTS_MODEL=mimo-v2.5-tts
# MIMO_BASE_URL=https://api.xiaomimimo.com/v1
```

两条提醒：

- **别让 key 出现在命令行参数里**（`--api-key sk-xxx`）——它会进进程列表和 shell 历史。
- **`.env` 用 UTF-8 保存**。记事本默认可能存成 GBK，PowerShell 5.1 的 `>` 会写出
  UTF-16，两种情况的表现都是"明明配了 key，却报没有 key"。

---

## 案例

**你给的：** 一份讲 `JavaScript sort()` 的三页笔记，要求"做成一节课，段首出个小题，
最后导个视频方便发群里"。

**你拿到的：** 一个 `lesson/` 目录

```text
lesson/
├── index.html                 # 成品页，双击就能放
├── interactive_runtime.js     # 交互内核
└── audio/
    ├── combined.wav           # 整条旁白
    └── narration_timing.json  # 逐句时间
```

打开后：开场先问"把 10、9、1 排序结果是什么"（多数人会答错）→ 六段递进讲解，每句旁白
配一屏图 → 段首有"先想一想"，答对才出「继续」→ 结尾回到开场那个问题 → 另外附一个
`lesson.mp4`，同样的内容，没有题目，纯线性播放。

想给听障学习者或后期剪辑配标准字幕，同一份时间轴还能一行导出 SRT / WebVTT
（默认 `lesson.srt` + `lesson.vtt` 放在课件目录根）：

```bash
python scripts/export_subtitles.py lesson
python scripts/export_subtitles.py lesson --format srt --speaker --hl-mark
```

仓库里有一份可以直接看效果的样例：`references/template.html` 是页面范本，
`references/template-narration.json` 是配套讲稿——把这份讲稿交给 agent，就是这个效果。

---

## 注意事项

**关于交付**

- 成品目录直接发出去、丢静态托管、本地双击，都行，不需要服务器。
- `audio/` 目录里只应有 `combined.wav` 和 `narration_timing.json`，逐句缓存在同级的
  `.courseware-cache/`，别把它当成品的一部分拷走。
- 改了讲稿要求重新生成，音频和时间轴必须是同一次生成的，混了会串。

**关于质量**

- 交付前会跑一遍通用检查（字幕同步、门禁放行、无 JS 错误），它保证课件**不会坏**，
  不保证课**讲得好**。讲得好不好取决于你给的素材和那句开场问题。
- 每课 1–2 道门禁就够了，最多 3 道。门禁放行是"继续的许可"，不是"已掌握"，
  页面上不会出现"已通关"这类说法。
- 如果合成中间有句子失败，默认会整条阻断而不是交一条带静音的残品——所以你拿到的成品
  要么完整，要么会明确告诉你哪儿没成。

**关于安全**

- 讲稿会被上传到 TTS 服务合成语音。涉密或隐私内容先确认数据策略，或者改用你自己的音频。
- key 只进 `.env`，不要写进任何会被提交的文件。

---

## 想深入

`SKILL.md` 是给模型读的契约（能力边界、四条铁律、工作流），`references/` 里五份文档分别
讲页面布局、画面渲染、交互门禁、讲稿分句、运行时与检查器。这些是改页面时才需要查的，
日常使用不必看。

---

## 开发与自测

仓库自带一套不依赖 TTS 密钥 / 网络的 pytest 套件（`tests/`），改脚本后跑一遍：

```bash
pip install -r requirements-dev.txt   # 首次：pytest + websocket-client（真实时钟回归用）
python -m pytest tests/ -q
```

- 单元测试覆盖分句、编码探测、`.env` 解析、契约校验、时间轴构建、页面对齐门、
  检查器解析器、导出帧计划、字幕导出等核心逻辑；
- `narration.py`（TTS 编排，唯一需密钥的模块）用 fake client 零网络零密钥覆盖：
  分块/对话展开/节拍全局换算、resume 缓存判定（指纹/失败占位/变速标记）、时序
  累加、静音兜底、致命失败短路、worker 崩溃降级；
- `tests/test_export_subtitles.py` 锁字幕导出的契约：时间戳格式（超小时进位、
  毫秒四舍五入、VTT 用点分毫秒）、逐句抽取与说话人 / 结论句标记 / 偏移、两道
  降级闸在库函数里生效（manifest 级 status 与句级 synth_failed，不只在 CLI）、
  SRT 序号连续、WebVTT 无序号、真实 CLI 子进程（页面目录默认命名、`--out`
  与 both 互斥）；
- `tests/test_pipeline_smoke.py` 用范本页面 + 合成音频走完真实 CLI 全链路
  （时间轴 → 组装 → 静态检查 → 浏览器冒烟 → 视频导出），并断言**成片时长 ≈ 旁白
  音频时长**——这个断言在守着一条真实的回归：concat demuxer 对列表尾帧的时长处理
  不可靠，曾让导出视频比音轨长出 4.6 秒；
- 视频导出另守一条截图路径的回归：new-headless 的 `--window-size` 高度会被
  浏览器内部 UI 吃掉一块（实测 Chromium 146 视口比窗口矮 87/139px），此前成片
  字幕带恰好落在视口外、整段缺失。`export_video.py` 现在把截图页的 `#root`
  钉成 block、窗口加高一个安全余量并在编码前裁回舞台盒（见
  `scripts/export_video.py` 的 `WIN_HEIGHT_MARGIN` 注释），保证字幕进入成片；
- `tests/test_narration_e2e.py` 把 fake OpenAI 注入 `sys.modules` 后跑
  `narration.py main()` **完整 CLI**（此前 narration 只有函数级覆盖）：验证对话
  展开、句级节拍（beat pause）、段级变速（--speed 后 duration 重测）、concat
  时长与 manifest 精确一致，并把产物接入 build_timeline → build_page →
  check_gates 真实下游；`--dry-run` 路径不需 key 不写音频；
- `tests/test_build_timeline.py` / `test_check_gates.py` 另覆盖两个被 mutation
  testing 抓出的假绿缺口：`_content_map` 必须滤掉空段再编号（否则 seg-N 与音频
  侧错位、title/hl 静默丢失）；`_timeline_from_html` 对"script 块存在但 JSON
  损坏"必须返回错误而非静默放行；
- `tests/test_audio.py` 另覆盖 concat 的**混格式归一**路径（TTS 48kHz 立体声 +
  静音占位 24kHz 单声道混列时必须先统一格式再拼接，否则时长错乱）；
- **真实时钟浏览器回归**（默认跳过）：check_gates 的浏览器冒烟用确定性时钟桩，
  从不真实播放音频；`REALCLOCK=1` 时额外用无头 Chrome + CDP 驱动真实
  `audio.currentTime`，在 4× 播放下验证字幕翻句延迟有界、任意采样点字幕与时间轴
  一致、五类门禁真实开/锁/继续、答对后音频真实恢复（需 Chromium/Edge 与
  websocket-client）：
  ```bash
  REALCLOCK=1 python -m pytest tests/test_pipeline_smoke.py::TestRealClockBrowser -q
  ```
- 没有 Chrome/Edge 时浏览器冒烟与导出用例自动跳过，静态链路仍必须通过；
- **无 TTS key 看成品**：`scripts/demo_lesson.py` 用范本讲稿 + ffmpeg 合成占位音轨
  跑通真实 CLI 全链路（时间轴 → 组装 → 交付检查 → 可选导出），不需要 MiMo key、
  不碰网络——输出目录默认在系统临时目录，显式 `--out` 也不能落在技能目录内：
  ```bash
  python scripts/demo_lesson.py                     # 成品在系统临时目录
  python scripts/demo_lesson.py --out ./demo        # 成品在 ./demo/lesson/
  python scripts/demo_lesson.py --out ./demo --export   # 额外导出 ./demo/lesson.mp4
  ```
  占位音轨只保证时长与时间轴一致，听感不真实，用途是"本地看成品结构"；
  对应测试还锁两条契约：有 Chrome 时 demo 的交付检查必须真跑浏览器冒烟
  （`[browser] captions=` 标记），且 `check_gates.py --require-browser`（SKILL §8
  的"CI 严格模式"）必须返回 0；`--export` 的成片必须含视频流、时长与时间轴
  `total_duration` 一致（±0.6s），拦"mp4 存在但画面全空 / 只混音轨"类回归；
- CI（`.github/workflows/ci.yml`）在 Ubuntu 上装好 ffmpeg 与 Chrome 后全量执行：
  Python 3.10 / 3.11 / 3.12 矩阵跑完整套件（含浏览器冒烟与导出），并额外以
  `REALCLOCK=1` 跑真实时钟回归（字幕翻句延迟、任意采样点字幕一致、门禁真实
  开/锁/继续、seek 门禁语义与截图模式）。变更记录见 `CHANGELOG.md`。
