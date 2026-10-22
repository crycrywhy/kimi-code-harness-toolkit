#!/usr/bin/env python3
"""rich_report_mcp —— 让 agent 把图直接画进对话流（P-RB2，<DATE>）

**为什么要有这个工具**：同类宿主 的内联可视化不是靠 skill、也不是靠文档规矩，
而是**把工具接口暴露给模型**——schema 每次会话自动进上下文，新 session 自然知道能用。
Kimi Code 里同构的机制就是 MCP 工具。所以这条能力长成一个 MCP server，不写 skill。

**契约**（与 webview 侧 P-RB2 补丁对齐，别改）：
  `render_diagram` 的返回 `output` 是**一段裸 SVG 文档**——去空白后以 `<svg` 开头、
  以 `</svg>` 结尾。宿主看到这种输出就把它当图渲染在工具卡片上（不进折叠区）。
  工具本身不落文件、不返回路径：图直接进对话流，跟 同类宿主 一样。

**传输**：stdio，stdout 只允许 JSON-RPC 消息，日志一律 stderr（stdio MCP 的硬规矩）。
**依赖**：只用 stdlib，不需要 venv。
"""
import json
import re
import sys
import threading

SERVER_NAME = 'rich_report'
SERVER_VERSION = '1.0.0'
PROTOCOL_FALLBACK = '<DATE>'

# ---------------------------------------------------------------- 画图约定

INSTRUCTIONS = """\
注意：本字段（MCP initialize 的 instructions）在 Kimi Code 0.8.2 里**不会被送进模型上下文**
（`getInstructions()` 零调用，已核实）。所以画图约定写在 `render_diagram` 的 description 里，
以及由 `check_diagram` 在执行时纠正。此处保留一份完整版，供将来包成 plugin 时搬到
`systemPrompt`（plugin 的 systemPrompt 确实会渲染进 `# Plugin Instructions` 段）。

完整约定（六条）：
1. 根元素 `<svg viewBox="0 0 W H">`；不写 width/height 百分比。单列 W=680，带侧支 720~760；
   宽度上限 760（工具卡片内容区比正文窄，超了会被等比缩小、字跟着变小）；字号 12–15，别小于 11。
2. 颜色只用 `currentColor` 或 CSS 变量，靠 `opacity` 分层次；不写硬编码 hex（深色主题下会成亮斑）。
   语义色（红=故障、绿=成功）可硬编码，但要在文字里说明。
3. 圆角 `rx="10"`，描边 0.5–1.5，不要阴影/渐变/滤镜。
4. 箭头用 `<defs><marker>` + `marker-end`，别用字符 `→` 冒充。
5. 文字 `text-anchor="middle"` + `dominant-baseline="central"` 居中放框内。
6. 不写 `<script>`、`<foreignObject>`、`on*=`、外链 `<image>`——会被净化器剥掉。
"""

TOOLS = [
    {
        'name': 'render_diagram',
        'description': (
            '把一张手写 SVG 图直接渲染到对话里（内联显示，不落文件）。'
            '用于流程图、故障链、状态机、结构/层次图、时序示意——'
            '凡是"讲清楚一段关系比一段文字更省力"的地方都该用。\n\n'
            '传入完整 SVG 文档（`<svg viewBox="0 0 W H">…</svg>`）。'
            '返回一个 ```wall 代码块——**把它原样贴进回复**，宿主会渲染成居中的可折叠 Figure 块，'
            '块内 SVG 跟随主题（currentColor）、点击可放大。\n\n'
            '写图前先想清楚三件事：读者要比什么、图里有几个节点、节点之间的箭头方向。'
            '不要为了好看加装饰；文字要能直接读懂，不要缩写。\n\n'
            '配色用 currentColor / var(--foreground) 跟主题；'
            '圆角 rx=10、字号 12–15、箭头用 marker。细节见服务器 instructions。'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'svg': {
                    'type': 'string',
                    'description': '完整 SVG 文档，以 <svg 开头、</svg> 结尾。必须含 viewBox。',
                },
                'caption': {
                    'type': 'string',
                    'description': '可选图注（一句话说明这张图画的是什么）。',
                },
                'max_width': {
                    'type': 'number',
                    'description': '图在块里显示的最大宽度（px），**由你按内容密度决定**：'
                                   '**默认 520**（绝大多数流程图用这个）；2–3 节点 420–480；'
                                   '节点多/文字密 640–800；不建议超过 900。',
                },
            },
            'required': ['svg'],
        },
    },
    {
        'name': 'build_image_wall',
        'description': (
            '把多张本地图片拼成一张图墙。**默认写一张全分辨率 PNG 到 '
            '~/tmp/chat_figures/walls/，并返回一行 markdown 图片引用——必须把这行原样贴进你的回复**，'
            '用户才能在对话里看到、并单击放大。做对比图用它，不要再手工用 PIL 拼图/拼接。\n\n'
            '为什么默认走文件而不是内联：harness 对单条工具结果有 **50,000 字符**上限，'
            '内联只能放低分辨率调色板图（4 张时每张约 450px，放进卡片会糊）；'
            '文件路线没有这个限制，直接用源图分辨率。只有 1–2 张小图、想直接显示在工具卡片里时才传 `inline=true`。\n\n'
            '布局由你按内容和图片数量自己定，不要机械套模板：\n'
            '  · 想强调某一张（主图+辅助）→ layout="bento"（首图跨两行）或 "hero"（左大右小）\n'
            '  · 等权对比 2 张 → layout="row"；3–4 张等权 → "grid"\n'
            '  · 要精确控制每张的位置和占比 → 传 cells（x/y/w/h，viewBox 坐标系），完全自由\n\n'
            '图片默认 fit="contain"（完整显示，**图表必须用这个**——slice 会裁掉坐标轴，'
            '实测 3.15:1 的图塞进 2.58 的格子丢 18% 宽）；照片想填满格子再传 fit="cover"。'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'files': {
                    'type': 'array', 'items': {'type': 'string'},
                    'description': '本地图片绝对路径列表（按展示顺序）。',
                },
                'layout': {
                    'type': 'string',
                    'enum': ['auto', 'bento', 'hero', 'grid', 'row', 'stack'],
                    'description': (
                        '布局。auto=按图片宽高比自动选（**默认，建议先用这个**）：'
                        '2 张→row 并排；3–4 张宽图→grid；很宽的图很多张→stack 竖排；接近方形→bento。'
                        '也可手动指定：bento=首图跨两行；hero=左大右小；grid=等宽网格；'
                        'row=横向一排；stack=竖向堆叠每张占满宽。'
                    ),
                },
                'cells': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'x': {'type': 'number'}, 'y': {'type': 'number'},
                            'w': {'type': 'number'}, 'h': {'type': 'number'},
                        },
                        'required': ['x', 'y', 'w', 'h'],
                    },
                    'description': '显式布局（viewBox 坐标），给了就忽略 layout。每项对应 files 里的同序图片。',
                },
                'fit': {
                    'type': 'string',
                    'enum': ['auto', 'cover', 'contain'],
                    'description': (
                        '填充方式。默认 contain（完整显示；**图表必须用这个**，slice 会裁掉坐标轴）；'
                        'cover=裁切填满（照片用，会丢 0–18% 边）；auto=按比例差自动选（>1.35 用 contain）。'
                    ),
                },
                'labels': {
                    'type': 'array', 'items': {'type': 'string'},
                    'description': '可选，每张图下方的小标签（如 "A" / "处理前"）。',
                },
                'title': {'type': 'string',
                          'description': '图块标题栏里显示的名字（跟在 Figure 后面），一句话说明这张图在看什么。'},
                'out_width': {'type': 'number',
                              'description': 'PNG 输出宽度（像素），默认 2400。卡片预览会按 1240 缩显，但单击放大后能看到细节；'
                              '4 图墙每格约 1180px，足够读小字。'},
                'out_path': {'type': 'string',
                             'description': 'PNG 落盘路径；不传则写到 ~/tmp/chat_figures/walls/wall_<时间戳>.png。'},
                'inline': {'type': 'boolean',
                           'description': 'true=返回内联 SVG（显示在工具卡片里，受 5 万字符限制、会降采样）；默认 false。'},
                'width': {'type': 'number', 'description': '（inline）viewBox 宽，默认 680。'},
                'height': {'type': 'number', 'description': '（inline）viewBox 高；不传则按 layout 自动算。'},
                'colors': {'type': 'number',
                           'description': '（inline）调色板颜色数，默认 128。越小体积越小。'},
                'max_img_width': {'type': 'number', 'description': '（inline）单张编码的最大宽度，默认 900。'},
            },
            'required': ['files'],
        },
    },
    {
        'name': 'check_diagram',
        'description': (
            '检查一段 SVG 是否符合本宿主的渲染约定（只检查、不渲染）。'
            '返回问题清单；没问题就返回 ok。'
            '改完图想确认没踩坑时用。'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'svg': {'type': 'string', 'description': '待检查的 SVG 文档。'}},
            'required': ['svg'],
        },
    },
]


# ---------------------------------------------------------------- 逻辑

FORBIDDEN = [
    (r'<\s*script', '含 <script>，会被净化器剥掉'),
    (r'<\s*foreignObject', '含 <foreignObject>，会被净化器剥掉'),
    (r'\son[a-zA-Z]+\s*=', '含 on*= 事件处理器，会被剥掉'),
    (r'javascript:', '含 javascript: URL，会被剥掉'),
    (r'<\s*image[^>]*\bhref\s*=\s*["\']https?:', '外链 <image>，CSP 会拦'),
]

HARD_HEX = re.compile(r'(?:fill|stroke)\s*=\s*["\']#([0-9a-fA-F]{3,8})["\']')

# 工具卡片内容区比正文窄，宽图会被等比缩小、字号跟着变小。
# 760 是实测比较稳的上限：680 宽的图在卡片里按原尺寸显示，760 基本不缩。
MAX_RECOMMENDED_WIDTH = 760


def _strip(s):
    return (s or '').strip()


def validate(svg):
    """返回 (problems, warnings)。problems 会挡住渲染，warnings 只提示。"""
    s = _strip(svg)
    problems, warnings = [], []
    if not s:
        return ['svg 为空'], []
    if not re.match(r'^<svg[\s>]', s, re.I):
        problems.append('必须以 <svg ...> 开头（不要包 <html>/<body>，也不要在前面加说明文字）')
    if not re.search(r'</\s*svg\s*>\s*$', s, re.I):
        problems.append('必须以 </svg> 结尾（文档要闭合）')
    if not re.search(r'viewBox\s*=\s*["\'][^"\']+["\']', s, re.I):
        problems.append('缺 viewBox；没有它宿主无法确定比例（写成 viewBox="0 0 680 420"）')
    for pat, why in FORBIDDEN:
        if re.search(pat, s, re.I):
            problems.append(why)
    if re.search(r'<svg[^>]*\swidth\s*=\s*["\']\d+%', s, re.I):
        warnings.append('width 写了百分比；宿主会改用 viewBox 固有宽，写不写都一样，建议删掉')
    vb = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', s, re.I)
    if vb:
        nums = re.split(r'[\s,]+', vb.group(1).strip())
        try:
            vw = float(nums[2])
        except (IndexError, ValueError):
            vw = 0.0
        if vw > MAX_RECOMMENDED_WIDTH:
            warnings.append(
                'viewBox 宽 %.0f 超过建议上限 %d：工具卡片内容区比正文窄，宽图会被等比缩小，'
                '字号跟着变小可能看不清。宽幅图建议压到 %d 以内，或拆成两张。'
                % (vw, MAX_RECOMMENDED_WIDTH, MAX_RECOMMENDED_WIDTH))
            # 按最窄处（卡片内容区）折算，最坏情况是缩到 MAX_RECOMMENDED_WIDTH
            scale = MAX_RECOMMENDED_WIDTH / vw
            sizes = [float(x) for x in re.findall(r'font-size\s*=\s*["\']([\d.]+)', s)]
            sizes += [float(x) for x in re.findall(r'font-size\s*:\s*([\d.]+)px', s)]
            if sizes:
                small = min(sizes) * scale
                if small < 11:
                    # 向下取一位小数：10.978 不能显示成 "11.0"（会和 "<11px" 自相矛盾）
                    shown = int(small * 10) / 10
                    warnings.append(
                        '缩到 %d 宽后，最小的 %gpx 字会变成约 %.1fpx（<11px 偏小，读起来费劲）。'
                        '要么把字号调大、要么减宽度、要么把图拆开。'
                        % (MAX_RECOMMENDED_WIDTH, min(sizes), shown))
    hexes = set(m.group(1).lower() for m in HARD_HEX.finditer(s))
    if hexes:
        shown = ', '.join('#' + h for h in sorted(hexes)[:6])
        warnings.append(
            '有硬编码颜色（%s）：深色主题下这些形状不会跟着变，可能变成亮斑。'
            '能用 currentColor / var(--foreground) 就别写 hex；语义色（红=故障、绿=成功）可以保留，'
            '但要在文字里说明。' % shown
        )
    if not re.search(r'currentColor|var\(--', s):
        warnings.append('整张图没有一处 currentColor / var(--…)：文字和描边在深色主题下可能看不清')
    if re.search(r'marker-end', s, re.I) and not re.search(r'<marker', s, re.I):
        problems.append('用了 marker-end 但没定义 <marker>，箭头不会画出来')
    return problems, warnings


def render(svg, caption='', max_width=520):
    problems, warnings = validate(svg)
    if problems:
        body = 'SVG 未通过检查，没有渲染：\n' + '\n'.join('- ' + p for p in problems)
        if warnings:
            body += '\n\n另外：\n' + '\n'.join('- ' + w for w in warnings)
        return body, True
    # 装进 Figure 块：宿主渲染成居中的可折叠图块（与 build_image_wall 同一套壳），
    # 不再把裸 SVG 单独挂在消息外面。
    spec = {"title": (caption or "图表"), "cols": 1, "maxWidth": int(max_width),
            "images": [{"svg": _strip(svg)}]}
    block = "\n\n```wall\n" + json.dumps(spec, ensure_ascii=False) + "\n```\n"
    return ("图表已生成，已在工具卡片里渲染成 Figure 块（不用贴进回复）：\n" + block), False


def check(svg):
    problems, warnings = validate(svg)
    if not problems and not warnings:
        return 'ok：符合本宿主的渲染约定，可以直接 render_diagram。', False
    lines = []
    if problems:
        lines.append('阻塞问题（会被拒绝渲染或画不出来）：')
        lines += ['- ' + p for p in problems]
    if warnings:
        lines.append('提示（能渲染，但可能不好看）：')
        lines += ['- ' + w for w in warnings]
    return '\n'.join(lines), bool(problems)




def wall_tool(a):
    """拼图墙。默认写全分辨率 PNG 并返回 markdown 片段；inline=true 时返回裸 SVG。"""
    import wall as _wall
    if a.get('inline'):
        r = _wall.build_wall(
            files=a.get('files') or [],
            layout=(a.get('layout') or 'auto'),
            width=a.get('width') or _wall.DEFAULT_W,
            height=a.get('height'),
            max_img_width=int(a.get('max_img_width') or 900),
            quality=int(a.get('quality') or 78),
            colors=int(a.get('colors') or 128),
            cells=a.get('cells'),
            labels=a.get('labels'),
            fit=(a.get('fit') or 'auto'),
        )
        if not r.get('ok'):
            return str(r.get('error') or 'build_image_wall failed'), True
        return r['svg'], False

    if a.get('split', True):
        r = _wall.render_cells(
            files=a.get('files') or [],
            layout=(a.get('layout') or 'auto'),
            cell_width=int(a.get('cell_width') or 1200),
            cells=a.get('cells'),
            labels=a.get('labels'),
            fit=(a.get('fit') or 'contain'),
            title=a.get('title'),
        )
        if not r.get('ok'):
            return str(r.get('error') or 'build_image_wall failed'), True
        return (
            "已生成 %d 张图片（每格一张，可各自单击放大）。工具卡片里会直接渲染成 Figure 块，"
            "**不用贴进回复**；列数由 JSON 里的 cols 决定：\n%s"
            % (r['count'], r['wall_block'])
        ), False

    r = _wall.render_png(
        files=a.get('files') or [],
        layout=(a.get('layout') or 'auto'),
        out_width=int(a.get('out_width') or 2400),
        cells=a.get('cells'),
        labels=a.get('labels'),
        # 图表默认 contain：slice 会裁掉坐标轴（实测 3.15:1 的图塞进 2.58 的格子丢 18% 宽）
        fit=(a.get('fit') or 'contain'),
        out_path=a.get('out_path'),
    )
    if not r.get('ok'):
        return str(r.get('error') or 'build_image_wall failed'), True
    return (
        "图墙已生成：%s（%d×%d px，%.1f KB，%d 张，%s）\n"
        "把下面这一行**原样贴进你的回复**，用户才能看到并单击放大：\n%s"
        % (r['path'], r['size'][0], r['size'][1], r['kb'], r['count'],
           r['layout'], r['markdown'].strip())
    ), False

HANDLERS = {
    'render_diagram': lambda a: render(a.get('svg', ''), a.get('caption') or '', a.get('max_width') or 520),
    'check_diagram': lambda a: check(a.get('svg', '')),
    'build_image_wall': wall_tool,
}


# ---------------------------------------------------------------- JSON-RPC

_out_lock = threading.Lock()
_workers = []


def log_err(msg):
    sys.stderr.write('[rich_report] %s\n' % msg)
    sys.stderr.flush()


def write_message(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
        sys.stdout.flush()


def reply(rid, result):
    write_message({'jsonrpc': '2.0', 'id': rid, 'result': result})


def reply_error(rid, code, message):
    write_message({'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}})


def tool_result(text, is_error=False, caption=None):
    payload = {'output': text, 'is_error': bool(is_error)}
    if caption:
        payload['note'] = caption
    return {'content': [{'type': 'text', 'text': text}], 'isError': bool(is_error), **payload}


def dispatch(name, args, rid):
    h = HANDLERS.get(name)
    if h is None:
        reply_error(rid, -32602, 'unknown tool: %r' % name)
        return
    caption = None
    try:
        text, is_error = h(args or {})
        if name == 'render_diagram' and not is_error:
            caption = (args or {}).get('caption') or None
        elif name == 'build_image_wall' and not is_error:
            _n = len((args or {}).get('files') or [])
            caption = '%d 张图的图墙（%s）' % (_n, (args or {}).get('layout') or 'custom')
    except Exception as e:
        text, is_error = 'internal error in %s: %s: %s' % (name, type(e).__name__, e), True
    reply(rid, tool_result(text, is_error, caption))


def handle(msg):
    if not isinstance(msg, dict):
        return
    method, rid = msg.get('method'), msg.get('id')
    if method == 'initialize':
        pv = (msg.get('params') or {}).get('protocolVersion')
        reply(rid, {
            'protocolVersion': pv if isinstance(pv, str) and pv else PROTOCOL_FALLBACK,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'instructions': INSTRUCTIONS,
        })
    elif method == 'tools/list':
        reply(rid, {'tools': TOOLS})
    elif method == 'tools/call':
        p = msg.get('params') or {}
        _t = threading.Thread(target=dispatch,
                              args=(p.get('name'), p.get('arguments') or {}, rid),
                              daemon=True)
        _workers.append(_t)
        _t.start()
    elif method == 'ping':
        reply(rid, {})
    elif rid is None:
        pass
    else:
        reply_error(rid, -32601, 'method not found: %r' % method)


def serve():
    log_err('started v%s pid=%d tools=%s' % (SERVER_VERSION, __import__('os').getpid(),
                                             ','.join(t['name'] for t in TOOLS)))
    while True:
        line = sys.stdin.readline()
        if not line:
            # stdin 关了不代表活干完了：dispatch 在线程里跑，先等它们写完再退。
            # （否则测试脚本一次灌多条请求时，只拿到 initialize 一条响应。）
            for _t in list(_workers):
                _t.join(timeout=30)
            log_err('stdin EOF, exiting')
            return
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            reply_error(None, -32700, 'parse error: %s' % e)
            continue
        handle(msg)


if __name__ == '__main__':
    try:
        serve()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
