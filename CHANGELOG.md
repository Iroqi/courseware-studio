# Changelog

本仓库的变更记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循语义化版本；未打 tag 前以 0.x 递增，随迭代推进。

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
