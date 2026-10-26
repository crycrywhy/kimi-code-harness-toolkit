// === LOCAL PATCH: markdown normalize (2026-10-15) v1.2.2 / v7.3.2 ===
// 目的：第二道工序——Markdown 结构规范器。
// 将第一道工序（newline_fix.js）清洗后的文本做 Markdown 结构归一化，
// 确保粘连块首、缺失表头分隔行等能够正常被渲染层解析。
//
// 铁律（v7.2 不变量）：
//   规范器永不净增 ** 或反引号字符（输出计数 ≤ 输入计数）。
//   规则 5（行内标记闭合）曾因在流式分片边界上补闭合而净增标记产生垃圾，现已取消补标记行为，改由只记审计日志。
//
// 表格治理（v7.3 升级）：
//   1. 规则 4（表格块前补空行）：基于跨分片累积完整行判定，仅当前一条完整行既非空行亦非表行时补入空行；
//      表行判定为 trim 后以 | 开头或结尾，或为分隔行。在下一个块首是 | 时，只要上一条完整行是表行/分隔行，一律不动。
//   2. 规则 B（表内空行删除）：表行之间若出现空行（如 | a | b |\n\n| c | d |）一律吃掉缝合，保持表格连续；
//      表结束后接正文/标题/列表的空行严格原样保留（反例门）。
//
// 门控与开关：
//   1. 模型门：默认仅对 /deepseek/i 生效，K3 与其它非武装模型逐字节原样透传；
//      支持 env KIMI_MD_FIX_MODELS（或 KIMI_NL_FIX_MODELS）覆盖，env KIMI_MD_FIX=0 一键总关。
//   2. 审计日志：env KIMI_MD_FIX_DEBUG=1 时向 /tmp/mdfix_audit.log 输出流水。
//   3. 状态隔离：与 __nlFix 共享 ST（如 this._nlState），支持流式实例隔离。

const __MD_DEFAULT_MODEL_RE = /deepseek/i;
const __MD_ST = Object.create(null);

function __mdIsArmed(model) {
  if (typeof process !== 'undefined' && process.env) {
    if (process.env.KIMI_MD_FIX === '0' || process.env.KIMI_MD_FIX === 'false') return false;
  }
  if (!model || typeof model !== 'string') return false;
  const m = model.trim();
  if (!m) return false;

  if (typeof process !== 'undefined' && process.env) {
    const raw = (process.env.KIMI_MD_FIX_MODELS || process.env.KIMI_NL_FIX_MODELS || '').trim();
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

  return __MD_DEFAULT_MODEL_RE.test(m);
}

function __mdLogAudit(channel, rule, beforeStr, afterStr) {
  try {
    if (typeof process === 'undefined' || !process.env) return;
    const dbg = process.env.KIMI_MD_FIX_DEBUG;
    if (dbg !== '1' && dbg !== 'true') return;
    const fsMod = (process.getBuiltinModule ? process.getBuiltinModule('fs') : null) ||
                  (typeof require !== 'undefined' ? require('fs') : null) ||
                  (typeof appendFileSync !== 'undefined' ? { appendFileSync } : null);
    if (!fsMod || !fsMod.appendFileSync) return;
    const ts = new Date().toISOString();
    const b20 = (beforeStr || '').slice(0, 20);
    const a20 = (afterStr || '').slice(0, 20);
    const line = `[${ts}] channel=${channel} rule=${rule} before=${JSON.stringify(b20)} after=${JSON.stringify(a20)}\n`;
    fsMod.appendFileSync('/tmp/mdfix_audit.log', line);
  } catch (_) {}
}

function __mdTableCols(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return 0;
  const parts = trimmed.split('|');
  return Math.max(0, parts.length - 2);
}

function __mdIsTableSep(line) {
  return /^\s*\|[\s:|-]+\|\s*$/.test(line);
}

function __mdIsTableRow(line) {
  if (typeof line !== 'string') return false;
  const t = line.trim();
  if (t === '') return false;
  if (__mdIsTableSep(t)) return true;
  return t.startsWith('|') || t.endsWith('|');
}

function __mdIsTableHead(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return false;
  if (__mdIsTableSep(trimmed)) return false;
  const parts = trimmed.split('|');
  if (parts.length - 2 < 2) return false;
  const cells = parts.slice(1, -1);
  for (const c of cells) {
    if (c.trim() === '') return false;
  }
  return cells.some(c => c.trim().length >= 2);
}

function __mdFix(channel, s, ST, model) {
  try {
    if (typeof s !== 'string' || s === '') return s;
    if (!__mdIsArmed(model)) return s;

    if (!ST) ST = __MD_ST;
    const st = ST[channel] || (ST[channel] = Object.create(null));
    if (!st.mdTail) st.mdTail = '';
    if (st.mdFenceOpen === undefined) st.mdFenceOpen = false;
    if (st.mdLastLine === undefined) st.mdLastLine = '';
    if (st.mdLineBuf === undefined) st.mdLineBuf = '';

    let res = s;

    // --- 规则 1 边界粘连（跨分片拼接）---
    if (st.mdTail.length > 0 && !/[\r\n]$/.test(st.mdTail)) {
      const lastChar = st.mdTail[st.mdTail.length - 1];
      if (/[。！？：；…）)】》」』”’"'`%\]\)]/.test(lastChar)) {
        if (/^#{1,6}[ \t]+\S/.test(res)) {
          __mdLogAudit(channel, "rule1:boundary_heading", lastChar + res.slice(0, 10), "\\n\\n" + res.slice(0, 10));
          res = '\n\n' + res;
        } else if (/^\|[^\n|]+\|.*\|/.test(res)) {
          __mdLogAudit(channel, "rule1:boundary_table", lastChar + res.slice(0, 10), "\\n\\n" + res.slice(0, 10));
          res = '\n\n' + res;
        } else if (/^-[ \t]+\S/.test(res) || /^\d+[.)][ \t]+\S/.test(res)) {
          __mdLogAudit(channel, "rule1:boundary_list", lastChar + res.slice(0, 10), "\\n" + res.slice(0, 10));
          res = '\n' + res;
        } else if (/^`{3,}/.test(res) || /^!\[/.test(res)) {
          __mdLogAudit(channel, "rule1:boundary_fence_image", lastChar + res.slice(0, 10), "\\n\\n" + res.slice(0, 10));
          res = '\n\n' + res;
        }
      }
    }

    // --- 规则 2 标题被换行劈开 ---
    const beforeR2 = res;
    res = res.replace(/(^|\n)(#{1,6})[ \t]*\n(?:\s*\n)*(#{1,6}[ \t]+\S)/g, '$1$3');
    res = res.replace(/(^|\n)(#{1,6})[ \t]*\n(?:\s*\n)*([^\s#|>`\-*+~].*)/g, '$1$2 $3');
    if (beforeR2 !== res) {
      __mdLogAudit(channel, "rule2:heading_split", beforeR2, res);
    }

    // --- 规则 1 分片内紧贴正文的块首粘连修复 ---
    const beforeR1 = res;
    res = res.replace(/([。！？：；…）)】》」』”’"'`%\]\)]|\.png|\.jpg|\.svg|\.jpeg|\.webp)[ \t]*(#{1,6}[ \t]+\S)/g, '$1\n\n$2');
    res = res.replace(/^(#{1,6}[ \t]+[^\n|]+?)[ \t]*(\|[^\n|]+\|.*\|[ \t]*)$/gm, '$1\n\n$2');
    res = res.replace(/([。！？：；…）)】》」』”’"'`%\]\)])[ \t]*(\|[^\n|]+\|.*\|[ \t]*)$/gm, '$1\n\n$2');
    res = res.replace(/([。！？：；…）)】》」』”’"'`%\]\)]|\.png|\.jpg|\.svg|\.jpeg|\.webp)[ \t]*(-[ \t]+\S)/g, '$1\n$2');
    res = res.replace(/([。！？：；…）)】》」』”’"'`%\]\)]|\.png|\.jpg|\.svg|\.jpeg|\.webp)[ \t]*(\d+[.)][ \t]+\S)/g, '$1\n$2');
    res = res.replace(/([。！？：；…）)】》」』”’"'`%\]\)]|\.png|\.jpg|\.svg|\.jpeg|\.webp)[ \t]*(```|!\[)/g, '$1\n\n$2');
    if (beforeR1 !== res) {
      __mdLogAudit(channel, "rule1:adhesion_split", beforeR1, res);
    }

    // --- 逐行规则 ---
    const lines = res.split('\n');
    const outLines = [];
    let inFence = st.mdFenceOpen || false;
    let prevLine = st.mdLastLine;

    for (let i = 0; i < lines.length; i++) {
      let line = lines[i];
      const trimmed = line.trim();
      const isLastSegment = (i === lines.length - 1);
      const currentFull = (i === 0 ? (st.mdLineBuf + line) : line);

      // 围栏跟踪
      if (/^`{3,}/.test(trimmed) || /^~{3,}/.test(trimmed)) {
        inFence = !inFence;
        outLines.push(line);
        if (!isLastSegment) {
          prevLine = currentFull;
          st.mdLineBuf = '';
        } else {
          st.mdLineBuf = currentFull;
        }
        continue;
      }
      if (inFence) {
        outLines.push(line);
        if (!isLastSegment) {
          prevLine = currentFull;
          st.mdLineBuf = '';
        } else {
          st.mdLineBuf = currentFull;
        }
        continue;
      }

      // --- 规则 4 & 规则 B：表格边界与表内空行处理（v7.3.2 升级）---
      if (__mdIsTableRow(currentFull)) {
        // (B) 兜底：若前序是空行且更前一条是表行，吃掉表内空行缝合表格（保留单换行）
        const isRealBlankLine = (i > 1) || (i === 1 && (outLines.length > 1 || /[\r\n]$/.test(st.mdTail)));
        if (isRealBlankLine && outLines.length > 0 && outLines[outLines.length - 1].trim() === '') {
          let j = outLines.length - 1;
          while (j >= 0 && outLines[j].trim() === '') j--;
          const beforeBlank = j >= 0 ? outLines[j] : prevLine;
          if (__mdIsTableRow(beforeBlank)) {
            while (outLines.length > 0 && outLines[outLines.length - 1].trim() === '') {
              outLines.pop();
            }
            __mdLogAudit(channel, "ruleB:table_internal_blank_eaten", beforeBlank, currentFull);
          }
        }

        // (A) 仅当前一条完整行既非空行亦非表行时，补入空行（保住 GFM 表格独立成块）
        // 在下一个块首是 | 时，只要上一条完整行是表行/分隔行，一律不动
        let effPrev = prevLine.trim();
        if (outLines.length > 0) {
          effPrev = outLines[outLines.length - 1].trim();
        }
        if (effPrev !== '' && !__mdIsTableRow(effPrev)) {
          __mdLogAudit(channel, "rule4:table_blank_line_before", effPrev, effPrev + "\n\n" + line);
          outLines.push('');
        }
      }

      // --- 规则 5：行内标记闭合审计（v7.2 取消补标记行为，只记审计日志，确保永不净增 ** 或反引号）---
      const strippedCode = line.replace(/```.*?```/g, '').replace(/```/g, '');
      const backtickCount = (strippedCode.match(/`/g) || []).length;
      if (backtickCount % 2 === 1) {
        __mdLogAudit(channel, "rule5:unclosed_backtick_audit", line, "audit_only");
      }
      if (!/^\s*(\*\s*){3,}\s*$/.test(line)) {
        const starCount = (line.match(/\*\*/g) || []).length;
        if (starCount % 2 === 1) {
          __mdLogAudit(channel, "rule5:unclosed_bold_audit", line, "audit_only");
        }
      }

      // --- 规则 7：CJK 间注入空格（中文正文内单空格删除，代码/表格单元格/英文/数字不动）---
      if (!__mdIsTableRow(currentFull)) {
        const beforeR7 = line;
        const parts = line.split(/(`[^`]*`)/);
        for (let p = 0; p < parts.length; p += 2) {
          let sub = parts[p];
          while (true) {
            const next = sub.replace(/([\u4e00-\u9fff]) ([\u4e00-\u9fff])/g, '$1$2');
            if (next === sub) break;
            sub = next;
          }
          parts[p] = sub;
        }
        line = parts.join('');
        if (beforeR7 !== line) {
          __mdLogAudit(channel, "rule7:cjk_space_remove", beforeR7, line);
        }
      }

      outLines.push(line);

      if (!isLastSegment) {
        prevLine = currentFull;
        st.mdLineBuf = '';
      } else {
        st.mdLineBuf = currentFull;
      }

      // --- 规则 3：表格块缺分隔行 ---
      if (__mdIsTableHead(currentFull)) {
        const cols = __mdTableCols(currentFull);
        if (i + 1 < lines.length) {
          const nextLine = lines[i + 1];
          const nextTrimmed = nextLine.trim();
          if (nextTrimmed.startsWith('|') && nextTrimmed.endsWith('|')) {
            if (!__mdIsTableSep(nextTrimmed)) {
              const nextCols = __mdTableCols(nextTrimmed);
              if (nextCols === cols) {
                const sep = '|' + '---|'.repeat(cols);
                __mdLogAudit(channel, "rule3:insert_table_separator", currentFull, currentFull + "\\n" + sep);
                outLines.push(sep);
              } else {
                __mdLogAudit(channel, "rule3:malformed_table_cols_mismatch", currentFull, nextLine);
              }
            }
          }
        }
      }
    }

    // 规则 6 审计：围栏未闭合
    if (inFence) {
      __mdLogAudit(channel, "rule6:fence_unclosed", res.slice(-30), "audit_only");
    }

    st.mdFenceOpen = inFence;
    st.mdLastLine = prevLine;
    st.mdTail = (st.mdTail + res).slice(-20);

    return outLines.join('\n');
  } catch (e) {
    return s;
  }
}
// === END LOCAL PATCH: markdown normalize ===

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { __mdFix, __mdIsArmed, __mdTableCols, __mdIsTableSep, __mdIsTableRow, __mdIsTableHead };
}
