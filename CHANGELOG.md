# Changelog

本仓库的变更记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循语义化版本；未打 tag 前以 0.x 递增，随迭代推进。

## [0.18.0] - 2026-10-19

### 新增（连线与标注——沿线流光 / 恒定线宽 / 自动错峰 / 描边光环）

**沿线流光（`.flow`）**。`pathLength:1` 归一之后 `stroke-dasharray:var(--flow-seg) 1`
（一段光 + 一整条空隙）、`stroke-dashoffset` 从 `--flow-seg` 补间到 `-1`，`linear` +
`--dur-flow:.9s`（比画线 `--dur-draw .55s` 长一档：光段要走完一整条路，才读得出"从这头
到那头"；`easeOutExpo` 会把大半行程挤进路尾，起点附近糊一下就完了）。关键是**两端都落在
"看不见"**：静止态光段整体在路头之外、终态被推到路尾之外，实测 `isPointInStroke()` 沿路
201 个采样点扫出 `0 → 31 → 32 → 13 → 3 → 1 → 0`，中段一路往路尾挪且始终只有一段光。
掠光是**事件不是状态**，它不留痕迹——于是导出注入 `animation:none`、系统关动效两种情形
都直接落回"一条普通连线"（实测切掉后 offset 回到 `.16px`、沿路零命中），纪律③与
"终态落在属性上"这一次同时满足，不需要额外的见旗分支。
三条实测边界写进了范本注释：③ **必须叠在一条看得见的底线上面**（自己静止时隐形，单独挂
等于什么都没有；消费者全部按"底线 `.draw` + 流光 `.flow` 分两个节点"摆，实测同 `d`、
`getBBox()` 同为 [300,137,400,41]）；④ **与 `.draw` 同节点 = 相互抵消**（两条都声明
`stroke-dasharray`，源序后者胜，实测叠一起只剩 `.16/1` 的图案、线根本画不满）；
⑤ **`pointer-events:none` 是规矩不是可选项**——虚线的**空隙不可命中**（实测 hit 落在
dash 间隙返回的不是这条 path），不禁用命中就等于在整条路上盖一条"看不见却挡热区"的带子
（与 0.17.0 羽化层同一课）。补一个 CDP 才浮出来的时序：`fill-mode` 是 `backwards`，
动画跑完就**不再挂在 `getAnimations()` 里**，用例每次取样前得按范本三段式重挂。

**恒定线宽（`.crisp`）**。`vector-effect:non-scaling-stroke` 把描边钉回**屏幕像素**宽度：
`#cam` 推到 1.8 时 1px 的世界分栏线在屏幕上变 1.8px、糊成一团。实测口径吃过一次亏——
先用 HTML `<div>` 的 CSS `transform:scale()` 量，两档在 1.8× 都变 4px 且带模糊斜坡，
那是**位图放大**、`vector-effect` 不参与重绘，读数无效；换成范本真实口径（SVG
`transform` **属性**缩放，每帧重算几何）才量到普通描边 0.5/1/2/3× → 2/4/6/10 屏幕像素、
`.crisp` 恒为 4。仓库用例不再拿绝对像素当契约（0.16.1 同一课）：改用 canvas 位图 A/B 的
**墨量累加**（threshold-free），实测同一份内联 SVG 里 1× 两条线墨量 4/4、1.8× 普通描边
涨到 7 而 `.crisp` 仍是 4。两条边界：① **它不继承**（写在相机分组那层等于没写，实测父层
计算值 non-scaling-stroke、子层 none），必须逐图元挂；② **只给注记/参照线用**——卡片边框、
结论色带这些"世界的东西"跟着镜头变粗是对的（真实摄影机的前后关系），用错对象推近后内容
显出纸片感。消费者：静态世界分栏线。

**自动错峰（`.stag`）**。`sibling-index()` / `sibling-count()` 现算 `--delay`，档间距取
`--stagger`（.06s）但**总量封顶在 `--stag-cap`**（.18s）：组越大间距越摊薄。实测同一份
CSS 在 1 / 2 / 3 / 6 项的组里分别得到 `0s`、`0 / .06s`、`0 / .06 / .12s`、
`0 / .036 / .072 / .108 / .144 / .18s`——**最后一项永远不超过 .18s**（总量守恒）。作者往组里
加图元不用回头改 CSS，也不会十项排到 0.6s。三条边界：① **减号两侧必须有空格**——写成紧凑的
`sibling-count()-1` 时整条 `calc()` 在计算值阶段作废，而**动画照样跑**（`animationName`
仍是 `cardIn`）、只是延迟全落 0s、控制台一句话都不报；这是本轮最容易静默通过的一条，静态契约
锁写法、CDP 用例同时挂带空格与不带空格两条规则做 A/B 把"错了会怎样"也钉住；② **必须锁在
分组那一层**（见下面的 `@property --delay`）；③ 分母用 `max(…, 1)` 兜住单项组（1 项时
分母为 0，`calc()` 作废——不是退化成 0s 而是非法值）。整块包在 `@supports (animation-delay:
calc(1ms * sibling-index()))` 里，不支持的浏览器落回 initial `0s` → 整组同进、内容一格不少。
消费者：门禁卡片 `.g-card.stag` 三项（实测 0 / .06 / .12s）。

**描边光环（`.halo`）**。`paint-order:stroke` + 一圈 `--halo-bg` 的描边，让标注字直接压在
弧线/图形上还读得清，不用垫一块药丸底（`badge` 那种底落在有渐变的方向线上会留一圈看得见
的边）。三条口径：① 整条包在 `@supports (paint-order:stroke)` 里——不支持时描边压在字面上
会**反过来啃字**（实测 `po:normal` 那行笔画明显被盖细一圈），所以不能只靠声明；② 光环色取
**舞台局部底色**而不是 `--bg`/`--panel`，取错就在字周围留一圈深色边；③ 描边宽度用 `em`
（`.22em`，实测 13px 字上 2.86px、比值 0.220）跟着字号走，写 px 常数会在小屏档（`put()`
统一放大字号）露缝。

**新令牌与注册变量**：`--dur-flow:.9s` / `--flow-seg:.16` / `--stagger:.06s` /
`--stag-cap:.18s` / `--halo-bg:#1a2029`，以及
`@property --delay{syntax:'<time>';inherits:false;initial-value:0s}`。
`inherits:false` 是本轮的正确性前提：不注册的话 `--delay` 是个**会继承**的 token，
`.stag>*` 写在容器上会在**每一个后代**上各自重算 `sibling-index()`，而 `put()` 的常态就是
嵌套建档（一个 `g` 里还有 rect 与 text），结果是深层小图元也各自错开——那不是错峰入场，
是满屏乱闪。实测注册后分组层得 0 / .06 / .12s、其内部后代全部 0s。

### 修正（两处，都是本轮量出来的真问题）

**门禁一族的 reduced-motion 兜底从来没落上过。** 旧那行
`.gate,.gate.leaving,.gate .g-card>*{animation:none}` 被源序排在媒体块**之后**的基础规则
（`.gate{animation:gateIn…}`、`.gate .g-card>*{animation:cardIn…}`）顶掉——同特异度平手时
后来者胜（与 0.15.0 折叠、0.16.0 侧栏入场、0.17.0 掠光完全同一课）。此前一直没被发现，
是因为 §⑤ 那条 `animation-duration:.01ms!important` 的时长钳制把动画压成不可感知，**但
`animation-delay` 不在钳制范围内**：门禁卡片一挂 `.stag`，减少动效下三张卡片仍然按
0 / .06 / .12s 一个接一个冒出来，正是该关掉的行为。修法是新加一条带 `#gate` 的
`#gate,#gate.leaving,#gate .g-card>*{animation:none}` 抬到 id 特异度（旧那行整串保留，它
同时兜住 `.in/.pop/.draw/.pulse`——那四条的基础规则写在本块之前，正常生效）。实测关掉后
`animationName` 为 `none`、三项延迟全落 0s。

**门禁卡片的三份 `nth-child` 魔法数换成 `.stag`。** 原来 `.04/.11/.18` 写死三项：runtime
动态生成的交互块一旦多于三项，第四项起延迟落回 0 与第三项同时进；少于三项时后面的档位白占着。
同时 `.flow` 补 `pointer-events:none`（首版漏了，实测命中会落在 dash 空隙之间的流光层上）。

### 一处行为收窄（升级页面要注意）

`--delay` 自 0.18.0 起注册成 `inherits:false`：**写在外层包装节点上的 `--delay` 不再传给
子节点的 `.in`**（以前作为未注册 token 会继承下去）。这是刻意的——不这么锁，`.stag` 就会
把嵌套建档的深层小图元一起错开。现有写法 `.st1/.st2/.st3` 与 `style="--delay:.55s"` 都仍
合法（都写在动的那个元素自己身上）。要让一组图元整体错开就挂 `.stag`，要让单个图元错开就
直接写在它身上。全文见 stage.md §3.4。

### 否决（四种看着前沿、实测不合格的写法）

`<animate>` / `animateMotion` 这类 **SMIL** 驱动流光——不吃 `animation:none`，可及性兜底与
导出那一刀都管不住它，`getAnimations()` 也冻不住（探针拿不到可设 `currentTime` 的对象），
"量过才写进范本"这条纪律根本没法执行；**`offset-path` + 一个小圆点沿线走**——移动的子节点
不进 `getBBox()`，检查器与几何口径看不见它，而且它有实体：切掉 animation 之后圆点落在路径
起点，导出帧上多出一个谁也没请的小点（`.flow` 两个端点都"看不见"正是为了避开这一坑）；
**marching-ants（无限循环的蚂蚁线）**——违反"循环动画的 `0%` 必须等于静止态"，逐帧冷启动时
每帧截到的相位都不一样，成片里流光位置乱跳；**用 `transition` 驱动 dashoffset**——renderer
同帧写起点与终态时根本不补间（实测计算值一步落 `-1px`，没有"上一状态"可比），要它补间就得
在两次写之间塞强制回流或一帧等待，那是在 renderer 里手搓两拍时钟。写进 references/stage.md
的"别这么干"，免得下轮重新踩。

### 其他

`references/stage.md` §3.4 表格增四行 + "连线与标注（0.18.0）"一节 + 本轮"别这么干"四条，
并把错峰那段的旧说法（"门禁卡片用 nth-child"）改成 `.stag`；`references/runtime.md` §7 补
0.18.0 四条的导出侧口径（只有 `.flow` 会动，而它**不需要见旗分支**）与一条 QA 取样提醒：
冷启动那一帧"看不见"是**正确定格**，不许把"这一层零像素/零命中"当失败信号；`SKILL.md` §5
增补一段。
`tests/test_iteration_round2.py` +4 例（共 41 例）：静态契约一枚（五个令牌与注册串、两处
减号空格、`@keyframes` 两端、`.flow` 三条不许（`infinite` / ease-out / 混进 `.nt` 过渡清单）、
`.crisp` 消费者、`.halo` 的 em 与 `@supports`、门禁挂 `.stag` 且旧 nth-child 整条消失、
兜底两行、四条否决技法不得回流）+ CDP 三例（流光两端零命中与一路连续、重挂口径与切掉
animation 的落点；错峰分组层 vs 深层、1/2/3/6 项总量守恒、带空格与不带空格的 A/B、
`vector-effect` 不继承、canvas 墨量 A/B、光环比值；减少动效下流光落回静止态、门禁一族真的
关成 `none` 且延迟归零）。本机全量 **389 passed / 2 skipped**（0.17.0 时 385 passed）。
模板未新增门、未动 GATES。

## [0.17.0] - 2026-10-18

### 新增（边缘与材质——羽化揭示 / 放行键掠光 / 排版留白层 / 主色浅档令牌）

**羽化揭示。** 新增 `@property --feather{syntax:'<percentage>';inherits:false;initial-value:134%}`，
注册之后 CSS 才能逐帧补间它，`mask-image:linear-gradient(90deg,#000 0 calc(var(--feather) - 34%),transparent var(--feather) 100%)`
里的 calc 才会跟着走。`initial-value` 必须同时是**终值**（134%）——这样不播、不能播、被导出注入
`animation:none!important` 时都落在"全可见"，兑现"动效不能当内容的门"那条硬约束（进场从 0% 由
`@keyframes feSoft{from{--feather:0%}to{--feather:134%}}` 负责）。34% 的羽化带让整行字
**先认得出、再读完整**，而不是硬边划过去。

关键一条：**mask 不裁命中区**（和 0.15 的 clip-path 正相反）。134% 时整行可点、点不出东西，
这个交互是坏的——所以 `.featherR/.featherL` 带 `pointer-events:none`，外层 `<g>` 只当几何锚点，
可点层照旧压在下面。实测补间取 `pause()+currentTime=t`：0→88.5→128.8→133.8→134，
渐变两个 stop 始终是 `feather-34%` / `feather`；`getBBox` 全程 `[4,6,292,28]` 一格不变（mask 不改几何，
对排版、导出、"不读 getBBox"那条纪律全都无扰动）。

**放行键掠光。** 只在"刚被解锁的那一下"扫过 `.go`：伪元素 `background-size:260% 100%`，
静息位 `120% 0`——**静息态是光带停在盒子外**，所以不播等于没有光带，导出侧天然干净。
`@keyframes passShine{from{background-position:120% 0}to{background-position:-60% 0}}`，
重放靠 `void gateGo.getBoundingClientRect()` 强制 reflow（0.14 实测 `offsetWidth` 同样有效，
`getBoundingClientRect` 语义更直白）。`syncGate()` 里**先取差异再写状态**：`var nextShown =
!gateNext.hidden;` 必须在 `gateNext.hidden = false;` 之前——写在之后则每次 sync 都"变了"，
掠光连点连闪，成了装饰而不是反馈。

一个 CDP 才浮出来的时序：`#pregate` 那 1.1 秒 overlay 还挂着期间，`#gate` 是 `display:none`，
`.go` 的盒子 0×0，**伪元素上的动画根本不会被创建、也不进 `document.getAnimations()`**。
所以"掠光没触发"这种误报要等 overlay 撤下、`gate-next` 真的可见之后再判；`currentTime`
在注册后的第一帧读也是 0，取增量得等它 > 0。reduced-motion 的关闭清单里写的是 `#gate-next .go.shine::after`
（`#gate-next` 特异度压过 `.gate-next`）——光带停盒外、不播即无光，终态天然成立。

**排版留白层。** `text-wrap:balance` 上标题（两行 19px：`[317,223]` → `[279,261]`，单行是 no-op）；
`text-box-trim:trim-both` + `text-box-edge:text` 上 tagline（盒高 22 → 15，字的位置不动）。
trim 只把行盒上下的 leading 收回，视觉上像
"字自己贴住上沿"，其实是排版盒子被削到字面——这一层跟运动无关，但直接决定元素之间"喘不喘气"。
Chrome 152 只认 `edge:text`，`cap` / `ex` 解析失败整条丢弃。

**主色浅档令牌。** `--key-soft / --key-softer / --key-act`（14% / 8% / 16%），基线是手抄 rgba、
`@supports (color:color-mix(in oklab,var(--key) 14%,transparent))` 里换 color-mix。
**不能写同名行**——不支持 color-mix 的浏览器会"后写覆盖"拿到一个无效的 `color-mix(...)` 而不是退回 rgba。
消费点是 HTML 侧那四处浅档（`.chapter .tag` 底色、`.rack .spd[data-off]`、`.hotspot-stage .hs` 的
虚线底、`.hs:hover`）；SVG 里的 `.12` / `.13` 字面量与 `::selection` 的 `.28` **不改**——
它们不是浅档，硬套令牌会顺带改掉暖色温。

### 修正（一处测试写法）

`.chapter .tag` 的 computed background 是 `oklab(...)`（color-mix 的解析结果），canvas 的 `fillStyle`
getter 原样回吐作者写的规格串，所以"字符串相等"不是像素相等的合法证法。改成两块同底色
（`#101319`）各自铺满后 `getImageData` 比像素——实测 `color-mix(in oklab,… 14%)` 与
`rgba(245,166,35,.14)` 合成结果都是 `(48,39,26)`，16% 那一对都是 `(52,42,26)`，逐字节相同。

### 否决（四种看着前沿、实测不合格的写法）

`@property` 整数 + `counter()` 数字轮（缺 `@keyframes` 与计数器变量时**直接渲染空串**，
不是退化成静态数字）；缩放 `<svg>` 上的 conic 扫描（栅格化按缩放后的盒子做，边缘糊）；
View Transitions / `content-visibility` / `calc-size()`（要么拿不到可靠的"跳过动画"开关，
要么直接崩导出侧逐句冷启的帧）；`animation` 简写属性引用未定义变量（一条 ACVT 警告把整个简写
作废，动画无声消失）。写进 references/stage.md 的"别这么干"，免得下轮重新踩。

### 其他

`references/stage.md` §3.4 增四行 + "边缘与材质（0.17.0）"一节；`references/runtime.md` §7 明确
这两条动画导出侧**不需要** shot-mode 分支（羽化落在 134% 全可见、掠光静息在盒外且 shot 里
`syncGate()` 提前 return，`gate-next` 永不开）；`SKILL.md` §5 增补一段。
`tests/test_iteration_round2.py` +4 例（共 37 例）：静态契约（令牌串、补间端点、JS 语句先后顺序、
禁用技法清单）+ CDP 三条（羽化补间与命中区、掠光走**真链路** recall 揭示 → `dataset.locked`
→ MutationObserver → `syncGate` 差异判断，只播一次不重播、静息回盒外、reduced-motion 下列表为空）。
模板未新增门、未动 GATES。

## [0.16.1] - 2026-10-17

### 修复（测试口径：浏览器用例别再拿绝对像素当契约，CI 与本地字体环境不同）

GitHub Actions 连续两轮（0.15.1 / 0.16.0）在 `Full test suite` 这一步红，失败用例
都是浏览器测试里**把某个具体渲染环境的测量结果当成了契约**——代码没坏。两种表现：

1. **绝对像素阈值**：本地 Chrome for Testing 152 与 runner 的 `google-chrome-stable
   155` 字体解析差一档，同一份 DOM 量到的字宽/框高整体差一档——侧栏折叠展开高本地
   246.5 / runner 182.1，小屏 26px 档字幕 bbox 本地 545.74 / runner 327.51。写死
   `h > 200` / `bbox > 500` 就把"环境字体差一档"误读成"折叠没演完 / 换档没重量"。
2. **过渡落定态的精确等值**：擦洗态 `.18s` 过渡靠真实帧时间推进，慢一点的 runner
   （实测 CI 3.11）固定帧数没走完收回的尾巴，停在 `bar = 6.02` / `tick = 12.03`，
   而断言写的是 `== 6` / `== 12`。上行方向本来就留了 `>= 8.9` 余量，收回/保持方向漏了。

- 折叠两头（`test_note_accordion_animates_both_ways` /
  `test_growth_reveal_collapse_reduced_motion`）：终态判据从"高度超过某个像素数"
  改成**与 `scrollHeight` 一致**（本来就有一条 `< 2.5` 的一致性断言，那才是钉死终态
  的），绝对阈值只保留"明显不是一条缝"的 `> 20`；收起起点改为**等于展开终态**
  （同一张静止定格）而非 `> 200`。
- 句内细线小屏档（`test_caption_progress_is_a_clock_reading` ⑤）：从"小屏 bbox
  应 > 500"改成**同一句文案在桌面/小屏两档下的宽度之比 > 1.2**。比值由 17px→26px
  字号决定、两量同环境一起随字体缩放，换渲染环境不动（本地实测 1.53）——比绝对
  像素稳，且"细线残留桌面档宽度"仍会被这条比例抓红。换档用 ended → 回拖同一句触发
  `capMeasure` 重跑，与真实页面 `onCompactChange` 走同一条重挂带子的公共路径。
- 擦洗态收回（`test_scrub_affordance_grows_on_intent`）：落定态改为 `settle_geom()`
  **轮询到过渡走完 + 容差 0.1**，不再用 `== 6` / `== 9` 精确等值、也不再固定帧数
  就取数——与折叠用例同一口径（推帧等到落定，谓词不成立到截止才返回最后读数）。

CI 实测：这一版先把 3.10 / 3.12 转绿（绝对像素三条在 runner 上验证成立），再补擦洗态
这条时序容差，目标三档 Python 全绿。本机全量 **381 passed / 2 skipped**；本轮只动测试
与注释，范本、runtime、文档口径不变。

## [0.16.0] - 2026-10-17

### 新增（时间刻度层——擦洗态随意图升级 + 句内进度细线 + 侧栏滚动驱动入场）

前面几轮的动效回答的是"这一步发生了什么"，这一轮回答的是**"现在到哪儿了"**：把
"进度"做成看得见、也摸得着的刻度。

- **擦洗态随意图升级（`.bar` / `.bar .tick`）**：平时进度条是一根 6px 的发丝线，只管
  "读到哪儿了"；指针落上来 / 键盘聚焦 / 按下拖动时轨道升到 **9px**、章节刻度从 12px
  长到 **16px**（affordance-on-intent：把"现在可以拖"画在你正指的地方，而不是在旁边
  再摆一个可见把手）。三条边界：只长高不加 margin（`.rack` 行高不跳，读数与倍速钮
  一格不动）；圆角跟着高度走（6/3 → 9/4.5），填充写 `border-radius:inherit`，否则
  9px 的条顶着一式为 6px 定的圆角、两端发方；**过渡清单只给 `height`/`border-radius`
  两条几何、不给 `width`**——填充那条走 `--dur-fill linear`（"进度连续滑动"的口径），
  两套混进一条清单等于给进度装第二个时钟。刻度改成 `top:50%` + 独立 `translate`
  居中，两种高度共用一条居中公式（各探出 (刻度高 − 条高)/2），不必为第二种高度补
  一套数字。Chrome 152 实测逐帧采到 6 → 7.13 → 7.92 → 8.41 → 8.84 → 9。
- **句内进度细线（`#cap-prog`）**：结论色带说"这句是结论"，这条细线说"**这一句念到
  哪儿了**"（karaoke / now-playing 的做法），但它走在字幕自己的宽度上，不新增一行
  常驻 UI。它**不是动效，是时钟的逐拍读数**，所以口径与其余原语全部相反：只写
  **呈现属性**（不写 `style`、不挂 `transition`）、**不进 reduced-motion 清单**
  （关了动效它也得跟上嘴）、也**不需要导出见旗分支**（切掉 transition 落的仍是那一
  秒的真读数）。几何 `capShow()` → `capMeasure()` 现量一次存进 `capGeo`，`tick()`
  每帧只按 `(t - sent.start) / sent.duration` 写 `width`——把 `getBBox()` 留在每帧
  路径上就是给逐帧循环加一条同步重排。实测三点比例 = 0.000 / 0.500 / 0.999。
- **侧栏滚动驱动入场（`animation-timeline: view()`）**：本轮唯一"前沿"做法，也是唯一
  一条**不归 JS 管**的运动——没有 scroll 监听、没有 IntersectionObserver、没有 rAF，
  进度直接由元素在滚动容器里的位置取（Chrome/Edge 115+ 与 Safari 26 已发布，Firefox
  正式版仍 behind flag，所以整块包在 `@supports (animation-timeline: view())` 里做
  渐进增强）。四条边界：① 隐藏态只写在 keyframe 的 `from`——不支持的浏览器整块跳过，
  文字一格不少（**绝不把内容锁进一个可能不跑的门里**）；② `animation-duration:auto`
  显式写（滚动时间线按进度而非秒分配），长写放在简写之后；③ 落点取 **entry 段内**
  （`entry 0% entry 60%`）；④ 位移挂独立 `translate`。
- **气泡弹簧落定（`.tip.tipPop` + `--ease-spring`）**：窥探气泡出现时轻弹 7px 落住，
  与刻度长高是同一个动作的两半。`transform` 里**每一帧都要重复写
  `translateX(-50%)`**——动画期间的 transform 整条接管静态值，只写 `translateY`
  会让气泡在落定那一瞬往左跳半个身位。重触发判据留在 JS 的差分里（`data-on` +
  节号）：`pointermove` 每帧都进这条处理器，无脑摘重挂就是"气泡一路抖着跟手"。
- **时长令牌**：新增 `--dur-scrub`（`.18s` 擦洗态几何）/ `--dur-tip`（`.26s` 气泡
  弹簧）。句内细线**故意不给令牌**——它是读数，补间只会让它落后于正在念的那个字。
- **reduced-motion 兜底同步**：三条各自关法——擦洗态几何关 **transition**（关时长
  即直接落 9px/16px，"可以拖"仍然看得见）、气泡关 **animation**（`data-on` 那条
  opacity 不是动效，气泡照出、只是不弹）、侧栏入场整条**撤掉 animation**（隐藏态
  只在 `from` 里，不跑就没有隐藏态）。撤掉那条同样要**抬特异度**
  （`ul.notes>li.note`）：入场规则写在 `.notes` 段里、源序排在媒体块之后，朴素
  `li.note{animation:none}` 盖不过它；也不能只靠全局 `.01ms!important` 的时长钳制
  ——滚动时间线要求 `duration:auto`，钳成 0.01ms 后落在哪一格是实现说了算。

### 修复

- **侧栏入场第一版把常驻内容留在了半透明那一格**：落点原本写 `entry 0% cover 32%`，
  而 entry 段只占 cover 全长的 e/(e+v)（条目 45px、栏 533px → 约 8%），cover 32%
  远在"完全进入"之后——实测 Chrome 152 下侧栏**根本不溢出**时下面三条停在
  0.998 / 0.974 / **0.845**。一条装饰性入场不该让参考要点常驻半透明，取 entry 段内
  后静止态全部落 1（CDP 用例 `test_sidebar_scroll_reveal_is_progressive` 锁的就是
  这一格回归）。
- **结束态回拖进同一句，字幕带一直空着**（范本既有缺陷，本轮补做导出验证时才现形）：
  `ended` 收起整条带子并清掉 `capSent`，之后把进度条拖回**最后那句之内**时句号没有
  变化、`tick()` 的换句分支不进——画面、读数、细线都跟着时钟回来了，只有字幕带还
  空着，违反"页面状态 = `currentTime` 的纯函数"。`hit.index >= 0` 那一支补一次
  `if (capSent !== hit.sentence) capShow(hit.sentence)`：同句不重挂、不重量，
  也就不会每帧多一次 `getBBox()`。
- **细线的调用点曾排在换句分支之前**：`tick()` 开头写 `capProgWrite(t)` 时，
  `capShow()` 还没写当句的 `capSent`/`capGeo`。直播里靠后续帧掩盖了，导出是**每帧
  一次冷启动、只跑一次 `tick()`**，而那一帧往往正是换句帧——实测截出来是"字在、
  细线空着"。移到本块之后，另加一条截图模式用例锁"一次 tick 即落读数"。
- `.bar-fill` 的 `inset` 曾在改写时误成 `inset:0 0 0 0`（把填充拉满整条轨道），
  已改回 `inset:0 auto 0 0`；圆角改为 `inherit` 随轨道走。

### 测试

- 静态契约 `test_time_scale_layer_present`：两个新令牌 + 细线**不给令牌**、擦洗态
  三态共用一条规则、`.bar` 的过渡清单里**不许出现 width**、刻度居中公式、
  `tipPop` 两帧都重复写 `translateX(-50%)`、`#cap-prog` 夹在 `#cap-hl` 与
  `#cap-text` 之间且不写 `style`、`capProgWrite` 调用点排在 `capShow` 之后、
  滚动入场整块包在 `@supports` 里且 `animation-range` 不得越过 entry 段、
  reduced-motion 三条各自关法且**不许出现朴素 `li.note{animation:none}`**。
  顺带更新 0.14.0 那条：`.tick` 的过渡清单多了 `height`，收口断言跟着改。
- 五条 CDP 用例：擦洗态逐帧采到中间值（写成瞬跳就失去提示）且离开/松手按意图收回
  （松手时指针仍在条上 → 保持 9px 才对，擦洗态跟意图不跟按钮状态）；句内细线三点
  比例即时钟比例、换句归零、句间空档停在满格、`ended` 归零、**回拖同一句带子回来**、
  小屏 26px 档跟 bbox 现量、`__coursewareShotMode` 下一次 tick 即落读数；气泡只在
  "从无到有 / 换节"重触发（同节内 `currentTime` 只增不减）、落定后 transform 纵向
  分量归 0；侧栏静止态六条全 1、强制溢出后栏外未显形、滚到底全部落定；
  reduced-motion 下擦洗态直接落 9px/16px、气泡照出不弹、入场整条撤掉而细线仍跟上
  时钟。
- 另有一条**真实导出验证**（`--dump-dom` 跑 `export_video._build_shot_page` 产出的
  截图页）：确认注入 `transition/animation:none` 之后细线仍落在那一秒的读数上——
  这条把"终态必须在属性上"从静态推断变成实测。
- 本机全量 **381 passed / 2 skipped**（0.15.1 时 375 passed）。
- 用真鼠标做拖动用例时记一条 CDP 坑：`Input.dispatchMouseEvent` 的
  `mouseReleased` 里 `button` 仍要写 `"left"`、`buttons` 归 0；写 `button:"none"`
  不会合成 `pointerup`，`data-drag` 就留在 1（0.12.0 那条既有用例本来就是对的写法）。

### 文档

- `references/stage.md` §3.4 新增"时间刻度层"一节（三件各自的责任划分：一条状态、
  一条读数、一条纯装饰，所以兜底方式各走各的）与 `cover 32%` 那次失败实测；§4 补
  "结束态回拖字幕必须在场"与字幕带派生视图的口径；§9 自检追加三条勾。
- `references/layout.md` §4 补 `#cap-prog`、§5 补"意图升级"手感、§6 新增"随滚随显是
  装饰，不是闸门"。
- `references/runtime.md` §7 导出契约补 0.16.0 三条的见旗口径（两条不需要、细线照常
  落读数）与两条新义务（`capProgWrite` 排在换句分支之后、`capSent` 差分补 `capShow`）。
- `SKILL.md` §5 动效词汇表补这三件；`README.md` 特性与回归说明同步。

## [0.15.1] - 2026-10-17

### 修复（浏览器预检：三档退路，堵住"浏览器冒烟静默没跑却绿灯"）

- **新增第三档退路：`--no-sandbox` + 隔离 HOME、不传 `--user-data-dir`**。容器里
  以 root 运行 Chrome for Testing 时有**两类**互不相同的故障：① 带沙箱那一档
  必然拒绝启动（`Running as root without --no-sandbox is not supported`）；
  ② 新建 `--user-data-dir` 的**一次性转储**（`--dump-dom` / `--screenshot`）
  **永久挂死**——进程起来了、永不归（实测 Chrome 152：同一条命令去掉
  `--user-data-dir` 后 0.4s 返回；把 profile 放哪、预创建、加 `--no-zygote`、
  `--disable-*` 各种组合、代理断网全部无效；只有把 profile 的落点换成一份
  干净的 `HOME` 才通）。旧预检只看 `--no-sandbox` 这一维，于是第二类故障吃掉
  60s / 120s 硬超时后，`check_gates` 报"预检未通过"、浏览器冒烟整段没跑而页面
  照样返回 0，`export_video` 直接失败。**报错点离病因很远**：CDP 常驻那条路径
  不受影响，所以同一台机器上 tests/ 的浏览器用例全绿，只有转储类调用在挂。
- **口径统一成一个决定**：`_browser_launch_strategy()` 返回
  `{'extra_flags': [...], 'isolated_home': bool}`——**只有决定、不带路径**。
  预检探的是 `--user-data-dir`，真实转储挂的也是它，两者必须是同一次决定，
  否则就是"预检过了、真实调用还挂死"的假绿。`check_gates.browser_check` 与
  `export_video` 逐帧截图都按这个决定拼命令：常规档照旧挂
  `--user-data-dir=<每帧全新>`，隔离档不传它、改把 `HOME`/`USERPROFILE` 指到
  **每帧自建**的临时目录（导出共用一份 profile 会撞 `profile in use`，
  这条既有纪律在隔离档同样成立）。
- **结果按 chrome 路径进程内缓存**：真实导出 33 帧 × 3 档 = 99 次挂死不可接受。
  缓存刻意不存路径，目录由调用方各自建、随 `temp_root` 一起回收——实测一次
  完整 demo（时间轴 → 组装 → 冒烟 → 导出）跑完，`%TEMP%` 里 0 个残留。
- **降级原因写进报告**（`_preflight_note`）：落到第 2/3 档时，人类路径打
  `[note] …`，`--json` 走既有 `note` 字段；浏览器**真的跑过时也打这一行**，
  因为只看"冒烟通过"的人会以为自己的 Chrome 没问题，换环境才知道差一档。
  note 文本不带 `[note]` 前缀，前缀由打印侧统一加（JSON 里保持纯文本）。
- **探测用一次性转储本身**（`--dump-dom`）而不是"进程活着就算过"：第三档的
  故障形态恰恰是浏览器起来了、转储永不归。转储内容不含探针文本也判失败。
- `_browser_preflight` 已删除（唯一调用方就是这两个入口，留一个只看
  `--no-sandbox` 的旧签名等于留一条会退回静默漏检的路）。`_run_chrome` 新增
  可选 `env` 参数（不传即继承进程环境，隔离档只改子进程的 HOME，绝不写回当前
  进程——那会把 `.env` 查找链一起换地方）。

### 测试

- 新增 `tests/test_launch_strategy.py`（19 例）：用一个 **fake chrome 脚本**把
  三类故障各自复现（`never` / `need-no-sandbox` / `hang-on-udd` /
  `isolated-only` / `wrong-output`），断言三档的顺序与各自 flag、落到隔离档
  时**确实不传** `--user-data-dir`（用 `Popen` spy 抓三条命令逐条比）、挂死档
  真的各吃掉一个 timeout（`5 < 总时长 < 60`，防止哪天改成"瞬间失败"却没人发现）、
  三档全挂才返回 None、缓存只探测一次且不含路径、隔离档缺 HOME 当场
  `ValueError`、探针绝不含 `--allow-file-access-from-files`、
  `_isolated_home_env` 不污染当前进程环境。真实 Chrome 那组只锁
  "至少过一档"（无 Chrome 自动跳过）。
- 本沙箱（root + Chrome for Testing 152 + ffmpeg）跑全量的结果：
  **375 passed / 2 skipped**，此前是 351 passed / 7 failed / 13 errors——
  失败的 5 例全部是转储类路径（`test_demo_builds_lesson`、
  `test_demo_export_flag`、`test_demo_check_gates_require_browser`、
  `TestPipelineBrowser::test_browser_smoke`、`test_export_duration_contract`），
  现在都真跑了浏览器冒烟与逐帧导出。

### 文档

- `references/runtime.md` §8：模式一段补"浏览器启动口径三档退路 + 降级原因进
  报告"。
- `SKILL.md` §8：补一句"预检未通过 = 三档都试过"，并说明 `--require-browser`
  在降级环境下会把这种环境故障显式判失败。
- `README.md`「开发与自测」：补 0.15.1 回归说明与本机实测数字。

## [0.15.0] - 2026-10-16

### 新增（生长与揭示——SVG 几何补间 + 擦除揭示 + 0↔auto 折叠面板）

- **SVG 几何属性补间（`.nt` 的 `x/y/width/height/rx/ry/cx/cy/r/d`）**：Chrome 把这些
  几何属性收进了 CSS，renderer 用 `setAttribute` 改它们时 transition 接得住——框可以
  **跟着字长大**，不再靠 `transform:scale()` 假胖（Chrome 152 实测：rect 采到中间值
  width 45.32 / x 22.66 / rx 4.93，circle 采到 cx 203.99 / r 11.06，终态都落回属性）。
  清单**不是全几何**，两条不在列的写进注释当"别这么干"：`<text>` 的 x/y 不是几何属性
  （计算值恒 0px，文字位移只能靠 transform）、`<line>` 的 x1/y1/x2/y2 同样没进 CSS
  （计算值为空，连线要动就用 `.draw` 或挂父层）。`path` 的 `d` 在列但要两条路径命令
  序列同构，否则整条跳档。
- **擦除揭示（`.reveal` / `.revealL`）**：不是"淡进来"，而是用 `clip-path:inset()`
  **从一侧扫出来**——方向本身带信息（左→右读作"往后推演"，右→左读作"回溯"），比
  同质的淡入多半句语义。范本两个消费者都在"这一行是刚串出来的"场合：舞台 B 的结论
  记法 `wipeR`、舞台 C 的降序行 `wipeL`。四条实测边界：① **只裁剪不位移**，
  `getBBox()` 全程恒定（取景、窄屏兜底、QA 几何判定都不受影响），但**命中测试跟着裁**
  ——还没擦到的那半截点不着，不留"看不见却点得着"的幽灵区；② 挂在**包装 `<g>`** 上
  而不是图元本身（`clip-path` 与 `.nt` 的 transform 补间同节点互相干扰，裁剪盒会跟着
  缩放走）；③ 关键帧**两端都必须写 `inset()`**；④ 与 `.in` 一样 `backwards`、
  不写 `forwards`（钉住终态会压住下一次 `setAttribute` 的 opacity）。
- **折叠面板（`interpolate-size` + `display` 参与过渡）**：侧栏 `.note-body` 从
  `display:none` + 一段 `noteIn` 淡入，换成 **0↔auto 高度连续开合**——收起也有动画、
  展开不再"啪"地出现。四件套少一样就退回某一边的硬跳：`:root` 的
  `interpolate-size:allow-keywords`（不用再猜 `max-height` 魔数、也不用 JS 量
  `scrollHeight`，实测开 18→26.2→111、收 111→84.6→18 两侧都采到中间值）；
  **`display` 同时进 `transition-property` 清单**（`allow-discrete` 只管已在补间的
  属性，漏列就还是即时切换——实测半边有动画半边是跳的；语义恰好要的：展开当帧即
  `block`、收起撑到补间结束才 `none`，折叠内容不进可访问树和 Tab 序列）；
  `transition-behavior` **单独一行**（塞进简写里出现不认的关键词整条作废，会把
  height 补间一起带走）；`@starting-style` 给展开一个起点（从 `none` 切 `block`
  那一帧没有"上一状态"）。
- **时长令牌**：新增 `--dur-grow`（`.42s` 几何生长）/ `--dur-reveal`（`.5s` 擦除）/
  `--dur-acc`（`.3s` 折叠开合）。
- **reduced-motion 兜底同步**：`.reveal` / `.revealL` 关动画（关掉后内容直接完整在场，
  不留"半截揭示"的定格）；几何生长随 `.nt` 的 `transition:none` 一并关掉；折叠面板
  两头关成 `none` 并停掉 `noteIn`。

### 修复

- **擦除揭示第一版是 discrete 两格硬跳**：`@keyframes` 只写了 `from`，隐式 `to` 取
  元素自身的 `clip-path:none`，而 `inset()` 与 `none` **不可插值**——浏览器按 discrete
  处理，整条动画只有"全遮"和"全显"两个画面、中间硬跳一下，那是闪不是擦。改为显式
  `to{clip-path:inset(0 0 0 0)}` 后才连上补间（复测采到 100%→85.4%→71.9%→…→none
  的连续序列）。跑起来看不出来，逐帧采样才看出来。
- **reduced-motion 关不掉折叠补间**：兜底行 `.note-body{transition:none}` 在源序上
  **早于**基础规则 `.note-body{transition:height…}`，两者特异度同为 (0,1,0) →
  后来者胜，收起态计算值仍是 `height, display`。改为
  `li.note .note-body,li.note[data-open="1"] .note-body` 两头抬过特异度才真的关成
  `none`。注意这一档不是靠全局 `.01ms!important` 钳制在兜——那条只管时长，
  计算值里补间还在。
- **世界卡写死框宽把字糊出框外**：`poseWorld()` 的 96 只算过不带引号的字形。改成
  先落占位框、`getBBox()` 现量标签字宽、再写终值（`max(74, 字宽 + 28)`，圆角跟宽度
  走）。实测桌面档 `fs40` 的 quoted `'10'` 字宽 96.3 已经压过边，小屏档（字号由
  `put()` 统一放大到 52）顶到 125.1——字整个飞出框外。**占位框只给新建档写**
  （`if (!REG[boxKey])`）：复用节点再写一遍占位宽会被当成一次真实状态变化，画面先
  补向 96 再掉头补向终态，是肉眼可见的抖动而不是生长。

### 测试

- `tests/test_iteration_round2.py::test_growth_reveal_collapse_present`：三原语的静态
  口径（三个新令牌、`interpolate-size` 在 `:root` 内的顺序断言、`.nt` 几何清单逐属性
  断言且 `x1`/`y1` 不得出现、两个 wipe keyframes 逐字含显式 `to`、两个消费者行、
  折叠四件套逐字、`Math.max(74, …)` 现量行与 `if (!REG[boxKey])` 占位行、
  reduced-motion 两条新行 + 旧口径字符串必须已消失）。
- 新增四条 CDP 用例（`full_lesson` 夹具）：
  `test_hero_card_grows_to_measured_width`（pause height/width 补间逐点取值：起点是
  实测字宽档的 76 而不是占位 96、中间值单调、终态 `bw ≥ tw + 27` 且 `x = -w/2` 居中；
  退回旧步再重演起点仍 76（证占位只给新建档）；`Emulation.setDeviceMetricsOverride`
  压到 480 后字号 52、`bw ≥ tw + 27` 仍不溢出）、
  `test_wipe_reveal_sweeps_and_settles`（同一 Promise 内 jump 上一步 → 两帧建档 →
  jump 本步取动画；`inset()` 四值逐点比较——出现非 inset 值即 discrete 回归、只动
  一边、单调变窄、`getBBox()` 恒定、9 个固定分数位命中测试未擦区必不中且命中总数
  单调增；再注入截图页 CSS + `__coursewareShotMode` 重进该步验无动画、`clip:none`、
  完整可见）、
  `test_note_accordion_animates_both_ways`（展开/收起两侧都采到中间高度、收起全程
  `display:block` 撑住到补间结束、终态 `h ≈ scrollHeight`、`aria-expanded` 归位、
  补间收干净）、
  `test_growth_reveal_collapse_reduced_motion`（三原语一步到位，`transition` 计算值
  写死必须为 `none`——旧口径下这里是 `height, display` 会被判过）。
- 采样手法三条教训（写进测试注释）：新建档节点的动画要等两帧才登记进
  `getAnimations()`；**取 CTM 换算命中点之前必须先 pause 擦除、再 rAF 轮询等 `#cam`
  补间 idle**，否则相机在飞、命中全错（wipeL 那一侧第一版全报 False）；headless
  帧稀，discrete 的 `display` 切换靠固定 sleep 会偶发停在 `display:running`，改用
  rAF 催帧轮询（终止条件"目标态成立且无在跑的 height/display 补间"）。

### 文档

- `references/stage.md`：§3.4 原语表增补三行，令牌清单补齐，新增 0.15.0 小节（三条
  原语 + 三个实测坑），§9 自检加三条对应勾。
- `references/layout.md`：§6 侧栏补折叠口径（默认合、`n.open` 只给一条、四件套不要
  精简）。
- `references/runtime.md`：§7 导出旗标义务补三条新原语（擦除揭示与几何补间照常落
  终态、折叠不需要见旗分支，都不新增抑制逻辑）。
- `references/template.html`：`.nt` / wipe / 折叠三处注释按实测重写（含写死框宽的
  实测量级）。
- `SKILL.md` §5、`README.md`「开发与自测」：动效原语与回归清单跟进。

## [0.14.0] - 2026-10-15

### 新增（空间与连续性——远景视差 + 方向感换场 + 结论句划重点）

- **远景视差（`#depth`）**：取景框里多一层与 `#cam` **同级**的远景点阵，镜头每落
  一次，它按 `--par-far`（.45）**同向**漂一小段——单层运镜只说明"看哪儿"，两层
  速度差才说明"画布是有深度的空间"（真实摄影机摇镜时背景跟得少）。远景只跟着漂、
  **不跟着放大**，这一条恰恰是"它很远"的线索。两个口径：① 同级而不是 `#cam` 的
  子节点——`#cam` 自己带 `transform` 属性值，再叠一层会互相顶掉，同级也保证它
  跟着被取景框裁切；② 位移取 `camTo()` 回传的 `tx/ty`（一并回传，倍率钳位公式
  从此只有那一份），系数由 JS 从令牌读，`parseFloat` 得 NaN 时落回默认档。
  实测（Chrome 152 CDP）：把 `#depth` 推到 `translate(-420,-260)`，`#world` 的
  `getBBox()` 一格没动——**视差不污染取景、窄屏兜底与 QA 几何判定**。
- **方向感换场（`.el.swF` / `.swB`）**：前进从右侧进、后退从左侧进。章节头已经
  报了"换节"，画面这一层再给出"往哪儿换"——回拖进度条重看时方向相反，观众不用
  读数字就知道自己在往前走还是往回走。判据与 `.chFlip` 同源（`lastSceneIdx`
  比对，**换节不是换句**，挂在换句分支上就是每句推一下的闪烁）；冷启动第一步不演
  （没有"上一节"可比）；擦洗与 `__coursewareShotMode` 下一律不演（与运镜模糊同
  一口径）。位移写在**独立 `translate` 属性**上，沿用 0.13.0 那条"独立属性不参与
  缩放、不顶掉作者写在 transform 上的位移"的教训，且不进 `getBBox()`。
- **结论句划重点（`#cap-hl`）**：`hl:true` 那一句除了换主色，再沿自己的字宽划一道
  半透明主色色带（kinetic typography 的克制版——给"这是结论"一个**连续动画**，
  而不是只换个颜色）。几何**从 `#cap-text` 现量**（`capShow()` → `capHl()` 取
  `getBBox()`）：字号有桌面 17 / 小屏 26 两套档位，写常数必错位。色带写在
  `#cap-text` **之前**，字压在上面。`x` / `width` 是 SVG 的 CSS 几何属性
  （Chrome 152 实测可补间：宽从 0 长到 136.4px），同时把值写进呈现属性兜底——
  不支持的浏览器直接落终态。**非结论句一律写 0 宽**：收起与生长起点是同一次写，
  否则上一句的色带会赖在下一句下面。色带是字幕带的派生视图，**不是第二份文案**，
  QA 读的仍是 `#cap-text`。
- **令牌收口**：新增 `--dur-swp` / `--dur-hl` / `--dur-tick` / `--dur-rack` /
  `--par-far`，顺带把章节刻度点亮（`.2s`）、窄屏与全屏播放器行浮起（两处成对的
  `.22s`）这几处残留魔法数收进令牌；悬浮/拖拽一类细手感统一到 `--dur-hover` /
  `--dur-fast`。
- **reduced-motion 兜底同步**：`.el.swF` / `.el.swB` 关动画、`#depth` 关补间、
  `#cap-hl` **只关过渡**——色带承担"这是结论"的强调，属状态不属装饰，终态留在
  属性上，关掉动效仍完整划在那一句上。

### 修复

- **换场 class 滞留导致旧动画重放**：`only()` 换幕时给退场图层一并摘掉 `swF`/`swB`。
  滞留的话下一次入镜会重放一遍旧动画——正常播放时 `sceneSlide()` 自己摘重挂看不
  出来，但导出是**每帧一次冷启动**且不跑 `sceneSlide()`，那一帧就停在半推半进的
  位移上（CDP 用例先撞出来的：截图模式下 `#stB` 仍报 `swF` 在跑）。

### 测试

- `tests/test_iteration_round2.py::test_parallax_transition_and_highlight_present`：
  三项原语的静态口径（视差层在 `#frameClip` 内且与 `#cam` 同级的**顺序断言**、
  `camTo` 回传 `tx/ty`、`depthShift` 只从令牌读系数、换节判据与双抑制、色带在
  `#cap-text` **之前**、几何现量 + 属性双写、五个新令牌与 reduced-motion 追加行）。
- 新增四条 CDP 用例（配 `_motion()` 过滤——`getAnimations()` 把 CSS transition
  也列进来而 transition 没有 `animationName`，不过滤会把幕布淡入误判成"演过了"）：
  `test_parallax_depth_moves_less_than_camera`（FULL 窗不漂、运镜后同向且比值锁
  .45、推到极端不改 `#world` bbox、回写落回同一档位）、
  `test_scene_transition_direction_full_lesson`（前进 `swF` / 后退 `swB` 互斥、
  同节换句不演、截图模式连 class 都不挂）、
  `test_highlight_grows_and_clears`（非结论句归零、色带左缘贴 bbox 左缘、宽度等于
  当句字宽、采到生长中的中间值、终态按 SVG→屏幕 CTM 缩放比换算后才比）、
  `test_transition_parallax_highlight_reduced_motion`（换场与视差不演、色带终态照留）。
- `full_lesson` 夹具改走 `build_timeline --source`：结论句 `hl` 只认旁白脚本里的
  句序号，写在 manifest 句子上不会被读——不传讲稿，成品页就没有 hl 句，划重点那条
  用例会空跑（与 `demo_lesson.py` 同一条真路）。

### 文档

- `references/stage.md`：§3 舞台模型图补 `#depth` 一行，§3.4 原语表增补三行 +
  新增 0.14.0 小节（含同级/回传口径、重放坑、色带归零），令牌清单补齐，§9 自检
  加三条对应勾。
- `references/layout.md`：§2 舞台补视差层的挂载位置与理由；§4 字幕补结论句色带
  （派生视图、不进 `#cap` 显隐链、不改变 QA 比对节点）。
- `references/runtime.md`：§7 导出旗标义务新增一条（方向感换场见旗不演；视差与
  色带照常落终态）。
- `SKILL.md`、`README.md`：动效原语与回归清单跟进。

## [0.13.0] - 2026-10-13

### 新增（注意力与手感原语——焦点弱化 / 按压 / 运镜模糊 / 章节翻场）

- **焦点弱化（注册自定义属性补间 filter）**：`@property --dimmer{syntax:'<number>'}`
  把 `--dimmer` 注册成数字，`transition` 才能逐帧补间它，`[data-dim="1"]` 用它驱动
  `filter:saturate()/brightness()`。弱化是**材质变化而不是消失**——退居背景的
  东西仍可当参照，比"opacity 压到一半卡住"更像"退到后面"。**只碰 filter 不碰
  opacity**：CSS opacity 会盖掉 renderer 写在呈现属性上的逐句档位。范本两处消费：
  seg-2 换组后的上一行、seg-3 被聚焦行挤到背景的对照组。不支持 `@property` 的
  浏览器里不补间、直接跳档，终态一致。
  注册名刻意避开调色板已有的 `--dim` 色值：`@property` 是整页生效的，把 `--dim`
  声明成 `<number>` 会让十几处 `color:var(--dim)` 的灰字在计算值阶段判为非法、
  整条作废回落到继承色（真实浏览器实测：`#8d95a8` → `#e9ecf3`）。这个坑是
  本轮自己在 CDP 里撞出来才改的名。
- **按压手感（`:active` + 独立 `scale`）**：选项 `.opt`、门禁/活动主按钮 `.go`
  缩到 `.94`，播放器 `.rack button` 与热点 `.hs` 缩到 `.9`。用独立变换属性而不是
  写进 `transform`：独立属性**先于** transform 合成，会连 transform 里的位移一起
  缩放——实测 `.hs` 靠 `transform:translate(-50%,-50%)` 居中时按下去漂 2.2px，
  改用 `translate:-50% -50%`（独立属性不参与缩放）后位移为 0。
- **运镜运动模糊（速度决定模糊）**：`camBlur` keyframes（0%/100% = `blur(0)`，
  中段 2.2px）由 `#cam.blurpass` 挂上，`--dur-blur:.42s` + `--ease-in-out`。
  只在**真的发生一次大幅运镜**时触发：`camBlurOK` 按世界坐标位移 > 180 或倍率
  相对变化 > 22% 判阈值；冷启动第一步、进度条擦洗期间（`scrubbing`）、导出截图
  模式（`__coursewareShotMode`）下一律不糊；不达标的这一步主动 `remove`，避免
  残留 class 让下一步白糊一次。0%/100% 落在 `blur(0)` 与"循环动画 0% = 静止态"
  那条导出纪律同源，切掉 animation 仍是清晰定格。
- **章节头翻场**：`#chapter` 换节时重放 `.chFlip`（6px 上浮 + 淡入，`--dur-ch:.22s`，
  `backwards`）。判据是"换节"而非"换句"——`tick()` 的换句分支每句都进，另存
  `lastSceneIdx` 比对；写成每句抖一下就成了闪烁。重触发口径与字幕翻句同款
  （remove → 读一次几何 → add）。
- **时长令牌收口**：把散落的魔法数补进 `:root`——`--dur-hover` / `--dur-cam` /
  `--dur-veil` / `--dur-el` / `--dur-ch` / `--dur-blur` / `--dim-focus` /
  `--dur-dim`；悬浮、幕布、元素淡入、热点、笔记头等一律引用令牌。
- **reduced-motion 兜底同步**：`#cam.blurpass{animation:none}`、
  `.chapter.chFlip{animation:none}`；按压手感与焦点弱化本是"状态即终态"，过渡被
  `transition:none` 收掉后直接落档——关掉动效，画面信息一格不少。

### 测试

- `tests/test_iteration_round2.py::test_focus_dim_press_feel_and_camera_blur_present`：
  `@property --dimmer`、`[data-dim="1"]` 的 filter 公式、`.nt` 的 `--dimmer` transition、
  两处 renderer 消费、四处 `:active{scale}`、`.hs` 的 `translate` 属性居中、
  `camBlur`/`blurpass`/`camBlurOK` 阈值与三种抑制、`chFlip` + `lastSceneIdx`、
  新令牌与 reduced-motion 追加行的静态契约。
- 新增 CDP 真实浏览器用例四条（配 `_cdp()` 连接助手 + `full_lesson` 夹具：时间轴
  取范本自带那一份 33 句，才够到弱化与大运镜所在的步骤）：
  `test_chapter_flip_only_on_scene_change`（换节翻场、同节换句不重放）、
  `test_focus_dim_tweens_filter_not_opacity`（喂非法值落回 `initial 0` 证注册生效、
  过渡中采到 `--dimmer` 中间值证真补间、终态 `filter` 生效且 `opacity .45` 未被接管）、
  `test_camera_blur_threshold_and_shot_mode`（小步运镜不糊且 class 被摘、大幅推近
  糊、落定回 `blur(0)`、截图模式一律不糊）、
  `test_press_feedback_and_reduced_motion_fallback`（`Input.dispatchMouseEvent`
  真按出 `scale .9` 并复原；`Emulation.setEmulatedMedia` 模拟系统减动效后翻场/
  模糊不跑而弱化档位照落）。

### 文档

- `references/stage.md`：§3.4 动效原语表增补 `[data-dim]` / `#cam.blurpass` 两行，
  令牌清单补齐，新增 0.13.0 小节（含独立变换属性合成顺序的实测坑）。
- `references/layout.md`：§5 播放器行的手感层补按压下沉。
- `SKILL.md`、`README.md`：动效条目一句话跟进。

## [0.12.0] - 2026-10-12

### 新增（播放器动效体验——手感层）

- **弹簧缓动令牌 `--ease-spring`**：`linear()` 逐点近似"过冲回弹再落定"（前沿
  缓动技术，模仿弹簧/橡皮筋物理手感）；浏览器不支持 `linear()` 时整条声明作废、
  自动回退上一值。已应用于答对反馈 `fbOk`（答对轻弹现在是弹簧落定）。
- **进度条拖动跟手模式**：按下进入（`.bar[data-drag]` 把填充过渡缩到 50ms，
  指针在跑不拖后腿），松手退出恢复 `--dur-fill` 滑行到位。
- **当前章节刻度点亮**：`.tick.on`（主题色 + 微光晕），由 tick() 按
  `sceneIdxAt` 逐拍维护——常驻"你在哪"位置 cue，纯颜色变化非循环动画。
- **窥探气泡改 pointer 事件**：指针捕获期间兼容 mouse 事件不可靠（自动化驱动
  与部分嵌入式内核不合成），pointermove 在悬停与拖动中都稳定触发——拖动时
  tip 一路跟随指针报"第 N 节 · 标题 · 时间"。

### 测试

- `tests/test_iteration_round2.py::test_player_motion_present`：`--ease-spring`
  令牌、`.bar[data-drag]` 规则与 data-drag 生命周期、`.tick.on` 与 tick() 维护
  循环的静态契约。
- 新增 CDP 真实浏览器用例 `test_bar_drag_follow_tick_highlight`：
  `Input.dispatchMouseEvent` 真实驱动按下/拖动/松手——跟手模式进入与退出
  （过渡 0.05s ↔ 0.25s）、拖动中 tip 点亮并报时间、seek 进 seg-1 后恰有一根
  `.tick.on` 且 `data-i=1`（本机有 Chromium 时直接跑，CI 兜底）。

### 文档

- `references/stage.md`：§3.4 补"播放器动效体验"小节。
- `references/layout.md`：§5 播放器行补手感层三件套。
- `SKILL.md`：§5 画面渲染补播放器动效一句。
- `README.md`：「开发与自测」补 0.12.0 回归说明。

## [0.11.0] - 2026-10-12

### 新增（微动效层——现代交互的"手感"）

- **进度条平滑补间**：`.bar-fill` 加 `transition:width var(--dur-fill) linear`
  （新令牌 `--dur-fill:.25s`），播放与拖动时进度不再每拍跳变，而是连续滑动。
- **字幕翻句微动效**：新令牌 `--dur-cap:.22s`；`#cap-text.capLift` 只在文本
  真的变化时由 `capShow` 重触发（`remove + getBoundingClientRect + add`），
  keyframes 纯上浮 6px——同句重显（pause 恢复）不重放，空档保留上一句不误
  触发；纯 transform，不碰检查器判字幕可见性的 opacity/visibility 链。
- **门禁反馈微动效**：新令牌 `--dur-fb:.32s`；答对 `fbOk` 轻弹入场（淡入 +
  上移 4px）、答错 `fbBad` 横向抖动（±4px 两摆）。runtime 每次尝试都
  remove+add 状态 class，动画天然重触发。
- 以上三条全部纳入 `prefers-reduced-motion` 兜底（`animation/transition:none`）。

### 测试

- `tests/test_iteration_round2.py::test_micro_motion_present`：新令牌、三处
  微动效的 CSS/JS 静态契约与 reduced-motion 扩展段。
- CDP 真实浏览器测试追加：翻句后 `#cap-text` 带 `.capLift` 且
  `animationName === 'capLift'`；reduced-motion 下字幕动画与舞台入场同关。

### 文档

- `references/stage.md`：§3.4 补"微动效"小节（三条的落点、触发与兜底）。
- `SKILL.md`：§5 画面渲染补微动效层一句。
- `README.md`：「开发与自测」补 0.11.0 回归说明。

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
