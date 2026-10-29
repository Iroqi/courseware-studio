# 运行时契约

> `scripts/interactive_runtime.js` 是**交互内核**，不是播放器。它只认 DOM 上的
> `data-interaction`，不认识时间轴、场景与门禁。页面持有唯一时钟；两者的分工见 §1 与 §6。

## 1. 职责边界

### 1.1 runtime 负责

- 给交互元素接事件；
- 处理 pointer / tap / drag；
- 根据 `data-interaction` 判断答案；
- 正确时写 `data-locked="1"`；
- 反复接线时保持幂等。

### 1.2 runtime 不负责

`audio.currentTime`、时间轴解析、场景切换、字幕、进度条、掌握度与学习记录。

### 1.3 拖拽兜底

拖拽手势有两处内建兜底：

- 窗口失焦（Alt-Tab / 系统弹窗）时 `up` 永不到达，按取消处理，ghost 与监听器当场
  清掉；
- 触屏上只跟随按下那根指针，第二根手指的 move / up 不劫持本次手势。

## 2. 五种交互

### 2.1 `choice`

```html
<div data-interaction data-interaction-type="choice">
  <button data-choice-id="a">选项 A</button>
  <button data-choice-id="b">选项 B</button>
  <div class="interaction-feedback" hidden></div>
</div>
```

```json
{
  "options": [
    {"id":"a","correct":false,"feedback":"再想一步。"},
    {"id":"b","correct":true,"feedback":"对。"}
  ]
}
```

选项**文案在 DOM 按钮上**，配置只负责判定与反馈；两边靠 `id` 对齐，对不上是 §5 的
契约错误。

作答后选中的选项带 `data-selected="1"`（其余为 `"0"`），页面可样式化。它挂在选项上，
选项随卡片重建一并消失，不在 §3.3 的重开清洗清单里。

### 2.2 `hotspot`

可点击元素带 `data-hotspot-id="node-1"`。配置与 `choice` 相同，也是 `options[]`。

可点区不限于 `<button>`——runtime 会给缺可聚焦语义的节点补 `tabindex="0"` 与
`role="button"`，Enter / 空格与点击同效。可点区的版式要求见 `interactions.md` §2.2。

### 2.3 `sequence`

```html
<ul class="sequence-list">
  <li class="sequence-item" data-sequence-id="a">…</li>
</ul>
<button data-sequence-submit>提交顺序</button>
```

```json
{"correct_order":["a","b","c"]}
```

重排有三种通路：

- **点选**（触屏主路径）：点条目 A 选中，再点条目 B 把 A 挪到 B 的位置，再点 A 一次
  取消选中；选中态同步在 `aria-pressed` 上，视觉与 bucket 选中同款；
- **拖拽**：鼠标按住位移超过 3px。拖拽期间 runtime 给跟手副本加 `.drag-ghost` 类、
  给原位条目加 `.drag-src` 类，页面 CSS 靠这两个类出样式；
- **键盘 ↑/↓**：聚焦条目后直接换位，提交键照常点选。Enter / 空格等价于点选那一下。

重排后 runtime 会按当前顺序重写 `.sequence-item .s-idx` 的文本（位次号跟位置不跟
条目）——页面用这个类名放序号即可，换名会静默失去位次更新。

`.sequence-item` 与 `.bucket-item` 的 `touch-action: pan-y` 是**页面 CSS 的义务**
（runtime 不设样式）：触屏用户靠点选作答，不靠拖。

### 2.4 `bucket`

```html
<li class="bucket-item" data-bucket-item="a">…</li>
<div data-drop data-bucket-id="left"><ul data-drop-slot></ul></div>
<button data-bucket-submit>提交归类</button>
```

```json
{
  "answer":{"a":"left","b":"right"},
  "feedback":{"a":"解释为什么放错。"}
}
```

既支持拖拽，也支持"点条目 → 点筐"（触屏主路径同样是这条：`touch-action:pan-y` 下
手指竖拖会先滚页面）。

键盘通路：

- 条目与筐都可聚焦，Enter / 空格等价于点击；选中态同步在 `aria-pressed` 上；
- 焦点在**条目**上按 Enter 是"选中 / 取消选中"；
- 焦点在**筐**上按 Enter 才是"把选中的条目放进这个筐"——runtime 会把条目上冒泡到
  筐的那一下挡掉，纯键盘可以完整作答。

筐拿的是 `role="group"` 而不是 `button`：容器若被标成 button，读屏会把筐里的条目
压成不可播报的子项。

### 2.5 `recall`

```html
<div data-interaction data-interaction-type="recall">
  <p class="q">先自己复述：……</p>
  <div class="recall-answer" hidden></div>
  <button data-recall-reveal>看参考答案</button>
  <div class="interaction-feedback" hidden></div>
</div>
```

```json
{"prompt":"题面","answer":"参考答案","hit_text":"可选放行话术"}
```

点「看参考答案」→ 答案填进 `.recall-answer` 并解除 `hidden` → 立即
`finish(correct:true)` 放行。

**没有判错分支**：口头复述没有可靠的机器判定，硬做判定只会把门禁退化成摆设。
`data-locked` 在 recall 处的语义比判定题型更弱一档（口径见 `interactions.md` §1.3）。
契约校验只保证展示通路完整（`prompt` / `answer` 非空 + 两个 DOM 钩子都在），空壳
recall 会在配置校验处 fail-closed。

### 2.6 可选提示与徽标钩子

不写就没有，runtime 不造节点。

| 钩子 | 行为 |
|---|---|
| `[data-hint-action]` + `.interaction-hint` | 点击前者切换后者的 `hidden`（题面自带的"看提示"按钮） |
| `.interaction-badge` | 答对时 runtime 把它从 `hidden` 放回来（"已通过"角标） |

## 3. 放行契约

### 3.1 唯一契约

正确答案在 runtime 内部最终走：

```js
finish(el, msg, {correct:true});
```

`finish()` 是 runtime 私有函数——页面**不要自己调用它**，也不该复制一份。页面与
runtime 之间唯一的契约是属性：

```js
el.dataset.locked = '1';
```

页面应使用 `MutationObserver` 或等价的事件式观察来放行，**不要轮询 `data-locked`**。

### 3.2 错答

显示反馈、保持未锁定、继续按钮仍不可用。反馈节点由 runtime 补
`aria-live="polite"` / `role="status"`，屏幕阅读器会自动播报，页面不必另做。

### 3.3 答对后 runtime 留下的状态

`finish(correct:true)` 除了写 `data-locked`，还会：

- 写 `data-completed="1"` 并给交互块加 `.is-completed` 类；
- **禁用块内所有 `<button>`**（含常驻的提交键）；
- 把块内的 `.interaction-badge`（若页面放了）从 `hidden` 放回来；
- 写反馈节点 `.interaction-feedback`：`textContent`、解除 `hidden`、换
  `.is-correct` / `.is-wrong` 类——**错答也走这条路**（并写 `data-completed="0"`），
  所以这是重开时最容易残留的一条：上一轮的"再想一步"会挂在题面下方；
- 题型附带状态：
  - hotspot：命中的可点区写 `data-hotspot-state="hit"`，点错的区写 `"miss"`（同一处
    不再重复反馈）；
  - bucket：全部条目写 `data-bucket-state="hit"`，选中态残留在 `data-picked` /
    `aria-pressed`；
  - recall：`.recall-answer` 被解除 `hidden` 并填入参考答案（重开时页面自己藏回去，
    runtime 不管回收）。

### 3.4 重开前的清洗

同一张卡复用时，页面在**重新打开**这道题前要把这些洗掉：

- 清 dataset；
- 去 `.is-completed` 类；
- 藏回 badge（若放了）；
- 收反馈区（文本清空 + `hidden` + 去对错类）；
- `b.disabled=false` 放回来。

`references/template.html` 的 `clearGateCards()` / `openGate()` 是参考实现（范本
没有 badge，"藏回 badge"一步要自己补）。

**不要**清 `data-bound` / `data-gesture`：它们是常驻按钮的防重复监听标记，清了重接线
会叠监听（"点一下触发两次"）。条目/选项节点若是重建的，本来就没有这些标记，无需
处理。

## 4. 动态接线

页面动态生成交互块后调用：

```js
window.coursewareStudioWire();
```

入口幂等：runtime 用节点级标记保证重复接线不叠加监听、不重复绑定。

- `data-bound` 是**已绑事件类型的逗号列表**（如 `click,keydown`，同一节点可分别按
  事件类型各绑一次）；
- `data-gesture` 标记已接手势的拖拽条目；
- 脚本放在 `<head>` 时首接线会顺延到 `DOMContentLoaded` 再执行（此时再调用入口也无害：
  绑定层自身幂等）。

动态 gate shell 初始可以只有空的 `data-interaction` 占位；真正配置写入后，runtime 会
严格校验题型契约。校验失败会先把其余块接完，再统一抛错给 QA 捕获；
`data-interaction` 不是合法 JSON 时抛的是"不是合法 JSON"，与结构违规的报错可区分。

调用顺序（必须在揭开门禁浮层之前）见 `interactions.md` §4.2。

## 5. 配置契约

### 5.1 各题型校验

| 题型 | 要求 |
|---|---|
| `choice` / `hotspot` | 配置与 DOM 的 id 集合必须完全一致，且恰好一个 `correct:true` |
| `sequence` | `correct_order` 必须存在、id 唯一，并完整覆盖全部 `.sequence-item`；条目必须全部放在单个 `.sequence-list` 容器内——校验与作答都以它为准，散在容器外的条目会被当场抛错（否则"过校验却永远判不出"） |
| `bucket` | `answer` 必须完整覆盖全部 `.bucket-item`，值只能引用现有 `data-bucket-id` |
| `recall` | `prompt` 与 `answer` 都必须是非空字符串（与 bucket 的 `answer` 对象不同形——recall 的是展示通路，不是判定表），且块内必须已有 `[data-recall-reveal]` 与 `.recall-answer` 两个钩子节点 |

### 5.2 别名

`options` 可写作 `choices`（choice）或 `spots`（hotspot），`sequence` 的顺序数组也可
用 `answer` 键下发——校验与报错口径不变。

### 5.3 契约失败

契约不满足时 runtime 直接抛错，让浏览器 QA 捕获，而不是静默把错误题目当成"永远答
不对"。

### 5.4 可选文案键

不写就用自己的默认话术。

| 键 | 题型 | 作用 |
|---|---|---|
| `options[].feedback` | choice / hotspot | 该选项被选/被点时替换默认反馈 |
| `wrong_text` | sequence | 顺序不对时的反馈 |
| `hit_text` | sequence / bucket / recall | 答对（recall：完成对照）时的反馈 |
| `unplaced_text` | bucket | 还有条目没进筐时的反馈 |
| `feedback`（对象） | bucket | `{条目id: 解释}`，放错时报对应条目的话 |

## 6. 接入原则

把 `scripts/interactive_runtime.js` 复制到课件输出目录，与 `index.html` 同级。
`references/` 不放副本。

页面自己拥有主循环四件套：

```js
RENDER
sentAt
syncGate
tick
```

runtime 不应重新实现其中任何一层。门禁控制器（`openGate` / `closeGate` /
`clearGateCards`）与字幕出口 `capShow` 同样归页面所有（控制器要洗的重开状态见 §3.4，
页面要留的钩子见 §7.1，字幕显示契约见 `stage.md` §4）。

## 7. QA 与导出契约

### 7.1 观察点

探针不驱动真实播放，而是桩掉 `currentTime` 后 dispatch `timeupdate`。页面必须留下
这些稳定的观察点：

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
| `#stage`（`data-state`: `idle` / `playing` / `paused`，页面写） | 舞台显隐状态源：QA 探针把它写成 `playing`、导出截图脚本写 `paused`，绕开模板在 `idle` 下隐藏画面的 CSS |
| `window.__coursewareRenderTrace` | 页面每次真正调用 renderer 后追加 `sceneId#index`；浏览器 QA 清空后逐句检查，防止音频/字幕在走而画面没有响应。它不是时钟，也不替代 `RENDER` 静态映射 |
| `window.__coursewareResetRenderTrace()` | QA 逐句复测前调用；必须同时清空 `__coursewareRenderTrace` 与页面内部 renderer 去重状态，避免单句课件误报 |

`#stage` 的 id 存在是**静态检查与导出**共同的硬前提（`--no-browser` 与导出都缺了
直接报错），但成片尺寸取自舞台 SVG 的 `viewBox`，不测量 `#stage`。

### 7.2 导出截图模式

截图页在 `<head>` 最前注入 `window.__coursewareShotMode`。页面侧有三条义务：

1. **`syncGate()` 见旗直接 return**：不占 `gOpen`、不回退冻结音频。否则浮层虽被
   导出脚本注入的 CSS 藏掉，时间轴却被门禁劫持。
2. **`setCompact()` 见旗按桌面渲染**：小屏放大不进视频，成片与桌面页面同一张脸。
3. **方向感换场见旗不演**：`tick()` 的换节分支里 `sceneSlide()` 与运镜模糊同样被
   旗标挡掉。导出每帧只跑一步的 renderer，"从哪一侧进场"没有上一帧可比，硬演只会
   让那一帧停在半推半进的位移上。

不需要见旗处理的：

- 远景视差层：它是 `camSet()` 写的属性终态，不是动画；
- 划重点色带：几何同时写进呈现属性，切掉 transition 就是定格；
- 擦除 / 羽化揭示：驱动量挂在 `animation` 上且 `fill-mode` 是 `backwards`，注入的
  `animation:none` 让计算值落回元素自身值（"全显"）；
- 几何补间、`.tuck*`、`.flip`、`.ride`、`.tilt`、`.lift`、`#spot`：走 transition，
  注入 `transition:none` 后直接落到 after-change style，也就是那一秒的真实档位；
- `.bloom`、`.gather`、`.settle`：走 animation 但静止态自己就是终态；
- `.crisp` / `.halo` / `.stag` / `text-spacing-trim`：静态口径，切掉动画什么都不变；
- 折叠面板、侧栏滚动入场、擦洗态变厚：侧栏与播放器行整列在截图模式被藏掉，且冷启动
  那一帧只会有 `n.open` 给的那条；
- 事件型原语（`.flow`、`.opt.rip`、`#ffwd`）：静止态与终态都不可见，成片里不可能
  出现半截中间帧。

### 7.3 逐帧冷启动与终态

截图页注入 `transition:none!important;animation:none!important`，并且**每帧一次冷
启动**（一帧一个新 Chrome，seek 到那一秒、只跑这一步的 renderer）。

于是成片里每一帧都是**静止的终态**，三条要求：

- **终态必须落在属性上**：`animation-fill-mode` 用 `backwards` 或不写，循环动画的
  `0%` 等于静止态；
- **renderer 必须是 (scene, step) 的纯函数**：靠"上一状态"演出来的移动进视频会整段
  消失，靠历史推导出来的状态会漏帧；
- **读取顺序**：`capProgWrite()` 必须排在换句分支之后。导出每帧只跑一次 `tick()`，
  而那一帧往往正好是换句帧；写在前面时 `capShow()` 还没写当句的 `capSent` /
  `capGeo`，截出来就是"字在、细线空着"。`capLitWrite()` 同理排在 `capProgWrite()`
  之后。

另一条状态纯函数化的义务：`ended` 收起字幕带后回拖进**同一句**，句号没变、换句分支
不进，`tick()` 要按 `capSent !== hit.sentence` 补一次 `capShow`。否则直播里表现为
"拖回去字幕不见了"，导出里表现为那一帧缺字幕。

导出自带两道帧查重：全部帧完全相同 → 中止（页面没响应 `currentTime`）；换场后的帧
仍与首帧逐像素相同 → 警告（开头几秒可能是静止画面）。

### 7.4 原语落法速查

| 机制 | 代表原语 | 冷启动 / 减动效后的落点 |
|---|---|---|
| animation + `backwards` | 擦除揭示、羽化揭示、`.gather`、`.settle` | 计算值回到元素自身值（全显 / 收好 / 静止盒） |
| animation 无 `forwards`，静止态即全开 | `.bloom` | 切掉动画后仍是全开 |
| transition | `.tuck*`、`.flip`、`.ride`、`.tilt`、`.lift`、`#spot`、几何补间 | 直接落到目标档位（`data-*` 声明的终态） |
| 事件不是状态 | `.flow`、`.opt.rip`、`#ffwd`、`.wind` 蓄力层 | 静止态与终态都不可见 / 位移由呈现属性承载 |
| 离散过渡 | `.pregate` | 截图模式被 `display:none!important` 摘掉，QA 读的 `hidden` 不受影响 |
| 静态口径 | `.crisp` / `.halo` / `.stag` / `text-spacing-trim` | 不改任何计算值 |

两条易漏的连带义务：

- `.wind > g` 的让拍（`transition-delay`）必须在 reduced-motion 里**一起归零**，
  否则页面留着一格"等着补间"的读数；
- `.tuckR/.tuckL` 的消费者要按"多停一拍"写（`step >= 1 && step <= 2`），否则落档
  那一帧压根没有这个节点。

### 7.5 版面压平与类名依赖

截图脚本的版面压平与隐藏 CSS 按模板结构类名写死：

| 类名 | 截图模式行为 |
|---|---|
| `.shell` | 两栏压平 |
| `.stage` | 截图留边 |
| `.rack` | 播放器行藏起 |
| `.col-side` | 侧栏藏起 |
| `.chapter` | 章节头藏起 |

上表的 id 钩子保证时间轴跑得动，但**成片入镜什么由这些类名决定**。类名缺失时导出只
打 `[warn]`（分不清"页面真的没有这块"与"换了名"）；改过结构的页面必须人工核对一帧
成片。

### 7.6 取样口径

QA 与导出共用同一批物理事实，下面几条是踩过的坑：

**通用**

- **位置断言同会话内相对化**。屏幕绝对坐标随视口漂：同一份页面在本机与 CI runner 上
  整体可差约 7 设备 px，而尺寸类读数逐值不变。接触线读承载位移的父层 `<g>` 的底边，
  终位读"采样最后一帧 = 落定之后"再对照三遍，"上没上去"读两步之差；写死一个 `y`
  就是把用例绑在自己的窗口高度上。
- **加 `textLength`** 再取字形墨迹。CI 镜像未必装中文字体，字形宽度会变窄，按固定
  列区间取"念过 / 未念"会取不到墨。`<text>` 上钉
  `textLength="520" lengthAdjust="spacing"`（字形不拉伸），带子由 mask 自身的百分比
  算，探针回报 `lit_n` / `dim_n`，取不到墨就报数而不是留 null。
- **比值来自同一批像素**：同一串字的两行（整行走实心段 / 整行走地板段）取同一列
  区间，比的才是"地板压掉多少"。
- **曲线读法分两类**：CSS 动画的曲线从 `getComputedStyle().animationTimingFunction`
  读——`effect.getComputedTiming().easing` 返回的是关键帧自己的 easing，CSS 动画那里
  恒为 `linear`；CSS 过渡相反，`getComputedTiming().easing` 才是对的。
- **`translate` 要容错解析**：零位移时计算值塌成一格（`"0px"` / `"none"`），有 y
  分量才读 `"0px -5px"`；`split()[1]` 会 IndexError，把 `"0px"` 当"动了 0"又会把
  "没动"混进样本。
- **"演过"按登记取样**：`getAnimations()` 登记过就算演过，行程只能 `pause()` +
  `currentTime` 钉档采。冷启动那一帧"看不见"对事件型原语是**正确定格**；要验它演过，
  必须按"该句已进入、动画已登记"取样，或在直播侧读补间值。
- **涟漪只在真实 `pointerdown` 之后存在**（`Input.dispatchMouseEvent`）。程序化
  `click()` 永远采不到——那是设计不是缺陷。

**SVG 几何**

- `.tilt` 的角度、`.ride` 的位移都不进 `getBBox()`（三档 / 三采同盒），判"沉哪边"
  "到没到"只能读屏幕盒或 `getScreenCTM()`。
- 命中**跟着变换走**：`elementFromPoint` 打在倾侧后的托盘上仍命中同一元素，所以既
  别拿它做几何判定，也别把它当"点不到"的证据。
- `.bloom` 绽开途中命中被 clip 裁掉是**正确行为**，别当图层丢失。
- `.gather` 验"演过"要冻结动画读 `letter-spacing` 的中间值；它在
  `@media (max-width:560px), (max-height:560px)` 分支里被关掉，浏览器用例必须按
  视口分档断言。
- `.settle` 的"底边不动"要按**九个冻结档位**分别读屏幕下沿，面积守恒是逐档的二维
  读数；只验两三格读不出"支点在接触边"。
- `.wind` 蓄力"看不看得见"只能沿**屏幕 y** 判（逐 rAF 采
  `getBoundingClientRect()`），读动画自身的 `translate` 会把"算出了位移"当成
  "观众看见了"。

**原语专属**

- `#spot` 的覆盖要拿 `#frameClip` 折出来的**取景框窗**断言，不是 svg 外框（它的
  bleed 比画布大得多，跟 svg 比反而看不出"幕沿进画"）；洞心读 `--spot-x` 的**计算
  值**，源文件里有那行令牌不算数。
- 掠光（`ffSweep`）只有 340ms 且不留终态，`getAnimations()` 里登记了就算演过；
  直播侧要按"浮层揭开"取样——preGate 那 1.1s 里 `#gate` 还是 `display:none`，伪元素
  动画要到元素第一次有盒才登记，抢先读会拿到假阴性。

### 7.7 改动契约的后果

改动 §7.1 的 id 或语义等于改 QA 契约：探针会把「配了门禁却从未弹出」「门禁在句中
标位置打开」等判为失败。

静态侧还要求：

- `RENDER = {sceneId: renderer}` 与 `var GATES = [{scene:'…'}]` 保持可解析的字面量
  形态。推送式拼装逃得过静态解析，但逃不过活动驱动检测。
- `RENDER` 的值必须是**裸函数名或内联匿名函数**（`function(){…}` 与箭头函数一视同仁
  地被解析）；`obj.fn` 这类成员引用会报专门的「不支持成员引用」，三元表达式等解析
  不出函数值的形态报「缺少对应 renderer」。
- 浏览器冒烟的虚拟时间预算按场景数扩容。

## 8. `check_gates.py` 能力与模式

### 8.1 定位

它是**课件交付检查器**（通用 QA，不是给某份课件单独维护的 SelfTest），保证课件
**不会坏**，不评价讲得好不好。

### 8.2 四类检查

1. **时间轴 / 字幕**：schema version、时间合法、句子不重叠、字幕节点与 `#stage`
   舞台钩子存在、`audio/` 交付目录无残留（只允许 `combined.wav` 与
   `narration_timing.json`）；**音频时长与时间轴一致性**（WAV 走 wave 模块样本精确
   路径、无需 ffmpeg：短于时间轴结束点 >1.5s 判 error——最后几句旁白没声音；长于
   >1.5s 判 warn——尾部余音；测不出时长只告警跳过，防"音频与时间轴串批次"）；
   **单句字幕超长告警**（>56 字：画布字幕带是 SVG `<text>` 不换行，超长句会溢出
   舞台被裁掉——`narration.py` 的 >45 字告警管写稿，这里是交付物级独立闸门）；
   **`<title>` 仍是范本默认标题告警**（照抄范本忘改，`build_page --title` 可注入）；
   浏览器中逐句验证字幕精确等于时间轴原文、**真的可见**（沿祖先链查 `opacity` /
   `visibility` / `hidden`），并校验空档（换场与场景内句间）保留上一句字幕。
2. **画面文字复述**：renderer 里名为 `txt()` / `badge()` 的写字函数（按函数名匹配）、
   `textContent` / `innerHTML` / `createTextNode` 直接赋值的字符串字面量，以及页面
   静态 SVG `<text>`（整页收集、跨幕比对），与旁白高度相似时提示"双字幕"（口径见
   `stage.md` §6）。
3. **门禁 / JS**：真实浏览器里自动走错答 → 正确答（recall 不判定，只走对照放行这一
   条路，且不算"错误路径未构造"警告），检查句子边界、`data-locked`、继续按钮。门禁
   检测**活动驱动**（真弹出就会被测，不依赖 `GATES` 字面量），配了却从未弹出的门禁
   会被报出。门禁打开期间若页面存在 `#rack` / `#veilplay` 且**它当前可达**（可见、
   且祖先链上没有 `inert`），未设 `inert` 判失败——不可见或祖先已 inert 时键盘本就
   够不到，豁免（键盘绕过义务见 `layout.md` §5，仅此浏览器模式断言）。逐句检查
   `__coursewareRenderTrace`。
4. **JS 错误**：探针注入 `<head>` 最前，页面**加载期**抛出的错误（早于任何业务脚本，
   含 runtime 契约错误）也进报告。

### 8.3 模式

| 模式 | 行为 |
|---|---|
| `--no-browser` | 只做静态检查：时间轴、字幕挂点、`#stage`、manifest 与 runtime 文件及对内联时间轴的一致性、`audio/` 残留、renderer 计时纪律（`RENDER` 引用的具名函数与内联匿名体里不得用 `setTimeout` / `setInterval` 排程，解析已剥离字符串 / 注释防误报；renderer 里的 `requestAnimationFrame` 不判错但进警告——声明式动效之后 renderer 不再需要手搓补间，警告行就是留给你人工确认的入口）；音频路径、gate 对应场景；>3 道门禁给警告 |
| 默认 | 静态通过后继续**浏览器冒烟**（动态配置契约与手势绑定只能在这一环验证）；有可用 Chrome / Edge 才冒烟，不可用时明确提示 |
| `--require-browser` | 冒烟未执行时返回失败（适合 CI，与 `--no-browser` 互斥） |
| `--keep` | 保留冒烟用的探针注入副本供排查 |
| `--allow-degraded` | 接受 `synth_failed` 降级句（默认拒收） |
| `--json` | 把整份结果（`verdict` / `static` / `browser` / `note` / `exit_code`）作为一个 JSON 文档打到 stdout，人类逐行输出关闭——给 CI 报告与 agent 消费（判绿红只看 `exit_code` 或 `verdict`） |

另有**页面本地资源引用完整性**检查：`link[href]` / `script[src]` / `img[src]` /
`source[src]` / `use[href]` / `a[href]` 的页面目录内相对引用必须指向存在的文件，防
"引用了文件却忘了拷贝"的静默破损；外部 URL / `data:` / 纯锚点 / 模板占位符 /
跨目录引用不判——runtime 与 audio 用各自更严的规则单独校验。

### 8.4 浏览器启动

**三档退路，不是一个 flag**（`_browser_launch_strategy`，`export_video.py` 共用同一
份判定与缓存）：

1. 带沙箱 + `--user-data-dir`；
2. `--no-sandbox` + `--user-data-dir`（无 user namespaces 的容器）；
3. `--no-sandbox` + **隔离 HOME、不传 `--user-data-dir`**——root 下运行 Chrome for
   Testing 时，新建配置目录的**一次性转储**（`--dump-dom` / `--screenshot`）会永久
   挂死（进程起来了、永不归），而 CDP 常驻那条路径不受影响，症状是"浏览器用例全绿、
   交付检查却报预检未通过"。

三档全失败才返回"预检未通过"。探测用一次性转储本身且校验转储内容（"进程活着"不等于
"能用"），判定结果按 chrome 路径进程内缓存（逐帧导出 33 帧不该重跑 3 档），缓存只存
决定不存路径——**每帧的 profile 仍各自新建**（共用一份会撞 `profile in use`）。

落到第 ②③ 档时报告多打一行 `[note]`（`--json` 走 `note` 字段），**浏览器真跑过时也
打**：只看"冒烟通过"的人不会知道自己的 Chrome 差一档。`--require-browser` 正是给 CI
用的——降级环境里这条环境故障会被显式判失败，而不是静默少一层覆盖。
