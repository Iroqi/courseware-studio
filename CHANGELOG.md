# Changelog

本仓库的变更记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循语义化版本；未打 tag 前以 0.x 递增，随迭代推进。

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
