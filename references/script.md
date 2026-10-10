# 讲稿与音频

## 1. 旁白脚本

```json
{
  "title": "主题",
  "opening": "先抛一个问题。",
  "segments": [
    {
      "id": "seg-1",
      "title": "第一段",
      "tagline": "一句话提示",
      "hl": [2],
      "beat": {"2": {"pause": 0.9}, "3": {"speed": 0.9, "pause": 0.5}},
      "text": "第一句。第二句。第三句。"
    }
  ],
  "closing": "最后给一个判断标准。"
}
```

`text` 是真正被朗读的正文，同时也是最终字幕的唯一文本来源。

段落可选 `beat` 是**句级节拍**（导演口，只管声音）：键是**这一段里的句序号**（与
`hl` 同一口径，从 1 数起；多轮对话跨轮连续计数），值只认两个键——

- `pause`：该句**之前**留多长静音（秒，≤5）。全局 `--gap` 是默认值，`beat.pause`
  按边界覆盖它——"抖包袱前停一下"就写在那一句上；
- `speed`：该句语速，覆盖段级 `speed` 与全局 `--speed`。

整稿第一句不许写 `pause`（它之前没有句间边界可插静音，起播空白不属于节拍）；键名
写错、句序号越界、值不是对象一律**当场报错**——节拍被静默丢弃等于"以为留了白、
成品听不出来"，只能靠耳朵重听整稿。拼接与时间轴读的是**同一个逐边界列表**，留白
不会造成字幕与音频错位。`--dry-run` 会把每条节拍打出来（`[beat] seg-1 第 2 句：
句前停 0.9s`），不用等烧完 TTS 才第一次对上眼。

开场与收尾各有一组可选顶层键：`opening_title` / `closing_title`（缺省时开场沿用
`title`、`title` 也缺省时用「开场」；收尾固定「小结」）、`opening_tagline` / `closing_tagline`（章节头一句话
提示）、`opening_speed` / `closing_speed`（见 §3）。不写这些键就用默认值。

多人对话（问答 / 情景剧）用顶层 `speakers` + 段落 `dialogue` 代替该段的 `text`：

```json
{
  "speakers": {
    "A": {"label": "小明", "voice_id": "…", "voice_style": "…"},
    "B": {"label": "小钢", "voice_id": "…"}
  },
  "segments": [
    {
      "id": "seg-2",
      "title": "一轮问答",
      "dialogue": [
        {"speaker": "A", "text": "第一问。第二句。"},
        {"speaker": "B", "text": "这是回答。"}
      ],
      "hl": [3]
    }
  ]
}
```

每一轮**独立分句**（短句不会被并进下一位说话人）；turn 级音色只取 `speakers[speaker]`，
在 turn 对象上自写 `voice_id` / `voice_style` 会被**当场拒收**（键名与 speakers 里
完全同款，静默丢弃等于"以为换了声、成品里听不出"——要换声改 speakers 配置）。
落成的时间轴句子条目带 `speaker`（即 `label`），
`build_timeline.py` 透传为 `runtime.narration[i].speaker`；范本字幕带已实现说话人标签
（`#cap-speaker`，见 `stage.md` §4——标签与 `#cap-text` 分离，不破坏字幕唯一来源）；
manifest 的句子条目另带该句**实际使用**的 `voice_id`（顶层 `voice_id` 只是 CLI 默认值，
多说话人成品要审计"这句是谁的声音"看逐句字段；`build_timeline.py` 不透传它进页面时间轴）；
`hl` 仍按**整段总句序**从 1 数起，跨轮连续计数。

## 2. 分句原则

一条作者句子 = 一个视觉步 = 一个字幕步。

脚本按终止标点分句，并对英文句点做缩写 / 小数点守卫。**不会自动把短句并到下一句**；
机械阈值只有两条、都只告警不拦截：少于 5 字报短句、超过 45 字报超长句。风格带宽
比阈值更严，是给你改稿用的：

- 大多数句子 12–28 字；
- 超过 40 字就该拆（45 字的 warn 是兜底，不是许可）；
- 每句用 `。！？` 或等价终止标点收尾；连续终止标点（`！？`、`！！`）与引号收尾的
  孤立标点并入前句，不会被丢——口播与字幕以分句结果为准；
- `hl` 从 1 开始计数，**只标结论句**（一句里最多一个，全篇少数几句）；
- 不要朗读"如图所示"“请看这里”。

### 讲稿自检（分句之前）

`check_gates.py` 检查的是课件*不会坏*；下面这几条检查的是课件*讲得清楚*，机器代劳
不了，只能在写稿时逐条过——课件做砸，几乎都砸在这一步而不是工程那一步：

- **误区先行**：开场抛的问题是学习者大概率会答错的那个（范本的 10、9、1 排序），
  不是讲师自己关心的问题；答对了才说明这场没有信息增量。
- **一景一问**：每个 segment 恰好回答一个问题，`title` 就是那个问题的短句答案。
  一句话概括不出的段落，拆或删。
- **hl 句是唯一的认知增量**：标 `hl` 的那句应当是"这场景只许带走一句"时你选的那句，
  不是习惯性标在段末。带不走它，这个场景就不该存在。
- **门禁考迁移，不考复读**：题面若把结论句挖个空让学习者回填原文，门禁就退化成了
  听写。换一个新情境、一个新例子去问同一个判断——答对才证明理解发生在概念上，
  而不是在句子上。
- **前后勾连**：at-start 门禁优先考一两景之前、甚至更早的结论（见
  `references/interactions.md` §3），别把复习的机会浪费在刚说完的那句上。

### 分镜自检（写完旁白、动笔画面之前）

一句旁白 = 一个视觉步，所以**这张句子表就是这一幕的分镜表**。逐句问一遍"这一句
画面上发生了什么"，答不上来的句子要么改稿、要么干脆删掉：

| 这句话在做什么 | 画面上该发生的动作 | 相机 |
|---|---|---|
| 新东西登场 / 结论落地 | 建档（`.in`）、落一枚徽标 | 推到它 |
| 同一批东西换了状态 | 写属性：滑过去、变色（**不重画**） | 跟着换窗 |
| "刚才那三个数" / "回到开头" | 世界层的常驻图元换姿势（`WORLD`） | 别切走 |
| 转折、反直觉 | `.draw` 描一条线 / `.pop` 点一下 | 停一拍 |

三条硬判据：

- **相邻两句画面一模一样 = 其中一句白说**。要么给它一个状态变化，要么把两句合稿；
- 需要"记住上一步演到哪儿"才成立的编排，改成 (scene, step) 的显式状态——导出 mp4
  是每帧一次冷启动（`references/runtime.md` §7），历史状态会漏进后面每一帧；
- 想"等一等再说"就写 `beat.pause`，不要靠多画一帧静止画面来凑时长。

## 3. TTS

```bash
python scripts/narration.py --source narration-source.json -o audio
```

输出：

```text
audio/combined.wav
audio/narration_timing.json
```

`narration_timing.json` 是时间轴的唯一入口形态：对象，顶层显式 `schema_version: 1`；
`scenes[]` 每场带 `step_id/start/duration/end` 与 `sentences[{start,duration,text}]`。
外部（其它 skill / 外部 TTS）产出的 timing 先归一成这个形状再进 `build_timeline.py`。
页面内联后仍保留这个版本号。
组装成品时，`build_page.py` 会要求 manifest 含非空 `scenes`，并与 `build_timeline.py` 走同一道门
（`_contracts.check_degraded_status`）拒收 `status` 非 `ok` 的降级 manifest——确要交付静音占位版才加
`--allow-degraded`；随后核对它与内联时间轴的场景顺序、句子文本、起止时间和句数。
已有输出文件要显式 `--force` 才覆盖；不要把不同批次的音频与 timing 混用。

未使用 `--resume` 时，句子音频只保存在临时工作目录，结束后清理；使用 `--resume` 时，缓存默认写在输出目录同级的 `.courseware-cache/<输出目录名>/sentences/`（输出目录名为 `audio` 时即 `.courseware-cache/audio/sentences/`），也可通过 `--cache-dir` 指定。无论哪种模式，`audio/` 交付目录只包含 `combined.wav` 与 `narration_timing.json`，不要把缓存目录当成成品模板。

当前 `narration.py` 是 **MiMo TTS 适配器**；Courseware Studio 真正需要的是：

```text
音频 + 全局 sentence timing + 原文 text
```

页面层只依赖这份统一数据，不依赖某个 TTS 厂商（能力边界见 SKILL.md §2）。

常用开关：`--dry-run`、`--resume`、`--clean-output`、`--speed`、`--gap`（句间静音，默认 0.4s，上限
10s——超过直接拒绝，别指望用它做长停顿）、段级 `speed` / `voice_id` / `voice_style`、`--on-fail silence`；
其余开关（`--workers`、`--api-timeout`、`--model`、`--base-url` 等）见 `--help`。
`voice_id` 会对着内置音色表校验（段落与 `speakers` 都查），写错立即失败并列出可用音色；
不确定选哪个音色先用 `python scripts/narration.py --list-voices` 看全表（无 key、无讲稿即可）。

语速优先级：`segments[].beat[句].speed` > `segments[].speed` > 全局 `--speed`；
`opening_speed` / `closing_speed`（顶层键）单独覆盖开场与收尾，缺省时它们**跟随全局 `--speed`**（不钉死 1.0）。要"开场略慢"就写一个小于当前语速的值。

`--resume` 的缓存是**内容寻址**的：文件名 `tts-<文本+音色+风格+模型+语速+服务地址 的 sha16>-<同文出现序>.wav`，
改稿插句 / 删句导致编号漂移不影响其它句子命中；指纹含服务地址，换 `--base-url` 会重烧。
上次合成后 atempo 没落上的句子带 `.needs-speed` 标记：下次 `--resume` 只重施变速、
不重烧 TTS。`--on-fail silence` 留下的失败静音占位带 `.failed`
标记；**下次 `--resume` 在默认 `--on-fail abort` 下会拒绝带着占位直接交付**（报错会列出
要删的缓存组——`.wav` + `.sha` + `.failed` 三件一起删，只删 `.failed` 不够：静音 wav
与 `.sha` 还在，命中缓存后照发静音且 `status` 变回 `ok`；删干净即重试那几句，或显式改
`--on-fail silence` 保留）。
任何一句碰到不可重试错误（鉴权 / 模型 / 地址类），线程池整体短路、退出码 1，不再白烧
其余句子——修好配置直接带 `--resume` 重跑，已完成的句子全部命中。TTS 失败且连静音
占位也没落成的句子会被整句丢弃，时间轴 `status` 记为 `degraded`
（`degraded.dropped_sentence_count`）——**交付前核对句数**，丢过的句子不会出现在音频里。

改了语速就必须重新生成音频与时间轴（指纹含语速，`--resume` 会自动重烧受影响的句子）。

## 4. 时间轴

```bash
python scripts/build_timeline.py \
  --timing audio/narration_timing.json \
  --source narration-source.json \
  -o timeline.html
```

`--bare` 输出裸 JSON；与 `-o timeline.json` 联用时会把裸 JSON 写入该文件，诊断信息仍输出到 stderr。

结果内联为：

```html
<script type="application/json" id="lesson-timeline">…</script>
```

内联形状示意（范本的真实数据内联在 `references/template.html` 的 `#lesson-timeline`，压成单行）：

```json
{"schema_version": 1, "scenes": [
  {"step_id": "seg-1",
   "content": {"title": "一个反直觉的结果", "tagline": "不是按数值，是按字符串"},
   "runtime": {"start": 4.939, "duration": 15.507, "end": 20.446,
     "narration": [
       {"start": 4.939, "duration": 4.591, "text": "很多人第一次看到…都会愣一下。"},
       {"start": 15.74, "duration": 4.706, "text": "它不传比较函数的时候…", "hl": true}
     ]}}
]}
```

时间是**全局秒**。页面直接读取 `runtime.narration[i]`，不要在运行时再 fetch 外部 JSON。

这条时间轴还能序列化成**标准字幕**（SRT / WebVTT），字幕文本与时间都只来自
`audio/narration_timing.json`，不在页面外维护第二份文案：

```bash
python scripts/export_subtitles.py <页面目录或 index.html>          # 默认 both：.srt + .vtt
python scripts/export_subtitles.py <页面目录> --format srt --speaker --hl-mark
```

`--speaker` 给说话人句加「说话人：」前缀；`--hl-mark` 给结论句（`hl`）加 ◆ 标记
（页面字幕不带，仅供导出）；`--offset` 整体时间偏移（秒，可负），用于与外部
音视频对齐。降级闸与交付同一口径：manifest 状态非 `ok` 或含 `synth_failed`
静音占位句默认拒收，确要导出降级成片的字幕才加 `--allow-degraded`。

## 5. TTS 配置

密钥解析优先级：CLI 参数 > 系统环境变量 > 项目级 `.env` > 用户级 `.env`：

```text
~/.config/courseware-studio/.env
```

项目级 `.env` 从讲稿所在目录向上找、并覆盖当前工作目录本身，**走到当前工作目录即停**：
命中带 `.git` 的项目根也停（该根的 `.env` 是最后一个候选）；不会越过 cwd 去吸它的父目录、更不吸盘根
或别人目录里的 `.env`。输出目录**不是**搜索起点——密钥跟着讲稿与工作目录走。`.env` 值支持成对引号与行内注释：
`KEY=sk-xxx # 备注` 取 `sk-xxx`，`KEY="sk-a#b"` 里的 `#` 是值的一部分；
不带引号时，只有"空白 + `#`"才起注释作用。

也可直接使用环境变量或 CLI 参数。不要读取其它 skill 的私有配置目录。

## 6. 工程自检

- [ ] dry-run 后句数和视觉步数对得上；
- [ ] 短句是显式改稿，不依赖脚本静默合并；
- [ ] `hl` 没有越界；
- [ ] 时间轴已内联；
- [ ] 最终音频始终是 `audio/combined.wav`，不产生第二个交付文件名；
- [ ] 复用已有输出目录时，确认只保留 `combined.wav` / `narration_timing.json`，或生成前显式使用 `--clean-output`；
- [ ] `synth_failed` 默认视为交付失败；只有明确要保留降级成片时才放行——
      开关在下游 `build_timeline.py` / `build_page.py` / `check_gates.py` 各一道
      （narration.py 没有这个参数）；
- [ ] 需要 SRT / VTT 字幕时用 `export_subtitles.py` 从同一份时间轴导出，
      不要手抄或二次转写（时间 / 文本会与旁白错位）。
