# fast-session-restore

**问题**：打开很长的历史会话时，扩展会把该会话下**所有** subagent 的 wire 也恢复一遍，
几万个事件全量回放 ⇒ 打开要等很久（实测 78 MB 会话卡到不可用）。

**做法**：在 bundle 里加一个零开销门控——**默认不加载任何历史 subagent**，
需要时用环境变量 `KIMI_CODE_LOAD_SUBAGENTS=true` 恢复全量。

| 项 | 说明 |
|---|---|
| 目标产物 | `…/moonshot-ai.kimi-code-<ver>-linux-x64/dist/extension.js` |
| 脚本 | `apply.py`（幂等，含 `--revert`）；`selfcheck.py` 校验锚点 |
| 适用版本 | 0.7.5 起；0.8.2 只保留 E4 这一处（上游已内建窗口化回放） |
| 风险 | 副作用是历史 subagent 的对话不再出现在恢复的会话里；需要时用环境变量打开 |

> 本目录不再保留内部的实测量表与长会话清单，只保留可直接重放的脚本。
