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
"""apply_ps.py —— P-S：模式显示的真正链路 + 后台静默切换

用户反馈（<DATE>）：
  1. 不管怎么切换都显示 Manual（读取链路不对）
  2. 切模式不应在对话框里发 /auto、/yolo（要后台执行）
  3. 会话工作中也能随时切换（像 Claude Code）

根因：宿主 `announceStatus()` 推给 webview 的 StatusUpdate 只带 model/thinking/plan/context，
      没有 permission；自建 getPermissionMode RPC 在会话未就绪时又返回默认 manual。
修法：
  A. 宿主 announceStatus 的 payload 增加 permission；
  B. 宿主 setPermissionMode 改为走 applyLegacyApproval（与 /auto、/yolo 同一条状态更新路径，
     且不产生聊天消息、不打断正在跑的回合）；
  C. webview 的 StatusUpdate 处理器把 permission 存进 store（permissionMode）；
  D. 芯片优先读 store 的 permissionMode，切换时调 setPermissionMode（静默），不再 sendMessage。
"""
import argparse, os, shutil, subprocess, tempfile, time

DIST = _KIMI_DIST
EXT, WEB = os.path.join(DIST, "extension.js"), os.path.join(DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-ps-status-permission"

# A. announceStatus payload
A_OLD = '\t\t\t\tplan_mode: status.planMode,\n\t\t\t\tcontext_usage: status.contextUsage\n'
A_NEW = ('\t\t\t\tplan_mode: status.planMode,\n\t\t\t\tcontext_usage: status.contextUsage,\n'
         '\t\t\t\tpermission: status.permission\n')

# B. setPermissionMode handler（替换 P-N 版本）
B_OLD = '''const setPermissionMode = async (params, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime === void 0) return { ok: false };
\tawait runtime.session.setPermission(params && params.mode);
\treturn {
\t\tok: true,
\t\tmode: params && params.mode
\t};
};
'''
B_NEW = '''const setPermissionMode = async (params, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime === void 0) return { ok: false };
\tconst mode = params && params.mode;
\tif (mode !== "manual" && mode !== "auto" && mode !== "yolo") return {
\t\tok: false,
\t\tmode: runtime.legacyApprovalFlags && runtime.legacyApprovalFlags.afk ? "auto" : (runtime.legacyApprovalFlags && runtime.legacyApprovalFlags.yolo ? "yolo" : "manual")
\t};
\t// 与 /auto、/yolo 同一条链路：更新 legacy flags + permission + 会话元数据；
\t// 不产生聊天消息，也不打断正在进行的回合。
\tawait runtime.applyLegacyApproval({
\t\tyolo: mode === "yolo",
\t\tafk: mode === "auto"
\t});
\treturn {
\t\tok: true,
\t\tmode
\t};
};
'''

# C. webview StatusUpdate：接收并把 permission 存进 store
C_OLD = 'const{context_usage:r,token_usage:t,plan_mode:n,model:a,thinking_effort:A,retrying:l}=i;'
C_NEW = 'const{context_usage:r,token_usage:t,plan_mode:n,model:a,thinking_effort:A,retrying:l,permission:pm}=i;'
C2_OLD = 'n!=null&&(e.planMode=n),'
C2_NEW = 'n!=null&&(e.planMode=n),typeof pm=="string"&&pm.length>0&&(e.permissionMode=pm),'

# D. 芯片：读 store，静默切换
D_OLD_START = 'function PModeChip(){'
NEW_FN = '''function PModeChip(){
\tconst{permissionMode:pm,sessionId:sid}=J0();
\tconst[mode,setMode]=S.useState("manual");
\tS.useEffect(()=>{if(typeof pm=="string"&&pm.length>0)setMode(pm)},[pm]);
\tconst refresh=()=>{Qe.getPermissionMode().then(r=>{if(r&&r.mode)setMode(r.mode)}).catch(()=>{})};
\tS.useEffect(()=>{refresh()},[sid]);
\tconst MODES=[["manual","Manual","Ask for approval before each action"],["auto","Auto","Approve routine actions, pause for risky ones"],["yolo","Yolo","Approve everything, never ask"]];
\tconst cur=MODES.find(m=>m[0]===mode)||MODES[0];
\tconst cls=mode==="yolo"?"text-red-500":mode==="auto"?"text-blue-500":"text-muted-foreground";
\tconst pick=u=>{if(u===mode)return;Qe.setPermissionMode(u).then(()=>{setMode(u);setTimeout(refresh,600)}).catch(()=>{})};
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
        if old is None:  # full-function replacement（括号匹配找函数边界）
            i = src.find(D_OLD_START)
            if i < 0:
                print(f"FAIL {name}: function not found")
                return None
            j = src.find("{", i)
            depth = 0
            end = -1
            for k in range(j, len(src)):
                if src[k] == "{":
                    depth += 1
                elif src[k] == "}":
                    depth -= 1
                    if depth == 0:
                        end = k + 1
                        break
            if end < 0:
                print(f"FAIL {name}: unbalanced")
                return None
            src = src[:i] + new + src[end:]
            print(f"OK   {name}")
            continue
        if src.count(new) >= 1 and src.count(old) == 0:
            print(f"SKIP {name}: already applied")
            continue
        n = src.count(old)
        if n != 1:
            print(f"FAIL {name}: anchor count = {n}")
            return None
        src = src.replace(old, new, 1)
        print(f"OK   {name}")
    if dry:
        return src
    bak = f"{path}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(path, bak)
    tmp = path + ".tmp-ps.js"
    open(tmp, "w", encoding="utf-8").write(src)
    ok, err = check(tmp)
    if not ok:
        os.remove(tmp)
        print("FAIL node --check:", err)
        return None
    os.replace(tmp, path)
    print(f"PATCHED {path}\nbackup  {bak}")
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    print("== extension.js ==")
    patch(EXT, [("announceStatus 带 permission", A_OLD, A_NEW),
                ("setPermissionMode 走 applyLegacyApproval", B_OLD, B_NEW)], a.check)
    print("== webview.js ==")
    patch(WEB, [("StatusUpdate 解构 permission", C_OLD, C_NEW),
                ("StatusUpdate 存 store", C2_OLD, C2_NEW),
                ("PModeChip 重写", None, NEW_FN)], a.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
