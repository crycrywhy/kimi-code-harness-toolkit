#!/usr/bin/env python3
"""wall.py —— 把多张本地图片拼成一张图墙。

**两条产出路线**

1. `build_wall()` → SVG（内嵌 base64）。优点：工具卡片里直接出图、跟随主题。
   缺点：受 harness 单条工具结果 **50,000 字符** 上限约束 ⇒ 只能低分辨率 + 调色板量化，
   4 张图时每张约 450px、32–64 色，放进 1240px 的卡片里会糊。**只适合 1–2 张小图预览**。

2. `render_png()` → 直接写一张全分辨率 PNG 文件（默认 1400px 宽，不限体积）。
   由 agent 在回复里用 markdown 图片引用 ⇒ 清晰、可单击放大、无 50k 限制。
   **多图对比、要看细节时用这条。**

**size 口径（实测 <DATE>）**：源图 2669×1013 / 3028×962 / 2372×835 / 1796×1062，
cell 比例 2.58 时 `slice` 会裁掉 18%/35%——图表必须用 `contain`，否则坐标轴被切。

**布局不写死**：`layout` 只是给 agent 的快捷方式（bento/hero/grid/row），
真正决定每张图位置的是显式的 `cells`（x/y/w/h，viewBox 坐标系）。agent 可以：
  - 直接给 `cells` —— 完全自由
  - 只给 `layout` —— 用内置规则算

**SVG 路线的体积（硬约束）**：harness 对单条工具结果的处理是——**总量 ≤ 50,000 字符就原样
放行；超了才落盘成指针文本，并对落盘副本按每行 2,000 字符切**。所以真正卡人的是总量。
实测：真彩色 PNG 4 张 450px 图表 = 156 KB（必死）；调色板 PNG 64 色 ≈ 43 KB（可过）。
超过 `MAX_TOTAL_KB` 会按"先降色数、再降宽"的阶梯重压。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time

# harness（0.8.2）的两道闸：TOOL_RESULT_MAX_LINE_CHARS=2000 / TOOL_RESULT_MAX_CHARS=50000。
# 单行超 2000 会被截断成 "[...truncated]" ⇒ 图永远渲染不出来；总量超 5 万会落盘成指针文本。
MAX_TOTAL_KB = 46           # 整张 SVG 的 base64 总量上限（+markup 后仍 < 50,000 字符）
LINE_CHUNK = 900            # base64 每 900 字符插一个换行，保证任何一行 < 2000
DEFAULT_W = 680             # 默认 viewBox 宽（与 RB1 的固有宽上限 760 兼容）
GAP = 8                     # 格子间距（viewBox 单位）
RADIUS = 8                  # 圆角
LABEL_H = 20                # 有标签时每格下方预留的高度


def _open_image(path):
    from PIL import Image
    im = Image.open(path)
    im.load()
    return im


def _encode(im, max_w, colors=128):
    """缩放 + PNG 编码 + base64。返回 (b64, w, h, mime)。

    **只用 PNG，不用 JPEG/WebP**（用户 <DATE> 定）。代价是体积：
    真彩色 PNG 4 张 450px 图表 = 156 KB，远超 harness 单条 5 万字符上限，
    所以转**调色板 PNG**（`Image.ADAPTIVE`，默认 128 色）。图表本身色块少、
    抗锯齿只有有限几种过渡色，64–128 色肉眼基本无差；照片类会有轻微色带。
    `colors` 由调用方的阶梯逐级下调（256→128→64→32），最后才缩宽度。
    """
    from PIL import Image
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    if w > max_w:
        im = im.resize((max_w, max(1, round(h * max_w / w))), Image.LANCZOS)
        w, h = im.size
    if colors and im.mode == "RGB":
        im = im.convert("P", palette=Image.ADAPTIVE, colors=colors)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True, compress_level=9)
    return base64.b64encode(buf.getvalue()).decode("ascii"), w, h, "image/png"


def _wrap_b64(s, n=LINE_CHUNK):
    """把 base64 切成多行——harness 单行上限 2,000 字符，超了整条结果被截断。

    换行插在属性值里：HTML 解析不做空白归一化，URL 解析按规范会剥掉
    base64 里的 ASCII 空白，实测 Chromium 正常解码。
    """
    return "\n".join(s[i:i + n] for i in range(0, len(s), n))


def _squarish(im):
    """裁成格子比例时的中心裁切（等价 object-fit:cover）。"""
    pass  # 交给 SVG 的 preserveAspectRatio="xMidYMid slice" 做，不用 PIL 裁



def suggest_layout(files, requested=None):
    """按图片宽高比给出建议布局。

    宽幅图表（宽高比 > 1.6）塞进 bento 的小方格里会缩得看不清（实测 162x90）。
    这种情况改用 row（横向一排）或 grid，让每张图拿到接近自身比例的格子。
    requested 显式给了就尊重，只在 auto/None 时计算。
    """
    if requested and requested != "auto":
        return requested
    ars = []
    for p in files:
        try:
            from PIL import Image
            with Image.open(p) as im:
                ars.append(im.size[0] / max(1, im.size[1]))
        except Exception:
            continue
    if not ars:
        return "grid"
    avg = sum(ars) / len(ars)
    n = len(files)
    if n == 1:
        return "row"
    # 2 张一律并排（对比场景就是要左右看，竖排会把对比变成上下滚动）
    if n == 2:
        return "row"
    if avg > 2.2:
        # 很宽的图 × 3+ 张：竖排每张占满宽，否则单格太窄看不清
        return "stack" if n > 4 else "grid"
    if avg > 1.5:
        return "grid"
    return "bento"                # 接近方形/竖图：bento 最好看

def _areas(layout, n, W, H):
    """按 layout 返回 [(x, y, w, h)] 列表（viewBox 坐标）。"""
    g = GAP
    if layout == "row" or n == 1:
        # 横向一排，等宽
        cw = (W - g * (n - 1)) / n
        return [(round(i * (cw + g)), 0, round(cw), H) for i in range(n)]

    if layout == "stack":
        ch = (H - g * (n - 1)) / n
        return [(0, round(i * (ch + g)), W, round(ch)) for i in range(n)]

    if layout == "grid":
        cols = 2 if n <= 4 else 3
        rows = (n + cols - 1) // cols
        cw = (W - g * (cols - 1)) / cols
        ch = (H - g * (rows - 1)) / rows
        out = []
        for i in range(n):
            r, c = divmod(i, cols)
            out.append((round(c * (cw + g)), round(r * (ch + g)), round(cw), round(ch)))
        return out

    if layout == "hero" and n >= 2:
        # 左大右小（竖向堆叠）
        hw = round(W * 0.62)
        sw = W - hw - g
        ch = (H - g * (n - 2)) / max(1, n - 1)
        out = [(0, 0, hw, H)]
        for i in range(1, n):
            out.append((hw + g, round((i - 1) * (ch + g)), sw, round(ch)))
        return out

    # bento：首图跨两行（2x2 小格），其余填格
    hw = round(W * 0.5)
    sw = (W - hw - 2 * g) // 2
    ch = (H - g) // 2
    out = [(0, 0, hw, H)]
    slots = [
        (hw + g, 0),
        (hw + g + sw + g, 0),
        (hw + g, ch + g),
        (hw + g + sw + g, ch + g),
    ]
    for i in range(1, n):
        if i - 1 < len(slots):
            x, y = slots[i - 1]
            out.append((x, y, sw, ch))
        else:
            # 超过 5 张：在后面按 grid 追加（把 H 拉长由调用方决定）
            break
    return out


def _avg_ar(files):
    """图片的平均宽高比（读不到就按 1.5 估）。"""
    ars = []
    for p in files:
        try:
            from PIL import Image
            with Image.open(p) as im:
                ars.append(im.size[0] / max(1, im.size[1]))
        except Exception:
            continue
    return (sum(ars) / len(ars)) if ars else 1.5


def _auto_height(layout, n, W, files=None, lbl_pad=0):
    """按布局和图片实际宽高比算 viewBox 高。

    关键：格子宽高比要贴近图片宽高比，否则 contain 会留大片白
    （实测 2:1 的图表塞进正方形格子，图只占一半高）。
    """
    ar = _avg_ar(files or []) if files else 1.5
    g = GAP
    if layout == "stack":
        cell_w = W
        cell_h = cell_w / ar + lbl_pad
        return round(n * cell_h + (n - 1) * g)
    if layout == "row" or n <= 1:
        cell_w = (W - g * (n - 1)) / n
        return round(cell_w / ar + lbl_pad)
    if layout == "grid":
        cols = 2 if n <= 4 else 3
        rows = (n + cols - 1) // cols
        cell_w = (W - g * (cols - 1)) / cols
        cell_h = cell_w / ar + lbl_pad
        return round(rows * cell_h + (rows - 1) * g)
    if layout == "hero":
        # 左大（62% 宽）右小：以左侧主图为准
        return round((W * 0.62) / ar + lbl_pad)
    # bento：主图占 50% 宽 × 全高 ⇒ 高度 = 主图宽 / ar
    return round((W * 0.5) / ar + lbl_pad)


def build_wall(files, layout="bento", width=DEFAULT_W, height=None,
               max_img_width=900, quality=78, cells=None, labels=None,
               fit="auto", colors=128):
    """把本地图片拼成一张 SVG。返回 dict（含 svg / bytes / count / 每图信息）。

    `quality` 保留只为兼容旧调用，**PNG 路径不用它**；实际压缩靠 `colors`
    （调色板颜色数）与 `max_img_width`。
    """
    ok_files = [p for p in (files or []) if p and os.path.isfile(p)]
    if not ok_files:
        return {"ok": False, "error": "没有可用的本地图片（路径不存在或为空）"}

    layout = suggest_layout(ok_files, layout)
    W = int(width or DEFAULT_W)
    _lbl_pad = LABEL_H if (labels and any(labels)) else 0
    H = int(height) if height else _auto_height(layout, len(ok_files), W, ok_files, _lbl_pad)

    # fit 策略：cover=裁切填满（照片），contain=完整显示+留边（图表/截图）
    # auto：按图片宽高比与格子宽高比的偏差决定——差得多就用 contain，
    # 否则 slice 会把坐标轴/标题裁掉（实测宽幅图表塞进近正方形格子，左右全没了）。
    def _pick_fit(path, cw, ch):
        if fit in ("cover", "contain"):
            return fit
        try:
            from PIL import Image
            with Image.open(path) as im:
                iw, ih = im.size
        except Exception:
            return "cover"
        if not cw or not ch:
            return "cover"
        img_ar = iw / max(1, ih)
        cell_ar = cw / max(1, ch)
        ratio = max(img_ar / cell_ar, cell_ar / img_ar)
        return "contain" if ratio > 1.35 else "cover"

    if cells:
        # 显式布局：每项 {x,y,w,h}，坐标是 viewBox 单位
        rects = []
        for c in cells[:len(ok_files)]:
            rects.append((int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"])))
        while len(rects) < len(ok_files):
            rects.append(rects[-1] if rects else (0, 0, W, H))
    else:
        rects = _areas(layout, len(ok_files), W, H)

    # 逐张编码；超预算就按"先降色数、再降宽"的阶梯重来
    # （顺序很重要：先保分辨率、只减调色板颜色；图表文字比颜色数更怕模糊）
    _c0 = max(16, min(256, int(colors or 128)))
    _ladder = [
        (max_img_width, _c0),
        (max_img_width, max(16, _c0 // 2)),
        (int(max_img_width * 0.8), max(16, _c0 // 2)),
        (int(max_img_width * 0.8), 64),
        (int(max_img_width * 0.65), 64),
        (int(max_img_width * 0.65), 32),
        (int(max_img_width * 0.5), 32),
        (int(max_img_width * 0.42), 32),
        (int(max_img_width * 0.35), 16),
    ]
    for mw, cols in _ladder:
        parts = []
        total = 0
        info = []
        _has_labels = bool(labels) and any(labels)
        for i, (path, (x, y, w, h)) in enumerate(zip(ok_files, rects)):
            # 有标签就把图区缩到 h-LABEL_H，标签画在剩下的条里
            _img_h = (h - LABEL_H) if (_has_labels and h > LABEL_H * 2) else h
            try:
                im = _open_image(path)
            except Exception as e:
                info.append({"file": path, "error": str(e)[:120]})
                continue
            b64, ew, eh, mime = _encode(im, mw, cols)
            total += len(b64)
            cid = "cl%d" % i
            # clipPath 外扩 0.5px：image 与 clip 边界重合时抗锯齿会漏出一条浅色缝
            # （实测右上角出现一个白点）。外扩后由 image 自身的 rect 遮住。
            parts.append(
                '<clipPath id="%s"><rect x="%s" y="%s" width="%s" height="%s" rx="%d"/></clipPath>'
                % (cid, x - 0.5, y - 0.5, w + 1, _img_h + 1, RADIUS))
            _fit = _pick_fit(path, w, _img_h)
            _par = "xMidYMid slice" if _fit == "cover" else "xMidYMid meet"
            if _fit == "contain":
                # 留边时给个底色，否则透明底在深色主题下会糊成一片
                parts.append(
                    '<rect x="%d" y="%d" width="%d" height="%d" rx="%d" '
                    'fill="currentColor" opacity="0.06"/>' % (x, y, w, _img_h, RADIUS))
            parts.append(
                '<image x="%d" y="%d" width="%d" height="%d" clip-path="url(#%s)" '
                'preserveAspectRatio="%s" href="data:%s;base64,%s"/>'
                % (x, y, w, _img_h, cid, _par, mime, _wrap_b64(b64)))
            lab = None
            if labels and i < len(labels) and labels[i]:
                lab = str(labels[i])
            if lab:
                # 标签放在格子**下方**（半透明底条压在图内会遮住坐标轴，
                # 实测宽幅图表被遮掉最下面一行刻度）。
                # 为此每格下方预留 LABEL_H，实际绘图区高度 = h - LABEL_H。
                parts.append(
                    '<rect x="%d" y="%d" width="%d" height="%d" rx="5" '
                    'fill="currentColor" opacity="0.10"/>'
                    % (x, y + h - LABEL_H + 2, min(w, 8 * len(lab) + 16), LABEL_H - 4))
                parts.append(
                    '<text x="%d" y="%d" font-size="10.5" fill="currentColor" '
                    'opacity="0.85" font-family="inherit">%s</text>'
                    % (x + 7, y + h - LABEL_H + 13, _esc(lab)))
            info.append({"file": path, "x": x, "y": y, "w": w, "h": h,
                         "fit": _fit, "enc_w": ew, "enc_h": eh,
                         "kb": round(len(b64) * 3 / 4 / 1024, 1)})
        # 换行会占额外字符（每 900 字符一个），预算按纯 b64 算即可
        if total / 1024 <= MAX_TOTAL_KB:
            break

    # 元素之间换行：让每条线段都远低于 2,000 字符（base64 已在 _wrap_b64 里分段）
    svg = ('<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">'
           '<defs>%s</defs>%s</svg>') % (
        W, H,
        "\n".join(p for p in parts if p.startswith("<clipPath")),
        "\n".join(p for p in parts if not p.startswith("<clipPath")),
    )
    # 兜底：降到最低档仍超 harness 上限就明确报错，别让宿主收到半截 SVG
    if len(svg) > 49000:
        return {"ok": False,
                "error": "图墙体积 %d 字符，超过 harness 单条工具结果上限（50,000）。"
                         "请减少图片张数（建议 ≤4）或调低 max_img_width 后重试。"
                         % len(svg)}
    return {"ok": True, "svg": svg, "count": len(info), "viewBox": [0, 0, W, H],
            "kb": round(len(svg.encode()) / 1024, 1), "images": info,
            "layout": "custom" if cells else layout}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ===== 全分辨率路线：直接合成一张 PNG（不受 50k 字符限制）=====

FONT_CJK = os.path.expanduser("~/.local/share/fonts/NotoSansCJKsc-Regular.otf")
FONT_LATIN = "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"


def _label_font(size):
    from PIL import ImageFont
    for p in (FONT_CJK, FONT_LATIN):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_png(files, layout="auto", out_width=1400, cells=None, labels=None,
               fit="contain", out_path=None, bg=(255, 255, 255)):
    """把多张图拼成一张**全分辨率 PNG** 并写盘。返回 dict（含 path / markdown / 每图信息）。

    和 `build_wall()` 用同一套布局几何（viewBox 680 宽），只是把坐标乘 `out_width/680`
    画到像素画布上。因为不经过工具结果，**没有 50k 字符上限**，可以用源图分辨率。

    `fit`：contain=完整显示（**图表默认，不裁坐标轴**）；cover=裁切填满（照片）。
    """
    from PIL import Image, ImageDraw

    ok_files = [p for p in (files or []) if p and os.path.isfile(p)]
    if not ok_files:
        return {"ok": False, "error": "没有可用的本地图片（路径不存在或为空）"}

    n = len(ok_files)
    layout = suggest_layout(ok_files, layout)
    W = DEFAULT_W
    _lbl_pad = LABEL_H if (labels and any(labels)) else 0
    H = _auto_height(layout, n, W, ok_files, _lbl_pad)
    S = float(out_width) / W
    # 底部留一点余量：标签 chip 落在最后一格的预留带里，贴边会显得被切
    _tail = 8 if _lbl_pad else 0
    OW, OH = int(round(W * S)), int(round((H + _tail) * S))

    if cells:
        rects = [(int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"])) for c in cells[:n]]
        while len(rects) < n:
            rects.append(rects[-1] if rects else (0, 0, W, H))
    else:
        rects = _areas(layout, n, W, H)

    canvas = Image.new("RGB", (OW, OH), bg)
    draw = ImageDraw.Draw(canvas)
    has_labels = bool(labels) and any(labels)
    font = _label_font(max(11, int(round(10.5 * S))))

    info = []
    for i, (path, (x, y, w, h)) in enumerate(zip(ok_files, rects)):
        cx, cy = int(round(x * S)), int(round(y * S))
        cw, ch = int(round(w * S)), int(round(h * S))
        img_h = ch - int(round(LABEL_H * S)) if (has_labels and ch > LABEL_H * S * 2) else ch
        try:
            im = _open_image(path)
        except Exception as e:
            info.append({"file": path, "error": str(e)[:120]})
            continue
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        iw, ih = im.size
        _fit = fit if fit in ("cover", "contain") else _pick_fit_ar(iw / max(1, ih), cw / max(1, img_h))
        if _fit == "contain":
            r = min(cw / iw, img_h / ih)
            nw, nh = max(1, int(iw * r)), max(1, int(ih * r))
            im2 = im.resize((nw, nh), Image.LANCZOS)
            canvas.paste(im2, (cx + (cw - nw) // 2, cy + (img_h - nh) // 2))
        else:
            r = max(cw / iw, img_h / ih)
            nw, nh = max(1, int(iw * r)), max(1, int(ih * r))
            im2 = im.resize((nw, nh), Image.LANCZOS)
            left = max(0, (nw - cw) // 2)
            top = max(0, (nh - img_h) // 2)
            canvas.paste(im2.crop((left, top, left + cw, top + img_h)), (cx, cy))
        lab = labels[i] if (labels and i < len(labels) and labels[i]) else None
        if lab:
            # 标签画在同一格的预留带里：chip 高 ~ (LABEL_H-6) 单位，别越出格子
            _band = int(round(LABEL_H * S))
            ty = cy + img_h + max(1, int(round(2 * S)))
            chip_h = max(10, _band - int(round(4 * S)))
            tw = draw.textlength(str(lab), font=font)
            draw.rounded_rectangle(
                [cx, ty, cx + tw + int(12 * S), ty + chip_h],
                radius=max(3, int(4 * S)), fill=(238, 238, 238))
            draw.text((cx + int(6 * S), ty + max(1, int(round(2 * S)))), str(lab),
                      font=font, fill=(50, 50, 50))
        info.append({"file": path, "x": x, "y": y, "w": w, "h": h, "fit": _fit,
                     "src": [iw, ih]})

    if out_path is None:
        out_path = os.path.expanduser("~/tmp/chat_figures/walls/wall_%d.png" % int(time.time()))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "PNG", optimize=True)
    _home = os.path.expanduser("~") + os.sep
    rel = out_path[len(_home):] if out_path.startswith(_home) else out_path
    return {"ok": True, "path": out_path, "markdown": "\n\n![图墙](%s)\n\n" % rel,
            "size": [OW, OH], "kb": round(os.path.getsize(out_path) / 1024, 1),
            "count": len(info), "images": info,
            "layout": "custom" if cells else layout}


def _pick_fit_ar(img_ar, cell_ar):
    ratio = max(img_ar / cell_ar, cell_ar / img_ar)
    return "contain" if ratio > 1.35 else "cover"


def render_cells(files, layout="auto", cell_width=1200, cells=None, labels=None,
                 fit="contain", out_dir=None, prefix=None, bg=(255, 255, 255),
                 draw_labels=False, title=None):
    """每格单独一张 PNG（**一张是一张**，各自可单击放大）。

    与 `render_png()` 的区别：不拼成一张，而是按格子比例各存一个文件。
    配合 webview 的 `p:has(>img~img)>img{width:50%}` 规则，把 N 张图写在**同一段**
    就自动排成 2×2 / 3 列；每张仍是独立 `<img>`，点击开自己的全屏预览。

    标签直接画进图片底部（markdown 没有图注）。
    """
    from PIL import Image, ImageDraw

    ok_files = [p for p in (files or []) if p and os.path.isfile(p)]
    if not ok_files:
        return {"ok": False, "error": "没有可用的本地图片（路径不存在或为空）"}

    n = len(ok_files)
    layout = suggest_layout(ok_files, layout)
    W = DEFAULT_W
    H = _auto_height(layout, n, W, ok_files, 0)
    if cells:
        rects = [(int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"])) for c in cells[:n]]
        while len(rects) < n:
            rects.append(rects[-1] if rects else (0, 0, W, H))
    else:
        rects = _areas(layout, n, W, H)

    out_dir = os.path.expanduser(out_dir or "~/tmp/chat_figures/walls")
    os.makedirs(out_dir, exist_ok=True)
    stamp = prefix or time.strftime("%Y%m%d-%H%M%S")

    font = _label_font(max(12, int(round(cell_width * 0.018))))
    markdown_parts, info, cells_out = [], [], []
    for i, (path, (x, y, w, h)) in enumerate(zip(ok_files, rects)):
        cw = int(cell_width)
        ch = max(1, int(round(cw * h / max(1, w))))
        canvas = Image.new("RGB", (cw, ch), bg)
        try:
            im = _open_image(path)
        except Exception as e:
            info.append({"file": path, "error": str(e)[:120]})
            continue
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        iw, ih = im.size
        band = int(round(ch * 0.10)) if (draw_labels and labels and i < len(labels) and labels[i]) else 0
        box_h = ch - band
        _fit = fit if fit in ("cover", "contain") else _pick_fit_ar(iw / max(1, ih), cw / max(1, box_h))
        if _fit == "contain":
            r = min(cw / iw, box_h / ih)
            nw, nh = max(1, int(iw * r)), max(1, int(ih * r))
            canvas.paste(im.resize((nw, nh), Image.LANCZOS), ((cw - nw) // 2, (box_h - nh) // 2))
        else:
            r = max(cw / iw, box_h / ih)
            nw, nh = max(1, int(iw * r)), max(1, int(ih * r))
            im2 = im.resize((nw, nh), Image.LANCZOS)
            left, top = max(0, (nw - cw) // 2), max(0, (nh - box_h) // 2)
            canvas.paste(im2.crop((left, top, left + cw, top + box_h)), (0, 0))
        lab = labels[i] if (labels and i < len(labels) and labels[i]) else None
        if lab and draw_labels:
            d = ImageDraw.Draw(canvas)
            d.text((int(cw * 0.012), box_h + int(band * 0.18)), str(lab), font=font, fill=(60, 60, 60))
        out_path = os.path.join(out_dir, "%s_%d.png" % (stamp, i + 1))
        canvas.save(out_path, "PNG", optimize=True)
        rel = out_path[len(os.path.expanduser("~") + os.sep):] if out_path.startswith(os.path.expanduser("~") + os.sep) else out_path
        alt = str(lab) if lab else ("图%d" % (i + 1))
        markdown_parts.append("![%s](%s)" % (alt, rel))
        cells_out.append({"src": rel, "label": (str(lab) if lab else "")})
        info.append({"file": path, "path": out_path, "fit": _fit, "size": [cw, ch],
                     "kb": round(os.path.getsize(out_path) / 1024, 1)})

    # ```wall 块：容器居中 + 固定宽高比，格内位置由 agent 通过 cells/layout 决定
    _cols = {"row": n, "stack": 1}.get(layout, 2)
    spec = {"title": (str(title).strip() if title else "图墙"),
            "cols": int(max(1, min(4, _cols))), "images": cells_out}
    wall_block = "\n\n```wall\n" + json.dumps(spec, ensure_ascii=False) + "\n```\n"
    return {"ok": True, "count": len(info), "paths": [x["path"] for x in info],
            "markdown": " ".join(markdown_parts), "wall_block": wall_block,
            "images": info, "layout": "custom" if cells else layout}
