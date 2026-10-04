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
见旗一律按桌面渲染（小屏放大不进视频，成片与桌面页面同一张脸）。截图脚本的版面压平与隐藏 CSS 按模板结构类名写死——
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

1. **时间轴 / 字幕**：schema version、时间合法、句子不重叠、字幕节点与 `#stage` 舞台钩子存在、`audio/` 交付目录无残留（只允许 `combined.wav` 与 `narration_timing.json`）；浏览器中逐句验证字幕精确等于时间轴原文、**真的可见**（沿祖先链查 `opacity` / `visibility` / `hidden`），并校验空档（换场与场景内句间）保留上一句字幕；
2. **画面文字复述**：renderer 里名为 `txt()` / `badge()` 的写字函数（按函数名匹配）、`textContent` / `innerHTML` / `createTextNode` 直接赋值的字符串字面量，以及页面静态 SVG `<text>`（整页收集、跨幕比对），与旁白高度相似时提示"双字幕"（口径详见 stage.md 纪律 3）；
3. **门禁 / JS**：真实浏览器里自动走错答 → 正确答（recall 不判定，只走对照放行这一条路，且不算"错误路径未构造"警告），检查句子边界、`data-locked`、继续按钮；门禁检测**活动驱动**（真弹出就会被测，不依赖 `GATES` 字面量），配了却从未弹出的门禁会被报出；门禁打开期间若页面存在 `#rack` / `#veilplay` 且**它当前可达**（可见、且祖先链上没有 `inert`），未设 `inert` 判失败——不可见或祖先已 inert 时键盘本就够不到，豁免（键盘绕过义务见 layout.md §5，仅此浏览器模式断言）；逐句检查 `__coursewareRenderTrace`；
4. **JS 错误**：探针注入 `<head>` 最前，页面**加载期**抛出的错误（早于任何业务脚本，含 runtime 契约错误）也进报告。

模式：`--no-browser` 只做静态检查（时间轴、字幕挂点、`#stage`、manifest 与 runtime 文件及对内联时间轴的一致性、`audio/` 交付目录残留、renderer 计时纪律——`RENDER` 引用的具名函数与内联匿名体里不得用 `setTimeout`/`setInterval` 排程，解析已剥离字符串 / 注释防误报；renderer 里的 `requestAnimationFrame` 不判错但进警告——声明式动效（stage.md §3.4）之后 renderer 不再需要手搓补间，警告行就是留给你人工确认那一处用法的入口——音频路径、gate 对应场景；>3 道门禁给警告）；静态通过后默认继续**浏览器冒烟**（动态配置契约与手势绑定只能在这一环验证），有可用 Chrome / Edge 才冒烟，冒烟不可用时明确提示；`--require-browser` 让冒烟未执行时返回失败（适合 CI，与 `--no-browser` 互斥）；`--keep` 保留冒烟用的探针注入副本供排查；默认不接受 `synth_failed` 降级句，保留降级成片需显式 `--allow-degraded`。
