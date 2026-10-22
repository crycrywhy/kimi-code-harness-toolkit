#!/usr/bin/env python3

import os as _os, glob as _glob
def _kimi_dist():
    p = _os.environ.get("KIMI_DIST")
    if p:
        return p
    c = sorted(_glob.glob(_os.path.expanduser("~/.vscode-server/extensions/moonshot-ai.kimi-code-*-linux-x64/dist")))
    if not c:
        raise SystemExit("找不到 Kimi Code 扩展目录，请设置 KIMI_DIST 指向 .../dist")
    return c[-1]
_KIMI_DIST = _kimi_dist()
"""apply_pt.py —— P-T：修 RPC 参数白名单拦截 + 切换后广播 + 前端闭环

Gemini 审查结论（<DATE>）：
  1) 宿主 validateParams 没有 getPermissionMode/setPermissionMode 分支 ⇒ 所有调用被
     "Invalid bridge params" 直接拒掉 ⇒ 芯片永远停在默认 manual（主因）；
  2) 无会话时 getPermissionMode 返回 {ok:false,mode:"manual"}，前端未校验 ok ⇒ 600ms
     后把用户刚选的模式洗回 manual；
  3) setPermissionMode 成功后没有广播 StatusUpdate，其它视图/重挂载拿不到新值；
  4) 芯片 pick 只改局部 state，没写回 store（对照 planMode 的 J0.setState 写法）；
  5) J0 初始 state 没有 permissionMode 字段。

修法：
  A. validateParams 增两个分支；
  B. getPermissionMode 无会话时返回 {ok:false}（不带 mode）；
  C. setPermissionMode 成功后对所有订阅视图 announceStatus；
  D. 芯片：refresh 校验 r.ok；pick 先写 store 再调 RPC；无会话时记为 pending，
     会话建立（sid 出现）后自动补发；
  E. J0 初始 state 增 permissionMode:"manual"。
"""
import argparse, os, shutil, subprocess, tempfile, time

DIST = _KIMI_DIST
EXT, WEB = os.path.join(DIST, "extension.js"), os.path.join(DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-pt-permission-fix"

A_OLD = '\t\tcase Methods$1.SetPlanMode: return hasBoolean(params, "enabled");\n'
A_NEW = ('\t\tcase Methods$1.GetPermissionMode: return params === void 0;\n'
         '\t\tcase Methods$1.SetPermissionMode: return isPlainObject$8(params) && typeof params["mode"] === "string";\n'
         '\t\tcase Methods$1.SetPlanMode: return hasBoolean(params, "enabled");\n')

B_OLD = '''const getPermissionMode = async (_, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime === void 0) return {
\t\tok: false,
\t\tmode: "manual"
\t};
'''
B_NEW = '''const getPermissionMode = async (_, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime === void 0) return { ok: false };
'''

C_OLD = '''\tawait runtime.applyLegacyApproval({
\t\tyolo: mode === "yolo",
\t\tafk: mode === "auto"
\t});
\treturn {
\t\tok: true,
\t\tmode
\t};
'''
C_NEW = '''\tawait runtime.applyLegacyApproval({
\t\tyolo: mode === "yolo",
\t\tafk: mode === "auto"
\t});
\ttry {
\t\tfor (const vid of runtime.subscribers) await runtime.announceStatus(vid);
\t} catch {}
\treturn {
\t\tok: true,
\t\tmode
\t};
'''

D_OLD_FN_START = 'function PModeChip(){'
NEW_FN = '''function PModeChip(){
\tconst pm=J0(s=>s.permissionMode);
\tconst sid=J0(s=>s.sessionId);
\tconst[mode,setMode]=S.useState(typeof pm=="string"&&pm.length>0?pm:"manual");
\tconst[pending,setPending]=S.useState(null);
\tS.useEffect(()=>{if(typeof pm=="string"&&pm.length>0)setMode(pm)},[pm]);
\tS.useEffect(()=>{
\t\tif(sid&&pending){const u=pending;setPending(null);Qe.setPermissionMode(u).then(()=>{setMode(u)}).catch(()=>{})}
\t},[sid]);
\tconst refresh=()=>{Qe.getPermissionMode().then(r=>{if(r&&r.ok&&r.mode)setMode(r.mode)}).catch(()=>{})};
\tS.useEffect(()=>{refresh()},[sid]);
\tconst MODES=[["manual","Manual","Ask for approval before each action"],["auto","Auto","Approve routine actions, pause for risky ones"],["yolo","Yolo","Approve everything, never ask"]];
\tconst cur=MODES.find(m=>m[0]===mode)||MODES[0];
\tconst cls=mode==="yolo"?"text-red-500":mode==="auto"?"text-blue-500":"text-muted-foreground";
\tconst pick=u=>{
\t\tif(u===mode)return;
\t\tsetMode(u);try{J0.setState({permissionMode:u})}catch(_){}
\t\tif(!sid){setPending(u);return}
\t\tQe.setPermissionMode(u).then(r=>{if(r&&r.ok===!1){setMode(mode)}}).catch(()=>{setMode(mode)});
\t};
\treturn d.jsxs(jc,{children:[
\t\td.jsxs(wr,{children:[
\t\t\td.jsx(yr,{asChild:!0,children:d.jsx(Hc,{asChild:!0,onClick:refresh,children:d.jsxs(o0,{variant:"ghost",size:"xs",className:"h-6 px-1.5 gap-1 text-xs font-medium",children:[d.jsx("span",{className:cls,children:cur[1]}),d.jsx(In,{className:"size-3 shrink-0 opacity-60"})]})})}),
\t\t\td.jsx(_r,{children:"Permission mode"})
\t\t]}),
\t\td.jsxs(Pc,{className:"w-72!",align:"start",children:MODES.map(m=>d.jsxs(Jc,{onClick:()=>pick(m[0]),className:"text-xs gap-2 items-start",children:[
\t\t\td.jsx(En,{className:Be("size-3 mt-0.5",m[0]!==mode&&"opacity-0")}),
\t\t\td.jsxs("div",{className:"flex flex-col gap-0.5",children:[
\t\t\t\td.jsx("span",{className:"font-medium",children:m[1]}),
\t\t\t\td.jsx("span",{className:"text-[10px] leading-snug text-muted-foreground whitespace-normal",children:m[2]})
\t\t\t]})
\t\t]},m[0]))})
\t]});
}
'''

E_OLD = 'const J0=qc((e,i)=>({sessionId:null,messages:[],isStreaming:!1,isCompacting:!1,handshakeReceived:!1,draftMedia:[],lastStatus:null,'
E_NEW = 'const J0=qc((e,i)=>({sessionId:null,permissionMode:"manual",messages:[],isStreaming:!1,isCompacting:!1,handshakeReceived:!1,draftMedia:[],lastStatus:null,'


def check(path):
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfile(path, tf.name)
        tmp = tf.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    return r.returncode == 0, (r.stderr or r.stdout)[:400]


def patch(path, steps, dry):
    src = open(path, encoding="utf-8").read()
    for name, old, new in steps:
        if old is None:  # 函数整体替换（括号匹配）
            i = src.find(D_OLD_FN_START)
            if i < 0:
                print(f"FAIL {name}: function not found"); return None
            j = src.find("{", i); depth = 0; end = -1
            for k in range(j, len(src)):
                if src[k] == "{": depth += 1
                elif src[k] == "}":
                    depth -= 1
                    if depth == 0: end = k + 1; break
            if end < 0:
                print(f"FAIL {name}: unbalanced"); return None
            src = src[:i] + new + src[end:]
            print(f"OK   {name}"); continue
        if src.count(new) >= 1 and src.count(old) == 0:
            print(f"SKIP {name}: already applied"); continue
        n = src.count(old)
        if n != 1:
            print(f"FAIL {name}: anchor count = {n}"); return None
        src = src.replace(old, new, 1); print(f"OK   {name}")
    if dry: return src
    bak = f"{path}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(path, bak)
    tmp = path + ".tmp-pt.js"
    open(tmp, "w", encoding="utf-8").write(src)
    ok, err = check(tmp)
    if not ok:
        os.remove(tmp); print("FAIL node --check:", err); return None
    os.replace(tmp, path)
    print(f"PATCHED {path}\nbackup  {bak}")
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    print("== extension.js ==")
    patch(EXT, [("validateParams 白名单", A_OLD, A_NEW),
                ("getPermissionMode 无会话不带 mode", B_OLD, B_NEW),
                ("setPermissionMode 后广播", C_OLD, C_NEW)], a.check)
    print("== webview.js ==")
    patch(WEB, [("J0 初始 permissionMode", E_OLD, E_NEW),
                ("PModeChip 重写", None, NEW_FN)], a.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
