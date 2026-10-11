# Changelog

本仓库的变更记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循语义化版本；未打 tag 前以 0.x 递增，随迭代推进。

## [0.10.0] - 2026-10-12

### 新增

- **动效设计令牌（学习模仿前沿动画设计思路）**：缓动与时长集中到模板 `:root`
  的 `--ease-out`（easeOutExpo `.22,1,.36,1`）/ `--ease-in-out` / `--dur-in` /
  `--dur-move` / `--dur-pop` / `--dur-draw` / `--dur-gate` / `--dur-pulse` /
  `--dur-breathe`，动效词汇（`.in/.pop/.draw/.pulse/.nt/#cam`、veilplay 呼吸、
  门禁进出、卡片错峰）统一引用——改全局节奏只动一处，不再散落魔法数。
- **错峰入场原语**：`.in` 支持 `--delay`（`animation-delay:var(--delay,0s)`），
  新增 `.st1/.st2/.st3`（60/120/180ms）档位——一组同幕图元按序上浮，是前沿
  动效最常见的节奏手法（模板门禁卡片的 `nth-child` 延迟同款推广为通用词汇）；
  最晚元素延迟 ≤ 180ms，单独截一帧仍讲得清（导出边界不变）。
- **`prefers-reduced-motion` 兜底（WCAG 2.3.3）**：模板新增词汇级媒体查询——
  入场/补间/呼吸全部关闭、循环动画停掉、终态保留；内容不依赖动效，关闭后
  每一帧仍是同一张正确定格（与导出截图页同口径）。

### 测试

- `tests/test_iteration_round2.py::test_motion_tokens_and_stagger_present`：
  令牌值、词汇引用、`.in` 的 `--delay`、`.st1/.st2/.st3`、reduced-motion 块
  的静态契约。
- CDP 真实浏览器测试追加：`Emulation.setEmulatedMedia` 模拟
  `prefers-reduced-motion: reduce` 后，`#stage .in` 的
  `animationName === 'none'`（无 Chrome 自动跳过）。

### 文档

- `references/stage.md`：§3.4 补令牌、错峰与 reduced-motion 三小节。
- `SKILL.md`：§5 画面渲染补动效令牌 / 错峰 / reduced-motion 一句。
- `README.md`：「开发与自测」补 0.10.0 回归说明。

## [0.9.0] - 2026-10-11

### 新增

- **`check_gates.py` 页面本地资源引用完整性检查**：`link[href]` /
  `script[src]` / `img[src]` / `source[src]` / `use[href]` / `a[href]`
  里的页面目录内相对引用必须指向真实存在的文件——防"引用了文件却忘了拷贝"
  的静默破损；外部 URL / data: / 纯锚点 / 模板占位符 / 跨目录引用不判；
  runtime 与 `audio/combined.wav` 已有更严的独立校验，这里不重复报。
- **`demo_lesson.py --title / --lang` 透传**：透传 `build_page --title`
  （HTML 转义）与 `--lang`——demo 也能带真实标题，`check_gates` 不再报
  "照抄范本忘改"；默认行为不变（沿用范本标题与 lang）。
- **`export_video.py` 导出参数边界校验**：`--fps` / `--scale` / `--crf` /
  `--width` 非正数在进帧循环前拒收（负数 / 0 会让 ffmpeg 静默产出坏成片）。

### 测试

- `tests/test_check_gates.py::TestLocalRefErrors`：缺失报错、存在放行、外部
  URL / data: / 纯锚点 / 模板占位符 / 跨目录不判、`data-src` 不误匹配、
  SVG `use[href]` 去片段、query 剥离、runtime/audio 不重复报、static_check
  集成（成品页零新错、人为悬空引用报错）。
- `tests/test_export_video.py::TestValidateExportParams`：合法参数放行、
  fps/scale/crf/width 非正数五例拒收。
- `tests/test_demo_lesson.py`：`--title` 注入 `<title>` 且 check_gates 标题
  告警消失；`--lang` 写入 `<html lang>`。

### 文档

- `README.md`：demo 用法补 `--title`/`--lang` 示例；「开发与自测」补 0.9.0
  回归说明。
- `SKILL.md`：§8 补页面本地资源引用完整性检查。
- `references/runtime.md`：§8 模式说明补引用完整性检查。

## [0.8.0] - 2026-10-11

### 新增

- **`check_gates.py --json` 结构化输出**：整份检查结果（`page` / `verdict` /
  `static` / `browser` / `note` / `exit_code`）作为一个 JSON 文档打到 stdout，
  人类逐行输出关闭——CI 报告与 agent 消费判绿红只看 `exit_code` / `verdict`；
  人类阅读路径（默认）逐字节不变。
- **模板静音（按钮 + M 键）**：播放器行新增 `#mute` 按钮（🔊/静音态两个 SVG
  图标，`body[data-muted]` 切换，与全屏按钮同一套模式），快捷键 M 任意状态可
  用（含门禁打开时）——只切 `audio.muted`，与"单一时钟"无关，音量是独立的
  传输控制；`aria-pressed` 与 `aria-label` 同步，屏幕阅读器可感知。
- **`export_video.py --no-audio` 纯画面成片**：不混音轨（`-an`），画面时序仍
  按旁白音频推进、成片时长裁剪与校验逻辑不变——预览画面 / 后期自行配音 /
  去掉占位音轨的场景用。

### 测试

- `tests/test_export_video.py::TestMuxCommand`：带音轨（`-c:a aac`、双 `-i`）
  与 `--no-audio`（`-an`、无 `-c:a`、单 `-i`）的命令差异，画面参数（x264 /
  crf / fps / 偶数宽高）两条路径完全一致。
- `tests/test_check_gates.py::TestCliJson`：`--json` 通过（`pass-static` /
  exit 0）、范本失败（`fail` / exit 1）、缺页（`fail` / exit 2）三路径，且
  stdout 只有 JSON、人类逐行输出被关闭。
- `tests/test_iteration_round2.py`：静音按钮与 M 键的静态钩子断言；
  CDP 真实浏览器测试追加——M 键切 `muted`、点按钮切回、`body[data-muted]`
  与 `aria-pressed` 同步（无 Chrome 自动跳过）。

### 文档

- `README.md`：特性清单补静音 / `--no-audio` / `--json`；「开发与自测」补
  0.8.0 回归说明。
- `references/layout.md`：§5 播放器行补 🔊 按钮与 M 键契约、窄屏控件枚举
  四改五。
- `references/runtime.md`：§8 模式说明补 `--json`。

## [0.7.0] - 2026-10-11

### 新增

- **`check_gates.py` 音频时长与时间轴一致性检查**：`audio/combined.wav` 时长
  比时间轴结束点**短**超过 1.5s 判 error（最后几句旁白没声音——音频与时间轴
  串批次是 README 明示的最常见装配错误），**长**超过 1.5s 只告警（尾部余音），
  测不出时长只告警跳过。WAV 走 wave 模块样本精确路径（帧数/帧率），**不依赖
  ffmpeg**；非 WAV 或解析失败才回退 ffmpeg。
- **模板进度条键盘可达（`role="slider"`）**：`.bar` 标记里写
  `role="slider"` / `tabindex="0"` / `aria-label`，`aria-valuemin/max/now`
  由 JS 在 `TOTAL` 就绪后写真实值并随播放/seek 同步；焦点在条上时
  `←/→` 微调 ±1s、`Home/End` 到头/尾——方向键在本元素 `stopPropagation`，
  不叠加 document 层的 ±8s 擦洗（细调与快擦是两种手势）。
- **模板 ended 后一键重播**：课程播完点播放器行 ▶ = 从头重播
  （`audio.currentTime = 0` + 清门禁状态），提示文案同步改为「点 ▶ 重播」。

### 测试

- `tests/test_check_gates.py`：占位音轨短于时间轴报 error、长出时间轴只告警、
  匹配时无告警（fixture 音轨时长改为匹配 `total_duration`）。
- `tests/test_iteration_round2.py`：slider 钩子与重播路径的静态断言（无浏览器
  也可锁）；真实浏览器 CDP 测试（有 Chrome 才跑）——焦点在进度条上 ←/→ 跳
  1s（不叠加全局 ±8s）、Home/End 到头/尾、ended 后点 ▶ 重置到 0 重播。

### 文档

- `SKILL.md`：§8 补音频时长一致性检查（会拦截的 error 路径）。
- `README.md`：特性清单补键盘可达与重播；「开发与自测」补新测试说明。
- `references/runtime.md`：§8 时间轴/字幕类检查补音频-时间轴一致性。
- `references/layout.md`：播放器行一节补进度条 slider 的键盘契约。

## [0.6.0] - 2026-10-11

### 新增

- **`check_gates.py` 静态检查新增两条交付级告警**：
  - **单句字幕超长**：画布字幕带是 SVG `<text>`（横跨 40→960px、字号 17、
    不换行），超长句会沿中线双向溢出并被舞台裁掉——成片里无声地缺字。
    超过 56 字报 `[warn]`（CJK 约 1em/字 ≈ 54 字上限，留窄字符余量）。
    这是交付物级独立闸门：`narration.py` 的 >45 字告警管写稿，这里管
    **成片**（外部 timing / 事后改稿都可能绕过前者）；
  - **`<title>` 仍是范本默认标题**：精确匹配范本那一行（"JavaScript 的
    sort() 到底怎么排序"），提示照抄范本后忘改——`build_page --title`
    已提供注入口，串成范本标题比"没写标题"更误导。两条都只告警不拦截，
    不破坏既有交付判定。
- **`export_subtitles.py --from-page`**：直接读页面内联时间轴
  （`<script id="lesson-timeline">`）导出字幕，无需 `audio/narration_timing.json`——
  拿到别人的成品页面也能独立生成字幕。内联形状（`runtime.narration`）在
  内部归一成 manifest 形状（`sentences`），与页面组装同一份解析
  （复用 `build_page._timeline_json`）；无 status 字段按"外部 timing"处理。

### 测试

- `tests/test_check_gates.py`：超长句告警（>56 字）与正常句不告警；范本标题
  泄漏告警与注入自定义标题后不告警。
- `tests/test_export_subtitles.py`：`runtime.narration → sentences` 归一
  （speaker / hl / synth_failed 透传）；`--from-page` 真实 CLI 子进程
  （speaker + hl-mark 前缀顺序、默认 both、与 `--timing` 互斥）。

### 文档

- `SKILL.md`：§8 检查器能力补两条新告警；§8 字幕导出用法补 `--from-page`。
- `README.md`：「开发与自测」补新测试说明。
- `references/runtime.md`：§8 静态检查清单补两条新告警。
- `references/script.md`：§4 字幕导出用法补 `--from-page`。

## [0.5.0] - 2026-10-10

### 新增

- **`scripts/export_subtitles.py`：时间轴 → 标准字幕文件（SRT / WebVTT）**。
  从 `audio/narration_timing.json`（唯一时间来源）序列化逐句字幕，不在页面外
  维护第二份文案：无障碍（听障跟随阅读）、剪辑 / 压制软件后期、多语言与复习
  交换格式。支持 `--speaker`（说话人前缀）、`--hl-mark`（结论句 ◆ 标记，
  页面字幕不带，仅供导出）、`--offset`（整体时间偏移，与外部音视频对齐）、
  `--format srt|vtt|both`（默认 both）与 `--out`（单一格式专用）。
- **`build_page.py --title` / `--lang`**：可选注入页面 `<title>` 与 `<html lang>`。
  作者照抄范本后最容易忘改的就是 `<title>`——浏览器标签 / 导出工具会把范本的
  标题串到别人的课程上，比"没写标题"更误导。默认不注入，行为与旧版完全一致。

### 测试

- `tests/test_export_subtitles.py`：时间戳格式（超小时进位、毫秒四舍五入、
  VTT 用点分毫秒）、逐句抽取与说话人 / 结论句 / 偏移、两道降级闸
  （manifest 级 status 与句级 synth_failed 都在库函数里生效，不只在 CLI）、
  SRT 序号连续、WebVTT 无序号、真实 CLI 子进程（页面目录默认命名、
  `--timing` + `--out` 单格式、`--out` 与 both 互斥、speaker/hl 透传）。
- `tests/test_build_page.py`：title 替换与 HTML 转义、lang 替换（已有 /
  缺失属性两种）、缺失钩子报错、官方范本注入后时间轴仍可解析。

### 文档

- `SKILL.md`：§3 工作流表补可选字幕导出步骤；§8 补 `export_subtitles.py`
  用法与降级闸口径；§10 参考文件表登记新脚本。
- `README.md`：特性清单与交付树补字幕文件；「开发与自测」补新测试说明。
- `references/script.md`：时间轴一节补"导出标准字幕"路径。

## [0.4.0] - 2026-10-10

### 测试加固

- **`check_gates --require-browser`（SKILL.md §8 的"CI 严格模式"）首次被测试锁定**：
  新增 `test_demo_check_gates_require_browser`，在有 Chrome 时直接以该开关跑生产
  检查器并断言返回 0、报告 `[browser] captions=` 与"浏览器冒烟通过"——此前该模式
  在仓库中没有任何执行路径（demo 内部调用不带此开关，本地无 Chrome 时需能静态通过）。
- **demo 交付检查必须真跑浏览器冒烟**：`test_demo_builds_lesson` 在有 Chrome 时
  断言 stdout 出现 `[browser] captions=`，锁"CI 装了 Chrome 就不能静默退化静态检查"。
- **导出 e2e 验证加深**：`test_demo_export_flag` 从"mp4 存在"升级为——成片必须含
  视频流（拦画面全空 / 只混音轨）、时长与时间轴 `total_duration` 一致（±0.6s）。
- **真实时钟回归硬上限放宽到 2000ms**（0.3.1 的 500ms 在共享 CI VM 上仍会假阳：
  3.12 runner 实测单点 842ms——GC/CPU steal 可让单个 rAF 帧任意延迟）。90 分位
  ≤200ms 的主契约不变，硬上限只拦"秒级卡死"类系统性故障。

## [0.3.1] - 2026-10-10

### 修复

- **CI 工作流实际跑不通**（0.2.0 引入）：`actions/setup-python@v5` 的 `cache: pip`
  要求仓库存在 `requirements.txt` 或 `pyproject.toml`（此前两者皆无），且工作流漏装
  `pytest`（runner 镜像不自带）——两轮推送的 CI 全部在 `Set up Python` 步失败。
  修复：新增 `requirements-dev.txt`（pytest + websocket-client），
  `setup-python` 配 `cache-dependency-path`，安装步骤改 `pip install -r`；
  并补 `workflow_dispatch` 手动触发入口。
- **speaker 浏览器测试在 CI 慢环境下假失败**：页面就绪改为轮询
  （`readyState === 'complete'` + 时间轴已注入），并按 URL 选中目标 tab
  （headless 可能带出 about:blank 附加页）。
- **测试浏览器优先级与生产查找器错位**：`HAVE_CHROME` 此前把
  `/usr/bin/chromium`（Ubuntu 24.04 的 snap 过渡包，冷启动不稳）排第一，
  导致同一台机器上部分用例连不上 CDP。统一改为 google-chrome 优先
  （与 `check_gates._find_chrome` 一致）；连接循环加进程存活检查并把
  stderr 落盘供诊断，`finally` 清理不再掩盖真实失败（kill + 静默超时）。
- **真实时钟回归的翻句延迟断言在 CI 负载下假阳**：单点 204ms 超 200ms
  硬上限。改为 90 分位 ≤200ms + 硬上限 500ms——仍拦"秒级卡死"类回归，
  但不因 4ms 抖动红掉整个 job。

## [0.3.0] - 2026-10-10

### 新增

- **范本落地多说话人标签**（`references/template.html`）：字幕带新增
  `#cap-speaker`，`capShow()` 按句子的 `speaker`（对话讲稿经 build_timeline 透传）
  显示说话人，无 speaker 时留空；与 `#cap-text` 分离，字幕唯一来源契约不受影响
  （`references/stage.md` §4 与 `script.md` §1 同步说明）。
- **`narration.py --list-voices`**：无 key、无讲稿即可列出内置八种音色与默认值
  （此前音色表只藏在 argparse choices 里）。
- **`.editorconfig`**：全仓统一 UTF-8 / LF / 尾随空白与末行换行规范。
- **契约锁定测试**（`tests/test_iteration_round2.py`）：
  - `check_gates.py` 对范本页面的文档化行为（恰好 3 条 error + 超 3 道门禁
    warning）——防检查器漂移把范本误判成可交付；
  - speaker 标签真实浏览器渲染（CDP 驱动，字幕纯净 + 标签正确）；
  - `--list-voices` 的 CLI 承诺（无需 key / source / output）。

### 变更

- CI：新增 `concurrency`（同分支新推送取消旧一轮）与 `timeout-minutes: 30`。
- `README.md`：特性清单补说话人标签；音色说明补 `--list-voices` 提示。
- `references/script.md`：TTS 开关补 `--list-voices`；对话段落补范本落地说明。
- `tests/test_demo_lesson.py`：QA 钩子清单加入 `#cap-speaker`。

## [0.2.0] - 2026-10-09

### 新增

- **CI 工作流**（`.github/workflows/ci.yml`）：兑现 README 的承诺——Ubuntu 上
  Python 3.10 / 3.11 / 3.12 矩阵全量执行 pytest（含浏览器冒烟与视频导出），
  并额外以 `REALCLOCK=1` 跑真实时钟回归（字幕翻句延迟、任意采样点字幕一致、
  门禁真实开/锁/继续、seek 门禁语义与截图模式）。
- **`scripts/demo_lesson.py`**：无 TTS key 构建样例课件的开发工具——读范本讲稿，
  ffmpeg 合成占位音轨，走完整真实 CLI 链路（时间轴 → 组装 → 交付检查 → 可选导出），
  默认输出到系统临时目录，产物守卫与各写盘入口一致（拒绝写入技能目录）。
- **`CHANGELOG.md`**：本文件。

### 变更

- `README.md`：「开发与自测」的 CI 描述从"未来式"改为实际配置；补 demo 用法与
  CI badge。
- `SKILL.md` §10 参考文件表：登记 `scripts/demo_lesson.py`。

## [0.1.0] - 2026-10-09

### 初始

- 初始化 Courseware Studio skill：讲稿 → 逐句旁白 → 音频 + 时间轴 → 单页课件。
- 页面范本 `references/template.html`（三层舞台：常驻图元 / 世界层 / 相机；
  五类门禁；章节进度条；响应式与全屏）。
- 脚本族：`narration.py`（MiMo TTS 适配器，含 resume 内容寻址缓存、节拍/变速、
  降级短路）、`build_timeline.py`、`build_page.py`（原子组装 + 一致性校验）、
  `interactive_runtime.js`（交互内核，幂等接线、a11y）、`check_gates.py`
  （交付检查：静态 + 浏览器冒烟 + 真实时钟回归）、`export_video.py`
  （逐句截帧导出 mp4）。
- 测试族：全链路冒烟（含成片时长 ≈ 旁白时长回归、new-headless 视口裁剪回归）、
  narration e2e、mutation testing 抓出的假绿缺口覆盖（空段滤除、损坏 JSON 报错）、
  混格式 concat 归一。
