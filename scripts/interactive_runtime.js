(() => {
  // 只负责交互接线：choice / hotspot / sequence / bucket / recall。
  // 时间轴、音频时钟、场景、字幕、进度条全部归宿主页面所有。

  function parseConfig(el){
    const raw = el.getAttribute('data-interaction') || '';
    if(!raw.trim()) return {};
    // 坏 JSON 不能吞成 {}：吞掉后 validateConfig 只会报"缺少 options"，
    // 排查方向被带偏；而且门禁会呈现一张无法作答的半开卡片。显式抛错，
    // 让浏览器 QA（JSERR 通道）直接指出是哪个元素的数据坏了。
    try { return JSON.parse(raw); }
    catch(e){
      configError(el.dataset.interactionType || 'unknown',
        (el.id ? '#'+el.id : '[data-interaction]') +
        ' 的 data-interaction 不是合法 JSON：' + e.message);
    }
  }
  function configError(kind, message){ throw new Error('Courseware interaction contract ('+kind+'): '+message); }
  function validateConfig(el, kind, config){
    const id = el.id ? '#'+el.id : kind;
    if(kind==='choice'){
      const opts=config.options||config.choices;
      if(!Array.isArray(opts)||!opts.length) configError(kind, id+' 缺少 options/choices');
      const domIds=[...el.querySelectorAll('[data-choice-id]')].map(n=>String(n.dataset.choiceId));
      const ids=opts.map(o=>String(o&&o.id));
      if(new Set(ids).size!==ids.length || new Set(domIds).size!==domIds.length) configError(kind, id+' 选项 id 必须唯一');
      if(ids.length!==domIds.length || ids.some(v=>!domIds.includes(v))) configError(kind, id+' 配置选项与 DOM 选项不一致');
      if(opts.filter(o=>o&&o.correct===true).length!==1) configError(kind, id+' 必须且只能有一个 correct=true');
    } else if(kind==='hotspot'){
      const opts=config.options||config.spots;
      if(!Array.isArray(opts)||!opts.length) configError(kind, id+' 缺少 options/spots');
      const domIds=[...el.querySelectorAll('[data-hotspot-id]')].map(n=>String(n.dataset.hotspotId));
      const ids=opts.map(o=>String(o&&o.id));
      if(new Set(ids).size!==ids.length || new Set(domIds).size!==domIds.length) configError(kind, id+' hotspot id 必须唯一');
      if(ids.length!==domIds.length || ids.some(v=>!domIds.includes(v))) configError(kind, id+' 配置 hotspot 与 DOM 不一致');
      if(opts.filter(o=>o&&o.correct===true).length!==1) configError(kind, id+' 必须且只能有一个 correct=true');
    } else if(kind==='sequence'){
      const orderCfg=config.correct_order||config.answer||[];
      // 与其它题型同口径：非数组 truthy 值要给契约错误，不能让 .map 抛裸 TypeError
      if(!Array.isArray(orderCfg)) configError(kind, id+' correct_order/answer 必须是数组');
      const expected=orderCfg.map(String);
      // 校验域必须等于作答域：提交读的是 .sequence-list 里的条目，校验却扫全块
      // 会让列表外的 .sequence-item"过校验却永远判错"——正是 §5 禁止的静默坏题。
      const seqList=el.querySelector('.sequence-list');
      if(!seqList) configError(kind, id+' 缺少 .sequence-list 容器（校验与作答都以它为准）');
      const stray=[...el.querySelectorAll('.sequence-item')].filter(n=>!seqList.contains(n));
      if(stray.length) configError(kind, id+' 有 '+stray.length+' 个 .sequence-item 不在 .sequence-list 内，无法被重排/判定');
      const domIds=[...seqList.querySelectorAll('.sequence-item')].map(n=>String(n.dataset.sequenceId));
      if(!expected.length) configError(kind, id+' 缺少 correct_order/answer');
      if(new Set(expected).size!==expected.length || new Set(domIds).size!==domIds.length) configError(kind, id+' sequence id 必须唯一');
      if(expected.length!==domIds.length || expected.some(v=>!domIds.includes(v))) configError(kind, id+' 正确顺序必须完整覆盖所有 sequence-item');
    } else if(kind==='bucket'){
      const answer=config.answer;
      if(!answer || typeof answer!=='object' || Array.isArray(answer) || !Object.keys(answer).length) configError(kind, id+' 缺少 answer');
      const itemIds=[...el.querySelectorAll('.bucket-item')].map(n=>String(n.dataset.bucketItem));
      const itemSet=new Set(itemIds), answerIds=Object.keys(answer).map(String), answerSet=new Set(answerIds);
      if(itemSet.size!==itemIds.length || answerSet.size!==answerIds.length) configError(kind, id+' bucket item id 必须唯一');
      if(itemSet.size!==answerSet.size || itemIds.some(v=>!answerSet.has(v))) configError(kind, id+' answer 必须覆盖全部 bucket-item');
      const bucketIds=new Set([...el.querySelectorAll('[data-drop][data-bucket-id]')].map(n=>String(n.dataset.bucketId)));
      answerIds.forEach(k=>{ if(!bucketIds.has(String(answer[k]))) configError(kind, id+' answer 引用了不存在的 bucket: '+answer[k]); });
    } else if(kind==='recall'){
      // recall 不判对错：配置只有题面 prompt 与参考答案 answer（都是字符串）。
      // 契约在于**展示通路**完整——没有 revealing 按钮或答案容器，点了给谁看？
      // 空壳 recall 一样能在点击处 vacuous 写出 data-locked，必须 fail-closed。
      if(!config.prompt || !String(config.prompt).trim()) configError(kind, id+' 缺少 prompt');
      if(!config.answer || !String(config.answer).trim()) configError(kind, id+' 缺少 answer（参考答案）');
      if(!el.querySelector('[data-recall-reveal]')) configError(kind, id+' 缺少 [data-recall-reveal] 按钮');
      if(!el.querySelector('.recall-answer')) configError(kind, id+' 缺少 .recall-answer 容器');
    } else {
      configError(kind||'unknown', id+' 不支持的 interaction type');
    }
  }
  // 放行信号契约见 runtime.md §3；这里补一句页面侧的：用 MutationObserver 观察
  // data-locked，不要轮询。
  function finish(el,msg,opts){
    const fb=el.querySelector('.interaction-feedback');
    if(fb){
      ensureLive(fb);   // 页面动态重建反馈区时也能补上播报属性
      // 内部调用点都传 `配置文案 || 默认话术` 或字面量，反馈区不会渲染空串；
      // 这里不做兜底——真出现空消息就是接线错了，宁可露白也别静默圆场。
      fb.textContent=msg;
      fb.hidden=false;
      fb.classList.remove('is-correct','is-wrong');
      if(opts.correct===true) fb.classList.add('is-correct');
      else if(opts.correct===false) fb.classList.add('is-wrong');
    }
    el.dataset.completed=opts.correct===true?'1':'0';
    if(opts.correct!==true) return;
    el.classList.add('is-completed');
    el.dataset.locked='1';
    el.querySelectorAll('button').forEach(b=>{b.disabled=true;});
    const badge=el.querySelector('.interaction-badge');
    if(badge) badge.hidden=false;
  }
  // ── 拖拽 / 点选手势内核 ──────────────────────────────────────────────
  // sequence（列表内排序）与 bucket（跨筐搬运）共用这一份。一次按下之后解析成两种手势：
  // 位移 ≤ 3px 算**点选**（onTap），否则算**拖拽**（onDrop）。写成一个内核而不是两套，
  // 是为了让两种形态手感一致；顺带让"点一下"成为一等手势——拖拽在触屏上不稳，
  // 点选式（点条目 → 点筐）是那条兜底的路。
  // 无 PointerEvent 的旧环境用鼠标事件（这一路不支持触屏）。原生 DnD 那条路已删：
  // 鼠标适配器覆盖同样的环境，还多一个"点"的手势，严格更优。
  const GEST = (window.PointerEvent)
    ? { down:'pointerdown', move:'pointermove', up:'pointerup', cancel:'pointercancel' }
    : { down:'mousedown',   move:'mousemove',   up:'mouseup',   cancel:null };

  // 元素级幂等绑定（按事件类型分别记账）：交互块重建后会重新接线，块里那些
  // **没被重建**的持久按钮（提交键、提示键）不该跟着多挂一层监听——多一层的
  // 表现是"点一下触发两次"，很隐蔽。同一节点可能同时接 click 与 keydown
  // （键盘通路），所以标记存的是已绑事件列表，不是单个 '1'。
  function once(node, ev, fn){
    if(!node) return;
    const bound = (node.dataset.bound || '').split(',').filter(Boolean);
    if(bound.includes(ev)) return;
    bound.push(ev); node.dataset.bound = bound.join(',');
    node.addEventListener(ev, fn);
  }

  // 无障碍三件套：反馈区对读屏播报、非原生可点元素可聚焦可回车、
  // 选中态用 aria-pressed 镜像 data-picked。
  function ensureLive(fb){
    if(fb && !fb.getAttribute('aria-live')){
      fb.setAttribute('aria-live', 'polite');
      fb.setAttribute('role', 'status');
    }
  }
  // role 默认 button（叶子可点元素：条目、hotspot 圈）；筐/待放区这类**容器**必须
  // 传 'group'：role=button 会把子树压成 presentational，读屏用户聚焦到筐后就
  // 听不见筐里有哪些条目了。
  function ensureFocusable(el, role){
    if(!el || el.matches('button,a[href],input,select,textarea')) return;
    if(!el.hasAttribute('tabindex') || el.getAttribute('tabindex') === '-1')
      el.setAttribute('tabindex', '0');
    if(!el.getAttribute('role')) el.setAttribute('role', role || 'button');
  }
  function isActivateKey(e){ return e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar'; }

  function dropHolder(c){ return c.querySelector('[data-drop-slot]') || c; }

  function makeDraggable(o){
    const root = o.root, itemSel = o.itemSel, dropSel = o.dropSel;
    const containers = () => dropSel ? [...root.querySelectorAll(dropSel)] : [root];
    // 指针落在哪个容器里，以及该插到该容器内哪一项之前
    const locate = (x, y) => {
      const box = containers().find(c => { const r = c.getBoundingClientRect();
        return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom; });
      if(!box) return null;
      const holder = dropHolder(box);
      const before = [...holder.querySelectorAll(itemSel)]
        .find(it => { const r = it.getBoundingClientRect(); return y < r.top + r.height/2; });
      return { holder: holder, before: before || null };
    };
    [...root.querySelectorAll(itemSel)].forEach(item => {
      if(item.dataset.gesture === '1') return;      // 幂等：接线可能被反复调用
      item.dataset.gesture = '1';
      ensureFocusable(item);
      if(!item.hasAttribute('aria-pressed')) item.setAttribute('aria-pressed', '0');
      item.addEventListener(GEST.down, e => {
        if(e.button !== undefined && e.button !== 0) return;
        if(!o.enabled()) return;
        e.preventDefault();   // 拦掉原生文本选区/拖拽，ghost 跟手
        // preventDefault 顺手压掉了默认的聚焦行为，而键盘重排（↑/↓）要求条目
        // 在拖完之后仍是焦点——不补这一脚，鼠标拖一次再想按方向键就失灵了。
        item.focus({ preventScroll: true });
        const r = item.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
        let moved = false, cancelled = false;
        // 多指触屏上第二根手指的 move/up/cancel 不能劫持本次手势：幽灵跟的是
        // 按下那根指针，另一根先抬起也不能提前结束手势。鼠标回退没有 pointerId，一律放行。
        const isMyPointer = ev =>
          e.pointerId === undefined || !ev || ev.pointerId === undefined || ev.pointerId === e.pointerId;
        const clone = item.cloneNode(true);
        clone.classList.add('drag-ghost');
        clone.style.cssText = 'position:fixed;left:'+r.left+'px;top:'+r.top+'px;width:'+r.width
          +'px;margin:0;z-index:9999;opacity:.97;pointer-events:none;';
        document.body.appendChild(clone);
        item.classList.add('drag-src');
        const move = ev => {
          if(!isMyPointer(ev)) return;
          if(!moved){
            const ddx = ev.clientX - e.clientX, ddy = ev.clientY - e.clientY;
            if(ddx*ddx + ddy*ddy > 9) moved = true;   // 位移超过 3px 才算拖拽
          }
          if(!moved) return;
          clone.style.left = (ev.clientX - dx) + 'px';
          clone.style.top  = (ev.clientY - dy) + 'px';
          const at = locate(ev.clientX, ev.clientY);
          if(at){ if(at.before) at.holder.insertBefore(item, at.before); else at.holder.appendChild(item); }
        };
        const end = ev => {
          if(ev && !isMyPointer(ev)) return;   // 别的指针先抬起：手势继续
          document.removeEventListener(GEST.move, move);
          document.removeEventListener(GEST.up, end);
          if(GEST.cancel) document.removeEventListener(GEST.cancel, cancel);
          window.removeEventListener('blur', cancel);
          item.classList.remove('drag-src');
          if(clone.parentNode) clone.parentNode.removeChild(clone);
          // 手势被浏览器收走（触屏竖滑滚动、系统弹窗）时会发 pointercancel：
          // 那一下不是用户意图，不能当成点选，否则滚个页面就选中一条。
          if(!moved && !cancelled) o.onTap(item);   // 没动 = 点选
          else if(moved) o.onDrop(item);
        };
        const cancel = ev => { if(!isMyPointer(ev)) return; cancelled = true; end(ev); };
        document.addEventListener(GEST.move, move);
        document.addEventListener(GEST.up, end);
        if(GEST.cancel) document.addEventListener(GEST.cancel, cancel);
        // 窗口失焦（Alt-Tab / 系统弹窗）时 up 永远不会来：按取消处理，
        // 否则 ghost 与监听器挂在页面上清不掉。
        window.addEventListener('blur', cancel);
      });
      // 键盘通路：Enter/Space 等价于"点选"手势；给了 keyboardReorder 的列表
      // （sequence）额外支持 ArrowUp/Down 移动当前项——拖拽对手指方便，
      // 键盘用户不该完全没有重排的路。
      item.addEventListener('keydown', e => {
        if(!o.enabled()) return;
        if(isActivateKey(e)){
          e.preventDefault();   // 拦掉空格的默认滚动，别让"没反应"变成"跳页"
          o.onTap(item);
        } else if(o.keyboardReorder && (e.key === 'ArrowUp' || e.key === 'ArrowDown')){
          e.preventDefault();
          const holder = item.parentElement;
          if(!holder) return;
          const sibs = [...holder.children].filter(n => n.matches && n.matches(itemSel));
          const i = sibs.indexOf(item);
          const j = e.key === 'ArrowUp' ? i - 1 : i + 1;
          if(i < 0 || j < 0 || j >= sibs.length) return;
          if(e.key === 'ArrowUp') holder.insertBefore(item, sibs[j]);
          else holder.insertBefore(item, sibs[j].nextSibling);
          item.focus();
          o.onDrop(item);
        }
      });
    });
  }

  function wireBlock(el){
      // 动态 gate 的 shell 在首次调用时还没有题目节点；openGate() 填入题目后
      // 必须能再次扫描新增节点。不能在元素层直接 return：幂等性由 once() 和
      // makeDraggable() 的节点级标记保证，既不会重复监听，也不会漏掉新节点。
      const config=parseConfig(el), kind=el.dataset.interactionType;
      // 模板里的 gate shell 初始只有空的 data-interaction，占位阶段不校验；
      // buildChoice/buildHotspot/buildBucket/buildSequence/buildRecall 写入真实配置后，openGate() 会再次接线并校验。
      if((el.getAttribute('data-interaction') || '').trim()) validateConfig(el, kind, config);
      // ── choice：单选 ───────────────────
      if(kind==='choice'){
        const fb=el.querySelector('.interaction-feedback'); ensureLive(fb);
        el.querySelectorAll('[data-choice-id]').forEach(btn=>once(btn,'click',()=>{
          if(el.dataset.locked==='1')return;
          const cfg=parseConfig(el);                       // 点击时再读：页面改了配置不用重接线
          const opts=cfg.options||cfg.choices||[];
          el.querySelectorAll('[data-choice-id]').forEach(b=>b.dataset.selected='0');
          btn.dataset.selected='1';
          const opt=opts.find(o=>String(o.id)===String(btn.dataset.choiceId));
          const correct=opt?.correct===true;
          // validateConfig 保证恰有一个 correct=true，走到这里必有正确性语义
          const msg=opt?.feedback||(correct?'正确，继续。':'再想一步，再试一次。');
          // 反馈节点交给 finish 现场重查再写（页面重建过反馈区也不写进游离节点）
          finish(el,msg,{correct});
        }));
      }

      // ── hotspot：在图上点部位 ──────────────────────────────────────────
      // 判定与单选同形（config.options[{id,correct,feedback}]），差别只在"答案不是一个句子，
      // 而是图上的一处"。可点区是任何带 [data-hotspot-id] 的元素：画布上的真图形、
      // 或门禁卡片里的示意图都行（画布被浮层盖着，把图放进卡片最稳，见 interactions.md §2）。
      if(kind==='hotspot'){
        const fb=el.querySelector('.interaction-feedback'); ensureLive(fb);
        el.querySelectorAll('[data-hotspot-id]').forEach(spot=>{
          ensureFocusable(spot);   // 可点区常是 SVG 图形/div：给键盘一条入场路
          const act = () => {
            if(el.dataset.locked==='1')return;
            if(spot.dataset.hotspotState==='miss')return;  // 同一处点第二遍不重复反馈
            const cfg=parseConfig(el);
            const opts=cfg.options||cfg.spots||[];
            const id=String(spot.dataset.hotspotId);
            const opt=opts.find(o=>String(o.id)===id);
            const correct=opt?.correct===true;
            const msg=opt?.feedback||(correct?'就是这一处。':'不是这一处，再看看。');
            // 反馈节点交给 finish 现场重查再写（与 choice 同一路）
            if(correct) spot.dataset.hotspotState='hit'; else spot.dataset.hotspotState='miss';
            finish(el,msg,{correct});
          };
          once(spot,'click',act);
          once(spot,'keydown',e=>{ if(isActivateKey(e)){ e.preventDefault(); act(); } });
        });
      }

      // ── sequence：列表内拖拽排序 ───────────────────────────────────────
      // 触屏是点选式主路径（touch-action:pan-y 下手指竖拖先滚页面，拖拽不可靠）：
      // 点 A → 点 B，A 挪到 B 的位置。键盘 Enter/Space 复用同一个 onTap。
      if(kind==='sequence'){
        const list=el.querySelector('.sequence-list');
        const deselect=it=>{ it.dataset.picked='0'; it.setAttribute('aria-pressed','0'); };
        // 位次号跟**位置**走，不跟条目走：建卡时写的 1..n 会在换序后留下
        // "2,1,3" 的读数，拖完/移完按当前 DOM 顺序重写一遍。
        const renumber=()=>{ if(list) list.querySelectorAll('.sequence-item .s-idx')
          .forEach((n,i)=>{ n.textContent=String(i+1); }); };
        if(list) makeDraggable({ root:list, itemSel:'.sequence-item',
          keyboardReorder:true,
          enabled:()=>el.dataset.locked!=='1',
          onTap:it=>{
            if(it.dataset.picked==='1'){ deselect(it); return; }   // 再点同一条 = 取消
            const picked=list.querySelector('.sequence-item[data-picked="1"]');
            if(!picked){ it.dataset.picked='1'; it.setAttribute('aria-pressed','1'); return; }
            const follows = !!(picked.compareDocumentPosition(it) & Node.DOCUMENT_POSITION_FOLLOWING);
            list.insertBefore(picked, follows ? it.nextSibling : it);
            deselect(picked);
            renumber();
          },
          onDrop:()=>{                                    // 拖完换位置，旧的选中态作废
            const p=list.querySelector('.sequence-item[data-picked="1"]');
            if(p) deselect(p);                            // 清的是**被点选的旧条目**，不是被拖的那条
            renumber(); } });
        once(el.querySelector('[data-sequence-submit]'),'click',()=>{
          const cfg=parseConfig(el);
          // 提交时现场取列表：once() 让提交键只绑第一次接线的闭包，页面若整体
          // 重建过 .sequence-list，闭包里的旧引用会永远读出空 order——静默坏题。
          const cur=el.querySelector('.sequence-list')||list;
          const order=cur?[...cur.querySelectorAll('.sequence-item')].map(x=>String(x.dataset.sequenceId)):[];
          const expected=(cfg.correct_order||cfg.answer||[]).map(String);
          // 空配置不许 vacuous 通过：expected 与 order 都空时 every() 恒真，
          // 一次提交就能写出 data-locked——占位壳被揭开的页面会直接放行。
          if(!expected.length){
            finish(el,'交互配置漂移：correct_order 为空，门禁无法判定。',{correct:false});
            return;
          }
          if(expected.length!==order.length || !order.every((v,i)=>v===expected[i])){
            // 错答统一走 finish：与 choice/hotspot 同路（写 data-completed='0'、
            // 反馈节点收口一处），runtime.md §3 的"错答也走这条路"对四种判定题型成立
            // （recall 不判定，没有错答路径）。
            finish(el,cfg.wrong_text||'顺序还不对，再调整一次。',{correct:false});
            return;
          }
          // 排对之后**必须**走 finish(correct:true)：data-locked 是页面唯一的放行信号（runtime.md §3）
          finish(el,cfg.hit_text||'顺序正确。',{correct:true});
        });
      }

      // ── bucket：把条目放进筐（归类 / 边界） ─────────────────────────────
      // config.answer = {条目id: 筐id}；config.feedback = {条目id: 放错时的解释}。
      // 拖进筐，或"点条目 → 点筐"。未归完或有放错 → 不锁；全对才 finish(correct:true)。
      if(kind==='bucket'){
        const itemSel='.bucket-item', dropSel='[data-drop]';
        // dataset/属性选择器拼用户写的 id：含引号或特殊字符会炸查询。CSS.escape
        // 是正解，老环境（无 CSS.escape）退化为手动转义引号与反斜杠。
        const cssEsc = (typeof CSS !== 'undefined' && CSS.escape)
          ? (s => CSS.escape(s))
          : (s => String(s).replace(/["\\]/g, '\\$&'));
        const placed=()=>[...el.querySelectorAll(itemSel)].reduce((acc,it)=>{
          const box=it.closest(dropSel);
          acc[String(it.dataset.bucketItem)]=box?String(box.dataset.bucketId||''):'';
          return acc; },{});
        const paint=()=>{ el.querySelectorAll(itemSel).forEach(it=>{
          const box=it.closest(dropSel);
          const at=box?String(box.dataset.bucketId||''):'';
          // 条目挪了位置（换筐 / 进出待放区）就作废上一次的"放错"红框：
          // miss 的语义是"当前放错了"，放对之后不许残留。
          if(it.dataset.bucketAt!==at) delete it.dataset.bucketState;
          it.dataset.bucketAt=at; }); };
        paint();
        makeDraggable({ root:el, itemSel:itemSel, dropSel:dropSel,
          enabled:()=>el.dataset.locked!=='1',
          onTap:it=>{                                   // 点条目：选中 / 取消选中
            const same=it.dataset.picked==='1';
            el.querySelectorAll(itemSel).forEach(x=>{
              x.dataset.picked='0'; x.setAttribute('aria-pressed','0'); });
            it.dataset.picked=same?'0':'1';
            it.setAttribute('aria-pressed', same?'0':'1');
          },
          onDrop:()=>{                                  // 拖完换位置，旧的选中态作废
            const p=el.querySelector(itemSel+'[data-picked="1"]');
            if(p){ p.dataset.picked='0'; p.setAttribute('aria-pressed','0'); }
            paint(); } });
        // 筐的放置动作**无状态**（"选中了哪个条目"只存在 DOM 的 data-picked 上），
        // 于是它可以只绑一次：页面重建条目、反复接线都不会把监听叠起来。
        const placeInto = box => {
          if(el.dataset.locked==='1')return;
          const picked=el.querySelector(itemSel+'[data-picked="1"]');
          if(!picked)return;
          dropHolder(box).appendChild(picked);          // 认 [data-drop-slot]（bucket 的内层 ul）
          picked.dataset.picked='0';
          picked.setAttribute('aria-pressed','0');
          paint();
        };
        el.querySelectorAll(dropSel).forEach(box=>{
          ensureFocusable(box, 'group');                // 键盘：聚焦筐 → 回车放入选中条目
          once(box,'click',ev=>{
            // 点在**条目**上的那一下会冒泡到筐/待放区，不能当成"往这里放"：
            // 否则"点 A 再点 B"时，点 B 的冒泡会把刚选中的 B 立刻放回原处，看起来是选不中。
            if(ev.target && ev.target.closest && ev.target.closest(itemSel))return;
            placeInto(box);
          });
          once(box,'keydown',ev=>{
            if(!isActivateKey(ev))return;
            // 与上面 click 同一件事：条目上的回车会冒泡到筐。不拦的话，键盘用户
            // 在条目上按 Enter 刚选中就被这层 placeInto 立刻取消选中——纯键盘
            // 永远做不完 bucket 题。
            if(ev.target && ev.target.closest && ev.target.closest(itemSel))return;
            ev.preventDefault(); placeInto(box);
          });
        });
        once(el.querySelector('[data-bucket-submit]'),'click',()=>{
          const cfg=parseConfig(el);
          const answer=cfg.answer||{}, why=cfg.feedback||{};
          const now=placed(), ids=Object.keys(answer);
          // 同 sequence：空 answer + 空条目会一路穿过三条检查，在 finish(correct:true)
          // 处 vacuous 锁上——空壳题必须按配置漂移拒判。
          if(!ids.length){
            finish(el,'交互配置漂移：answer 为空，门禁无法判定。',{correct:false});
            return;
          }
          const domIds=[...el.querySelectorAll(itemSel)].map(it=>String(it.dataset.bucketItem));
          if(domIds.length!==ids.length || domIds.some(k=>!Object.prototype.hasOwnProperty.call(answer,k))){
            // 接线时 validateConfig 已核对过一致；走到这里说明配置与条目是**之后**
            // 各自变化的（页面动态重建）。裸 throw 会让门禁既不反馈也过不去，
            // 这里改为可见反馈：用户卡住时至少知道是配置漂移。
            // 三条错答路径统一走 finish(correct:false)：反馈节点由 finish 现场重查
            // （页面重建过反馈区也不会写进游离节点），并写 data-completed='0'。
            finish(el,'交互配置漂移：answer 与当前条目不一致，门禁无法判定。',{correct:false});
            return;
          }
          const empty=ids.filter(k=>!now[k]);
          if(empty.length){
            finish(el,cfg.unplaced_text||'还有没放进筐的。',{correct:false});
            return;
          }
          const wrong=ids.filter(k=>String(answer[k])!==String(now[k]));
          if(wrong.length){
            finish(el,why[wrong[0]]||'有一处放错了，再想想。',{correct:false});
            wrong.forEach(k=>{ const it=el.querySelector('[data-bucket-item="'+cssEsc(k)+'"]');
              if(it)it.dataset.bucketState='miss'; });
            return;
          }
          el.querySelectorAll(itemSel).forEach(it=>{ it.dataset.bucketState='hit'; });
          finish(el,cfg.hit_text||'分对了。',{correct:true});
        });
      }

      // ── recall：复述 / 对照（不判定） ──────────────────────────────────
      // config = {prompt, answer, hit_text?}。学习者先自己复述，点"看参考答案"
      // 对照后即放行。没有判错分支：机器无法核对口头复述，硬做判定只会把门禁
      // 退化成摆设。locked 的语义比判定题型更弱——"完成对照"，不是"答对"。
      if(kind==='recall'){
        const fb=el.querySelector('.interaction-feedback'); ensureLive(fb);
        once(el.querySelector('[data-recall-reveal]'),'click',()=>{
          if(el.dataset.locked==='1')return;
          const cfg=parseConfig(el);
          const box=el.querySelector('.recall-answer');
          if(box){ box.textContent=String(cfg.answer||''); box.hidden=false; }
          // validateConfig 保证 answer 非空，点击必有可见内容；空壳走不到这行。
          finish(el,cfg.hit_text||'参考答案已给出，对照后即可继续。',{correct:true});
        });
      }
      once(el.querySelector('[data-hint-action]'),'click',()=>{
        const h=el.querySelector('.interaction-hint'); if(h)h.hidden=!h.hidden;});
  }

  function wireInteractions(){
    // 单块契约违规不拖垮其它块：逐块接线、收集失败，最后统一抛出，
    // 保留"浏览器 QA 捕获 JSERR"的契约（runtime.md §5）。
    const errors=[];
    document.querySelectorAll('[data-interaction]').forEach(el=>{
      try{ wireBlock(el); }catch(e){ errors.push(e); }
    });
    if(errors.length){
      const first=errors[0];
      throw errors.length>1
        ? new Error(first.message+'（另有 '+(errors.length-1)+' 处交互契约违规）')
        : first;
    }
  }

  // 先暴露再首接线：首接线抛契约错时 coursewareStudioWire 也必须已经可用。
  window.coursewareStudioWire = wireInteractions;
  // 脚本被放进 <head>（readyState 还是 loading）时首接线扫不到任何块，门禁会
  // 静默半开。补一次 DOMContentLoaded 重扫：once()/gesture 标记按节点记账，
  // 重复接线不会叠加监听。
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', wireInteractions);
  } else {
    wireInteractions();
  }
})();
