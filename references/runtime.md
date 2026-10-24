# 运行时契约

> `scripts/interactive_runtime.js` 是**交互内核**，不是播放器。

## 1. 它负责什么

它只负责：

- 给交互元素接事件；
- 处理 pointer / tap / drag；
- 根据 `data-interaction` 判断答案；
- 正确时写 `data-locked="1"`；
- 反复接线时保持幂等。

它不负责：

- `audio.currentTime`；
- 时间轴解析；
- 场景切换；
- 字幕；
- 进度条；
- 掌握度 / 学习记录。

## 2. 五种交互

拖拽手势有两处内建兜底：窗口失焦（Alt-Tab / 系统弹窗）时 `up` 永不到达，按取消
处理，ghost 与监听器当场清掉；触屏上只跟随按下那根指针，第二根手指的 move/up
不劫持本次手势。

### `choice`

```html
<div data-interaction data-interaction-type="choice">
  <button data-choice-id="a">选项 A</button>
  <button data-choice-id="b">选项 B</button>
  <div class="interaction-feedback" hidden></div>
</div>
```

配置：

```json
{
  "options": [
    {"id":"a","correct":false,"feedback":"再想一步。"},
    {"id":"b","correct":true,"feedback":"对。"}
  ]
}
```

选项**文案在 DOM 按钮上**（上面那段 HTML），配置只负责判定与反馈；两边靠 `id`
对齐，对不上是 §5 的契约错误。

作答后选中的选项带 `data-selected="1"`（其余为 `"0"`），页面可样式化；它挂在选项上，
选项随卡片重建一并消失，不在 §3 的重开清洗清单里。

### `hotspot`

可点击元素带 `data-hotspot-id="node-1"`。配置与 `choice` 相同，也是 `options[]`。
可点区不限于 `<button>`——runtime 会给缺可聚焦语义的节点补 `tabindex="0"` 与
`role="button"`，Enter/空格与点击同效。

### `sequence`

```html
<ul class="sequence-list">
  <li class="sequence-item" data-sequence-id="a">…</li>
</ul>
<button data-sequence-submit>提交顺序</button>
```

配置：

```json
{"correct_order":["a","b","c"]}
```

重排有三种通路：**点选**（触屏主路径：点条目 A 选中，再点条目 B 把 A 挪到 B 的位置，
再点 A 一次取消选中；选中态同步在 `aria-pressed` 上，视觉与 bucket 选中同款）、
**拖拽**（鼠标按住位移超过 3px；拖拽期间 runtime 给跟手副本加 `.drag-ghost` 类、给
原位条目加 `.drag-src` 类，页面 CSS 靠这两个类出样式）与**键盘 ↑/↓**（聚焦条目后直接
换位，提交键照常点选）。Enter/空格等价于点选那一下。重排后 runtime 会按当前顺序重写
`.sequence-item .s-idx` 的文本（位次号跟位置不跟条目）——页面用这个类名放序号即可，
换名会静默失去位次更新。`.sequence-item` 与 `.bucket-item` 的 `touch-action: pan-y`
是**页面 CSS 的义务**（runtime 不设样式；理由见 `template.html` 拖拽态的 CSS 注释）
——触屏用户靠点选作答，不靠拖。

### `bucket`

```html
<li class="bucket-item" data-bucket-item="a">…</li>
<div data-drop data-bucket-id="left"><ul data-drop-slot></ul></div>
<button data-bucket-submit>提交归类</button>
```

配置：

```json
{
  "answer":{"a":"left","b":"right"},
  "feedback":{"a":"解释为什么放错。"}
}
```

既支持拖拽，也支持"点条目 → 点筐"（触屏主路径同样是这条：`touch-action:pan-y` 下
手指竖拖会先滚页面；条目与筐都可键盘聚焦，Enter/空格等价于点击；
选中态同步在 `aria-pressed` 上）。焦点在**条目**上按 Enter 是"选中/取消选中"，
焦点在**筐**上按 Enter 才是"把选中的条目放进这个筐"——runtime 会把条目上冒泡到
筐的那一下挡掉，纯键盘可以完整作答。筐拿的是 `role="group"` 而不是 `button`：
容器若被标成 button，读屏会把筐里的条目压成不可播报的子项。

### `recall`（复述 / 对照，唯一不判定的题型）

```html
<div data-interaction data-interaction-type="recall">
  <p class="q">先自己复述：……</p>
  <div class="recall-answer" hidden></div>
  <button data-recall-reveal>看参考答案</button>
  <div class="interaction-feedback" hidden></div>
</div>
```

配置：

```json
{"prompt":"题面","answer":"参考答案","hit_text":"可选放行话术"}
```

点「看参考答案」→ 答案填进 `.recall-answer` 并解除 `hidden` → 立即
`finish(correct:true)` 放行。**没有判错分支**：口头复述没有可靠的机器判定，硬做
判定只会把门禁退化成摆设。`data-locked` 在 recall 处的语义比判定题型更弱一档
（口径见 interactions.md §1）。
契约校验只保证展示通路完整（`prompt`/`answer` 非空 + 两个 DOM 钩子都在），
空壳 recall 会在配置校验处 fail-closed。

### 可选提示与徽标钩子（不写就没有，runtime 不造节点）

| 钩子 | 行为 |
|---|---|
| `[data-hint-action]` + `.interaction-hint` | 点击前者切换后者的 `hidden`（题面自带的"看提示"按钮） |
| `.interaction-badge` | 答对时 runtime 把它从 `hidden` 放回来（"已通过"角标） |

## 3. 放行契约

正确答案在 runtime 内部最终走：

```js
finish(el, msg, {correct:true});
```

`finish()` 是 runtime 私有函数——页面**不要自己调用它**，也不该复制一份；
页面与 runtime 之间唯一的契约是属性：

```js
el.dataset.locked = '1';
```

页面应使用 `MutationObserver` 或等价的事件式观察来放行，**不要轮询 `data-locked`**。

错误答案：显示反馈、保持未锁定、继续按钮仍不可用。反馈节点由 runtime 补
`aria-live="polite"` / `role="status"`，屏幕阅读器会自动播报，页面不必另做。

### 答对后 runtime 留下的状态（重开门禁前页面要清）

`finish(correct:true)` 除了写 `data-locked`，还会：

- 写 `data-completed="1"` 并给交互块加 `.is-completed` 类；
- **禁用块内所有 `<button>`**（含常驻的提交键）；
- 把块内的 `.interaction-badge`（若页面放了）从 `hidden` 放回来；
- 写反馈节点 `.interaction-feedback`：`textContent`、解除 `hidden`、换
  `.is-correct` / `.is-wrong` 类——**错答也走这条路**（并写 `data-completed="0"`），
  所以这是重开时最容易残留的一条：上一轮的"再想一步"会挂在题面下方；
- hotspot：命中的可点区写 `data-hotspot-state="hit"`（点错的区写 `"miss"`，同一处
  不再重复反馈）；bucket：全部条目写 `data-bucket-state="hit"`、选中态残留在 `data-picked` / `aria-pressed`；recall：`.recall-answer` 被解除 `hidden` 并填入参考答案（重开时页面自己藏回去，runtime 不管回收）。

同一张卡复用时，页面在**重新打开**这道题前要把这些洗掉（清 dataset、
去 `.is-completed` 类、藏回 badge（若放了）、**收反馈区**（文本清空 + `hidden` +
去对错类）、`b.disabled=false` 放回来）——
`references/template.html` 的 `clearGateCards()` /
`openGate()` 是参考实现（范本没有 badge，"藏回 badge"一步要自己补）。
**不要**清 `data-bound` / `data-gesture`：它们是常驻
按钮的防重复监听标记，清了重接线会叠监听（"点一下触发两次"）。条目/选项节点
若是重建的，本来就没有这些标记，无需处理。

## 4. 动态交互

页面动态生成交互块后调用：

```js
window.coursewareStudioWire();
```

入口幂等：runtime 用节点级标记保证重复接线不叠加监听、不重复绑定——`data-bound`
是**已绑事件类型的逗号列表**（如 `click,keydown`，同一节点可分别按事件类型各绑一次），
`data-gesture` 标记已接手势的拖拽条目。脚本放在 `<head>` 时首接线会顺延到
`DOMContentLoaded` 再执行（此时再调用入口也无害：绑定层自身幂等）。
动态 gate shell 初始可以只有空的 `data-interaction` 占位；真正配置写入后，
runtime 会严格校验题型契约（校验失败会先把其余块接完，再统一抛错给 QA 捕获；
`data-interaction` 不是合法 JSON 时抛的是"不是合法 JSON"，与结构违规的报错可区分）。

## 5. 配置契约

`choice` / `hotspot`：配置与 DOM 的 id 集合必须完全一致，且恰好一个 `correct:true`。

`sequence`：`correct_order` 必须存在、id 唯一，并完整覆盖全部 `.sequence-item`；
条目必须全部放在单个 `.sequence-list` 容器内——校验与作答都以它为准，散在容器外
的条目会被当场抛错（否则"过校验却永远判不出"）。

`bucket`：`answer` 必须完整覆盖全部 `.bucket-item`，并且值只能引用现有 `data-bucket-id`。

`recall`：`prompt` 与 `answer` 都必须是非空字符串（注意与 bucket 的 `answer` 对象
不同形——recall 的是展示通路，不是判定表），且块内必须已有 `[data-recall-reveal]`
与 `.recall-answer` 两个钩子节点。

别名同样被接受：`options` 可写作 `choices`（choice）或 `spots`（hotspot），`sequence`
的顺序数组也可用 `answer` 键下发——校验与报错口径不变。

契约不满足时 runtime 直接抛错，让浏览器 QA 捕获，而不是静默把错误题目当成“永远答不对”。

### 可选文案键（runtime 读取，不写就用自己的默认话术）

| 键 | 题型 | 作用 |
|---|---|---|
| `options[].feedback` | choice / hotspot | 该选项被选/被点时替换默认反馈 |
| `wrong_text` | sequence | 顺序不对时的反馈 |
| `hit_text` | sequence / bucket / recall | 答对（recall：完成对照）时的反馈 |
| `unplaced_text` | bucket | 还有条目没进筐时的反馈 |
| `feedback`（对象） | bucket | `{条目id: 解释}`，放错时报对应条目的话 |

## 6. 接入原则

把 `scripts/interactive_runtime.js` 复制到课件输出目录，与 `index.html` 同级。`references/` 不放副本。

页面自己拥有：

```js
RENDER
sentAt
syncGate
tick
```

runtime 不应重新实现其中任何一层。这是主循环四件套；门禁控制器
（`openGate` / `closeGate` / `clearGateCards`）与字幕出口 `capShow` 同样归页面
所有（控制器要洗的重开状态见 §3，页面要留的钩子见 §7，字幕显示契约见
stage.md §4）。

## 7. QA 契约（`check_gates.py` 浏览器冒烟依赖）

探针不驱动真实播放，而是桩掉 `currentTime` 后 dispatch `timeupdate`，因此页面必须给 QA 留下这些稳定的观察点：

| 钩子 | 用途 |
|---|---|
| `#main-audio` | 唯一时钟源；`timeupdate` 驱动 `tick()` |
| `#lesson-timeline`（`<script type="application/json">`） | 逐句字幕与场景区间的比对基准 |
| `#cap-text[data-courseware-caption]` | 字幕正文节点；QA 读它的 `textContent` 与祖先链可见性 |
| `#gate` | 门禁浮层容器（`hidden` 属性 = 开关） |
| `#gate-host`（`dataset.stepId`） | 当前门禁归属的场景 id，QA 用它对上时间轴 |
| `#gate-next` | 答对后出现的「继续」**行容器**（`hidden` 收起整行，按钮在它内部） |
| `#gate-go` | 容器里的「继续」按钮，QA 用它关闭门禁 |
| `#pregate` | preGate 过渡层；QA 视「gate 或 pregate 任一未 hidden」为门禁活动中，据此等待过渡结束 |
| `#stage`（`data-state`: `idle`/`playing`/`paused`，页面写） | 舞台显隐状态源：QA 探针把它写成 `playing`、导出截图脚本写 `paused`，绕开模板在 `idle` 下隐藏画面的 CSS；id 存在是**静态检查与导出**共同的硬前提（`--no-browser` 与导出都缺了直接报错），但成片尺寸取自舞台 SVG 的 `viewBox`，不测量 `#stage` |
| `window.__coursewareRenderTrace` | 页面每次真正调用 renderer 后追加 `sceneId#index`；浏览器 QA 清空后逐句检查它，防止音频/字幕在走而画面没有响应。它不是时钟，也不替代 `RENDER` 静态映射 |
| `window.__coursewareResetRenderTrace()` | QA 逐句复测前调用；必须同时清空 `__coursewareRenderTrace` 与页面内部 renderer 去重状态，避免单句课件误报 |

**导出附加依赖（只有 `export_video.py` 用到）**：截图页注入
`window.__coursewareShotMode` 旗标（写到 `<head>` 最前）。页面契约只有一条——
`syncGate()` 见旗必须直接 return：不占 `gOpen`、不回退冻结音频，否则浮层虽被
（导出脚本注入的 CSS）藏掉、时间轴却被门禁劫持；另有一条取景义务：`setCompact()`
见旗一律按桌面渲染（小屏放大不进视频，成片与桌面页面同一张脸）。0.14.0 起再加
一条同类义务：**方向感换场见旗不演**（`tick()` 的换节分支里 `sceneSlide()` 与
运镜模糊同样被 `__coursewareShotMode` 挡掉）——导出每帧只跑一步的 renderer，
"从哪一侧进场"没有上一帧可比，硬演只会让那一帧停在半推半进的位移上。远景视差
层**照常落位**（它是 `camSet()` 写的属性终态，不是动画），划重点色带**照常完整**
（几何同时写进呈现属性，切掉 transition 就是定格）。0.15.0 新增的三条原语里，擦除
揭示**照常完整**（`clip-path` 只挂在 `animation` 上且 `fill-mode` 是 `backwards`，
注入的 `animation:none` 让计算值直接回到 `none`，不需要额外的见旗分支），几何补间
**照常落终态**（renderer 把现量结果写进呈现属性，切掉 transition 就是定格），折叠
面板**不需要见旗处理**——侧栏整列在截图模式被 `.col-side` 藏掉，且展开态由用户点击
写 `data-open`，冷启动那一帧永远只有 `n.open` 给的那条，导不出"演到一半"的中间态。
0.16.0 三条里，擦洗态变厚与侧栏滚动入场**同样不需要见旗处理**（前者长在 `.rack` 的
进度条上、播放器行在截图模式被 `.rack` 藏掉；后者在 `.col-side` 里，且隐藏态只在
keyframe 的 `from`，注入 `animation:none` 后直接落完整在场）；句内进度细线**照常落
那一秒的读数**——它逐拍写呈现属性、不挂 transition，切掉动效等于什么都没切。它另外
欠导出一条更硬的义务：**`capProgWrite()` 必须排在换句分支之后**。导出每帧只跑一次
`tick()`，而那一帧往往正好是换句帧；写在前面时 `capShow()` 还没写当句的
`capSent`/`capGeo`，截出来就是一句"字在、细线空着"的画面（实测如此）。同类问题还
有一条：`ended` 收起字幕带后回拖进**同一句**，句号没变、换句分支不进，`tick()` 要按
`capSent !== hit.sentence` 补一次 `capShow`——状态必须是 `currentTime` 的纯函数，
直播里表现为"拖回去字幕不见了"，导出里表现为那一帧缺字幕。
0.17.0 两条新动画**都不需要见旗分支**，理由各自成立：羽化揭示**照常完整**（驱动变量
`--feather` 的注册终值 134% 就是"全显"，注入的 `animation:none` 让计算值落回元素自身
值，与擦除揭示同一口径）；掠光**照常不可见**（静止态 `background-position:120% 0` 把
高光带停在盒外，而门禁浮层在截图模式本就被 CSS 压掉、`syncGate()` 见旗直接 return，
`gateNext` 的 hidden→visible 那条分支根本不会走）。掠光有一条**直播侧**的时序值得记住：
引导浮层（preGate）那 1.1s 里 `#gate` 整层还是 `display:none`，放行行摘掉 hidden 也还没有
盒，伪元素动画要到元素**第一次有盒**才登记进 `getAnimations()`——QA 用例要按"浮层揭开"
取样，抢先读会拿到假阴性。
0.18.0 四条里只有 `.flow` 会真的动，而它**不需要见旗分支**：`stroke-dashoffset` 的静止态
与补间终点是同一个口径（`--flow-seg` / `-1`，两端光段都在路外），注入的 `animation:none`
让计算值落回元素自身的 `--flow-seg`，截出来就是"一条普通的完整连线"（实测像素扫描零光段、
offset `0.16px`）——掠光是事件不是状态，导出天然拿不到中间帧。`.crisp` / `.halo` 分别是
线宽与描边顺序的**静态口径**、`.stag` 只写 `animation-delay`，三者都不是动画，切掉
animation 之后什么都不变；门禁卡片挂 `.stag` 后 `cardIn` 仍在既有兜底清单里，导出侧无需
另处理。**QA 取样口径**新增一条要注意的：`.flow` 在冷启动那一帧"看不见"是**正确定格**，
浏览器用例不能把"这一层零像素"当失败信号——要验它演过，必须像 0.17.0 掠光那样按"该句已进入、
动画已登记"取样（`getAnimations()` 冻结 + `currentTime` 设到中段），或在直播侧读补间值。
0.19.0 两条里 `.tuckR/.tuckL` 会真的动，而它同样**不需要见旗分支**，理由与上面几条都不同：
收拢走的是 **transition**，截图页那一刀（`transition:none!important`）让计算值直接落到
after-change style，也就是那一秒的**真实档位**——实测注入后 s1→s2 立刻读到
`inset(-15% -15% -15% 115%)` 且 `getAnimations()` 为空，既不停在"半收"也不回到"全展开"。
所以导出模型对它的要求只有一条：消费者得按"多停一拍"写（`step >= 1 && step <= 2`），
否则 s2 那一帧压根没有这个节点可落档。`--lit` 与 `#cap-prog` 一样是**逐拍读数**，切掉
transition/animation 不影响它——它不靠补间，靠 `tick()` 里那一次写；`capLitWrite()`
排在 `capProgWrite()` 之后同一条理由（换句分支里 `capShow` 才写 `capSent`）。
`text-spacing-trim` 不改盒宽、只改墨迹位置，导出与 QA 读到的 bbox / client rects 逐值不变。
0.20.0 三条**都不需要见旗分支**，但落法分两组。`.flip` / `.ride` 走 transition：注入的
`transition:none!important` 让计算值直接落到 after-change style，也就是那一秒的真实档位——
实测一刀切后 θ 立即 180° 且背面墨量与"同一行不翻面的参照字"逐像素相同（69 = 69）、正面落
`visibility:hidden`；`offset-distance` 立即 100%、锚点停在**路线终点**那一格（这正是它与
0.18.0 那条被否决写法的分界：由 animation 驱动时切掉动画落回**路径起点**，导出帧上会多出
一个谁也没请的小点）。`.bloom` 走 animation 但**不写 forwards**、静止态自己就是全开，切掉
animation 后计算值仍是 `circle(75% at 50% 50%)`（实测取消动画逐值相同）。三条在
`getAnimations()` 里都恒空，与 `prefers-reduced-motion` 同口径。
0.21.0 三条也**都不需要见旗分支**，落法仍分两组。`.tilt` 走 transition：注入的
`transition:none!important` 让计算值直接落到 `data-pan` 那一档——实测模拟 reduce 与导出
一刀两种口径下三档逐值相同（−6.5° / 0° / +6.5°）、`getAnimations()` 恒空，成片里那一格
就是"已经读完方向的天平"；`.gather` 走 animation 但 fill 只有 `backwards`，切掉之后计算值
回到元素自身的 `normal`，实测副题排宽与不动画时逐值相同（117px，一个字不少），成片永远是
收好的那一句；`.opt.rip` 是**事件不是状态**，静止态与终态本来就都是 `opacity:0 / scale:0`
（实测两种切法同值），导出帧上不可能出现半截琥珀色圆盘压在字上。
0.22.0 三条同样**都不需要见旗分支**，但各落自己那一格。`#spot` 走 transition：注入的
`transition:none!important` 让 `--spot-x/y/r` 直接落到 `camBox()` 声明的那一档——实测导出
一刀下 s2 读到 697/143/108.8、`opacity` 仍是 `--spot-k`，成片每一帧都是"灯已经落在那一句的
主角上"，关掉的只是行程；而开合挂在呈现属性 `data-on` 上，不认识 `@property` 的引擎照样有
灯（降级掉的是走位不是内容）。`#ffwd` 是**事件不是状态**：静止态本来 `opacity:0`、不写
`forwards`，成片上不可能出现半排雪佛龙压在正文上，截图模式另外还有一道 `__coursewareShotMode`
不演。`.pregate` 走离散过渡：导出注入的 `#gate,#pregate{display:none!important}` 直接把它
摘掉——`!important` 压得住补间，而 QA 读的 `hidden` 那把真值一行都没改。
0.23.0 三条**都不需要见旗分支**，但落法分三组，而且**都只有一格**。`.settle` 走 animation
且 fill 只有 `backwards`：注入的 `animation:none!important` 让计算值回到元素自身的
`scale:none`——实测导出帧与 reduce 帧都读到徽标静止盒（`a-rule` 高 30.966、屏幕下沿
514.05 与不演时逐值相同），成片里不可能出现半截压扁判词。`.wind` 也是 animation、两端与
静止态都是 `translate:0 0`（**事件不是状态**），切掉之后蓄力层什么都不留，位移照旧由主角
那层的 `transform` **呈现属性**承载——实测那一帧直接落 `translate(500,84) scale(0.46)`
（"已经跳上去"那一格），而随它挂上去的让拍（`.wind > g` 的 `transition-delay`）必须在
reduced-motion 里**一起归零**，否则页面留着一格"等着补间"的读数（实测归零后读回 `0s`，
不归零读回 `0.0952s`——清单为空时它不动任何东西，但读数就是证据通道）。`.lift` 走
transition 且**清单里只有 `--elev`**：`transition:none!important` 让计算值直接落到
`data-lift` 那一档——实测 reduce 与导出两种切法下抬起那枚都是 `--elev:5px` /
`translate:0px -5px` / 影子 `0px 7.5px 6px`，没抬的那枚 `0px` / `0px`，成片每一帧都在说
"这一对被拿起来比、谁在前面"，关掉的只是抬升那 .38s 的行程。

**QA 取样口径**这一轮加四条：① **CSS 动画的曲线不在 `effect.getComputedTiming().easing`
上**——它返回关键帧自己的 easing，CSS 动画那里恒为 `linear`，于是 --ease-in-out 的 `.wind`
会被读成"没缓动"；曲线要从 `getComputedStyle().animationTimingFunction` 读（CSS 过渡相反，
`getComputedTiming().easing` 才对，0.22.0 那条）——回归用例把两个读数都断言一遍就是在锁这个
读法；② `getComputedStyle().translate` 在零位移时**塌成一格**（静止读 `"0px"` / `"none"`，
有 y 分量才读 `"0px -5px"`），`split()[1]` 会 IndexError，而把 `"0px"` 当"动了 0"读又会把
"没动"混进样本——探针要统一走一个容错解析器；③ 蓄力"看不看得见"只能沿**屏幕 y** 判：
`getBoundingClientRect()` 逐 rAF 采（范本 s2→s3 起点 348.17，现状 dip 81.00px / 只让不蓄
68.58px / 只蓄不让 0.00px），读动画自身的 `translate` 会把"算出了位移"当成"观众看见了"——
④ `.settle` 的"底边不动"要按**九个冻结档位**分别读屏幕下沿（只验两三格读不出"支点在接触边"
这句话的分量），面积守恒同理是逐档的二维读数（压扁 −4.2% / 拉长 −0.2% / 回弹 +0.9%）。

**QA 取样口径（0.23.1 CI 复按）**加两条，都是"本机绿、CI 红"那一类：① **屏幕绝对坐标随
视口漂**——同一份页面在 GitHub runner（Chrome 155）上整体比本机（Chrome 152）低 **6.96 设备px**
（徽标下沿 514.05 → 521.01、主角 shelf 终位 283.65 → 290.61），而尺寸类读数逐值不变
（77.414×30.966 两边一样）。所以位置类断言一律**同会话内相对化**：接触线读承载位移的父层
`<g>` 的底边、终位读"采样最后一帧 = 落定之后的读数"再加三遍互相对照、"上没上去"读两步之差；
写死一个 `y` 就是把用例绑在自己的窗口高度上。② **SVG-as-image 的字形取样不许依赖字体**——
`_LIT_INK` 原来按固定列区间取"念过 / 未念"两段，CI 跑图里没装中文字体、字形宽度变窄，暗段
落在字尾之外取不到墨，`cr_*` 直接是 `null`（`TypeError: '>=' … NoneType`，红了一个版本才被抓到）。
现在 `<text>` 上钉 `textLength="520" lengthAdjust="spacing"`（字形不拉伸），带子由 mask 自身的
百分比算，探针同时回报 `lit_n` / `dim_n`，取不到墨就报数而不是留一个 null 给下游炸。
更根本的一条：**比值必须来自同一批像素**——探针改成同一串字的两行（行 A `--lit:118%` 整行
走实心段、行 B `--lit:RAMP%` 整行走地板段）取同一列区间，比的才是"地板压掉多少"而不是
"哪几个字落在左半边"。旧口径本机 1.132 / CI 1.108 会红在阈值上，新口径本机 1.128 / 1.121 /
1.140 / 1.128，阈值 ≥1.10、`.92` 那档 <1.09（实测 1.063）。

**QA 取样口径**这一轮加三条：① 灯罩的覆盖要拿 `#frameClip` 折出来的**取景框窗**断言，不是
svg 外框——`#spot` 的 `getBoundingClientRect()` 因为 bleed 比画布大得多，跟 svg 比反而看不出
"幕沿进画"这类漏；② 洞心读的是 `--spot-x` 的**计算值**，源文件里有那行令牌不算数（本轮踩过：
一条不成对的注释吞掉 `--dur-spot`，CSSOM 规则数一点没变而整条 transition 在计算值阶段作废）；
③ 掠擦只有 340ms 且不留下终态，`getAnimations()` 里登记了 `ffSweep` 就算"演过"，行程本身
只能 `pause()` + `currentTime` 钉档采（0.21.0 那条采法教训同样成立）。
**QA 取样口径**这一轮再多三条：① `.tilt` 的角度不进 `getBBox()`（实测三档同盒
[406,280,188,12]），判"沉哪边"只能读屏幕盒或 `getScreenCTM()`——但命中**跟着变换走**
（playing 态下 `elementFromPoint` 打在倾侧后的左托盘上仍命中 `c-pan-l`），所以既别拿它做
几何判定、也别把它当"点不到"的证据；② `.gather` 要验"演过"得冻结动画读 `letter-spacing`
的中间值（或像掠光那样按"已进入 + 动画已登记"取样），而它在 `@media (max-width:560px),
(max-height:560px)` 分支里被关掉，浏览器用例必须按视口分档断言；③ 涟漪只能在真实
`pointerdown` 之后采到（`Input.dispatchMouseEvent`），程序化 `click()` 永远没有——那是
设计不是缺陷，别把它写成"事件没绑上"的假阴性。
**QA 取样口径**这一轮要多记两条：`.ride` 的位移不进 `getBBox()`（实测用户坐标下
[-6,-6,16,12] 起步/中途/落定三采同值），要判"抵达"只能读 `getScreenCTM()` 的平移分量或
屏幕盒中心，拿 bbox 断言会得到"根本没动"的假阴性；`.bloom` 绽开途中命中被 clip 裁掉是
**正确行为**（实测 60ms 那一格点不着、150ms 起命中），别把它当图层丢失。逐帧冷启动的成片
里这三条都只有"完成那一格"，行程只服务直播播放（stage.md §3.4）。
截图脚本的版面压平与隐藏 CSS 按模板结构类名写死——
`.shell`（两栏压平）、`.stage`（截图留边）、`.rack` / `.col-side` / `.chapter`（截图模式要藏起来的播放器行/侧栏/章节头）。上表的 id 钩子保证时间轴跑得动，但**成片入镜什么由这些类名决定**。类名缺失时导出只打 `[warn]`（分不清"页面真的没有这块"与"换了名"）；改过结构的页面必须人工核对一帧成片。

导出对**动效**的契约同样硬：截图页注入 `transition:none!important;animation:none!important`，
并且**每帧一次冷启动**（一帧一个新 Chrome，seek 到那一秒、只跑这一步的 renderer）。
于是成片里每一帧都是**静止的终态**——终态必须落在属性上（`animation-fill-mode` 用
`backwards` 或不写、循环动画的 `0%` 等于静止态），renderer 必须是 (scene, step) 的纯
函数。靠"上一状态"演出来的移动进视频会整段消失，靠历史推导出来的状态会漏帧（范本
实测过：开场那一排影子漏进了后面每一帧）。要拍进成片的，是**这一步该看到什么**；
运动只服务直播播放（stage.md §3.4）。

导出自带两道帧查重：全部帧完全相同 → 中止（页面没响应 `currentTime`）；换场后的帧仍与首帧逐像素相同 → 警告（开头几秒可能是静止画面）。

改动这些 id 或语义等于改 QA 契约：探针会把「配了门禁却从未弹出」「门禁在句中标位置打开」等判为失败。静态侧还要求 `RENDER = {sceneId: renderer}` 与 `var GATES = [{scene:'…'}]` 保持可解析的字面量形态（推送式拼装逃得过静态解析，但逃不过上面的活动驱动检测）；`RENDER` 的值必须是**裸函数名或内联匿名函数**（`function(){…}` 与箭头函数一视同仁地被解析）——`obj.fn` 这类成员引用会报专门的「不支持成员引用」，三元表达式等解析不出函数值的形态则报「缺少对应 renderer」。浏览器冒烟的虚拟时间预算按场景数扩容。

## 8. `check_gates.py` 能力与模式

它是**课件交付检查器**（通用 QA，不是给某份课件单独维护的 SelfTest），四类检查：

1. **时间轴 / 字幕**：schema version、时间合法、句子不重叠、字幕节点与 `#stage` 舞台钩子存在、`audio/` 交付目录无残留（只允许 `combined.wav` 与 `narration_timing.json`）；**音频时长与时间轴一致性**（WAV 走 wave 模块样本精确路径、无需 ffmpeg：短于时间轴结束点 >1.5s 判 error——最后几句旁白没声音；长于 >1.5s 判 warn——尾部余音；测不出时长只告警跳过，防"音频与时间轴串批次"）；**单句字幕超长告警**（>56 字：画布字幕带是 SVG `<text>` 不换行，超长句会溢出舞台被裁掉——narration.py 的 >45 字告警管写稿，这里是交付物级独立闸门）；**`<title>` 仍是范本默认标题告警**（照抄范本忘改，`build_page --title` 可注入）；浏览器中逐句验证字幕精确等于时间轴原文、**真的可见**（沿祖先链查 `opacity` / `visibility` / `hidden`），并校验空档（换场与场景内句间）保留上一句字幕；
2. **画面文字复述**：renderer 里名为 `txt()` / `badge()` 的写字函数（按函数名匹配）、`textContent` / `innerHTML` / `createTextNode` 直接赋值的字符串字面量，以及页面静态 SVG `<text>`（整页收集、跨幕比对），与旁白高度相似时提示"双字幕"（口径详见 stage.md 纪律 3）；
3. **门禁 / JS**：真实浏览器里自动走错答 → 正确答（recall 不判定，只走对照放行这一条路，且不算"错误路径未构造"警告），检查句子边界、`data-locked`、继续按钮；门禁检测**活动驱动**（真弹出就会被测，不依赖 `GATES` 字面量），配了却从未弹出的门禁会被报出；门禁打开期间若页面存在 `#rack` / `#veilplay` 且**它当前可达**（可见、且祖先链上没有 `inert`），未设 `inert` 判失败——不可见或祖先已 inert 时键盘本就够不到，豁免（键盘绕过义务见 layout.md §5，仅此浏览器模式断言）；逐句检查 `__coursewareRenderTrace`；
4. **JS 错误**：探针注入 `<head>` 最前，页面**加载期**抛出的错误（早于任何业务脚本，含 runtime 契约错误）也进报告。

模式：`--no-browser` 只做静态检查（时间轴、字幕挂点、`#stage`、manifest 与 runtime 文件及对内联时间轴的一致性、`audio/` 交付目录残留、renderer 计时纪律——`RENDER` 引用的具名函数与内联匿名体里不得用 `setTimeout`/`setInterval` 排程，解析已剥离字符串 / 注释防误报；renderer 里的 `requestAnimationFrame` 不判错但进警告——声明式动效（stage.md §3.4）之后 renderer 不再需要手搓补间，警告行就是留给你人工确认那一处用法的入口——音频路径、gate 对应场景；>3 道门禁给警告）；静态通过后默认继续**浏览器冒烟**（动态配置契约与手势绑定只能在这一环验证），有可用 Chrome / Edge 才冒烟，冒烟不可用时明确提示；`--require-browser` 让冒烟未执行时返回失败（适合 CI，与 `--no-browser` 互斥）；`--keep` 保留冒烟用的探针注入副本供排查；默认不接受 `synth_failed` 降级句，保留降级成片需显式 `--allow-degraded`；`--json` 把整份结果（`verdict`/`static`/`browser`/`note`/`exit_code`）作为一个 JSON 文档打到 stdout，人类逐行输出关闭——给 CI 报告与 agent 消费（判绿红只看 `exit_code` 或 `verdict`），与人类阅读路径互不影响。另有**页面本地资源引用完整性**检查（`link[href]` / `script[src]` / `img[src]` / `source[src]` / `use[href]` / `a[href]` 的页面目录内相对引用必须指向存在的文件，防"引用了文件却忘了拷贝"的静默破损；外部 URL / data: / 纯锚点 / 模板占位符 / 跨目录引用不判——runtime 与 audio 用各自更严的规则单独校验）。

**浏览器启动口径是三档退路，不是一个 flag**（`_browser_launch_strategy`，`export_video.py` 共用同一份判定与缓存）：① 带沙箱 + `--user-data-dir`；② `--no-sandbox` + `--user-data-dir`（无 user namespaces 的容器）；③ `--no-sandbox` + **隔离 HOME、不传 `--user-data-dir`**——root 下运行 Chrome for Testing 时，新建配置目录的**一次性转储**（`--dump-dom` / `--screenshot`）会永久挂死（进程起来了、永不归；实测去掉 `--user-data-dir`、把 profile 落点换成干净 `HOME` 即通），而 CDP 常驻那条路径不受影响，所以症状是"浏览器用例全绿、交付检查却报预检未通过"。三档全失败才返回"预检未通过"；探测用一次性转储本身且校验转储内容（"进程活着"不等于"能用"），判定结果按 chrome 路径进程内缓存（逐帧导出 33 帧不该重跑 3 档），缓存只存决定不存路径——**每帧的 profile 仍各自新建**（共用一份会撞 `profile in use`）。落到第 ②③ 档时报告多打一行 `[note]`（`--json` 走 `note` 字段），**浏览器真跑过时也打**：只看"冒烟通过"的人不会知道自己的 Chrome 差一档，换台机器才知道。`--require-browser` 正是给 CI 用的——降级环境里这条环境故障会被显式判失败，而不是静默少一层覆盖。
