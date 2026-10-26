// === LOCAL PATCH: stream newline fix (2026-10-15) v7.3.1 ===
// 目的：吃掉**上游流式载荷里的噪声换行**，并（v5 起）吃掉噪声**空格**。
// v7.1（2026-10-15）：
//  1. 【核心】表内撕裂修复（X:table-tear）：吃掉"表行内部"的注入换行。
//     当左行以 | 起头，若未完工（未以 | 闭合、少于 2 列、或分片内看不见后续字符 fns===null），且断点右侧非块首 ⇒ 判吃。
//     配合 A2_TABLE_GUARD 紧贴收紧门，彻底修复 0501.sse 中单元格间注入换行导致的表格撕裂缺陷。
// v7（2026-10-15）：
//  1. 【核心】模型门 __nlIsArmed(model)：默认只对 /deepseek/i 生效，K3 与其它模型逐字节原样透传；
//     支持 env KIMI_NL_FIX_MODELS 覆盖，env KIMI_NL_FIX=0 一键总关。
//  2. 块首判留提前：将 if (bs !== null) 移至 if (st.hot) 之前，彻底解决热态误吃标题/表格/列表/围栏前合法空行导致无法渲染的缺陷。
//  3. 噪声计数剔除合法块首：lead 定义仅统计以 \n\n 开头且后接非块首符号（跳过空白首字符排除 #/|/>/围栏/-/*/+/[0-9]+[.)]/!）的分片，干净流不再自我点着 hot。
//  4. 状态按请求隔离：支持通过调用点传入 ST（例如 this._nlState）实现单流独立状态仓，避免跨请求常驻热态。
//  5. 调试日志落盘：env KIMI_NL_FIX_DEBUG=1 时向 /tmp/nlfix_models.log 输出时间戳、channel、model、是否武装。
//
// ===== 上游噪声的三种形态（2026-10-14 深夜实测）=====
//  形态①「分片头 \n\n」：v2/v3/v4 已治（raw SSE 与 wire 逐字相同，853 分片里 357 个以 \n\n 开头）。
//  形态②「分片头单 \n」：**v5 新增**。/tmp/zen_tap/0654.sse 的 content 通道 460 分片里 186 个以单个 \n 开头，
//        分片长度中位 3 字，拼出正文 1469 字、换行密度 **35.26/百字**（健康 0.95–2.02）。
//  形态③「分片头空格」：**v5 新增**。/tmp/zen_tap/0652.sse content 通道 491 分片里 34 个以空格开头。
//        实测确认：客户端对 ②③ 一字不改（wire step 32/2、33/2 的 part 数与密度与 raw 完全一致）⇒ 只能客户端归一。
//
// ===== 判据 v4（保留，一字未改）=====
// 状态字段（每通道一份）：
//   ch    : 通道名（'text' / 'think'），决定英文句末规则是否生效
//   tail  : 本通道已输出文本的最后 12 个字符（跨分片滚动；判 pre / rPre / cb 用）
//   prev  : 上一个输出字符（tail 为空时兜底）
//   line  : 当前行文本（最后一个"已输出"的 "\n" 之后，上限 1000 字）
//   stars : 当前行里 "**" 的出现次数（判"加粗开着/已闭合"）
//   seen  : 本通道累计遇到的"结构性撕裂"次数（表行撕裂 / 加粗撕裂）——G 的迟滞门用
//   win   : 最近 12 个非空分片是否为噪声前导（1/0）——v7 剔除合法块首
//   hot   : 热模式（win 满 12 且其中 ≥4 个带前缀且比例 ≥15%；或最近 4 个里 ≥3 个带前缀）
//
// 规则表（判定一个 "\n\n"，按顺序，先到先得）：
//   A2 表格结束   左行是完整表格行 且 断点后（跳空格）不是 "|"  ⇒ 留
//   A  表行撕裂   左行是完整表格行 且 断点后（跳空格）紧跟 "|"  ⇒ 吃
//   B  符号串劈开 前导是标记符号、run=1、断点后紧邻同类符号且 run=1 ⇒ 吃
//   C  悬空单标记 前导是标记符号且 run=1  ⇒ 吃
//   D  续接标点   断点后（跳空格）是 ，、；：。！？）】》」』’”  ⇒ 吃
//   G  加粗撕裂   前导是 "**"（run≥2）且 本行 "**" 计数为奇数（加粗开着）⇒ 吃（迟滞：seen>1 才吃）
//   I  块起点     断点后（跳空格）是 # | > ``` 或 "[-*+] " 或 "N. " 或 "![..." ⇒ 留（v7 提至 hot 之前）
//   HOT 热模式    A2 与 A/B/C/D/I 照常；此外只留 E、F、以及"左行是表格行/围栏行"
//   E  句末铁律   前导 ∈ 。！？!?…（+ think 通道的英文句点）⇒ 留
//   F  闭合围栏   前导是 ≥3 个反引号 且 整行就是围栏 ⇒ 留
//   K5 加粗 run   前导是 "**"（run≥2）且本行 "**" 计数为偶数（已闭合）或首见 ⇒ 留
//   H  句末右括号 前导是 ）且其前一字符 ∈ 句末 ⇒ 留
//   J  完整块行   左行是完整块行（标题/列表项/表格行/独立加粗标签/围栏/独立括号行）⇒ 留
//   K  其余 ⇒ 吃
//
// ===== 判据 v5 新增（只对"孤立 \n"与"分片头空格"生效；上面的 v4 一个字未改）=====
// 命名：**孤立 \n** = 这个 \n 的紧邻原始字符（本分片内，或跨分片的上一个原始字符）不是 \n，
//   且紧邻的下一个原始字符也不是 \n。分片末尾看不到下一字符时**扣住**不发、等下一分片首字符再定夺——
//   但**只在"碎分片（≤8 字）或噪声流"里扣**；健康流的大分片按 v4 直接输出（避免状态跨消息残留；
//   __nlFix 生产调用传入 this._nlState 隔离，外部 2 参调用降级至 __NL_ST 兼容）。
//
// 【规则 N】孤立 \n —— 冷模式（未进入噪声模式），两条并列、任一成立即吃：
//   N-a 断在词中：断点前一**输出**字符与后一原始字符两侧同为 CJK 表意字。
//   N-b 碎分片边界：该 \n 落在分片头/尾、本分片 ≤ __NL_TINY(8) 字、下一行以**非 ASCII 字母数字**起头、
//       且断点前一字符不是句末/收尾标点（__NL_EDGE_STOP）、且下一行不是 markdown 块起点。
//
// 【规则 N-hot】孤立 \n —— 噪声模式（__nlNoise(st) 为真时反转默认）：
//   默认**吃**，只在**下一行是 markdown 块起点**时留（__nlBlockStart：标题 # / 表行 | / 引用 > /
//   围栏 ``` / 列表 "[-*+] " / 有序 "N. " / 图片 "![..."）。
//
// 【规则 S】分片头空格 —— **默认关闭**（__NL_SPACE_FIX = false）
//
// ===== 判据 v6 新增（只加一道"收紧门"，v4/v5 的既有判定一个字未改）=====
// 【MIN = J_CLEAN + E_ID_GUARD + A2_TABLE_GUARD + I_PIPE_GUARD】四条"吃"规则（纯判定层）
// ===== v6′ 修正：**关掉 COLLAPSE_SAFE**（__NL_COLLAPSE = false 默认）=====
const __NL_MARK = new Set(['*', '`', '|', '#', '>', '~', '_', '-', '=']);
const __NL_STRONG = new Set('。！？!?…'.split(''));
const __NL_CONT = new Set('，、；：。！？）】》」』’”'.split(''));
const __NL_DOT_END_CH = new Set(['think']);   // 英文句点算句末的通道（正文以中文为主，`.` 更可能是路径/版本号）
const __NL_RE_HEADING = /^#{1,6}\s+\S/;
const __NL_RE_LIST = /^([-*+]|\d+[.)])\s+\S/;
const __NL_RE_TABLE = /^\|.*\|$/;
const __NL_RE_BOLDLABEL = /^\*\*[^*]+\*\*[：:]?$/;
const __NL_RE_FENCE = /^`{3,}/;
const __NL_RE_PAREN = /^（[^（）]{1,30}）[。！？]?$/;
const __NL_LINE_CAP = 1000, __NL_TAIL_CAP = 12;
const __NL_C_HOT_ONLY = false;   // 悬空单标记(C)只在热模式（已声明在噪）吃
const __NL_V2END = new Set(['。','！','？','!','?','；',';','：',':','…','—','）','】','》','」','』','\"',"'",'”','`','*',']','}','|']);
const __NL_KEEP_V2_END = false;  // 冷模式下额外保留 v2 的 END 集后的换行——门 A 保守档
// ---- v5 新增常量 ----
const __NL_ISO_DENS = 3.0;       // 噪声模式：窗口内孤立 \n 密度下限（每 100 原始字符）
const __NL_RW_CAP = 240;         // 原始字符滑动窗口长度
const __NL_RW_MIN = 24;          // 窗口至少这么长才敢判噪声
const __NL_ISO_MIN = 6;          // 窗口内孤立 \n 的绝对条数下限（挡住短健康流）
const __NL_LENS_MIN = 6;         // 至少这么多分片历史才敢判噪声
const __NL_TINY = 8;             // "碎分片"阈值（噪声模式与边界吃 \n 都要求碎分片）
const __NL_STAT_CAP = 24;        // 监测环长度（分片长度 / 分片头噪声前缀）
const __NL_AGGRESSIVE = false;   // 激进档：噪声模式下吃 ASCII 侧"疑似劈词"（空格 / 孤立 \n）
const __NL_SPACE_FIX = false;    // 规则 S 总开关（默认 false = **不动任何空格**；理由见文件头）
const __NL_SPACE_LEAD = 0.12;    // "空格噪声模式"阈值：近期分片以空格/单 \n 开头的占比
const __NL_SPACE_MINN = 6;       // 判定空格噪声至少要有这么多分片历史
const __NL_RE_LOAL = /^[a-z0-9]$/;   // ASCII 小写字母或数字
const __NL_EDGE_STOP = new Set(('。！？…；：!?;:）】》」』”’)]}"\'').split(''));   // 边界吃 \n 时遇到句末/收尾标点就收手
const __NL_ST = Object.create(null);   // 集成适配①：不传 ST 时的每通道状态仓
// ---- v6 新增常量 ----
const __NL_TIGHTEN = true;        // MIN 四条"吃"规则总开关（false ⇒ 退回 v5 判定）
const __NL_COLLAPSE = false;      // v6′ 关（默认）——见文件头 v6′ 节：它专挑"合法结构断行"压，52/52 全误伤
const __NL_J_HEADING_MAX = 80;    // J_CLEAN：标题行超这个长度即视为 run-on
const __NL_RE_J_STOP = /[。！？；]/;   // J_CLEAN：左行含句末标点即视为 run-on
const __NL_A2_SEG = 82;           // A2_TABLE_GUARD：分片内看不到行尾时，往后最多看这么多字符


// ===== v7 新增：模型武装门 / 调试日志 / 噪声前导判据 =====
const __NL_DEFAULT_MODEL_RE = /deepseek/i;

function __nlIsArmed(model) {
  if (typeof process !== 'undefined' && process.env) {
    if (process.env.KIMI_NL_FIX === '0' || process.env.KIMI_NL_FIX === 'false') return false;
  }
  if (!model || typeof model !== 'string') return false;
  const m = model.trim();
  if (!m) return false;

  if (typeof process !== 'undefined' && process.env && process.env.KIMI_NL_FIX_MODELS) {
    const raw = process.env.KIMI_NL_FIX_MODELS.trim();
    if (raw) {
      if (raw.startsWith('/') && raw.lastIndexOf('/') > 0) {
        try {
          const lastSlash = raw.lastIndexOf('/');
          const pattern = raw.slice(1, lastSlash);
          const flags = raw.slice(lastSlash + 1);
          return new RegExp(pattern, flags).test(m);
        } catch (_) {}
      }
      const list = raw.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
      const ml = m.toLowerCase();
      return list.some(item => ml.includes(item));
    }
  }

  return __NL_DEFAULT_MODEL_RE.test(m);
}

function __nlLogDebug(channel, model, armed) {
  try {
    if (typeof process === 'undefined' || !process.env) return;
    const dbg = process.env.KIMI_NL_FIX_DEBUG;
    if (dbg !== '1' && dbg !== 'true') return;
    const fsMod = (process.getBuiltinModule ? process.getBuiltinModule('fs') : null) || (typeof require !== 'undefined' ? require('fs') : null) || (typeof appendFileSync !== 'undefined' ? { appendFileSync } : null);
    if (!fsMod || !fsMod.appendFileSync) return;
    const ts = new Date().toISOString();
    const line = `[${ts}] channel=${channel} model=${JSON.stringify(model)} armed=${armed}\n`;
    fsMod.appendFileSync('/tmp/nlfix_models.log', line);
  } catch (_) {}
}

function __nlIsLeadNoise(s) {
  if (typeof s !== 'string' || !s.startsWith('\n\n')) return false;
  let k = 2;
  while (k < s.length && (s[k] === ' ' || s[k] === '\t')) k++;
  if (k >= s.length) return true; // \n\n 之后无内容或纯空白，仍属噪声前导
  const rest = s.slice(k);
  const c0 = rest[0];
  if (c0 === '#' || c0 === '|' || c0 === '>' || c0 === '!' || c0 === '-' || c0 === '*' || c0 === '+') return false;
  if (rest.startsWith('```') || rest.startsWith('~~~')) return false;
  if (/^[0-9]+[.)](\s|$)/.test(rest)) return false;
  return true;
}

function __nlLineKind(line) {
  if (__NL_RE_HEADING.test(line)) return 'heading';
  if (__NL_RE_LIST.test(line)) return 'list';
  if (__NL_RE_TABLE.test(line)) return 'table';
  if (__NL_RE_BOLDLABEL.test(line)) return 'boldlabel';
  if (__NL_RE_FENCE.test(line)) return 'fence';
  if (__NL_RE_PAREN.test(line)) return 'paren';
  return null;
}
function __nlBlockStart(s, j) {
  let k = j;
  while (k < s.length && (s[k] === ' ' || s[k] === '\t')) k++;
  if (k >= s.length) return null;
  const c = s[k];
  let run = 0; while (k + run < s.length && s[k + run] === c) run++;
  if (c === '`' && run >= 3) return 'fence';
  if (c === '#' || c === '|' || c === '>') return c;
  if (c === '!' && s[k + 1] === '[') return 'image';
  if ('-*+'.includes(c) && run === 1 && (s[k + 1] === ' ' || s[k + 1] === '\t')) return 'list';
  if (/[0-9]/.test(c)) {
    let m = 0; while (k + m < s.length && /[0-9]/.test(s[k + m])) m++;
    if ((s[k + m] === '.' || s[k + m] === ')') && (s[k + m + 1] === ' ' || s[k + m + 1] === '\t')) return 'olist';
  }
  return null;
}
function __nlNew(ch) {
  return { ch: ch || '', tail: '', prev: '\n', line: '', stars: 0, seen: 0, win: [], hot: false,
           rw: '', rawPrev: '\n', hold: false, lens: [], leads: [], inFence: false, holdTableEnd: false, tableRows: 0 };   // v5 追加字段
}

// 判定分片 s 里位置 i 的 "\n\n"：true = 保留，false = 吃掉；why 可选，回填 {rule, keep}
function __nlIsCompleteTable(line) {
  if (typeof line !== 'string') return false;
  const t = line.trim();
  return t.startsWith('|') && t.endsWith('|');
}

// 判定分片 s 里位置 i 的 "\n\n"：三值判决 'eat' / 'collapse' / 'keep'（v7.3.2）
function __nlKeep(st, s, i, why) {
  const T = st.tail;
  const pre = T.length ? T[T.length - 1] : st.prev;
  let rPre = 0;
  while (T.length - 1 - rPre >= 0 && T[T.length - 1 - rPre] === pre) rPre++;
  const cb = T.length - 1 - rPre >= 0 ? T[T.length - 1 - rPre] : '\n';
  const nx = i + 2 < s.length ? s[i + 2] : null;
  let rNx = 0; if (nx !== null) while (s[i + 2 + rNx] === nx) rNx++;
  let k = i + 2; while (k < s.length && (s[k] === ' ' || s[k] === '\t')) k++;
  const fns = k < s.length ? s[k] : null;
  const bs = __nlBlockStart(s, i + 2);
  const lk = __nlLineKind(st.line);
  const isMark = __NL_MARK.has(pre);
  const set = (r, act) => { if (why) { why.rule = r; why.act = act; } return act; };
  const dotEnd = __NL_DOT_END_CH.has(st.ch) && pre === '.' && /[A-Za-z]/.test(cb)
    && (nx === null || !/[a-z0-9.]/.test(nx));

  // 流首空行噪声
  if (st.tail === '' && st.prev === '\n') {
    return set('lead_noise_eat', 'eat');
  }

  const leftStartsPipe = st.line.replace(/^[ \t]+/, '').startsWith('|');
  const isLeftComplete = __nlIsCompleteTable(st.line);

  // 【情形 1】左行是表行碎片（不完整）且右侧是 | ⇒ eat（撕裂修复）
  if (leftStartsPipe && !isLeftComplete && (bs === null || bs === '|')) {
    return set('X:table-tear', 'eat');
  }

  // 【情形 1】左行是完整表行或分隔行
  if (isLeftComplete) {
    if (fns === '|') {
      // 表块内部行间只留一个换行（collapse）
      return set('table_internal', 'collapse');
    }
    if (fns === null) {
      if ((st.tableRows || 0) < 2) {
        st.holdTableEnd = true;
        return set('table_end_hold', 'collapse');
      }
      return set('table_end_keep', 'keep');
    }
    // A2_TABLE_GUARD: 表行被劈（断点后到行尾仍含 |）⇒ eat
    const nl = s.indexOf('\n', i + 2);
    const seg = s.slice(i + 2, nl < 0 ? s.length : nl);
    if (seg.indexOf('|') >= 0) {
      return set('A2_TABLE_GUARD', 'eat');
    }
    // 表行后接普通正文/标题/列表 ⇒ keep（表格结束的合法空行）
    return set('A2:table-end', 'keep');
  }

  // 【情形 2】右侧是表行（| 起头），但左行不是表行 ⇒ keep（段落/标题/列表之后接表格的合法空行）
  if (fns === '|') {
    return set('before_table_keep', 'keep');
  }

  // 【情形 3】右行是块首（# / > / 列表 - * + / 有序 1. / 围栏 ``` / 图片 ![）⇒ keep
  if (bs !== null) {
    return set('I:block-start:' + bs, 'keep');
  }

  // 标点续接（规则 D：断点后紧跟 ，、；：。！？ 等续接标点 ⇒ eat）
  if (fns !== null && __NL_CONT.has(fns)) {
    return set('D:cont-punct', 'eat');
  }

  // 符号串劈开（除 | 之外）
  if (isMark && rPre === 1 && nx === pre && rNx === 1 && pre !== '|') {
    return set('B:split', 'eat');
  }

  // E_ID_GUARD：字面省略号落在标识符里
  if (pre === '…' && __nlIsWord(cb) && __nlIsWord(fns)) {
    return set('E_ID_GUARD', 'eat');
  }

  // 加粗未闭合撕裂与加粗闭合保护
  const tornBold = (pre === '*' && rPre >= 2 && (st.stars & 1) === 1);
  if (tornBold) st.seen++;
  if (tornBold && st.seen > 1) {
    return set('G:bold-tear', 'eat');
  }
  const isBoldClose = (pre === '*' && rPre >= 2 && (st.stars & 1) === 0);
  if (isBoldClose) {
    if (__NL_STOP_PUNCT.has(cb) || lk !== null) return set('bold_close_keep', 'keep');
    return set('K5:bold-run', 'keep');
  }

  // 【情形 8】HOT 模式（重度噪声流）：只留强句末或完整表行/围栏
  if (st.hot) {
    return set('HOT', (__NL_STRONG.has(pre) || dotEnd || isLeftComplete || lk === 'fence') ? 'keep' : 'eat');
  }

  // 【情形 6】左字符是句末标点（。！？；：…）) 等）⇒ keep
  if (__NL_STOP_PUNCT.has(pre) || dotEnd) {
    return set(dotEnd ? 'E:dot-end' : 'E:strong-end', 'keep');
  }

  // 完整块行保护（标题行、列表项、独立加粗标签、围栏行、独立括号行）
  if (lk !== null) {
    if (lk === 'heading' && (__NL_RE_J_STOP.test(st.line) || st.line.length > __NL_J_HEADING_MAX)) {
      return set('J_CLEAN', 'eat');
    }
    return set('J:block-line:' + lk, 'keep');
  }

  // 【情形 5】词中劈裂：左字符与右字符都是词字符（排除 * 格式符，* 由 G:bold-tear 迟滞门管辖）
  const isLeftWord = __nlIsWord(pre) || pre === '_' || pre === '-' || pre === '…' || pre === '、' || pre === '，';
  const isRightWord = __nlIsWord(fns) || fns === '_' || fns === '-';
  if (!st.inFence && isLeftWord && fns !== null && isRightWord) {
    return set('word_split_eat', 'eat');
  }

  // 加粗未闭合撕裂兜底
  if (pre === '*' && rPre >= 2) {
    if ((st.stars & 1) === 1 && st.seen > 1) return set('G:bold-tear', 'eat');
    return set('K5:bold-run', 'keep');
  }



  // 【情形 7】不确定 ⇒ keep
  return set('default_keep', 'keep');
}
function __nlTighten(st, s, i, rule) {
  const T = st.tail;
  const pre = T.length ? T[T.length - 1] : st.prev;
  const nextNS = s.slice(i + 2).replace(/^[ \t]+/, '');
  const nx0 = nextNS[0] || '';   // 断点后（跳空白）首个字符；'' = 分片内看不到
  if (__NL_TIGHTEN) {
    // (a) E_ID_GUARD：字面省略号 "…" 落在标识符/文件名里（两侧都是词字符）⇒ 噪声
    if (rule[0] === 'E' && pre === '\u2026') {
      const cb1 = T.length > 1 ? T[T.length - 2] : st.prev;
      if (__nlIsWord(cb1) && __nlIsWord(nx0)) return 'eat';
    }
    // (b) A2_TABLE_GUARD：所谓"表格结束"实为同一张表被劈——断点后到行尾仍含 '|' ⇒ 吃
    if (rule.slice(0, 2) === 'A2') {
      const nl = s.indexOf('\n', i + 2);
      const seg = s.slice(i + 2, nl < 0 ? Math.min(s.length, i + __NL_A2_SEG) : nl);
      if (seg.indexOf('|') >= 0) return 'eat';
      if (st.hot && nl < 0 && seg.length <= 4 && !/[。！？#]/.test(seg)) return 'eat';
    }
    // (c) I_PIPE_GUARD：判留只因断点后紧跟 '|'，而左行本身以 '|' 起头（表行碎片）⇒ 吃
    if (rule[0] === 'I' && nx0 === '|' && st.line.trim().charAt(0) === '|') return 'eat';
    // (d) J_CLEAN：左"行"已被前一次吃掉 \n\n 撑成 run-on（含句末标点 / 标题超长）⇒ "完整块行"不成立
    if (rule[0] === 'J') {
      const lk = __nlLineKind(st.line);
      if ((lk === 'heading' || lk === 'list')
          && (__NL_RE_J_STOP.test(st.line) || (lk === 'heading' && st.line.length > __NL_J_HEADING_MAX)))
        return 'eat';
    }
  }
  // (e) COLLAPSE_SAFE：整条流已判定在噪 ⇒ 把"判留"的 \n\n 降级为单 \n（排除围栏/表以保代码块与表）
  //     **v6′ 起默认关**（__NL_COLLAPSE=false）：它压的正是"吃过噪声后剩下的"合法结构断行。
  if (__NL_COLLAPSE && (st.hot || __nlNoise(st))) {
    const lkc = __nlLineKind(st.line);
    if (rule[0] !== 'F' && rule.slice(0, 2) !== 'A2' && lkc !== 'fence' && lkc !== 'table')
      return 'collapse';
  }
  return 'keep';
}

// ===== v5 新增：字符分类 / 原始窗口 / 噪声模式 / 两条新规则 =====
function __nlIsAsciiWord(c) {
  if (c === null || c === undefined) return false;
  const o = c.charCodeAt(0);
  return (o >= 48 && o <= 57) || (o >= 65 && o <= 90) || (o >= 97 && o <= 122);
}
function __nlIsCJK(c) {
  if (c === null || c === undefined) return false;
  const o = c.codePointAt(0);
  return (o >= 0x3400 && o <= 0x4DBF) || (o >= 0x4E00 && o <= 0x9FFF) || (o >= 0xF900 && o <= 0xFAFF);
}
function __nlIsWord(c) { return __nlIsAsciiWord(c) || __nlIsCJK(c); }
function __nlRawPush(st, c) {
  st.rawPrev = c;
  if (st.rw.length < __NL_RW_CAP) st.rw += c;
  else st.rw = st.rw.slice(1) + c;
}
function __nlNoise(st) {
  const n = st.rw.length;
  if (n < __NL_RW_MIN) return false;
  let iso = 0;
  for (let i = 0; i < n; i++) {
    if (st.rw[i] === '\n' && (i === 0 || st.rw[i - 1] !== '\n') && (i + 1 >= n || st.rw[i + 1] !== '\n')) iso++;
  }
  if (!(iso >= __NL_ISO_MIN && 100 * iso / n >= __NL_ISO_DENS)) return false;
  // 还要求近期分片是"碎分片"——否则英文代码块/表格里密集的合法换行会误触发
  const L = st.lens;
  if (L.length < __NL_LENS_MIN) return false;
  const k = L.length < 12 ? L.length : 12;
  const tail = L.slice(L.length - k).sort((a, b) => a - b);
  return tail[(k - 1) >> 1] <= __NL_TINY;
}
const __NL_STOP_PUNCT = new Set(['。', '！', '？', '；', '：', '…', '）', ')', '}', ']', "'", '"', '”', '’', '!', '?', ';', ':']);
const __NL_RIGHT_MARK = new Set(['*', '`', '|', '#', '>', '~', '_', '-', '=', '.', '/', ':', '+', '(', '[', '{', '!', '@', '$', '%', '^', '&']);

function __nlIsWordChar(c) {
  if (c === null || c === undefined) return false;
  return __nlIsWord(c) || c === '_' || c === '-' || c === '`' || c === '*';
}

function __nlIsWordOrMark(c) {
  if (c === null || c === undefined) return false;
  return __nlIsWordChar(c) || __NL_RIGHT_MARK.has(c);
}

// 孤立 \n：tail = \n 之后的原文；nx = tail 首字符（null = 看不到）
// v7.2：升级为词中劈裂判定（左侧词字符 + 右侧词/标记字符 + 右侧非块首 + 不在围栏内 ⇒ 吃）
function __nlKeepOne(st, pre, nx, tail, edge, tiny) {
  if (nx === null) return true;                       // 兜底：看不到下一个字符 ⇒ 不动
  let f = 0; while (f < tail.length && (tail[f] === ' ' || tail[f] === '\t')) f++;
  const fns = f < tail.length ? tail[f] : null;       // 跳空白后的第一个字符

  // 判留 1：当前在围栏内或当前行是围栏行 ⇒ 保留
  if (st.inFence || /^`{3,}/.test(st.line.trim()) || /^~{3,}/.test(st.line.trim())) {
    return true;
  }

  // 判留 2：右侧是块首（# / | / > / 围栏 / 列表 / 图片）⇒ 保留
  const bs = __nlBlockStart(tail, 0);
  if (bs !== null) {
    return true;
  }

  // 判留 3：左侧是句末标点（。！？；：…）等）或英文句点（think 通道）⇒ 保留
  if (__NL_STOP_PUNCT.has(pre) || (__NL_DOT_END_CH.has(st.ch) && pre === '.')) {
    return true;
  }

  // 判留 4：左侧是完整块行（标题、独立加粗标签、独立括号行、围栏行）⇒ 保留
  const lk = __nlLineKind(st.line);
  if (lk === "heading" || lk === "boldlabel" || lk === "paren" || lk === "fence") {
    return true;
  }
  // 左侧包含句末标点加闭合加粗（如 。**）⇒ 属句末，保留
  if (st.tail && /[。！？；：…!?]\*\*$/.test(st.tail)) {
    return true;
  }

  // 判吃（词中劈裂 ⇒ 吃）：
  // 断点左侧字符是词字符（CJK / 字母 / 数字 / _ / - / 反引号 / *）
  // 且右侧跳过空白后的首字符也是词字符或标记字符
  // 且右侧不是块首（已在上文通过 bs === null 检查）
  // 且当前不在围栏内（已在上文通过 !st.inFence 检查）
  if (__nlIsWordChar(pre) && fns !== null && __nlIsWordOrMark(fns)) {
    return false;
  }

  // 噪声模式（已识别大量噪声换行）：默认吃，仅上文判留的保留
  if (__nlNoise(st)) {
    return false;
  }

  // 碎分片边界（N-b）：边界 + 碎分片 + 下一行以非 ASCII 起头
  if (edge && tiny && !__NL_EDGE_STOP.has(pre) && fns !== null && !/[A-Za-z0-9]/.test(fns)) {
    return false;
  }

  return true;
}
// "空格噪声模式"：近期分片里以空格（或单 \n）开头的占比够高，且分片是碎分片
function __nlSpaceNoise(st) {
  const L = st.leads, F = st.lens;
  if (L.length < __NL_SPACE_MINN) return false;
  const k = L.length < __NL_STAT_CAP ? L.length : __NL_STAT_CAP;
  let n = 0; for (let i = L.length - k; i < L.length; i++) n += L[i];
  if (n / k < __NL_SPACE_LEAD) return false;
  const lens = F.slice(F.length - k).sort((a, b) => a - b);
  return lens[(k - 1) >> 1] <= __NL_TINY;
}
// 规则 S：只在分片头调用（s 就是本分片）。默认档 __NL_SPACE_FIX=false ⇒ 直接留。
function __nlKeepSpace(st, s) {
  if (!__NL_SPACE_FIX) return true;
  const pre = st.tail.length ? st.tail[st.tail.length - 1] : st.prev;
  let j = 0; while (j < s.length && (s[j] === ' ' || s[j] === '\t')) j++;
  const nx = j < s.length ? s[j] : null;
  if (nx === null) return true;
  if (__nlIsCJK(pre) && __nlIsCJK(nx) && (__NL_AGGRESSIVE || __nlSpaceNoise(st))) return false;   // CJK 侧：凭空空格 ⇒ 吃
  if (__NL_AGGRESSIVE && __nlNoise(st) && __NL_RE_LOAL.test(pre) && __NL_RE_LOAL.test(nx)) return false;
  return true;
}
// 只读监测出口（不参与判定）；返回 null 表示该通道还没状态
function __nlStats(channel, ST) {
  const st = (ST || __NL_ST)[channel];
  if (!st) return null;
  const rw = st.rw, n = rw.length;
  let iso = 0;
  for (let i = 0; i < n; i++) {
    if (rw[i] === '\n' && (i === 0 || rw[i - 1] !== '\n') && (i + 1 >= n || rw[i + 1] !== '\n')) iso++;
  }
  const lens = st.lens.slice().sort((a, b) => a - b);
  const med = lens.length ? lens[(lens.length - 1) >> 1] : 0;
  const leadRatio = st.leads.length ? st.leads.reduce((a, b) => a + b, 0) / st.leads.length : 0;
  return { n: st.leads.length, medLen: med, leadRatio: leadRatio,
           isoDens: n ? 100 * iso / n : 0, noise: __nlNoise(st) };
}

function __nlFix(channel, s, ST, model) {
  try {
    if (typeof s !== 'string' || s === '') return s;
    const armed = __nlIsArmed(model);
    __nlLogDebug(channel, model, armed);
    if (!armed) return s;   // v7 模型门：非武装模型逐字节原样透传，不污染状态仓

    if (!ST) ST = __NL_ST;   // 集成适配①：未传 ST 时降级至模块级仓，调用点传 this._nlState 时独立按流隔离
    const st = ST[channel] || (ST[channel] = __nlNew(channel));
    const lead = __nlIsLeadNoise(s);   // v7 噪声前导：排除合法块首，防止干净流点着 hot
    // 监测环：分片长度 + 分片头噪声前缀（单 \n 或空格）
    st.lens.push(s.length); if (st.lens.length > __NL_STAT_CAP) st.lens.shift();
    st.leads.push((s[0] === ' ' || (s[0] === '\n' && !s.startsWith('\n\n'))) ? 1 : 0);
    if (st.leads.length > __NL_STAT_CAP) st.leads.shift();
    let out = '', i = 0;
    // ⓪-A 结算上一分片表行末尾扣住的换行（若下一分片非表行，补出第 2 个换行）
    if (st.holdTableEnd) {
      st.holdTableEnd = false;
      let f = 0; while (f < s.length && (s[f] === ' ' || s[f] === '\t')) f++;
      const fns = f < s.length ? s[f] : null;
      if (fns !== '|') {
        out += '\n';
        st.tail = (st.tail + '\n').slice(-__NL_TAIL_CAP);
        st.line = ''; st.stars = 0; st.prev = '\n';
      }
    }
    // ⓪-B 结算上一分片末尾被扣住的孤立 \n（那时看不到后续字符）
    if (st.hold) {
      st.hold = false;
      const nx = s.length ? s[0] : null;
      const pre = st.tail.length ? st.tail[st.tail.length - 1] : st.prev;
      // 只在"下一分片仍是碎分片"时才把扣住的 \n 放出来：否则下一分片多半是**下一条消息**，
      // 放出来会往新消息头部塞一个换行。（nx === '\n' 时仍保持 v4 的"两个换行"语义。）
      if (nx !== null && (nx === '\n' || __nlKeepOne(st, pre, nx, s, true, s.length <= __NL_TINY))) {
        out += '\n';
        if (/^`{3,}/.test(st.line.trim()) || /^~{3,}/.test(st.line.trim())) {
          st.inFence = !st.inFence;
        }
        st.tail = (st.tail + '\n').slice(-__NL_TAIL_CAP);
        st.line = ''; st.stars = 0; st.prev = '\n';
      }
    }
    while (i < s.length) {
      if (s.startsWith('\n\n', i)) {
        if (__nlIsCompleteTable(st.line)) {
          st.tableRows = (st.tableRows || 0) + 1;
        }
        const why = {};
        const act = __nlKeep(st, s, i, why);
        if (act === 'eat') {
          i += 2; __nlRawPush(st, '\n'); __nlRawPush(st, '\n'); continue;
        }
        if (act === 'collapse') {                                // 压成单个 \n
          out += '\n'; __nlRawPush(st, '\n');
          st.tail = (st.tail + '\n').slice(-__NL_TAIL_CAP);
          st.line = ''; st.stars = 0; st.prev = '\n';
          i += 2; continue;
        }
        // act === 'keep'：照 v5 原样输出这两个字符（落到下面逐字符处理）
      }
      const c = s[i];
      if (c === '\n') {
        const rawPrevNl = i > 0 ? s[i - 1] === '\n' : st.rawPrev === '\n';
        const rawNextNl = i + 1 < s.length ? s[i + 1] === '\n' : null;   // null = 分片到头
        if (!rawPrevNl && rawNextNl !== true) {
          // 分片末尾的孤立 \n（看不到后一个字符）：只在"碎分片 / 噪声流"里才扣住等下一分片，
          // 健康流（大分片、非噪声）一律按 v4 直接输出——避免状态跨消息残留。
          if (rawNextNl === null) {
            const pre = st.tail.length ? st.tail[st.tail.length - 1] : st.prev;
            if (s.length <= __NL_TINY || __nlNoise(st) || __nlIsWordChar(pre)) {
              st.hold = true; __nlRawPush(st, '\n'); i += 1; continue;
            }
          } else {
            const pre = st.tail.length ? st.tail[st.tail.length - 1] : st.prev;
            if (!__nlKeepOne(st, pre, s[i + 1], s.slice(i + 1), i === 0 || i === s.length - 1, s.length <= __NL_TINY)) {
              __nlRawPush(st, '\n'); i += 1; continue;
            }
          }
        }
      } else if (i === 0 && (c === ' ' || c === '\t')) {
        if (!__nlKeepSpace(st, s)) {
          while (i < s.length && (s[i] === ' ' || s[i] === '\t')) { __nlRawPush(st, ' '); i += 1; }
          continue;
        }
      }
      out += c;
      __nlRawPush(st, c);
      st.tail = (st.tail + c).slice(-__NL_TAIL_CAP);
      if (c === '\n') {
        if (/^`{3,}/.test(st.line.trim()) || /^~{3,}/.test(st.line.trim())) {
          st.inFence = !st.inFence;
        }
        if (__nlIsCompleteTable(st.line)) {
          st.tableRows = (st.tableRows || 0) + 1;
        } else if (st.line.trim() !== '') {
          st.tableRows = 0;
        }
        st.line = ''; st.stars = 0;
      }
      else {
        if (st.line.length < __NL_LINE_CAP) st.line += c;
        if (c === '*' && st.tail.length > 1 && st.tail[st.tail.length - 2] === '*') st.stars++;
      }
      st.prev = c; i++;
    }
    st.win.push(lead ? 1 : 0);
    if (st.win.length > 12) st.win.shift();
    const n = st.win.reduce((a, b) => a + b, 0);
    if (st.win.length >= 12) st.hot = n >= 4 && n / st.win.length >= 0.15;
    if (!st.hot && st.win.length >= 4) st.hot = st.win.slice(-4).reduce((a, b) => a + b, 0) >= 3;
    return out;
  } catch (e) { return s; }
}
// === END LOCAL PATCH: stream newline fix ===
