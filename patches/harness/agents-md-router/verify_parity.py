#!/usr/bin/env python3
"""Verify parity between JS matchAgentsMdRule and Python l0.l0."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER_DIR = os.path.normpath(os.path.join(HERE, "../../router"))
sys.path.insert(0, ROUTER_DIR)
import l0

DATA_DIR = os.path.expanduser(os.environ.get("PARITY_DATA_DIR", "~/data/reminder/data"))
RULES_FILE = os.path.join(HERE, "rules.json")

def main():
    rules_data = json.load(open(RULES_FILE, encoding="utf-8"))

    # Build JS script to test
    ro = json.dumps(rules_data["gate_readonly"])
    wo = json.dumps(rules_data["gate_writeop"])
    sc = json.dumps(rules_data["state_changing"], ensure_ascii=False)

    rules_lines = []
    for r in rules_data["rules"]:
        pat = json.dumps(r["pattern"])
        secs = json.dumps(r["sections"], ensure_ascii=False)
        rid = r["id"]
        no_gate = ", no_gate: true" if r.get("no_gate") else ""
        rules_lines.append(f'  {{ id: {rid}, sections: {secs}, pattern: new RegExp({pat}, "i"){no_gate} }}')
    rules_str = ",\n".join(rules_lines)

    js_script = f"""
const GATE_READONLY = new RegExp({ro}, "i");
const GATE_WRITEOP = new RegExp({wo});
const STATE_CHANGING_SECTIONS = new Set({sc});
const AGENTS_MD_RULES = [
{rules_str}
];

function matchAgentsMdRule(action) {{
  if (!action) return null;
  const isReadonly = GATE_READONLY.test(action) && !GATE_WRITEOP.test(action);
  for (const rule of AGENTS_MD_RULES) {{
    if (rule.pattern.test(action)) {{
      const hasStateChanging = rule.sections.some((sec) => STATE_CHANGING_SECTIONS.has(sec));
      if (isReadonly && !rule.no_gate && !hasStateChanging) {{
        continue;
      }}
      return rule.sections[0];
    }}
  }}
  return null;
}}

const fs = require("fs");
const path = require("path");

const dataDir = "{DATA_DIR}";
const files = ["windows_gold2.jsonl", "windows_dev.jsonl"];
const out = {{}};

for (const f of files) {{
  const full = path.join(dataDir, f);
  const lines = fs.readFileSync(full, "utf-8").trim().split("\\n");
  for (const l of lines) {{
    if (!l.trim()) continue;
    const item = JSON.parse(l);
    out[item.id] = matchAgentsMdRule(item.next_action || "");
  }}
}}
console.log(JSON.stringify(out));
"""

    res = subprocess.run(["node", "-e", js_script], capture_output=True, text=True)
    if res.returncode != 0:
        print("Node error:", res.stderr)
        return 1
    js_results = json.loads(res.stdout)

    diffs = []
    total = 0
    files = ["windows_gold2.jsonl", "windows_dev.jsonl"]
    for f in files:
        full = os.path.join(DATA_DIR, f)
        count_in_file = 0
        for l in open(full, encoding="utf-8"):
            if not l.strip():
                continue
            item = json.loads(l)
            iid = item["id"]
            action = item.get("next_action") or ""
            py_res = l0.l0(action)
            py_sec = py_res[0] if py_res else None
            js_sec = js_results.get(iid)
            total += 1
            count_in_file += 1
            if py_sec != js_sec:
                diffs.append((f, iid, action, py_sec, js_sec))
        print(f"File {f}: checked {count_in_file} records.")

    print(f"\nTotal records checked across all files: {total}")
    print(f"Total differences between JS and Python: {len(diffs)}")

    if diffs:
        print("\nDifferences:")
        for file_name, iid, action, py_sec, js_sec in diffs[:20]:
            print(f"[{file_name} {iid}]")
            print(f"  action : {action[:100]}")
            print(f"  Python : {py_sec}")
            print(f"  JS     : {js_sec}")
        return 2

    print("SUCCESS: 0 differences found! JS matching is 100% identical to Python l0.l0.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
