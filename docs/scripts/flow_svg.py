#!/usr/bin/env python3
"""Render a flow-diagram spec (JSON) to a self-contained SVG in the warm-neutral house style.

Usage: python3 flow_svg.py spec.json [-o out.svg]

Spec (all keys optional except nodes):
{
  "title": "Heading above the diagram",
  "caption": "One muted line under the diagram",
  "nodes": [
    {"id": "bot", "col": 1, "row": 0, "title": "bot.py",
     "lines": ["allowlist guild/channel", "rate limit"], "muted": "filters answers",
     "kind": "normal | highlight | rejected | external"}
  ],
  "groups": [{"label": "Host machine · starts at login", "cols": [1, 3], "rows": [0, 1]}],
  "edges": [{"from": "a", "to": "b", "both": false, "label": "", "route": "auto | h | v"}]
}

Columns auto-size to their widest text; rows auto-size to their tallest node.
Colors adapt to prefers-color-scheme. No external fonts, scripts or images.
"""
import argparse
import json
import sys
import unicodedata
from html import escape

FONT = ("'IBM Plex Sans Thai','Noto Sans Thai','Sukhumvit Set',Thonburi,system-ui,"
        "-apple-system,'Segoe UI',Roboto,sans-serif")
STYLE = """
.bg{fill:#FFFFFF}.ttl{fill:#1F1E1C}.cap{fill:#8C8880}
.grp{fill:#F4F3EF;stroke:#CFCBC2}.glbl{fill:#6F6C66}
.nd{fill:#FBFAF7;stroke:#CFCBC2}.nt{fill:#1F1E1C}.nb{fill:#3A3935}.nm{fill:#8C8880}
.hl{fill:#DDE7F2;stroke:#3D7BD6}.rj{fill:#FFFFFF;stroke:#C8473D}.ex{fill:#FFFFFF;stroke:#CFCBC2}
.ar{stroke:#B3AFA6}.ah{fill:#B3AFA6}.elb{fill:#6F6C66}.elbg{fill:#FFFFFF}
@media (prefers-color-scheme: dark){
.bg{fill:#1E1D1B}.ttl{fill:#F1EFEA}.cap{fill:#8F8B83}
.grp{fill:#262522;stroke:#4A4740}.glbl{fill:#A29E95}
.nd{fill:#2B2A27;stroke:#4A4740}.nt{fill:#F1EFEA}.nb{fill:#D6D3CC}.nm{fill:#8F8B83}
.hl{fill:#1F3550;stroke:#5B9BEF}.rj{fill:#1E1D1B;stroke:#E0675C}.ex{fill:#1E1D1B;stroke:#4A4740}
.ar{stroke:#6D6A63}.ah{fill:#6D6A63}.elb{fill:#A29E95}.elbg{fill:#1E1D1B}}
"""

PAD_X, PAD_TOP, PAD_BOTTOM = 22, 22, 20
TITLE_FS, BODY_FS, LINE_H, TITLE_GAP = 17, 14.5, 22, 30
MIN_W, COL_GAP, ROW_GAP = 210, 64, 64
GROUP_PAD, GROUP_LABEL_H = 22, 34
MARGIN, HEAD_H, CAP_H = 28, 56, 40


def text_width(text, size):
    """Rough rendered width; Thai combining marks take no advance."""
    width = 0.0
    for ch in text:
        if unicodedata.combining(ch) or ch in 'ั็่้๊๋์ํ๎':
            continue
        if '฀' <= ch <= '๿':
            width += 0.58
        elif ch.isupper() or ch in 'mwMW@%':
            width += 0.68
        elif ch in 'il.,:;|!\'` ':
            width += 0.3
        else:
            width += 0.54
    return width * size


def node_lines(n):
    return list(n.get('lines', [])) + ([n['muted']] if n.get('muted') else [])


def layout(spec):
    nodes = spec['nodes']
    ids = [n['id'] for n in nodes]
    if len(set(ids)) != len(ids):
        sys.exit('duplicate node id')
    ncols = max(n['col'] for n in nodes) + 1
    nrows = max(n['row'] for n in nodes) + 1
    col_w = [MIN_W] * ncols
    row_h = [0] * nrows
    for n in nodes:
        need = max([text_width(n['title'], TITLE_FS) * 1.08] +
                   [text_width(t, BODY_FS) for t in node_lines(n)]) + 2 * PAD_X
        col_w[n['col']] = max(col_w[n['col']], int(need + 0.999))
        h = PAD_TOP + TITLE_GAP + LINE_H * len(node_lines(n)) + PAD_BOTTOM - 6
        row_h[n['row']] = max(row_h[n['row']], h)
    groups = spec.get('groups', [])
    top = MARGIN + (HEAD_H if spec.get('title') else 0)
    # Widen a column gap when a labelled edge has to fit in it.
    gaps = [COL_GAP] * ncols
    cell = {n['id']: (n['col'], n['row']) for n in nodes}
    for e in spec.get('edges', []):
        if e.get('label'):
            (ca, _), (cb, _) = cell[e['from']], cell[e['to']]
            if abs(ca - cb) == 1:
                g = min(ca, cb)
                gaps[g] = max(gaps[g], int(text_width(e['label'], 13)) + 56)
    xs, x = [], MARGIN + (GROUP_PAD if any(g['cols'][0] == 0 for g in groups) else 0)
    for c, w in enumerate(col_w):
        xs.append(x)
        x += w + gaps[c]
    ys, y = [], top
    for r, h in enumerate(row_h):
        if any(g['rows'][0] == r for g in groups):
            y += GROUP_LABEL_H + GROUP_PAD
        ys.append(y)
        y += h + ROW_GAP
    boxes = {n['id']: (xs[n['col']], ys[n['row']], col_w[n['col']], row_h[n['row']]) for n in nodes}
    gboxes = []
    for g in groups:
        c0, c1 = g['cols']
        r0, r1 = g['rows']
        gx = xs[c0] - GROUP_PAD
        gy = ys[r0] - GROUP_PAD - GROUP_LABEL_H
        gw = xs[c1] + col_w[c1] + GROUP_PAD - gx
        gh = ys[r1] + row_h[r1] + GROUP_PAD - gy
        gboxes.append((g, gx, gy, gw, gh))
    width = max([xs[-1] + col_w[-1]] + [gx + gw for _, gx, _, gw, _ in gboxes]) + MARGIN
    # Never clip the heading or caption: widen the canvas to fit them.
    if spec.get('title'):
        width = max(width, int(2 * MARGIN + text_width(spec['title'], 22) * 1.08 + 0.999))
    if spec.get('caption'):
        width = max(width, int(2 * MARGIN + text_width(spec['caption'], 14) + 0.999))
    bottom = max([ys[-1] + row_h[-1]] + [gy + gh for _, _, gy, _, gh in gboxes])
    height = bottom + (CAP_H if spec.get('caption') else 0) + MARGIN
    return boxes, gboxes, width, height, top


def edge_path(a, b, route):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy, bcx, bcy = ax + aw / 2, ay + ah / 2, bx + bw / 2, by + bh / 2
    horizontal = route == 'h' or (route != 'v' and abs(acy - bcy) < 1)
    vertical = route == 'v' or (route != 'h' and abs(acx - bcx) < 1)
    if vertical and not horizontal:
        y1, y2 = (ay + ah, by) if bcy > acy else (ay, by + bh)
        if abs(acx - bcx) < 1:
            return [(acx, y1), (bcx, y2)]
        mid = (y1 + y2) / 2
        return [(acx, y1), (acx, mid), (bcx, mid), (bcx, y2)]
    x1, x2 = (ax + aw, bx) if bcx > acx else (ax, bx + bw)
    if abs(acy - bcy) < 1:
        return [(x1, acy), (x2, bcy)]
    mid = (x1 + x2) / 2
    return [(x1, acy), (mid, acy), (mid, bcy), (x2, bcy)]


def render(spec):
    boxes, gboxes, width, height, top = layout(spec)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'width="{width}" height="{height}" role="img" aria-label="{escape(spec.get("title", "Flow diagram"))}" '
           f'font-family="{FONT}">',
           f'<style>{STYLE}</style>',
           '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path class="ah" d="M0,0 L10,5 L0,10 z"/></marker></defs>',
           f'<rect class="bg" width="{width}" height="{height}" rx="16"/>']
    if spec.get('title'):
        out.append(f'<text class="ttl" x="{MARGIN}" y="{MARGIN + 24}" font-size="22" font-weight="700">'
                   f'{escape(spec["title"])}</text>')
    for g, gx, gy, gw, gh in gboxes:
        out.append(f'<rect class="grp" x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="18" '
                   'stroke-width="1.5" stroke-dasharray="7 6"/>')
        out.append(f'<text class="glbl" x="{gx + GROUP_PAD}" y="{gy + 28}" font-size="15">{escape(g["label"])}</text>')
    for e in spec.get('edges', []):
        pts = edge_path(boxes[e['from']], boxes[e['to']], e.get('route', 'auto'))
        d = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in pts)
        start = ' marker-start="url(#ah)"' if e.get('both') else ''
        out.append(f'<path class="ar" d="{d}" fill="none" stroke-width="1.6"{start} marker-end="url(#ah)"/>')
        if e.get('label'):
            (x1, y1), (x2, y2) = pts[len(pts) // 2 - 1], pts[len(pts) // 2]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            w = text_width(e['label'], 13) + 12
            out.append(f'<rect class="elbg" x="{mx - w / 2:.1f}" y="{my - 11:.1f}" width="{w:.1f}" height="20" rx="4"/>')
            out.append(f'<text class="elb" x="{mx:.1f}" y="{my + 4:.1f}" font-size="13" text-anchor="middle">'
                       f'{escape(e["label"])}</text>')
    for n in spec['nodes']:
        x, y, w, h = boxes[n['id']]
        kind = n.get('kind', 'normal')
        cls, sw, dash = {'normal': ('nd', 1.5, ''), 'highlight': ('hl', 2.5, ''),
                         'rejected': ('rj', 2, ' stroke-dasharray="7 6"'),
                         'external': ('ex', 1.5, '')}[kind]
        out.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="12" stroke-width="{sw}"{dash}/>')
        ty = y + PAD_TOP + TITLE_FS
        out.append(f'<text class="nt" x="{x + PAD_X}" y="{ty}" font-size="{TITLE_FS}" font-weight="700">'
                   f'{escape(n["title"])}</text>')
        ly = ty + TITLE_GAP - 4
        for t in n.get('lines', []):
            out.append(f'<text class="nb" x="{x + PAD_X}" y="{ly}" font-size="{BODY_FS}">{escape(t)}</text>')
            ly += LINE_H
        if n.get('muted'):
            out.append(f'<text class="nm" x="{x + PAD_X}" y="{ly}" font-size="{BODY_FS}">{escape(n["muted"])}</text>')
    if spec.get('caption'):
        out.append(f'<text class="cap" x="{MARGIN}" y="{height - MARGIN - 6}" font-size="14">'
                   f'{escape(spec["caption"])}</text>')
    out.append('</svg>')
    return '\n'.join(out) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('spec')
    p.add_argument('-o', '--output')
    a = p.parse_args()
    with open(a.spec, encoding='utf-8') as f:
        spec = json.load(f)
    known = {n['id'] for n in spec['nodes']}
    for e in spec.get('edges', []):
        if e['from'] not in known or e['to'] not in known:
            sys.exit(f'edge references unknown node: {e}')
    svg = render(spec)
    if a.output:
        with open(a.output, 'w', encoding='utf-8') as f:
            f.write(svg)
    else:
        sys.stdout.write(svg)


if __name__ == '__main__':
    main()
