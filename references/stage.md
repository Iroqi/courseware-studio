# 舞台与逐句渲染

> 写 renderer 与动效时读。`references/template.html` 是语法范本（class、令牌、关键帧都
> 在里面），本文件说明渲染模型、原语语义与边界。所有约定都服务同一条主线：
> **页面状态是 `audio.currentTime` 的纯函数**。

## 1. Renderer 的职责

### 1.1 调用形状

renderer 接收一个句序号：

```js
renderScene(step)
```

输出这一句应该看到的 SVG / DOM 状态。主循环负责：

```text
audio.currentTime
  ↓
sentAt(t)
  ↓
如果 scene / index 变了 → begin() → RENDER[scene](index) → worldFor() → camSet()
```

### 1.2 必须是纯函数

**renderer 必须是 (scene, step) 的纯函数**：同一句在任何路径上都画出同一张图。

这不是洁癖——`export_video.py` 导 MP4 时**每帧开一个新浏览器**、直接 seek 到那一秒，
只跑这一步的 renderer。任何"记住上一步演到哪儿"的写法（模块级可变状态、按历史推导
出来的姿势）都会把第一句的画面漏进后面每一帧。

## 2. 四条纪律

### 2.1 不在 renderer 里计时

renderer 不拥有时间语义。禁止用 `setInterval` / `setTimeout` 排程后续视觉状态——句
序号是唯一驱动源，`check_gates.py` 会把它报成错误。

要"动"就声明**目标状态**，让 CSS 去补间（§3.4）。renderer 里既不排程也不轮询，
一次性 `requestAnimationFrame` 也不需要。`check_gates.py` 对 renderer 体内的
`requestAnimationFrame` 仍报警告：那是在用 JS 手搓补间，回到声明式写法即可。

### 2.2 句号决定步数

一幕有 6 句旁白，就按 6 个主要视觉步设计。不要凭感觉再造第 7 个中间状态。

### 2.3 字幕不属于 renderer

renderer 画图，时间轴写字幕。

图元上的文字只能是**短标注**：公式、代码、记号（`🔤 [10,9,1].sort() → [1,10,9]`、
`只看符号`）。检查器会把三类素材与旁白原句做**形状**比对：

- renderer 体内名为 `txt()` / `badge()` 的写字函数调用（自造 helper 起这两个名同样
  被覆盖）；
- `textContent` / `innerHTML` / `createTextNode` 直接赋值的字符串字面量；
- 页面除字幕钩子外的静态 SVG `<text>`。

判据是形不相似，不是意不相似：把标注写成短语 / 代码而不是完整句子，天然安全；把
旁白那句话原样抄进图里必踩。机制细节见 `runtime.md` §8.2。

### 2.4 图形表达优先于装饰

| 结构 | 常见形式 |
|---|---|
| 顺序 | 流程图 |
| 关系 | 关系图 / 连线 |
| 系统组成 | 分区 / 架构图 |
| 对比 | 双栏 / 对照矩阵 |
| 状态 | 状态块 / 状态图 |

装饰可以有，但不要因此再造一个时间系统。结构之外还要先选**表现方式**（真实体可以
上照片、抽象概念可以生图，见 §8），内联 SVG 仍是默认形态。

## 3. 舞台模型：常驻图元 + 世界层 + 相机

一幕一屏、每步重建节点的写法有两个天花板：节点是新的，所以东西**没有"从哪儿来"**
（只能凭空出现）；场景是孤岛，所以**上一幕的主角走不进这一幕**。模板把舞台换成三层：

```text
<g clip-path="#frameClip">   ← 取景框：字幕带以上才是画面（硬边界）
├ <g id="depth">             ← 远景视差层（纯装饰，比 #cam 慢一拍）
└ <g id="cam">               ← 唯一的相机，camSet() 每步声明一个取景窗
  ├ <g id="world">           ← 跨幕常驻的主角：POSE 给姿势，WORLD 给哪一步用哪个
  └ <g id="stX"> / <g id="x-body"> ← 这一幕的局部图层
```

### 3.1 建档、写状态与撤走

| 调用 | 作用 |
|---|---|
| `begin()` | 主循环每步一次，开一个世代号 |
| `put(parent, tag, key, attrs)` | **写目标状态**：这个 key 没见过就建档（入场），见过就地改属性 |
| `txt(parent, key, attrs, s)` | `put` 的写字形状 |
| `badge(parent, key, x, y, s, col)` | 短标注：药丸底 + 居中字 |
| `sweep(parent)` | 本步没被写过的 key 撤下台（递归整棵子树） |

规则只有一条：**这一步看到的每个图元，都要在这一步被写过一次**。写了就留下，属性
变了就补间，没写就被撤走。所以"淡出"是把 `opacity:0` 再写一步、下一步不再引用——
不是自己消失。

节点复用是这一切的前提：同一个 key 在下一步拿到的是**同一个 DOM 节点**，CSS
transition 才有"上一状态"可补间。key 同时写进 `id` 与 `data-key`，所以嵌套建档只要
父节点那一步写了 key 就够。

### 3.2 世界层：跨幕常驻

段与段之间最贵的不是新画面，是"刚才那三个数去哪了"。把主角写进世界层，它就被观众
一路认出来：收上台当参照物、被搞乱、最后回到台上回答问题。

```js
var POSE  = { bench:{y,xs,k,op,quoted,tone,order}, shelf:{…}, messed:{…} };
var WORLD = { 'seg-1': ['bay','bay','sorted','shelf'], 'seg-5': ['off','off','off','off','messed','messed'] };
```

- 姿势是**整步的快照**：位置、缩放、灯亮不亮、染色、顺序一次给全，段内不换姿势就
  不会在两句之间漂一下重心；
- `WORLD[scene]` 里没写到的那一步**沿用本幕最近一个写过的姿势**（画面上就是站着
  不动）；整幕没写 = 主角不在台上（`off`）。沿用是**在这一步就地回推**的，不是记住
  "上一幕演到哪儿"——那是 §1.2 纯函数约束的头号反例；
- 局部图层不要在世界层里重画主角。范本舞台 B 只画两行逐位比较，被排的那三个数始终
  由 `#world` 承担。

### 3.3 相机：取景窗就是构图声明

```js
var CAM = { 'seg-2': [ FULL, [230,61,580,120], [300,72,460,110], … ] };
```

窗是**世界坐标** `[x, y, w, h]`；没写的那一步 = `FULL`（identity，与"不做任何变换"
等价，不会莫名缩放）。`camTo()` 把窗的中心钉到画布可用区中心，倍率钳在 `0.85–1.8`：
低于下限会把内容裁掉，高于上限字会糊到画布外。

三件事一并解决：

- **运镜**：旁白说到哪一位，窗就框到哪一位。`#cam` 的补间时长由这一趟真的走了多少
  现求（`0.5s–0.78s`，见 §3.4.3 位置与状态）；
- **重心**：声明了窗就等于声明了构图中心——§6 那条"逐场景量 `getBBox()` 配平"从
  人工活变成读表活，一眼就能看出哪一步把内容丢在了画外；
- **窄屏兜底**：窄屏下没声明窗的步按 `contentBox()`（可见内容并集包围盒）居中，桌面
  不动镜头。

取景框是**硬的**：`#cam` 外面套了一层 `clip-path`。镜头推近后可见区仍然是整个
`viewBox`（比窗大），不裁的话被留在身后的内容会一路画到字幕带上、字压字。所以任何
落在字幕分隔线以下的内容都会被裁掉：排版时把它当画布下沿。

### 3.4 动效原语

class 就是节拍词汇。原语只描述**目标状态**与**一次事件的确认**，时长与曲线全部来自
`:root` 令牌。

#### 3.4.1 词汇总览

| 族 | 回答的问题 | 代表原语 |
|---|---|---|
| 入场与节拍 | 新东西怎么上台 | `.in`、`--delay` / `.st1–3`、`.stag`、`.pop`、`.pulse`、`.draw` |
| 位置与状态 | 已在台上的东西怎么变 | `.nt`、几何补间、`#cam`、`#depth`、`.swF/.swB`、`--cam-dur` |
| 揭示与退场 | 怎么长出来 / 怎么让位 | `.reveal`、`.featherR/L`、`.tuckR/L`、`.bloom` |
| 关系、注意力与标注 | 该看哪儿、两者是什么关系 | `[data-dim]`、`#spot`、`#cap-hl`、`.mark`、`.strike`、`.scope`、`.brace`、`.flow`、`.halo`、`.crisp` |
| 行进与换位 | 谁走到哪儿、谁先动 | `.ride`、`.wake`、`.swap`、`.trail` |
| 判定与重量 | 结论落下来、天平读出方向 | `.settle`、`.wind`、`.lift`、`.tilt` |
| 文字 | 念到哪儿、词怎么成形 | `--lit`、逐字解码、`.gather` |
| 交互反馈 | 手按下去收到了什么 | `.opt.rip`、`.hot`、磁吸迎手、`.mag`、`.go.shine` |
| 播放器 | 时间被搬到哪儿 | 擦洗态升级、`#ffwd`、`.bar[data-band]`、`.tip` |
| 氛围与排版 | 这一段什么温度、文字怎么排 | `data-mood`、`.pregate`、`text-wrap:balance`、`text-spacing-trim`、`--key-soft` 族、侧栏折叠 |

#### 3.4.2 节拍与令牌

**节奏是令牌，不是散落魔法数**：缓动与时长集中在模板 `:root`，按域分组：

| 域 | 令牌（例） |
|---|---|
| 缓动 | `--ease-out`、`--ease-in-out`、`--ease-spring` |
| 入场 / 退场 | `--dur-in`、`--dur-move`、`--dur-pop`、`--dur-draw`、`--dur-swp`、`--dur-tuck` |
| 相机 | `--cam-dur`、`--cam-pan`、`--cam-zoom`、`--dur-cam-min/max/pan/zoom`、`--par-far`、`--dur-blur` |
| 注意力 | `--dim-focus`、`--dur-dim`、`--dur-spot`、`--spot-k`、`--spot-soft`、`--spot-bleed` |
| 字母与读数 | `--dur-hl`、`--dur-cap`、`--lit-floor`、`--lit-ramp`、`--flow-seg`、`--dur-flow` |
| 原语专属 | 每个原语 2–4 个（如 `--dur-settle` / `--squash-x` / `--squash-y`） |

改全局节奏只动一处。曲线是 easeOutExpo 家族（`.22,1,.36,1`）：入场与补间共用它，
退场用 inOut。**唯一例外是 `.flip`**：翻面要求"角度对时间居中"，只有对称的
`--ease-in-out` 成立（用 `--ease-out` 实测背面要倒着走 235ms）。

**派生令牌**：`--cam-dur` 不是写死的档，而是由 `--cam-pan` / `--cam-zoom`（JS 每趟
写的行程读数）与两端、权重四个常数在 `clamp()` 里现求出来的时长——令牌仍然只有一处，
数值的来源多了一个连续量。

**错峰入场**：`.in` 支持 `--delay`（`animation-delay:var(--delay,0s)`），现成档位
`.st1/.st2/.st3`（60/120/180ms），也可直接写在元素上（`style="--delay:.55s"`）。
**错峰只用于同一步的一组图元，最晚元素延迟 ≤ 180ms**——单独截一帧仍然讲得清。

`--delay` 注册成 `inherits:false`：写在**外层包装节点**上不再传给子节点的 `.in`。要
一组整体错开就挂 `.stag`，要某个图元单独错开就直接写在它自己身上。

**`.stag` 自动错峰**：`sibling-index()` / `sibling-count()` 现算延迟，档间距取
`--stagger`，总量封顶在 `--stag-cap`（实测 2/3/6 项分别落在 0；0/.06/.12；
0/…/.18s，最后一项永不超过 .18s）。作者加图元不用回头改 CSS。三条边界：

- **减号两侧必须有空格**：紧凑写法让整条 `calc()` 在计算值阶段作废，而动画照样跑、
  延迟全落 0s，控制台不报错；
- **必须锁在分组那一层**：`.stag>*` 只命中直接子节点；`--delay` 不注册的话每个后代
  各自重算 `sibling-index()`，深层小图元也各自错开，满屏乱闪；
- 分母用 `max(…, 1)` 兜住单项组。整块包在 `@supports (animation-delay:calc(1ms *
  sibling-index()))` 里，不支持的浏览器整组同进，内容一格不少。

#### 3.4.3 分族手册

**（1）入场与节拍**

- `.in`：建档自带，淡入 + 轻微上浮；终态就是常驻态。
- `.pop`：弹一下，用于"已经在那儿的东西"被点名。
- `.pulse`：呼吸，用于等点击 / 提示该看哪儿。
- `.draw`：描线，配 `pathLength:1`；`stroke-dashoffset` 从 1 到 0。
- `.stag` 与 `--delay`：见 §3.4.2。

**（2）位置与状态**

- `.nt`：属性补间（位置 / 尺寸 / 颜色）。同一 key 的节点复用才有"上一状态"。
- **几何补间**：`rect` 的 `x/y/width/height/rx`、`circle` 的 `cx/cy/r`、`ellipse`
  的 `rx/ry`、`path` 的 `d` 可补间（`d` 要求两条路径命令序列同构）。`<text>` 的
  `x/y` 与 `<line>` 的 `x1/y1/x2/y2` **不在列**——文字的位移只能靠 `transform` 或
  独立 `translate`，连线要动就用 `.draw` 或挂父层。
  现量写法：新建档先落一个够用的占位框，`getBBox()` 量出字宽再写终值；**占位框只给
  新建档写**（`if (!REG[boxKey])`），复用节点再写一遍会被当成真实状态变化，画面先
  补向占位值再掉头，是肉眼可见的抖动。
- `[data-dim="1"]`：焦点弱化。`@property --dimmer` 注册成数字驱动
  `filter:saturate()/brightness()`；**只碰 filter，不碰 opacity**（opacity 会盖掉
  renderer 写在呈现属性上的逐句档位）。弱化档同时写 `opacity:.45` 作兜底：退到背景
  的东西仍要能被看见当参照。
- `#cam`：推拉摇移，窗由 `CAM` 声明。**位移定拍**：JS 只写 `--cam-pan` /
  `--cam-zoom` 两个读数，`--cam-dur` 由 `clamp()` 现求（地板 `.5s` == `--dur-spot`，
  灯永远不落在镜头后面；封顶 `.78s` < `--dur-ride .8s`）。两个读数**故意不注册**
  （注册了就去补间"行程"，时长自己也在爬）；**读数写在 `transform` 之前**（反过来
  会夹一次强制布局，补间拿到上一趟的时长）；擦洗与截图模式一律写 `0/0`。
- `#depth`：远景视差。与 `#cam` **同级兄弟**、共用世界坐标，镜头每落一次按
  `--par-far` **同向**漂一小段、**不放大**。位移取 `camTo()` 回传的 `tx/ty`，不要在
  别处重算钳位公式；系数从令牌读（改名时 `parseFloat` 得 NaN，落回默认档）。位移挂
  父层 `transform`，不改子节点 `getBBox()`。
- `.el.swF` / `.swB`：方向感换场——换节时新画面从行进方向一侧进场。判据是**换节**
  不是换句（`lastSceneIdx` 比对）；冷启动第一步、擦洗、截图模式不演。位移写独立
  `translate`。退场那一幕要在 `only()` 里把 class 摘干净，否则下次入镜重放旧动画
  （导出每帧冷启动，那一帧就停在半推半进上）。
- `.blurpass`：运镜运动模糊。0%/100% 都是 `blur(0)`，只在中段糊一下；只在真的挪了
  一次镜头时触发（位移 > 180 世界单位，或倍率相对变化 > 22%）；不达标的这一步必须
  主动 `remove`，否则下一步小移动会再跑一遍。

**（3）揭示与退场**

- `.reveal` / `.revealL`：擦除揭示，`clip-path:inset()` 从一侧扫出；方向带语义
  （左→右"往后推演"，右→左"回溯"）。边界：
  - 只裁不位移，`getBBox()` 全程恒定，但**命中跟着裁**；
  - 挂在**包装 `<g>`** 上，不与 `.nt` 的 transform 同节点；
  - **两端都写成 `inset()`**（`inset()` 与 `none` 不可插值，只写 `from` 会按
    discrete 两格硬跳）；
  - 用 `backwards`，不写 `forwards`。
- `.featherR` / `.featherL`：羽化揭示，mask 渐变带掠出，软边。用于结论/记法这类
  不打扰观众的内容。边界：
  - `@property --feather` 初值取终值 134%（不透明段占到 100%），于是不支持
    `@property`、不支持 mask、导出切掉 animation 三种"没跑"的情形都落在**完整可见**；
  - **mask 不裁命中**，所以这一层必须 `pointer-events:none`；需要 Learner 点的图元
    一律用 `.reveal`；
  - 挂包装 `<g>`、不进 `.nt` 清单；两端写百分比 + `backwards`。
- `.tuckR` / `.tuckL`：收拢退场，沿行进方向把那一行收走，`.reveal` 的反函数。边界：
  - 走 **transition**：对象必然在前一步已被写过，复用节点就地改 `clip-path` 天然有
    完整行程；冷启动第一步直接落终态，导出切掉 transition 落"那一秒的真实档位"；
  - 两端都写**四长度 `inset()`**；静止档 −15%（外扩）、收拢档 115%（`inset(0)` 会
    啃掉描边与箭头，85% 会留能点着的残角）；
  - **消费者要"多停一拍"**（`step >= 1 && step <= 2`）：新建档没有上一状态；
  - 只挂**纯包装 `<g>`**，除 `gone` 外不写任何 `.nt` 会补间的属性——同节点两条
    transition 简写会互相顶掉，计算值只剩 `clip-path`；
  - **需要 Learner 点的图元不许收拢**。
- `.bloom`：圆形绽开，`clip-path:circle()` 关键帧从 0% 到 75%。`circle()` 的百分比
  按 `sqrt(w²+h²)/√2` 归一，覆盖整盒的下限恒为 √2/2 ≈ 70.7%，所以终态取 75%（50%
  咬边、100% 白给）。关键帧两端同型、静止态写在元素自己身上、不写 `forwards`。clip
  会裁命中：**要点的图元不要开 `.bloom`**。

**（4）关系、注意力与标注**

- `#cap-hl`：结论句划重点。几何从 `#cap-text` 现量（`capShow()` → `capHl()`），
  色带写在 `#cap-text` **之前**、字压在上面；`x`/`width` 同时写进呈现属性作兜底；
  **非结论句一律写 0 宽**（否则上一句的色带赖在下一句下面）。它是**状态**，减动效只
  关 transition、终态照留。
- `#spot`：落点灯。`#cam` 最后一个孩子，一层 `mask-image:radial-gradient()` 暗幕，
  `--spot-x/y/r` 注册成 `<length>` 才能逐帧插值。
  - 洞心从 `camBox()` 现量：目标 `getBBox()` 沿 `transform.baseVal` 逐层乘到 `#cam`
    坐标系；**不用 `getScreenCTM()`**（补间半途取到上一帧矩阵，灯照歪）；
  - 半径取外接圆 `hypot(w,h)/2 + 26`；幕深按 §8 合成反推（封顶 `.15`，洞外正文仍需
    过 4.5:1；量的是**相对亮度跌幅**不是 WCAG 比值）；
  - 暗幕四边各向外多铺 `--spot-bleed`（≥ 取景框宽 / .85 / 2）：幕要盖住整个取景框，
    不是 `viewBox`。`rect` 坐标、令牌、`mask` 里的 `calc(+bleed)` 三处同改；
  - 整层全局、不随 `sweep()` 撤走，所以 `begin()` 每步 `spotOff()`；renderer 不声明
    就是没有聚光；
  - `pointer-events:none`；字幕带在相机之外，不进灯；
  - **主角在世界层的那一句别打灯**；全景步也不打。
- `.mark`（`markBehind()`）：荧光笔标记，色带按目标文本 `getBBox()` 现量（宽 +8、
  高 ×.55、起点 y+45%、左缘 x−4）。必须 `insertBefore` 到文字**之前**（SVG 没有
  z-index）；只有琥珀字配亮带（红字合成后跌破 4.5:1）；斜度走独立 `rotate`，终态写
  在元素自己身上；一次性标记，下一步 `sweep()` 收掉。
- `.strike`（`strikeThrough()`）：反证划线，append 在文字**之后**；斜率按
  `--strike-tilt` 算进端点（水平线的 fill-box 高为 0，rotate 没有稳定旋转盒）；文字
  同时压到 `#8d95a8`；`pathLength=1` + 自建 `strikeIn`，不挂 `.draw`；线宽
  `max(2, bbox高×.09)`。
- `.scope`（`scopeFrame()`）：对位镜，两端 tick + 中间读数 `i=k`，线 + tick 合成一条
  path 配 `.draw`。同一行换位只补组上 `transform`，换行/换对重建重画。只做注解：
  `pointer-events:none`、墨色 `--scope-ink`、读数写短记号。
- `.brace`（`braceUnder()`）：集合括注。并集从被括节点 `getBBox()` 沿 `transform`
  属性现推（不读 `getScreenCTM()`）；两臂各一条 path、同收一枚括尖（写成一条会读成
  划线）；标签晚 `--dur-draw` 的 .6 落；落点在内容下方、字幕带之上；`pointer-events:
  none`；只括"同组"，不括整列。
- `.flow`：沿线流光，`pathLength:1` + `dasharray` + `dashoffset`，必须**叠在一条
  看得见的 `.draw` 底线之上**、分两个节点（同节点会互相顶掉）。静止态与终态都
  "看不见"——掠光是**事件**不是状态。`pointer-events:none`（虚线空隙不命中）。
- `.crisp`：`vector-effect:non-scaling-stroke` 恒定屏幕线宽。**不继承**，逐图元挂；
  只给注记 / 参照线用（卡片边框、结论色带跟着镜头变粗是对的）。
- `.halo`：`paint-order:stroke` + 一圈舞台局部底色的描边，让标注字压在线上仍可读。
  光环色取舞台那圈的渐变底色（不是 `--bg`），宽度用 `em` 跟着字号走。包 `@supports`。

**（5）行进与换位**

- `.ride`：沿线抵达。`offset-path:var(--route)` + `offset-rotate:auto`，只补间
  `offset-distance` 到 `data-go="1"` 的 100%，`linear`。路线是**数据**，写在呈现
  属性。终态写在 `data-go` 上（绝不能由 animation 驱动，否则切掉动效落回路径起点）。
  `getBBox()` 不跟着走：只能当**视觉指引**，不当热区、不参与 §5 连线判定；挂在不会
  提前收拢的层上。
- `.wake`：`.ride` 的余像修饰层。同一条 `--route`、同一个 `data-go`，只加
  `transition-delay:calc(var(--trail-step) * var(--wake-i))` 与 `pointer-events:
  none`。余像是**兄弟节点、建在头之前**，只描圆环、透明度递减；到站完全收在头下；
  行程太短别挂；reduce 只归零 delay，别关 `data-go`。
- `.swap`：换位弧凸。直线位移照旧走 `.nt` 的 transform，另在包装层叠一条垂直抛物线
  `offset-path`，两端回原点。包装层在蓄力层之外、只挂 `.swap` 自己的 transition
  （computed 只剩 `offset-distance`）；`offset-rotate:0deg` 必须显式写（默认 auto
  会把卡转 90°）；峰值 = 行程 × `--swap-k`、封顶 `--swap-rise`、短于 `--swap-min`
  的平移直行；换位判据由 `(scene, step)` 就地推导且 **sticky**（"排好了 → 收上架子"
  不重放；灯灭着的 `off` 不立基准）。
- `.trail`：拖尾跟随。只写长档 `transition-delay`（不重述简写，否则 `.nt` 十七项
  清单被整条顶掉）；先后从 `--trail-step` 派生、rank 写在元素自己身上
  （`--trail-r`，故意不注册）；行程为 0 的卡不给档；同一步只许一个让拍来源
  （`.wind > g` 特异度更高，拖尾自动退让）。

**（6）判定与重量**

- `.settle`：挤压落位，独立 `scale` 关键帧。压扁与拉长由同一对令牌**镜像**（`0%`
  写 `calc(2 - var(--squash-x))`）；支点取 `fill-box` 底边中点（九档冻结读数的屏幕
  下沿必须同一个数）；只走独立 `scale`；只挂纯包装 `<g>` 且同节点不再挂第二条
  `animation`；曲线 `linear`；`backwards` 不写 `forwards`。**第一次落位才演**——复用
  同一 key 换位置是补间，不是又一次落地。
- `.wind`：预备后蓄。蓄力层在主角**之外**、**从建档那一步就在**（重新入树没有
  before-change style，那一次位移会当场落成终态）；走独立 `translate`；曲线
  `--ease-in-out`；两端与静止态都回零。主动作必须让出 `--wind-hold`
  （`--dur-wind × .34`），让拍走 `transition-delay` 长档写在**被补间的那一层**上
  （`.wind > g`）；reduced-motion 里让拍要**一起归零**。
- `.lift` + `--elev`：提牌投影。`@property{syntax:'<length>'; initial-value:0px}`
  （零档 = 还在台面上）；补间清单**只有 `--elev` 一项**，`translate` 与
  `drop-shadow` 都由它派生（影子比抬升走得远，系数 1.5 / 1.2）；档位写呈现属性
  `data-lift`（reduce 关补间不关档位）；只挂纯包装 `<g>`。**不许改用 `box-shadow`**
  （SVG 上计算值读得到而屏幕 0 个像素变）；投影不裁命中，别加 `pointer-events:none`。
- `.tilt`：倾侧定盘。只有离散三档（−/0/+，`--tilt-deg` 6.5°），终态写在
  `data-pan`；只补间独立 `rotate`、不写进 `transform`；`transform-box:fill-box` +
  `transform-origin:50% 50%`，且包装层包围盒中心必须正好是支点（往这层多塞一个
  下方图元就把支点顶跑）；`rotate` 不改 `getBBox()`，命中却跟着变换走——**要判定的
  东西别挂 `.tilt`，要动的东西别拿它当坐标**。

**（7）文字**

- `--lit`：跟读点亮。`@property` 注册成百分比、`inherits:false`，写在 `#cap-text`
  的 `mask-image` 停靠点上。
  - initial 取 **118%（100% + 带宽）**而不是 100%——`--lit` 是"停靠点"不是"完成度"，
    取 100% 时每句末尾 18% 永久留在地板上；
  - 用 mask 不用 clip-path：两者都不改几何，但 clip-path 会裁掉命中；
  - 它是**读数不是动效**：逐拍写 `style`、不挂 transition、**不进** reduced-motion
    清单；`capLitWrite()` 排在 `capProgWrite()` 之后（都在换句分支之后）；
  - 地板 `--lit-floor` 取 .85，按 §8 合成算对比度（非结论字 7.97:1 / 8.53:1，结论
    字 6.08:1 / 6.53:1）；改这一档必须重算。
- **逐字解码**（`decodeArm` / `decodeWrite`）：术语按句内进度从噪声里逐字定住。
  - **不许用随机数**：字面由 (目标字符, 下标, 句内进度) 现算，导出同一秒的两帧才
    一致；
  - **字宽不许漂**：建档时把终态宽度钉进 `textLength` + `lengthAdjust=
    spacingAndGlyphs`，量的必须是 `getComputedTextLength()`（推进量）而不是
    `getBBox().width`（墨盒）；
  - 噪声池按字符类分（CJK 换 CJK、数字换数字）；噪声若恰好算到真字自己，顺延一格；
  - 落定节奏 `round((1 − (1 − k)²) × 字数)`（缓出），前 `--decode-lead`（.72）走完；
  - reduce 与导出直接落完整的词（未定住的格本身就不可读）。
- `.gather`：字距归位。走 animation（"散→收"不是两个状态之间的行程）；关键帧两端
  同为 `<length>`（`.3em → 0`），不依赖 `normal` 的宽容；改的是排宽，档位按"最坏
  那句"选；只在**换节**那一拍重放；窄屏按**余量**关掉。

**（8）交互反馈**

- `.opt.rip`：触点涟漪。`::after` 一枚圆从触点涨满再淡掉，走 `pointerdown`。
  圆心与直径**在 handler 里现量**（`:active{scale}` 会把 rect 缩过）；base 与终态都
  不可见（事件不是状态）；`pointer-events:none` + 宿主 `overflow:hidden`；键盘路径
  不编圆心。
- `.hot` + `--hot-x/y` + `data-hot`：触点辉光，光跟着指针走。
  - 光心走注册属性不走 `left/top`（改后者每个 `pointermove` 都重排）；
  - 坐标注册成 `<length-percentage>` 且 **`inherits:true`**（消费者是宿主的伪元素）；
  - 位置补间 `linear`、`opacity` 配 `--ease-out`；静止态兜在 `50%`，`data-hot` 不
    存在；
  - **不用 `:hover` 驱动**（headless 与触屏的 hover 媒体查询均不成立）；
  - `pointer-events:none` + `border-radius:inherit`；键盘路径不点亮；放行后
    `[data-locked]` 按灭。
- **磁吸迎手**（`magMove` / `magClear`）：指针进到按钮盒边外 `--mag-r` 的场里，按钮
  朝指针挪最多 `--mag-pull`，位移按 `sin(πd/r)` 衰减。只写独立 `translate`；事件委托
  在 `#gate`（卡片每次开题重建）；场半径从**盒边**算并扣掉当前位移；只认鼠标/笔；
  `mqReduce` / 截图模式**一个字节都不写**；`focusin` / `pointerdown` / `pointerleave`
  即清位移。
- `.mag`：同一套磁吸给选项药丸。因为 `.opt` 已挂全属性 transition 简写，位移写在外层
  `span.mag` 上，只演 `translate`；答对后按钮禁用即不再迎接。
- `.gate-next .go.shine`：放行键掠光。伪元素 + `background-position`；静止态停在盒外
  （`120% 0`）；`pointer-events:none` + 宿主 `overflow:hidden`；只演"这一行从无到有"
  那一次，判据取 `hidden` 的**旧值**（`var nextShown = !gateNext.hidden;` 必须排在
  `gateNext.hidden = false;` 之前）。

**（9）播放器**

- **擦洗态升级**：6px 轨道 → 9px、刻度 12px → 16px。只长高、不加 margin；圆角跟着
  高度走，填充 `border-radius:inherit`；过渡清单只给 `height` / `border-radius`，
  不给 `width`（进度走 `--dur-fill`，混进来就是第二个时钟）；刻度用 `top:50%` +
  独立 `translate` 居中。
- `#cap-prog`：句内进度细线。只写呈现属性、不挂 transition、**不进 reduced-motion
  清单**；几何在 `capShow()` 时现量存进 `capGeo`，`tick()` 每帧只写 `width`；调用点
  排在换句分支之后；空档不清零（停在满格），`ended` / 开场前才归零。
- `.bar[data-band]` + `--band`：越界橡皮筋。`seek` 把**未夹的 raw** 交给
  `bandWrite`、只把**夹过的**写给 `currentTime`——给手感不给时间，时间轴一个字都
  不许多说。气泡按 `cap·x/(x+soft)` 亚线性溢出，松手删档位由 `--dur-band` +
  `--ease-spring` 弹回；`--band` 注册成 `<length>`、`inherits:false`。
- `.tip`：窥探气泡。`pointer` 事件驱动；出现时轻弹 7px 落定，`transform` 里每帧都要
  重复写 `translateX(-50%)`（动画期间的 transform 整条接管静态值）；重触发判据留在
  JS 差分里。
- 进度填充：`.bar-fill` 的 `width` 走 `--dur-fill linear`——播放与拖动时连续滑动，
  不每拍跳变。

**（10）氛围与排版**

- `data-mood` + `--tint`：色温定调。注册成 `<color>`、`inherits:true`；补间只写在
  `:root` 一处，两个消费者（`#depth .wash` 的 `fill` 与 `body::before` 光晕）都不写
  transition。补间空间是 sRGB 逐通道线性（Oklab 实测偏离 40+ 通道单位）；三档必须
  **近等亮度**并按 §8 合成后逐格重算对比度；`--dur-mood` 长于任何一次运镜；换档挂在
  **换节**分支；档表没登记的段落继承上一档（不回落 calm）；柔光矩形排在点阵**之后**。
- `.pregate`：引导浮层离散进出。`transition-property` 包含 `opacity` / `translate` /
  `display`，`transition-behavior:allow-discrete` **单独一行**，`@starting-style`
  给进场起点；`[hidden]` 那一边写终态；整块包 `@supports
  (transition-behavior:allow-discrete)`。**`hidden` 属性仍是唯一真值**——QA 的放行
  信号与导出注入的 `display:none!important` 都不动。`#gate` 本体不转。
- **折叠面板**：`interpolate-size:allow-keywords` 让 `height:auto` 参与插值；
  `display` 必须同进清单；`transition-behavior` 单独一行；`@starting-style` 给起点；
  兜底带 `li.note` 抬特异度（基础规则源序在后）。
- **侧栏滚动驱动入场**：`animation-timeline: view()`，整块包 `@supports`、隐藏态只
  写在 keyframe 的 `from`、`animation-duration:auto` 显式写、`animation-range` 落
  **entry 段内**（取 cover 段会让不溢出的侧栏停在半透明格）、位移挂独立 `translate`、
  兜底用 `ul.notes>li.note` 抬特异度。
- **排版留白**：`text-wrap:balance` 让多行标题每行等长；`text-box-trim:trim-both` +
  `text-box-edge:text` 收走行盒上下的 leading（`text-box-edge` 只认 `text`，其他值
  整条作废）。只管 HTML 侧文本，SVG `<text>` 与字幕带不受影响。
- `text-spacing-trim:trim-start`：收掉行首全角标点的半格。只挂会换行的 HTML 正文，
  整块包 `@supports`；字幕带、等宽 `code`、`.kv` 不挂（单行无收益，却要动派生视图
  共用的 bbox 读数）。量法用 `Range` 墨迹宽，块盒 `getBoundingClientRect().width`
  量不到变化。
- `--key-soft/softer/act`：主色浅档底色，认得 `color-mix` 的浏览器由 `--key` 现调。
  兜底写在 `@supports` 里，不靠"同名两条声明"（自定义属性解析期什么都收，坏的是
  替换之后那条 `background`）。

#### 3.4.4 导出与减动效

截图页注入 `transition:none!important;animation:none!important` 且每帧冷启动；
`prefers-reduced-motion` 关闭动效。两者落的是**同一族终态**，按机制分四类：

| 机制 | 代表原语 | 落点 |
|---|---|---|
| animation + `backwards` | 擦除 / 羽化揭示、`.gather`、`.settle` | 计算值回到元素自身值（全显 / 收好 / 静止盒） |
| animation 无 `forwards`，静止态即全开 | `.bloom`、`.ffwd`、`.opt.rip`、`.flow` | 切掉后落"看不见"或"全开"，不留半截 |
| transition | `.tuck*`、`.flip`、`.ride`、`.tilt`、`.lift`、`.swap`、`#spot`、`--cam-dur` | 落到 after-change style，即那一秒的真实档位 |
| 离散 / 静态 | `.pregate`、`.crisp`、`.halo`、`.stag`、`text-spacing-trim` | `hidden` 即时生效；静态口径本就不变 |

口径：

- **终态必须落在属性上**：`animation-fill-mode` 用 `backwards` 或不写；循环动画的
  `0%` 必须等于静止态；
- **只关补间，不关档位**：`data-pan` / `data-lift` / `data-go` / `--spot-*` /
  `--swap-route` 这些读数照旧写出；清单里不许出现它们；
- **过渡由状态驱动时天然安全**（`.tuck` / `.flip` / `.ride`），由 animation 驱动时
  要确认终态不依赖 `forwards`；
- **reduced-motion 兜底要真的盖过基础规则**：基础规则写在媒体块**之前**时同特异度
  后者胜，不用抬；写在**之后**（折叠、侧栏、掠光、涟漪、`.pregate`）必须抬特异度
  （`li.note` / `ul.notes>li.note` / `#gate-next` / `#gate-block` / `#pregate`）；
- 让拍（`transition-delay` / `animation-delay`）要在兜底里**单独归零**——全局
  `.01ms!important` 的时长钳制管不到 delay。

#### 3.4.5 常见静默失效

这些错误不会报错，只会"看着像没生效"：

- **CSS 简写互相顶掉**：同一节点两条 `transition` 简写（`.nt` 与 `.tuck*` /
  `.flip` / `.lift` / `.swap` / `.trail`）后写的那条把前一条清单整串清掉。原语只挂
  纯包装 `<g>`，或只写长档 `transition-delay`。
- **`animation` 简写引用未定义的自定义属性**：整条在计算值阶段作废、动画根本不跑。
  缓动/时长要么给字面量，要么保证令牌在 `:root`。
- **`animation-delay` 里的 `calc()` 缺空格**（`sibling-count()-1`）；**分母为 0**。
  都是整条作废，动画照跑、延迟全 0。
- **`@property` 注册名撞上现有色值令牌**（如 `--dim`）：全页 `color:var(--dim)` 在
  计算值阶段非法，整条作废。注册前全仓 grep 同名令牌。
- **不成对的注释**：漏开 `/*` 的收尾 `*/` 会把紧跟的那条声明吞进去，CSSOM 规则数
  不变，只有 `:root` 的解析结果少一项。改完 `<style>` 先扫注释配对，再断言令牌在
  解析后的 `:root` 里。
- **对已入树的节点重复 `appendChild` / `insertBefore`**：DOM 插入会先摘下再插回，
  浏览器**重放 CSS 动画**（划线每步重划、荧光笔同句划两次），而静态断言全绿。
  helper 只在"位置不对"时才搬节点。
- **`transform` 属性 + 独立 `scale` 同节点**：合成顺序 scale 先于 transform，位移
  会被缩放。居中用独立 `translate`，位移与缩放分层。
- **`getScreenCTM()` 量补间中的目标**：拿到的是上一帧矩阵。要"最终在哪"就读
  `transform.baseVal`。
- **拿 `el.style` 读补间中间值**：读到的是刚写进去的目标值。插值中的那一格只有
  `getComputedStyle` 读得到；`CSSTransition` 也没有 `.delay`，delay 在
  `getComputedTiming()` 上。
- **量 SVG 文字宽度**：`getBBox().width` 是墨盒，会随字面抖动；钉宽度、算容器宽都用
  `getComputedTextLength()`。

### 3.5 阈值

```js
if (step >= 3) showToken();
if (step === 4) flashOnce();
```

阈值直接写成常量，不用由句数推导出的魔法表达式。

## 4. 字幕区

### 4.1 DOM 与数据流

模板提供：

```html
<text id="cap-text" data-courseware-caption="1"></text>
<text id="cap-speaker"></text>   <!-- 可选：对话课的说话人标签 -->
<rect id="cap-prog" .../>        <!-- 可选：句内进度细线（派生视图） -->
```

每次 `sentAt(t)` 返回当前句时，把该句对象交给 `capShow()`。页面不维护第二份字幕表。

说话人标签是同一句数据的**派生视图**：`capShow()` 把 `s.speaker`（对话讲稿经
`build_timeline.py` 透传的 label，见 `script.md` §1.5）写进标签，没有 `speaker` 时
留空。它**不碰 `#cap-text`**——QA 比对的是字幕节点本身；标签文案只是称呼，不得复制
旁白正文。

### 4.2 空档与结束态

- **空档保留上一句**：换场空档（`scene` 为 `null`）与场景内两句之间的空档
  （`index < 0`）都不清空字幕带，停在上一句上。只有开场前（还没有上一句）与课程
  结束才收起。`check_gates.py` 在两处各放探针。
- **结束态回拖**：`ended` 收起整条带子后，把进度条拖回**最后那句之内**时句号没变、
  换句分支不进，必须靠"这一拍有活跃句子"补回来——`tick()` 里按
  `capSent !== hit.sentence` 补一次 `capShow`。画面与读数都回来了而字幕空着，就是
  状态不再是 `currentTime` 的纯函数。

### 4.3 派生视图

`#cap-hl`（结论色带）、`#cap-prog`（句内细线）、`#cap-speaker`（说话人）都不算第二
份文案：几何从 `#cap-text` 现量、内容由当句数据算出，QA 的比对节点始终是
`#cap-text`。

| 视图 | 几何 | 更新 |
|---|---|---|
| `#cap-hl` | `getBBox()` 现量 | 补间 `x` / `width`，同时写呈现属性 |
| `#cap-prog` | `capMeasure()` 一次存 `capGeo` | 每帧只写 `width`，不挂 transition |
| `--lit` | `mask-image` 停靠点 | 每帧写 `style`，不改几何 |
| `#cap-speaker` | 文本由数据算 | 与 `#cap-text` 同拍 |

跟读点亮是三者里唯一**只碰呈现**的一层：`textContent`、`getBBox()`、
`getClientRects()` 全部逐值不变。

一条**量出来的纠正**：clip-path 与 mask 对 rect / bbox 的影响其实相同（都不会让
`getClientRects().length` 变 0），差别只在**命中测试**——`clip-path` 会裁掉命中、
mask 不裁。所以 `captionShown()` 那套"祖先链样式 + client rects"的判据对 `--lit`
完全安全；反过来，需要 Learner 点的图元不能收在 clip-path 后面（见 `.tuck*`）。

## 5. 连线要真的连上（几何）

连线（实线 / 虚线 / 带箭头的都算）**两端必须落在它连接的两个图形的边界上**：不悬空
留缝，也不插进图形内部。

- 端点坐标**从图形算出来，不靠手感给**：矩形用 `x` / `x + width`，椭圆用
  `cx ± rx`；算完让两端各留 **2 单位呼吸缝**。
- **带 `marker-end` 时终点再缩一个箭头长度**，否则箭头会压进目标图形、或与它的边线
  叠在一起。缩多少由自己的 marker 决定：`refX` ÷ `markerUnits` 的换算结果，量级通常
  在 **8–10 单位**。
- **改了图形的坐标 / 尺寸后，回来把它所有的连线一起改**——连线是图形的派生数据。
- 导轨（时间轴主线、分支轨道）本来就自由延伸，不受这条约束；受约束的是**连接件**
  （把 A 指向 B 的那根线）。

## 6. 内容底线

字幕带有固定区域。主要图形留在分隔线以上，不与字幕重叠。真实尺寸由 `viewBox` 与
内容并集决定，不把模板某个数字当成通用魔法数字。

**每一步的画面重心都要落在画布横向中线上**（viewBox 宽的一半）。桌面全屏 / 宽视口
会把画布撑到最宽，偏心在这个尺寸下最扎眼；紧凑态（窄屏、横屏手机）反而看不出来
（`camSet()` 在窄屏按内容包围盒自动取景），所以只在桌面截图很容易漏。两种常见病因：

- 成排图元的行中心 ≠ 画布中心；
- 分步生长的流程图：不长的步态留右侧大空带。解法是按步态给整条管线配平移，长出的
  那一步再归位。

用相机配平更省事：窗的中心会被钉到可用区中心，所以**把窗写正，重心就正**（§3.3）。
改完构图仍要逐场景量一次 `getBBox()` 中心（或全屏截图目测）——导出是每帧冷启动，
**每一步都得单独站得住**。

## 7. emoji 装饰：默认装饰层

**默认给画面图元和侧栏配 emoji 锚点**，不等用户开口要。装饰仍服从纪律 2.4：图形优先，
emoji 只是记号的点睛。

- 每个概念记号**至多一个** emoji，贴着图元文字写（`📤 push`、`🧩 action`）；
- 选**语义贴合**的，不选热闹的：比喻要能帮记忆；
- 静态呈现：emoji 不驱动状态、不参与步进，renderer 里不许为它造计时；
- 三条禁区：**旁白 `text` / 字幕不放**（TTS 会念出来，且破坏字幕唯一来源）；**门禁
  题面与反馈不放**；**QA 钩子节点不放**。

## 8. 画面来源与对比度

### 8.1 先定呈现，再定来源

内联矢量绘制仍是默认形态。本节只回答一件事：这一段观众该看到什么、素材从哪里来。

| 内容特征 | 首选呈现 |
|---|---|
| 顺序 / 流程 / 关系 / 对比 / 状态（绝大多数讲解步） | 内联 SVG |
| 数值比较 / 占比 / 趋势 | 数据图表，以内联 SVG 绘制 |
| 真实人物、产品、地点、事件现场 | 真实照片（用环境任一可用的联网检索能力） |
| 外观 / 场景 / 氛围意象 | 图片生成能力 |

两条边界判据：**抽象且不承载严谨数据 → 意象图；抽象但逻辑严谨 → 内联示意 / 图表**。
有明确真实实体的内容不要用生成图替代事实照片。

"联网检索""生图"都是**能力泛称**：用当前环境里任一可调用的执行；一个都没有时直接
退回内联 SVG 绘制——它是 always-on 的兜底形态。

### 8.2 生图与降级链

选生图时：prompt 先描述"讲什么"（主体 / 场景 + 核心动作或结构）再描述"怎么画"
（风格、背景明暗），构图优先接近画布横向比例、主体居中；同一课的多张图锁同一套背景
明暗与风格词；**图内不放文字**——可读信息由图元标注和字幕承担。

素材只承担**增量信息**：旁白和图元标注已经把话说清时，不要为填满画布配图，也不要为
"高级感"堆 3D、仪表盘、拼贴这类不帮助理解的装饰。

降级链：照片检索失败 → 意象生图 → 简化内联示意，逐级**降低视觉复杂度**，绝不为了
补画面编造信息。

### 8.3 素材纪律

- 外部素材落进课件目录并记录来源；真实照片要打开原图核对，不看缩略图选图；长边低于
  约 1200px 的图进入成片容易发虚，宁降复杂度不硬凑。
- **来源只进文件，不进画面**。出处、生成方式、工具名写进项目目录里的记录文件（如
  `assets/provenance.json`）；给图配注脚只配**内容性**的。
- 不以 AI 生图冒充现场照片；同一张泛化图不反复盖到多个不相关场景。
- 数据红线：不为"好看"改数字，估算值必须标明，不用截断坐标轴、面积失真对比等误导
  比例，不省略单位，不编造数据点。

### 8.4 图内文字：对比度三查

判据（WCAG）：正文与标签 ≥ 4.5:1，图内大标题 ≥ 3:1，低于线重画。图内小字还要经
响应式缩放与视频压缩，**次要文字按 ≥ 7:1 取色**。

三条最常踩的：

- **深色文字只能待在明确的浅色底板上**。改任何底板颜色，同时复核压在它上面的全部
  文字；`fill` 能就近写死就别靠 `<g>` 继承。
- **主题背景色族不得当文字色**。判据是"这个色值属不属于背景族"，不是"看着有没有
  对比"。
- **半透明按合成后的颜色算对比度**。要弱化文字就换一个更暗（或更亮）的实色，不要用
  透明度表达层级。

外部 SVG 素材若以 `<img>` 载入是独立文档，页面字体变量渗不进去，其内部 `<style>`
要自带中文无衬线字体栈；且它内部的文字对 `check_gates.py` 完全不透明——这正是不作
特殊要求时画面留在内联渲染的理由。

## 9. 自检

**基础纪律**

- [ ] 一个句子对应一个主要视觉步；
- [ ] renderer 不计时（无 `setTimeout` / `setInterval`；`requestAnimationFrame` 已人工确认）；
- [ ] renderer 是 (scene, step) 的**纯函数**：没有"记住上一步演到哪儿"的可变状态；
- [ ] 常驻图元在这一步都被写过；要淡出的先写 `opacity:0` 再收尾；
- [ ] 跨幕的主角放在世界层、姿势逐句对账，各幕没有重画它；
- [ ] 没有第二份字幕正文；图元文字是必要的短标注；
- [ ] 每个视觉步先过了 §8 的来源判断：画的是增量信息，不是为填画布凑的图。

**舞台与相机**

- [ ] `CAM` 每一窗都框住了这一步该看的东西，内容没有沉到字幕带以下；
- [ ] 每一步重心落在画布横向中线上（逐场景量过 `getBBox()` 中心或全屏截图目测）；
- [ ] 视差层是 `#cam` 的**同级兄弟**、位移取 `camTo()` 回传的 `tx/ty`、系数只在
      `--par-far` 一处；
- [ ] 方向感换场判据是**换节**不是换句，擦洗与截图模式不演，退场图层把 class 摘干净；
- [ ] `--cam-dur` 的权重与两端只在 `:root`；`--cam-pan/zoom` 未注册；读数写在
      `transform` 之前；擦洗与截图模式写 `0/0`；"跑动中时长 == 计算值"的断言逐趟
      等落定。

**字幕**

- [ ] 空档保留上一句，只有开场前与结束才收起；
- [ ] 结束态回拖进同一句时靠 `capSent !== hit.sentence` 补 `capShow`；
- [ ] `#cap-hl` 几何从 `#cap-text` 现量、非结论句归零、色带在文字之前；
- [ ] `#cap-prog` 只写呈现属性、不挂 transition、不进 reduced-motion、调用点在换句
      分支之后、空档不清零；
- [ ] `--lit` 初值 118%、`inherits:false`、逐拍写、`capLitWrite()` 在 `capProgWrite()`
      之后、地板档改动后重算对比度。

**原语逐条**

- [ ] `.reveal`：两端 `inset()`、挂包装 `<g>`、只裁不位移；
- [ ] `.feather*`：初值 134%、`pointer-events:none`、挂包装 `<g>`；
- [ ] `.tuck*`：纯包装 `<g>`、四长度 `inset()`、-15%/115%、消费者多停一拍、可点图元
      不收拢；
- [ ] `.bloom`：终态 ≥71%（范本 75%）、两端 `circle()`、不写 `forwards`、可点图元
      不开；
- [ ] `.flip`：对称 `--ease-in-out`、两面 `visibility 0s linear calc(--dur-flip / 2)`、
      终态只写 `data-over`、包装层不写别的状态；
- [ ] `.ride`：`--route` 在呈现属性、终态 `data-go`、`linear`、只当视觉指引、挂在不
      提前收拢的层；
- [ ] `.wake`：与头共用 class / route / `data-go`；余像建在头之前、只描圆环、外沿 < 头
      半径；`--wake-i` 不注册；行程太短不挂；reduce 只归零 delay；
- [ ] `.swap`：包装层在蓄力层之外、只挂自己的 transition、`--swap-route` 在呈现属性、
      `offset-rotate:0deg`、峰值现算、判据 sticky；
- [ ] `.trail`：只写长档 delay、rank 在节点上、行程为 0 不给档、同一步只有一个让拍
      来源；
- [ ] `.settle`：镜像令牌、支点底边中点、独立 `scale`、纯包装 `<g>`、`linear`、
      `backwards`、第一次落位才演；
- [ ] `.wind`：蓄力层在主角之外且建档即在、独立 `translate`、`--ease-in-out`、让出
      `--wind-hold`、让拍走长档且 reduce 归零；
- [ ] `.lift`：`initial-value:0px`、清单只有 `--elev`、档位在 `data-lift`、纯包装
      `<g>`、不用 `box-shadow`、不加 `pointer-events:none`；
- [ ] `.tilt`：三档、终态 `data-pan`、独立 `rotate`、支点 = 包围盒中心、要判定的图元
      不挂；
- [ ] `.opt.rip`：handler 里现量、独立 `translate` 居中、`pointer-events:none`、base
      与终态不可见、只挂 pointerdown、兜底带宿主前缀；
- [ ] `.gather`：两端 `<length>`、只在换节重放、按最坏句选档、窄屏按余量关；
- [ ] `#spot`：洞心从 `camBox()`、`<length>` 注册且只写 viewBox 单位、外接圆半径、
      幕深合成反推、bleed 三处同改、`begin()` 归零、主角句不打灯；
- [ ] `#ffwd`：帧间差过 `--ff-min`、`linear` 不写 `forwards`、方向与箭头同号、四道
      不演；
- [ ] `.pregate`：`display` 进清单、`transition-behavior` 单独一行、`@supports`、
      `hidden` 仍是唯一真值、兜底带 `#pregate`；
- [ ] `.mark` / `.strike`：几何现推、前者在文字前后者在文字后、斜度/斜率从令牌读、
      不重复 `appendChild`、红字不配荧光带；
- [ ] `.scope` / `.brace`：并集沿 `transform` 属性现推、单条 path 收在同一括尖、
      标签是短记号、`pointer-events:none`、不压字幕带；
- [ ] `.hot`：坐标 `inherits:true`、位置 `linear`、静止态 `50%`、不用 `:hover` 驱动、
      键盘不点亮；
- [ ] 磁吸：只写独立 `translate`、委托在 `#gate`、场半径扣位移、触屏 / reduce / 截图
      模式不启动、键盘路径清位；
- [ ] `data-mood`：`<color>` 注册、两消费者不写 transition、三档近等亮度、换档在换节、
      未登记段落继承上一档。

**导出与 QA**

- [ ] 动效终态落在属性上（`fill-mode:backwards`、循环动画 `0%` = 静止态），单独一帧
      也讲得清；
- [ ] 读了补间中间值的断言用 computed，不读 `el.style`；先后断言把补间 `pause()` 后
      钉格采样；
- [ ] 位置类断言同会话内相对化，不写死屏幕绝对坐标；SVG-as-image 字形取样钉
      `textLength` + `lengthAdjust="spacing"`；
- [ ] 量文字宽度分清推进量（`getComputedTextLength()`）与墨盒（`getBBox().width`）；
- [ ] 手感类补间（橡皮筋、拖尾、解码）不改证据读数：`currentTime` / 字幕字面 / 门禁
      判定按夹过与落定之后的值走；
- [ ] 关键图形没有压住字幕；
- [ ] 每条**连接件**两端都落在图形边界上（坐标算出来的，不是目测）；带箭头时终点缩了
      箭头长度；
- [ ] `check_gates.py` 没有报高相似度双字幕提示。

**内容与素材**

- [ ] 画面图元 / 侧栏默认配了贴切的 emoji 锚点（旁白、字幕、门禁题面不放）；
- [ ] 用了外部素材的：按降级链降复杂度而非编造，来源已记录且只记在文件里；
- [ ] 图内文字过了 §8.4 对比度三查（底色族不当字色、深字有实底、弱化用实色）。
