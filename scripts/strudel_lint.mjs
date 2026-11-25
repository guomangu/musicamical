// v4.0 strudel_lint.mjs — linter Strudel : en cas d'erreur JS/mininotation, ne JAMAIS renvoyer
// une stacktrace Node de 800-1500 tokens au LLM, mais <= 2 lignes « ERR L<l>:<c> <msg>. Fix: <hint>. »
const OPEN = { '(': ')', '[': ']', '{': '}' };
const CLOSE = { ')': '(', ']': '[', '}': '{' };

export function lintSource(src) {
  const errors = [];
  const lines = String(src).split('\n');
  const stack = [];
  let inStr = null;
  let inTmpl = false;
  let sawMeta = false;
  for (let li = 0; li < lines.length && errors.length < 2; li++) {
    const ln = lines[li];
    if (/^\s*\/\/\s*@(strudel-meta|meta)\b/.test(ln)) { sawMeta = true; continue; }  // v4.1 : `// @meta ...` 1 ligne accepté
    if (/^\s*\/\//.test(ln) || !ln.trim()) continue;
    let col = 0;
    for (let i = 0; i < ln.length; i++) {
      const ch = ln[i];
      col = i + 1;
      if (inStr) {
        if (ch === '\\') { i++; continue; }
        if (ch === inStr) inStr = null;
        continue;
      }
      if (ch === '"' || ch === "'" || ch === '`') { inStr = ch; continue; }
      if (OPEN[ch]) { stack.push({ ch, line: li + 1, col }); continue; }
      if (CLOSE[ch]) {
        const top = stack.pop();
        if (!top || top.ch !== CLOSE[ch]) {
          errors.push(`ERR L${li + 1}:${col} Unexpected '${ch}'. Fix: balance it with '${OPEN[ch] || ch}' above.`);
          break;
        }
      }
    }
    if (inStr) {
      errors.push(`ERR L${li + 1}:${col} Unclosed string. Fix: close the ${inStr} before end of line.`);
      inStr = null;
      break;
    }
  }
  if (errors.length < 2 && stack.length) {
    const top = stack[stack.length - 1];
    errors.push(`ERR L${top.line}:${top.col} Unclosed '${top.ch}'. Fix: close '${OPEN[top.ch]}' before the next '$piste:' line.`);
  }
  if (errors.length < 2 && !sawMeta) {
    const first = lines.find((l) => l.trim() && !l.trim().startsWith('//'));
    if (first && !/^\$[A-Za-z0-9_]*\s*:/.test(first.trim())) {
      errors.push(`ERR L1:1 Missing meta header. Fix: start with '// @meta 140 C:minor trap_rap form=I4-V8-C8-O4' (1 ligne) ou '// @strudel-meta {json}'.`);
    }
  }
  return errors.slice(0, 2);
}
