#!/usr/bin/env python3
"""补丁：流式噪声换行与 Markdown 结构归一（stream-newline-fix）v7.3.1 / 0.8.2 适配版 (2026-10-15)

做什么：
  第一道工序（newline_fix.js）：流式噪声换行清洗
    1. 【模型门】默认只对 DeepSeek 模型生效（/deepseek/i），K3 及其他模型逐字节原样透传。
    2. 【块首提前】保住 ## 标题、表格、列表、围栏、图片前合法空行。
    3. 【表内撕裂修复】吃掉表格行内注入换行。
    4. 【状态按流隔离】调用点传入 __NLST（单流独立状态仓），不跨请求常驻热态。
    5. 【调试日志】env KIMI_NL_FIX_DEBUG=1 时向 /tmp/nlfix_models.log 记录流水。

  第二道工序（md_normalize.js）：Markdown 结构规范器
    1. 【模型门 & 开关】与第一道工序同门控同开关：默认非 /deepseek/i 逐字节透传，KIMI_MD_FIX=0 一键总关。
    2. 【管道顺序】先 __nlFix 再 __mdFix，串联在同一个调用点，共享同一个 __NLST 状态仓。
    3. 【结构规范规则】：
       - 规则 1 紧贴正文的块首粘连修复。
       - 规则 2 标题被换行劈开修复。
       - 规则 3 表格块缺分隔行。
       - 规则 4 表格块与上文补空行。
       - 规则 5 行内标记闭合。
       - 规则 6 围栏闭合。
       - 规则 7 CJK 间注入空格删除。
    4. 【审计日志】env KIMI_MD_FIX_DEBUG=1 时向 /tmp/mdfix_audit.log 输出改动明细流水。

在 0.8.2 中改哪里（sink.onDelta 出口接入）：
  上游 0.8.2 将流式出口统一到 sink.onDelta。
  本补丁在四大适配器（OpenAI Chat, Anthropic, GoogleGenAI, OpenAI Responses）的
  createStreamParser 作用域中声明流级隔离的 __NLST，并在各个 text/think 出口处串联：
  text: __mdFix("text", __nlFix("text", <原值>, __NLST, __nlModel), __NLST, __nlModel)
  think: __mdFix("think", __nlFix("think", <原值>, __NLST, __nlModel), __NLST, __nlModel)
  各适配器从调用点 ctx.model 以及 chunk/event 元数据中动态提取并刷新 __nlModel。
  本脚本亦兼容 0.7.5 测试副本（满足单元门 10/7 的真语法闸门）。

用法：
  python3 apply.py             # 备份 → 打补丁 → node --check(.mjs 探针) → 报数
  python3 apply.py --check     # 只看现状（是否已打 / 锚点计数）
  python3 apply.py --revert    # 回到最近一次本补丁的备份
"""
import argparse, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))


def _latest_bundle():
    """默认取已安装的最新 kimi-code 版本目录（0.8.3 起不再硬编码版本号）。"""
    import glob as _g
    cands = _g.glob(os.path.expanduser(
        "~/.vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"))

    def _ver(p):
        m = re.search(r"kimi-code-([0-9][^-]*)-", p)
        try:
            return tuple(int(x) for x in (m.group(1).split(".") if m else []))
        except ValueError:
            return (0,)
    if not cands:
        return os.path.expanduser(
            "~/.vscode-server/extensions/moonshot-ai.kimi-code-0.8.2-linux-x64/dist/extension.js")
    return sorted(cands, key=_ver)[-1]


EXT = os.environ.get("KIMI_EXT") or _latest_bundle()

NL_HELPER = open(os.path.join(HERE, "newline_fix.js"), encoding="utf-8").read()
MD_HELPER = open(os.path.join(HERE, "md_normalize.js"), encoding="utf-8").read()
HELPER = NL_HELPER.rstrip("\n") + "\n\n" + MD_HELPER.strip() + "\n"

HEAD_ANCHOR = "const __filename = __cjsShimFileURLToPath(import.meta.url);"
START = "// === LOCAL PATCH: stream newline fix"
END_MD = "// === END LOCAL PATCH: markdown normalize ==="
END_NL = "// === END LOCAL PATCH: stream newline fix ==="
SUFFIX = "-before-stream-newline-fix-082"
SUFFIX_075 = "-before-stream-newline-fix-v8"

# 0.7.5 兼容常量
CHUNK_ID_TARGET_075 = "if (chunk.id) this._id = chunk.id;"
CHUNK_ID_REPL_075 = "if (chunk.id) this._id = chunk.id; if (chunk.model) this._nlModel = chunk.model;"
STREAM_TEXT_PAT_075 = re.compile(
    r"text:\s*(__mdFix\(\"text\",\s*__nlFix\(\"text\",\s*delta\.content(?:(?!\n|\s*\}).)*|__nlFix\(\"text\",\s*delta\.content(?:(?!\n|\s*\}).)*|delta\.content)"
)
STREAM_TEXT_REPL_075 = 'text: __mdFix("text", __nlFix("text", delta.content, (this._nlState ??= Object.create(null)), this._nlModel), (this._nlState ??= Object.create(null)), this._nlModel)'
STREAM_THINK_PAT_075 = re.compile(
    r'(\.observe\(delta\);\s*if \(reasoning !== void 0\) yield \{\s*type: "think",\s*think: )'
    r'(__mdFix\("think",\s*__nlFix\("think",\s*reasoning(?:(?!\n|\s*\}).)*|__nlFix\("think",\s*reasoning(?:(?!\n|\s*\}).)*|reasoning)'
)
STREAM_THINK_REPL_075 = r'\1__mdFix("think", __nlFix("think", reasoning, (this._nlState ??= Object.create(null)), this._nlModel), (this._nlState ??= Object.create(null)), this._nlModel)'
NONSTREAM_THINK_PAT_075 = re.compile(
    r'(\.observe\(message\);\s*if \(reasoning !== void 0\) yield \{\s*type: "think",\s*think: )'
    r'(__mdFix\("think",\s*__nlFix\("think",\s*reasoning(?:(?!\n|\s*\}).)*|__nlFix\("think",\s*reasoning(?:(?!\n|\s*\}).)*|reasoning)'
)
NONSTREAM_THINK_REPL_075 = r'\1__mdFix("think", __nlFix("think", reasoning, (this._nlState ??= Object.create(null)), response.model), (this._nlState ??= Object.create(null)), response.model)'

# 0.8.2 锚点定义清单 (name, anchor, replacement, sig)
SUBS_082 = [
    # 1. 顶部插入 helper 函数块
    (
        "头部插入 helper 函数块",
        HEAD_ANCHOR,
        HEAD_ANCHOR + "\n" + HELPER,
        "function __nlFix("
    ),

    # 2. OpenAI executeOpenAIRequest: 传入 model
    (
        "OpenAI execute: 传入 ctx.model",
        "\tconst parse = format.createStreamParser({\n\t\treasoningKey: trait?.reasoningKey,",
        "\tconst parse = format.createStreamParser({\n\t\tmodel: ctx.model?.model ?? ctx.model,\n\t\treasoningKey: trait?.reasoningKey,",
        "model: ctx.model?.model ?? ctx.model,\n\t\treasoningKey:"
    ),

    # 3. OpenAI createOpenAIFormat: 声明 __NLST 与 __nlModel
    (
        "OpenAI parser: 声明 __NLST 与 __nlModel",
        "function createOpenAIFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst bufferedToolCalls = /* @__PURE__ */ new Map();\n\t\tlet seenReasoningContent = false;",
        "function createOpenAIFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\tconst bufferedToolCalls = /* @__PURE__ */ new Map();\n\t\tlet seenReasoningContent = false;",
        "const __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\tconst bufferedToolCalls"
    ),

    # 4. OpenAI chunk loop: 动态刷新 __nlModel
    (
        "OpenAI chunk loop: 动态刷新 __nlModel",
        "\t\treturn (chunk, sink) => {\n\t\t\tif (typeof chunk.id === \"string\" && chunk.id.length > 0) sink.onMessageId?.(chunk.id);",
        "\t\treturn (chunk, sink) => {\n\t\t\tif (chunk.model) __nlModel = chunk.model;\n\t\t\tif (typeof chunk.id === \"string\" && chunk.id.length > 0) sink.onMessageId?.(chunk.id);",
        "if (chunk.model) __nlModel = chunk.model;"
    ),

    # 5. OpenAI think outlet
    (
        "OpenAI outlet: think",
        """\t\t\tfor (const reasoning of extractReasoningStrings(delta)) {
\t\t\t\tif (reasoning.key === "reasoning_content") seenReasoningContent = true;
\t\t\t\tsink.onDelta({
\t\t\t\t\ttype: "think",
\t\t\t\t\tthink: reasoning.value,
\t\t\t\t\treasoningKey: reasoning.key
\t\t\t\t});
\t\t\t}""",
        """\t\t\tfor (const reasoning of extractReasoningStrings(delta)) {
\t\t\t\tif (reasoning.key === "reasoning_content") seenReasoningContent = true;
\t\t\t\tsink.onDelta({
\t\t\t\t\ttype: "think",
\t\t\t\t\tthink: __mdFix("think", __nlFix("think", reasoning.value, __NLST, __nlModel), __NLST, __nlModel),
\t\t\t\t\treasoningKey: reasoning.key
\t\t\t\t});
\t\t\t}""",
        'think: __mdFix("think", __nlFix("think", reasoning.value, __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 6. OpenAI text outlet
    (
        "OpenAI outlet: text",
        """\t\t\tif (typeof delta.content === "string" && delta.content.length > 0) sink.onDelta({
\t\t\t\ttype: "text",
\t\t\t\ttext: delta.content
\t\t\t});""",
        """\t\t\tif (typeof delta.content === "string" && delta.content.length > 0) sink.onDelta({
\t\t\t\ttype: "text",
\t\t\t\ttext: __mdFix("text", __nlFix("text", delta.content, __NLST, __nlModel), __NLST, __nlModel)
\t\t\t});""",
        'text: __mdFix("text", __nlFix("text", delta.content, __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 7. Anthropic execute: 传入 ctx.model
    (
        "Anthropic execute: 传入 ctx.model",
        "\tonEvent?.({\n\t\ttype: \"llm.streaming.headers\",\n\t\theaders: headersToRecord(response.headers) ?? {}\n\t});\n\tconst parse = format.createStreamParser();",
        "\tonEvent?.({\n\t\ttype: \"llm.streaming.headers\",\n\t\theaders: headersToRecord(response.headers) ?? {}\n\t});\n\tconst parse = format.createStreamParser({ model: ctx.model?.model ?? ctx.model });",
        "format.createStreamParser({ model: ctx.model?.model ?? ctx.model });"
    ),

    # 8. Anthropic parser: 声明 __NLST 与 __nlModel
    (
        "Anthropic parser: 声明 __NLST 与 __nlModel",
        "function createAnthropicFormat() {\n\treturn { createStreamParser() {\n\t\treturn (chunk, sink) => {\n\t\t\tif (chunk.type === \"message_start\") {",
        "function createAnthropicFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\treturn (chunk, sink) => {\n\t\t\tif (chunk.type === \"message_start\") {\n\t\t\t\tif (chunk.message?.model) __nlModel = chunk.message.model;",
        "createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\treturn (chunk, sink) => {\n\t\t\tif (chunk.type === \"message_start\") {\n\t\t\t\tif (chunk.message?.model) __nlModel = chunk.message.model;"
    ),

    # 9. Anthropic block think
    (
        "Anthropic outlet: block think",
        """\t\t\t\tif (block.type === "thinking" && typeof block.thinking === "string" && block.thinking) {
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "think",
\t\t\t\t\t\tthink: block.thinking
\t\t\t\t\t});
\t\t\t\t\treturn;
\t\t\t\t}""",
        """\t\t\t\tif (block.type === "thinking" && typeof block.thinking === "string" && block.thinking) {
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "think",
\t\t\t\t\t\tthink: __mdFix("think", __nlFix("think", block.thinking, __NLST, __nlModel), __NLST, __nlModel)
\t\t\t\t\t});
\t\t\t\t\treturn;
\t\t\t\t}""",
        'think: __mdFix("think", __nlFix("think", block.thinking, __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 10. Anthropic block text
    (
        "Anthropic outlet: block text",
        """\t\t\t\tif (block.type === "text" && typeof block.text === "string" && block.text) sink.onDelta({
\t\t\t\t\ttype: "text",
\t\t\t\t\ttext: block.text
\t\t\t\t});""",
        """\t\t\t\tif (block.type === "text" && typeof block.text === "string" && block.text) sink.onDelta({
\t\t\t\t\ttype: "text",
\t\t\t\t\ttext: __mdFix("text", __nlFix("text", block.text, __NLST, __nlModel), __NLST, __nlModel)
\t\t\t\t});""",
        'text: __mdFix("text", __nlFix("text", block.text, __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 11. Anthropic delta text
    (
        "Anthropic outlet: delta text",
        """\t\t\t\tif (delta.type === "text_delta" && delta.text) {
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "text",
\t\t\t\t\t\ttext: delta.text
\t\t\t\t\t});
\t\t\t\t\treturn;
\t\t\t\t}""",
        """\t\t\t\tif (delta.type === "text_delta" && delta.text) {
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "text",
\t\t\t\t\t\ttext: __mdFix("text", __nlFix("text", delta.text, __NLST, __nlModel), __NLST, __nlModel)
\t\t\t\t\t});
\t\t\t\t\treturn;
\t\t\t\t}""",
        'text: __mdFix("text", __nlFix("text", delta.text, __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 12. Anthropic delta think
    (
        "Anthropic outlet: delta think",
        """\t\t\t\tif (delta.type === "thinking_delta" && delta.thinking) {
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "think",
\t\t\t\t\t\tthink: delta.thinking
\t\t\t\t\t});
\t\t\t\t\treturn;
\t\t\t\t}""",
        """\t\t\t\tif (delta.type === "thinking_delta" && delta.thinking) {
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "think",
\t\t\t\t\t\tthink: __mdFix("think", __nlFix("think", delta.thinking, __NLST, __nlModel), __NLST, __nlModel)
\t\t\t\t\t});
\t\t\t\t\treturn;
\t\t\t\t}""",
        'think: __mdFix("think", __nlFix("think", delta.thinking, __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 13. GoogleGenAI execute: 传入 ctx.model
    (
        "GoogleGenAI execute: 传入 ctx.model",
        "\tconst stream = await Promise.race([models.generateContentStream(request.params), abortPromise(signal)]);\n\tconst parse = format.createStreamParser();",
        "\tconst stream = await Promise.race([models.generateContentStream(request.params), abortPromise(signal)]);\n\tconst parse = format.createStreamParser({ model: ctx.model?.model ?? ctx.model });",
        "models.generateContentStream(request.params), abortPromise(signal)]);\n\tconst parse = format.createStreamParser({ model: ctx.model?.model ?? ctx.model });"
    ),

    # 14. GoogleGenAI parser: 声明 __NLST 与 __nlModel
    (
        "GoogleGenAI parser: 声明 __NLST 与 __nlModel",
        "function createGoogleGenAIFormat() {\n\treturn { createStreamParser() {\n\t\treturn (chunk, sink) => {\n\t\t\tconst response = chunk;",
        "function createGoogleGenAIFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\treturn (chunk, sink) => {\n\t\t\tconst response = chunk;\n\t\t\tif (response?.modelVersion) __nlModel = response.modelVersion;",
        "createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\treturn (chunk, sink) => {\n\t\t\tconst response = chunk;\n\t\t\tif (response?.modelVersion) __nlModel = response.modelVersion;"
    ),

    # 15. GoogleGenAI part outlet
    (
        "GoogleGenAI outlet: extractChunkParts",
        "\t\t\tfor (const part of extractChunkParts(response)) sink.onDelta(part);",
        """\t\t\tfor (const part of extractChunkParts(response)) {
\t\t\t\tif (part.type === "text" && typeof part.text === "string") {
\t\t\t\t\tsink.onDelta({ ...part, text: __mdFix("text", __nlFix("text", part.text, __NLST, __nlModel), __NLST, __nlModel) });
\t\t\t\t} else if (part.type === "think" && typeof part.think === "string" && part.think.length > 0) {
\t\t\t\t\tsink.onDelta({ ...part, think: __mdFix("think", __nlFix("think", part.think, __NLST, __nlModel), __NLST, __nlModel) });
\t\t\t\t} else {
\t\t\t\t\tsink.onDelta(part);
\t\t\t\t}
\t\t\t}""",
        'sink.onDelta({ ...part, text: __mdFix("text", __nlFix("text", part.text, __NLST, __nlModel), __NLST, __nlModel) })'
    ),

    # 16. OpenAI Responses execute: 传入 ctx.model
    (
        "OpenAI Responses execute: 传入 ctx.model",
        "\tonEvent?.({\n\t\ttype: \"llm.streaming.headers\",\n\t\theaders: headersToRecord(response.headers) ?? {}\n\t});\n\tconst parse = format.createStreamParser({ resolveUsage: trait?.extractUsage === void 0 ? void 0 : (chunk, defaultUsage) => {",
        "\tonEvent?.({\n\t\ttype: \"llm.streaming.headers\",\n\t\theaders: headersToRecord(response.headers) ?? {}\n\t});\n\tconst parse = format.createStreamParser({ model: ctx.model?.model ?? ctx.model, resolveUsage: trait?.extractUsage === void 0 ? void 0 : (chunk, defaultUsage) => {",
        "format.createStreamParser({ model: ctx.model?.model ?? ctx.model, resolveUsage:"
    ),

    # 17. OpenAI Responses parser: 声明 __NLST 与 __nlModel
    (
        "OpenAI Responses parser: 声明 __NLST 与 __nlModel",
        "function createOpenAIResponsesFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst functionCallArgumentsByIndex = /* @__PURE__ */ new Map();",
        "function createOpenAIResponsesFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";\n\t\tconst functionCallArgumentsByIndex = /* @__PURE__ */ new Map();",
        "function createOpenAIResponsesFormat() {\n\treturn { createStreamParser(options) {\n\t\tconst __NLST = Object.create(null);\n\t\tlet __nlModel = options?.model ?? \"\";"
    ),

    # 18. OpenAI Responses text outlet
    (
        "OpenAI Responses outlet: text",
        """\t\t\t\tcase "response.output_text.delta":
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "text",
\t\t\t\t\t\ttext: requireStringField(event, "delta", type)
\t\t\t\t\t});
\t\t\t\t\treturn;""",
        """\t\t\t\tcase "response.output_text.delta":
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "text",
\t\t\t\t\t\ttext: __mdFix("text", __nlFix("text", requireStringField(event, "delta", type), __NLST, __nlModel), __NLST, __nlModel)
\t\t\t\t\t});
\t\t\t\t\treturn;""",
        'text: __mdFix("text", __nlFix("text", requireStringField(event, "delta", type), __NLST, __nlModel), __NLST, __nlModel)'
    ),

    # 19. OpenAI Responses think outlet
    (
        "OpenAI Responses outlet: think",
        """\t\t\t\tcase "response.reasoning_summary_text.delta":
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "think",
\t\t\t\t\t\tthink: requireStringField(event, "delta", type)
\t\t\t\t\t});
\t\t\t\t\treturn;""",
        """\t\t\t\tcase "response.reasoning_summary_text.delta":
\t\t\t\t\tsink.onDelta({
\t\t\t\t\t\ttype: "think",
\t\t\t\t\t\tthink: __mdFix("think", __nlFix("think", requireStringField(event, "delta", type), __NLST, __nlModel), __NLST, __nlModel)
\t\t\t\t\t});
\t\t\t\t\treturn;""",
        'think: __mdFix("think", __nlFix("think", requireStringField(event, "delta", type), __NLST, __nlModel), __NLST, __nlModel)'
    ),
]


def backups(target_file):
    d = os.path.dirname(target_file)
    pat = re.compile(re.escape(os.path.basename(target_file)) + r"\.bak-.*(?:stream-newline-fix).*$")
    return sorted((os.path.join(d, f) for f in os.listdir(d) if pat.search(f)))


def node_check(path):
    """真语法闸门：先 cp 成 .mjs 再 node --check。"""
    with tempfile.TemporaryDirectory(prefix="mdfix-check-") as tmp:
        probe = os.path.join(tmp, "extension.mjs")
        shutil.copyfile(path, probe)
        r = subprocess.run(["node", "--check", probe], capture_output=True, text=True)
    print("node --check (.mjs probe):", "OK" if r.returncode == 0 else "FAILED\n" + r.stderr[:800])
    return r.returncode == 0


def apply_075(src, target_file):
    c_chunk_id = src.count(CHUNK_ID_TARGET_075)
    c_text = len(STREAM_TEXT_PAT_075.findall(src))
    c_stream_think = len(STREAM_THINK_PAT_075.findall(src))
    c_nonstream_think = len(NONSTREAM_THINK_PAT_075.findall(src))

    if START not in src and src.count(HEAD_ANCHOR) != 1:
        sys.exit(f"0.7.5 头部锚点异常（{src.count(HEAD_ANCHOR)} 处），需人工适配")
    if not (c_text == 3 and c_stream_think == 3 and c_nonstream_think == 3):
        sys.exit(f"0.7.5 调用点锚点计数异常：text={c_text}, stream_think={c_stream_think}, nonstream_think={c_nonstream_think}")

    bak = target_file + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + SUFFIX_075
    shutil.copy2(target_file, bak)
    print(f"备份: {bak}")

    if START in src:
        i = src.index(START)
        j = src.index(END_MD) + len(END_MD) if END_MD in src else (src.index(END_NL) + len(END_NL))
        out = src[:i] + HELPER.rstrip("\n") + src[j:]
    else:
        out = src.replace(HEAD_ANCHOR, HEAD_ANCHOR + "\n" + HELPER, 1)

    if CHUNK_ID_TARGET_075 in out:
        out = out.replace(CHUNK_ID_TARGET_075, CHUNK_ID_REPL_075)
    out, n_text = STREAM_TEXT_PAT_075.subn(STREAM_TEXT_REPL_075, out)
    out, n_st = STREAM_THINK_PAT_075.subn(STREAM_THINK_REPL_075, out)
    out, n_nst = NONSTREAM_THINK_PAT_075.subn(NONSTREAM_THINK_REPL_075, out)

    open(target_file, "w", encoding="utf-8").write(out)
    return node_check(target_file)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--file", default=EXT, help="目标 bundle 路径")
    a = ap.parse_args()

    target_file = a.file
    if not os.path.exists(target_file):
        sys.exit(f"找不到 bundle: {target_file}")
    src = open(target_file, encoding="utf-8").read()

    is_082 = "function createOpenAIFormat" in src
    patched = ("function __mdFix(" in src and
               "function __nlFix(" in src and
               src.count("__mdFix(") >= 6)

    print(f"bundle: {target_file} (版本: {'0.8.2 架构' if is_082 else '0.7.5 架构'})\n"
          f"  大小 {len(src)} 字符｜已打补丁: {patched}")

    if a.check:
        print("  锚点检查详情：")
        if is_082:
            for name, anchor, repl, sig in SUBS_082:
                c_anchor = src.count(anchor)
                c_sig = src.count(sig)
                print(f"    [{name:<40}]: anchor count={c_anchor}, sig count={c_sig}")
        else:
            c_chunk_id = src.count(CHUNK_ID_TARGET_075)
            c_text = len(STREAM_TEXT_PAT_075.findall(src))
            c_stream_think = len(STREAM_THINK_PAT_075.findall(src))
            c_nonstream_think = len(NONSTREAM_THINK_PAT_075.findall(src))
            print(f"    chunk.id={c_chunk_id}/3, text={c_text}/3, stream_think={c_stream_think}/3, nonstream_think={c_nonstream_think}/3")
        return 0

    if a.revert:
        bs = backups(target_file)
        if not bs:
            sys.exit("没有本补丁的备份可还原")
        shutil.copy2(bs[-1], target_file)
        print(f"已还原自: {bs[-1]}")
        return 0 if node_check(target_file) else 1

    if patched:
        print("已打过本补丁（幂等跳过）")
        return 0

    if not is_082:
        ok = apply_075(src, target_file)
        return 0 if ok else 1

    # 0.8.2 架构应用
    for name, anchor, repl, sig in SUBS_082:
        if sig in src:
            continue
        c = src.count(anchor)
        if c != 1:
            sys.exit(f"FATAL: 锚点 [{name}] 在 bundle 中匹配了 {c} 次 (预期恰好 1 次) — 中止操作，未修改任何文件。")

    bak = target_file + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + SUFFIX
    shutil.copy2(target_file, bak)
    print(f"备份: {bak}")

    out = src
    applied = []
    for name, anchor, repl, sig in SUBS_082:
        if sig in out:
            continue
        out = out.replace(anchor, repl, 1)
        applied.append(name)

    print(f"已应用 {len(applied)} 项编辑: {', '.join(applied)}")

    open(target_file, "w", encoding="utf-8").write(out)
    if not node_check(target_file):
        shutil.copy2(bak, target_file)
        sys.exit("node --check 失败，已自动回滚")
    print("完成。**请重载 VS Code 窗口**生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
