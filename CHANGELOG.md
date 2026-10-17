# Changelog

本仓库的变更记录。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循语义化版本；未打 tag 前以 0.x 递增，随迭代推进。

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
