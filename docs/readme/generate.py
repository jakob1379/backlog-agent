import math
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

INK = "#0F1B2E"
INK2 = "#132540"
LINE = "#26385A"
TEXT = "#EEF3FA"
MUTED = "#8C9CB5"
DIM = "#4A5E82"
AGENT = "#8EA2FF"
OWNER = "#F7B955"
CLEAN = "#52DBA8"
FONT = (
    "Inter, 'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"
)


def pct(t, T):
    return f"{100 * t / T:.3f}%"


def keyframes(name, stops, T):
    body = " ".join(f"{pct(t, T)}{{{css}}}" for t, css in stops)
    return f"@keyframes {name}{{{body}}}"


def frame(w, h, defs, style, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{FONT}">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{INK2}"/><stop offset="1" stop-color="{INK}"/></linearGradient>
<filter id="glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
{defs}
</defs>
<style>
{style}
@media (prefers-reduced-motion: reduce){{*{{animation:none!important}}}}
</style>
<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="24" fill="url(#bg)" stroke="{LINE}" stroke-width="2"/>
{body}
</svg>
"""


RED = "#FF6B6B"
EYE = "#CFF3FF"
CREW = {
    "foreman": ("Foreman", AGENT, "stopwatch", "runs the hour"),
    "patch": ("Patch", "#5CC8E8", "wrench", "clears dependency work"),
    "probe": ("Probe", "#C49BFF", "magnifier", "diagnoses issues"),
    "rivet": ("Rivet", "#F28FB0", "hammer", "builds approved plans"),
    "ledger": ("Ledger", "#C3CEDD", "book", "keeps the docs true"),
}


def squircle(cx, cy, rx, ry, n=4.5, steps=72):
    pts = []
    for k in range(steps):
        a = 2 * math.pi * k / steps
        c, s = math.cos(a), math.sin(a)
        pts.append(
            (
                cx + rx * math.copysign(abs(c) ** (2 / n), c),
                cy + ry * math.copysign(abs(s) ** (2 / n), s),
            )
        )
    return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"


BOT_DEFS = (
    f'<path id="bodyp" d="{squircle(0, -48, 33, 32)}"/>'
    f'<path id="visorp" d="{squircle(0, -55.5, 25, 14.5, n=4)}"/>'
    '<clipPath id="bodyclip"><use href="#bodyp"/></clipPath>'
    '<linearGradient id="bodyg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#EEF3FA"/><stop offset="1" stop-color="#A9B7CE"/></linearGradient>'
    '<radialGradient id="shade" cx=".35" cy=".3" r=".9"><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#0F1B2E" stop-opacity=".28"/></radialGradient>'
)
BOT_CSS = (
    ".disc{animation:disc .8s linear infinite}@keyframes disc{to{stroke-dashoffset:-16}}"
    ".blink{transform-box:fill-box;transform-origin:center;animation:blink 4.4s infinite}"
    "@keyframes blink{0%,91%,97%,100%{transform:scaleY(1)}94%{transform:scaleY(.1)}}"
    ".rays{animation:rays 1.4s linear infinite}.rays.fast{animation-duration:.55s}@keyframes rays{to{transform:rotate(360deg)}}"
    ".glow{animation:glow 1.6s ease-in-out infinite}@keyframes glow{50%{opacity:.45}}"
    ".flash{animation:flash .5s steps(1) infinite}@keyframes flash{50%{opacity:.2}}"
    ".twinkle{transform-box:fill-box;transform-origin:center;animation:twinkle 1.2s ease-in-out infinite}@keyframes twinkle{50%{transform:scale(.3);opacity:.3}}"
    ".bob{animation:bob 1s ease-in-out infinite}@keyframes bob{50%{transform:translateY(-4px)}}"
    ".shake{animation:shake .32s linear infinite}@keyframes shake{25%{transform:translateX(-2.5px) rotate(-2deg)}75%{transform:translateX(2.5px) rotate(2deg)}}"
    ".hop{animation:hop 1.3s cubic-bezier(.3,0,.3,1) infinite}@keyframes hop{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-9px)}}"
)


def tool(kind, accent):
    return {
        "stopwatch": f'<circle cx="-2" cy="8" r="7.5" fill="#EAF0F8" stroke="{accent}" stroke-width="2.5"/><rect x="-3.5" y="-2.5" width="3" height="3" fill="{accent}"/><path d="M-2 8 V3.5 M-2 8 L1.5 9.5" stroke="{INK}" stroke-width="1.8" stroke-linecap="round"/>',
        "wrench": '<path d="M0 0 L-3 12" stroke="#C3CEDD" stroke-width="4" stroke-linecap="round"/><circle cx="-4" cy="17" r="5" fill="none" stroke="#C3CEDD" stroke-width="4" stroke-dasharray="22 9"/>',
        "magnifier": f'<path d="M0 0 L-3 7" stroke="#6479A0" stroke-width="4" stroke-linecap="round"/><circle cx="-6" cy="14" r="7.5" fill="#9FE8FF" fill-opacity=".25" stroke="{accent}" stroke-width="3"/>',
        "hammer": '<path d="M0 -6 L0 14" stroke="#C8A27A" stroke-width="3.5" stroke-linecap="round"/><rect x="-8" y="-11" width="16" height="7" rx="2" fill="#8C9CB5"/>',
        "book": f'<rect x="-9" y="2" width="14" height="17" rx="2" fill="#EAF0F8" stroke="{accent}" stroke-width="2"/><path d="M-6 8 H2 M-6 12 H2 M-6 16 H-1" stroke="#8C9CB5" stroke-width="1.5" stroke-linecap="round"/>',
    }[kind]


def rays(color, fast=False):
    return (
        f'<g transform="translate(16 -95)"><g class="rays{" fast" if fast else ""}">'
        f'<path d="M0 0 L32 -7 L32 7Z" fill="{color}" fill-opacity=".38"/><path d="M0 0 L-32 -7 L-32 7Z" fill="{color}" fill-opacity=".38"/></g></g>'
    )


def lamp(color, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f'<path{c} d="M10 -92 A6 6 0 0 1 22 -92 Z" fill="{color}"/>'


def face(state):
    if state == "idle":
        return f'<g class="blink"><rect x="-14" y="-61" width="8" height="12" rx="4" fill="{EYE}"/><rect x="6" y="-61" width="8" height="12" rx="4" fill="{EYE}"/></g>'
    if state == "work":
        return (
            f'<rect x="-15" y="-57.5" width="10" height="5" rx="2.5" fill="{EYE}"/><rect x="5" y="-57.5" width="10" height="5" rx="2.5" fill="{EYE}"/>'
            f'<circle class="glow" cx="16" cy="-94" r="10" fill="{AGENT}" fill-opacity=".3"/>'
            + lamp(AGENT)
        )
    if state == "wait":
        return (
            rays(OWNER)
            + f'<circle cx="-10" cy="-55" r="5.5" fill="{OWNER}"/><circle cx="10" cy="-55" r="5.5" fill="{OWNER}"/>'
            f'<circle cx="-9" cy="-57" r="2" fill="{INK}"/><circle cx="11" cy="-57" r="2" fill="{INK}"/>'
            + lamp(OWNER)
        )
    if state == "happy":
        star = lambda x, y, d: (
            f'<path class="twinkle" style="animation-delay:{d}s" d="M{x} {y - 5} Q{x} {y} {x + 5} {y} Q{x} {y} {x} {y + 5} Q{x} {y} {x - 5} {y} Q{x} {y} {x} {y - 5}Z" fill="{CLEAN}"/>'
        )
        return (
            f'<path d="M-15 -52 l5 -5.5 l5 5.5 M5 -52 l5 -5.5 l5 5.5" fill="none" stroke="{CLEAN}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle class="glow" cx="16" cy="-94" r="10" fill="{CLEAN}" fill-opacity=".3"/>'
            + lamp(CLEAN)
            + star(-38, -78, 0)
            + star(36, -64, 0.6)
        )
    return (
        rays(RED, fast=True)
        + f'<path d="M-15 -60 l7 4.5 l-7 4.5 M15 -60 l-7 4.5 l7 4.5" fill="none" stroke="{RED}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
        + lamp(RED, "flash")
    )


def bot(kind, x, y, sc, states, motion="", arm="", spin=True, delay=0.0):
    """states: {state: css class or ''}; a '' class means always shown."""
    _, accent, tl, _ = CREW[kind]
    layers = "".join(
        f'<g class="{cls}">{face(st)}</g>' if cls else face(st)
        for st, cls in states.items()
    )
    disc_cls = ' class="disc"' if spin else ""
    return (
        f'<g transform="translate({x} {y}) scale({sc})"><g class="{motion}">'
        f'<ellipse cx="0" cy="0" rx="38" ry="5" fill="#000" fill-opacity=".28"/>'
        f'<ellipse cx="0" cy="-6" rx="34" ry="8" fill="#22324D" stroke="#3A5180" stroke-width="2"/>'
        f'<ellipse{disc_cls} cx="0" cy="-6" rx="26" ry="4.5" fill="none" stroke="#6479A0" stroke-width="2" stroke-dasharray="4 4"/>'
        f'<rect x="-27" y="-20" width="54" height="12" rx="5" fill="#33476A"/>'
        f'<path d="M12 -79 L16 -90" stroke="#8C9CB5" stroke-width="3" stroke-linecap="round"/>'
        f'<rect x="10" y="-93" width="12" height="4" rx="1.5" fill="#5A6F93"/>{lamp(DIM)}'
        f'<use href="#bodyp" fill="url(#bodyg)"/>'
        f'<rect x="-34" y="-34" width="68" height="8" fill="{accent}" clip-path="url(#bodyclip)"/>'
        f'<use href="#bodyp" fill="url(#shade)"/>'
        f'<circle cx="0" cy="-22.5" r="2.2" fill="#5A6F93"/>'
        f'<use href="#visorp" fill="#0B1526" stroke="#22324D" stroke-width="2"/>'
        f'<path d="M-17 -65 H-7" stroke="#fff" stroke-opacity=".18" stroke-width="2" stroke-linecap="round"/>'
        f'<g transform="translate(-31 -44)"><g class="{arm}" style="animation-delay:{delay}s">'
        f'<path d="M0 0 L-10 14" stroke="#AEBBD1" stroke-width="6" stroke-linecap="round"/>'
        f'<g transform="translate(-10 14)">{tool(tl, accent)}</g><circle cx="-10" cy="14" r="4" fill="#E6EDF7"/></g></g>'
        f"{layers}</g></g>"
    )


def win(name, ranges, T, e=0.15):
    st = []
    for a, b in ranges:
        if a <= 0:
            st.append((0, "opacity:1"))
        else:
            if not st:
                st.append((0, "opacity:0"))
            st += [(max(a - e, st[-1][0]), "opacity:0"), (a, "opacity:1")]
        if b >= T:
            st.append((T, "opacity:1"))
        else:
            st += [(b, "opacity:1"), (b + e, "opacity:0")]
    if st[-1][0] < T:
        st.append((T, "opacity:0"))
    default = "1" if ranges[0][0] <= 0 else "0"
    return (
        keyframes(name, st, T)
        + f".{name}{{animation:{name} {T}s linear infinite;opacity:{default}}}"
    )


def timeline(prefix, spans, T):
    """spans: [(t0, t1, state)] covering [0, T]; returns (states dict, css)."""
    by = {}
    for a, b, stt in spans:
        by.setdefault(stt, []).append((a, b))
    css = "".join(win(f"{prefix}-{stt}", r, T) for stt, r in by.items())
    return {stt: f"{prefix}-{stt}" for stt in by}, css


def hero():
    W, H, cx, cy, T = 1280, 460, 1010, 230, 12
    s, defs, body = [], [], []
    defs.append(
        f'<pattern id="dots" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="13" cy="13" r="1.3" fill="#2A3B5A"/></pattern>'
        f'<radialGradient id="fade" cx="{cx / W}" cy="{cy / H}" r="0.55"><stop offset="0" stop-color="#fff" stop-opacity="1"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
        f'<mask id="m"><rect width="{W}" height="{H}" fill="url(#fade)"/></mask>'
        f'<clipPath id="panel"><rect x="2" y="2" width="{W - 4}" height="{H - 4}" rx="23"/></clipPath>'
    )
    s.append(
        f"text{{font-feature-settings:'ss01','cv11'}}"
        f".in{{animation:in .9s cubic-bezier(.2,.7,.2,1) backwards}}"
        f"@keyframes in{{from{{opacity:0;transform:translateY(8px)}}}}"
        f".tick{{animation:tick .5s ease-out backwards}}@keyframes tick{{from{{opacity:0}}}}"
        f".sweep{{animation:spin {T}s linear infinite}}@keyframes spin{{to{{transform:rotate(360deg)}}}}"
        f".halo{{transform-box:fill-box;transform-origin:center;opacity:0;animation:ping {T}s linear infinite}}"
        f"@keyframes ping{{0%{{transform:scale(1);opacity:.85}}22%{{transform:scale(3.4);opacity:0}}100%{{transform:scale(3.4);opacity:0}}}}"
        f".c{{fill:{CLEAN};animation:clean {T}s linear infinite}}.o{{fill:{OWNER};animation:owner {T}s linear infinite}}"
        f"@keyframes clean{{0%,10%{{fill:{AGENT}}}28%,100%{{fill:{CLEAN}}}}}"
        f"@keyframes owner{{0%,10%{{fill:{AGENT}}}28%,100%{{fill:{OWNER}}}}}"
        f".breathe{{animation:breathe 4s ease-in-out infinite}}@keyframes breathe{{50%{{opacity:.55}}}}"
    )
    body.append(
        f'<g clip-path="url(#panel)"><rect width="{W}" height="{H}" fill="url(#dots)" mask="url(#m)"/></g>'
    )

    body.append(
        f'<g class="in"><text x="72" y="168" font-size="78" font-weight="800" letter-spacing="-3" fill="{TEXT}">backlog-agent</text></g>'
        f'<g class="in" style="animation-delay:.15s"><text x="74" y="222" font-size="31" font-weight="500" letter-spacing="-.4" fill="#CBD6E8">A personal quality scrubber that runs every hour.</text></g>'
        f'<g class="in" style="animation-delay:.3s" font-size="21" fill="{MUTED}">'
        f'<text x="74" y="276">It clears dependency and security work, triages the</text>'
        f'<text x="74" y="306">backlog, and leaves the owner only the calls that are theirs.</text></g>'
    )
    legend = [
        (AGENT, "agent at work"),
        (OWNER, "waiting on the owner"),
        (CLEAN, "clean"),
    ]
    x = 74
    items = []
    for color, label in legend:
        items.append(
            f'<circle cx="{x + 7}" cy="372" r="7" fill="{color}"/><text x="{x + 24}" y="379" font-size="19" fill="{MUTED}">{label}</text>'
        )
        x += 24 + len(label) * 9.6 + 34
    body.append(f'<g class="in" style="animation-delay:.45s">{"".join(items)}</g>')

    dial = [f'<g transform="translate({cx} {cy})">']
    dial.append(
        f'<circle r="184" fill="{INK}" fill-opacity=".7" stroke="{LINE}" stroke-width="2"/>'
    )
    dial.append(
        '<circle r="120" fill="none" stroke="#22324D" stroke-width="2" stroke-dasharray="2 8" stroke-linecap="round"/>'
    )
    for i in range(60):
        a = math.radians(i * 6)
        major = i % 5 == 0
        r0, r1 = (150, 170) if major else (160, 170)
        sa, ca = math.sin(a), -math.cos(a)
        dial.append(
            f'<line class="tick" style="animation-delay:{i * 0.018:.3f}s" x1="{r0 * sa:.1f}" y1="{r0 * ca:.1f}" x2="{r1 * sa:.1f}" y2="{r1 * ca:.1f}" '
            f'stroke="{"#6479A0" if major else "#33476A"}" stroke-width="{3 if major else 2}" stroke-linecap="round"/>'
        )
    sweep = ['<g class="sweep">']
    n, step = 18, 3
    for k in range(n):
        a0, a1 = math.radians(-k * step), math.radians(-(k + 1) * step - 0.4)
        r_in, r_out = 46, 176
        pts = [
            (r_in * math.sin(a0), -r_in * math.cos(a0)),
            (r_out * math.sin(a0), -r_out * math.cos(a0)),
            (r_out * math.sin(a1), -r_out * math.cos(a1)),
            (r_in * math.sin(a1), -r_in * math.cos(a1)),
        ]
        op = 0.26 * (1 - k / n) ** 1.6
        sweep.append(
            f'<path d="M{pts[0][0]:.1f} {pts[0][1]:.1f}L{pts[1][0]:.1f} {pts[1][1]:.1f}A{r_out} {r_out} 0 0 0 {pts[2][0]:.1f} {pts[2][1]:.1f}L{pts[3][0]:.1f} {pts[3][1]:.1f}A{r_in} {r_in} 0 0 1 {pts[0][0]:.1f} {pts[0][1]:.1f}Z" fill="{AGENT}" fill-opacity="{op:.3f}"/>'
        )
    sweep.append(
        f'<line x1="0" y1="-46" x2="0" y2="-178" stroke="{AGENT}" stroke-width="3" stroke-linecap="round"/>'
    )
    sweep.append(f'<circle cy="-178" r="5" fill="{AGENT}" filter="url(#glow)"/>')
    sweep.append("</g>")
    dial.extend(sweep)

    angles = [8, 33, 55, 79, 103, 129, 153, 181, 206, 232, 259, 291, 324]
    owner = {4, 10}
    for i, deg in enumerate(angles):
        a = math.radians(deg)
        x, y = 120 * math.sin(a), -120 * math.cos(a)
        delay = deg / 360 * T - T
        cls = "o" if i in owner else "c"
        dial.append(
            f'<circle class="halo" style="animation-delay:{delay:.2f}s" cx="{x:.1f}" cy="{y:.1f}" r="7" fill="none" stroke="{AGENT}" stroke-width="2"/>'
            f'<circle class="{cls}" style="animation-delay:{delay:.2f}s" cx="{x:.1f}" cy="{y:.1f}" r="7.5"/>'
        )
    dial.append(f'<circle r="44" fill="{INK}" stroke="#2E4268" stroke-width="2"/>')
    dial.append(
        f'<circle class="breathe" r="52" fill="none" stroke="{AGENT}" stroke-opacity=".25" stroke-width="2"/>'
    )
    dial.append(
        f'<text y="11" text-anchor="middle" font-size="30" font-weight="700" letter-spacing="-1" fill="{TEXT}">1h</text>'
    )
    dial.append("</g>")
    body.extend(dial)
    return frame(W, H, "".join(defs), "".join(s), "\n".join(body))


def loop():
    W, H, y, T = 1280, 336, 150, 14
    yb = y + 156
    xs = [190 + i * 180 for i in range(6)]
    steps = [
        ("Guard", "on main and", "writable"),
        ("Reconcile", "record what", "children did"),
        ("Plan", "read GitHub and", "the board, decide"),
        ("Dispatch", "children run", "one at a time"),
        ("Steward", "keeps the docs", "true to reality"),
        ("Report", "silent when", "nothing changed"),
    ]
    arrive = [0, 2, 4, 6, 10, 12]
    leave = [1, 3, 5, 9, 11, 13]
    s, body = [], []
    s.append(
        ".flow{animation:flow 1.6s linear infinite}@keyframes flow{to{stroke-dashoffset:-24}}"
    )
    back = f"M{xs[-1] + 26} {y} C{xs[-1] + 110} {y} {xs[-1] + 110} {yb} {xs[-1]} {yb} H{xs[0]} C{xs[0] - 110} {yb} {xs[0] - 110} {y} {xs[0] - 26} {y}"
    body.append(
        f'<path class="flow" d="{back}" fill="none" stroke="#33476A" stroke-width="2" stroke-dasharray="2 10" stroke-linecap="round"/>'
    )
    body.append(
        f'<rect x="{640 - 92}" y="{yb - 14}" width="184" height="28" fill="{INK}"/>'
    )
    body.append(
        f'<text x="640" y="{yb + 7}" text-anchor="middle" font-size="18" fill="{DIM}">again next hour</text>'
    )

    L = xs[-1] - xs[0]
    body.append(
        f'<line x1="{xs[0]}" y1="{y}" x2="{xs[-1]}" y2="{y}" stroke="{LINE}" stroke-width="4" stroke-linecap="round"/>'
    )
    prog: list[tuple[float, str]] = [(0, f"stroke-dashoffset:{L}")]
    for i in range(6):
        off = L - (xs[i] - xs[0])
        prog += [(arrive[i], f"stroke-dashoffset:{off}")]
    prog += [
        (13.4, "stroke-dashoffset:0;opacity:1"),
        (14, "stroke-dashoffset:0;opacity:0"),
    ]
    s.append(
        keyframes("prog", prog, T) + f".prog{{animation:prog {T}s linear infinite}}"
    )
    body.append(
        f'<line class="prog" x1="{xs[0]}" y1="{y}" x2="{xs[-1]}" y2="{y}" stroke="{CLEAN}" stroke-opacity=".7" stroke-width="4" stroke-linecap="round" stroke-dasharray="{L} {L}" stroke-dashoffset="0"/>'
    )

    for i, (title, c1, c2) in enumerate(steps):
        x = xs[i]
        ring = [
            (0, "stroke:#33476A"),
            (max(arrive[i] - 0.01, 0), "stroke:#33476A"),
            (arrive[i] + 0.15, f"stroke:{AGENT}"),
            (leave[i], f"stroke:{AGENT}"),
            (leave[i] + 0.3, f"stroke:{CLEAN}"),
            (13.6, f"stroke:{CLEAN}"),
            (14, "stroke:#33476A"),
        ]
        num = [
            (0, f"fill:{DIM}"),
            (max(arrive[i] - 0.01, 0), f"fill:{DIM}"),
            (arrive[i] + 0.15, f"fill:{TEXT}"),
            (leave[i], f"fill:{TEXT}"),
            (leave[i] + 0.3, f"fill:{CLEAN}"),
            (13.6, f"fill:{CLEAN}"),
            (14, f"fill:{DIM}"),
        ]
        halo = [
            (0, "opacity:0"),
            (max(arrive[i] - 0.01, 0), "opacity:0"),
            (arrive[i] + 0.2, "opacity:.5"),
            (leave[i], "opacity:.5"),
            (leave[i] + 0.3, "opacity:0"),
            (14, "opacity:0"),
        ]
        s.append(
            keyframes(f"r{i}", ring, T)
            + keyframes(f"n{i}", num, T)
            + keyframes(f"h{i}", halo, T)
        )
        s.append(
            f".r{i}{{animation:r{i} {T}s linear infinite}}.n{i}{{animation:n{i} {T}s linear infinite}}.h{i}{{animation:h{i} {T}s linear infinite;opacity:0}}"
        )
        body.append(
            f'<text x="{x}" y="{y + 60}" text-anchor="middle" font-size="23" font-weight="650" letter-spacing="-.3" fill="{TEXT}">{title}</text>'
        )
        body.append(
            f'<circle class="h{i}" cx="{x}" cy="{y}" r="34" fill="{AGENT}" fill-opacity=".18" filter="url(#glow)"/>'
        )
        body.append(
            f'<circle class="r{i}" cx="{x}" cy="{y}" r="26" fill="{INK}" stroke="#33476A" stroke-width="3"/>'
        )
        body.append(
            f'<text class="n{i}" x="{x}" y="{y + 7}" text-anchor="middle" font-size="20" font-weight="700" fill="{DIM}">{i}</text>'
        )
        body.append(
            f'<text x="{x}" y="{y + 88}" text-anchor="middle" font-size="18" fill="{MUTED}">{c1}</text>'
        )
        body.append(
            f'<text x="{x}" y="{y + 110}" text-anchor="middle" font-size="18" fill="{MUTED}">{c2}</text>'
        )

    fs, fcss = timeline(
        "fm", [(0, 5.2, "work"), (5.2, 12, "idle"), (12, 14, "happy")], T
    )
    s.append(BOT_CSS + fcss)
    kids = [
        ("patch", xs[3], 6.1),
        ("probe", xs[3], 7.0),
        ("rivet", xs[3], 7.9),
        ("ledger", xs[4], 10.1),
    ]
    for kind, kx, a in kids:
        ks, kcss = timeline(
            f"k{kind}", [(0, a + 0.55, "work"), (a + 0.55, T, "happy")], T
        )
        s.append(kcss + win(f"v{kind}", [(a, a + 0.85)], T, e=0.12))
        body.append(
            f'<g class="v{kind}">' + bot(kind, kx + 66, y - 30, 0.5, ks) + "</g>"
        )
    pulse = [
        (0, "transform:translateX(0);opacity:0"),
        (0.3, "transform:translateX(0);opacity:1"),
    ]
    for i in range(6):
        dx = xs[i] - xs[0]
        if i:
            pulse.append((arrive[i], f"transform:translateX({dx}px);opacity:1"))
        pulse.append((leave[i], f"transform:translateX({dx}px);opacity:1"))
    pulse += [
        (13.5, f"transform:translateX({L}px);opacity:0"),
        (14, f"transform:translateX({L}px);opacity:0"),
    ]
    s.append(
        keyframes("pulse", pulse, T)
        + f".pulse{{animation:pulse {T}s cubic-bezier(.45,0,.25,1) infinite}}"
    )
    body.append('<g class="pulse">' + bot("foreman", xs[0], y - 32, 0.72, fs) + "</g>")
    return frame(W, H, BOT_DEFS, "".join(s), "\n".join(body))


def board():
    W, H, cy = 1280, 356, 176
    xs = [92 + i * 156 for i in range(8)]
    stages = [
        ("Backlog", "every open", "issue", "idle"),
        ("Needs triage", "you ask, or", "the top-up picks", "idle"),
        ("Triage run", "reproduce,", "then diagnose", "run"),
        ("Needs approval", "drag to", "approve", "owner"),
        ("Approved", "build starts", "next hour", "idle"),
        ("Build run", "one PR", "per repo", "run"),
        ("In Review", "review, or the", "land gate merges", "owner"),
        ("Done", "every PR", "merged", "done"),
    ]
    movers = [MUTED, AGENT, AGENT, OWNER, AGENT, AGENT, OWNER]
    dwell = [1.2, 1.2, 2.4, 2.8, 1.2, 2.4, 2.2, 2.2]
    MOVE = 0.75
    arrive, leave, t = [], [], 0.0
    for d in dwell:
        arrive.append(t)
        t += d
        leave.append(t)
        t += MOVE
    T = leave[-1] + 0.8
    stripe = {"idle": "#5A6F93", "run": AGENT, "owner": OWNER, "done": CLEAN}
    stripe_by = ["idle", "idle", "run", "owner", "run", "run", "owner", "done"]

    s, body = [], []
    for i, (title, c1, c2, kind) in enumerate(stages):
        x = xs[i]
        stroke = {
            "owner": OWNER,
            "run": "#3A5180",
            "idle": "#2A3D60",
            "done": "#2A3D60",
        }[kind]
        dash = ' stroke-dasharray="6 6"' if kind in ("owner", "idle", "done") else ""
        sop = ".75" if kind == "owner" else "1"
        lit = [
            (0, "fill-opacity:0"),
            (max(arrive[i] - 0.01, 0), "fill-opacity:0"),
            (arrive[i] + 0.25, "fill-opacity:.10"),
            (leave[i], "fill-opacity:.10"),
            (leave[i] + 0.35, "fill-opacity:0"),
            (T, "fill-opacity:0"),
        ]
        glow = OWNER if kind == "owner" else (CLEAN if kind == "done" else AGENT)
        s.append(
            keyframes(f"s{i}", lit, T)
            + f".s{i}{{animation:s{i} {T}s linear infinite;fill-opacity:0}}"
        )
        body.append(
            f'<text x="{x}" y="{cy + 80}" text-anchor="middle" font-size="19" font-weight="650" letter-spacing="-.3" fill="{TEXT}">{title}</text>'
        )
        body.append(
            f'<rect x="{x - 64}" y="{cy - 50}" width="128" height="100" rx="14" fill="{INK}" stroke="{stroke}" stroke-opacity="{sop}" stroke-width="2"{dash}/>'
        )
        body.append(
            f'<rect class="s{i}" x="{x - 64}" y="{cy - 50}" width="128" height="100" rx="14" fill="{glow}"/>'
        )
        cap = OWNER if kind == "owner" else MUTED
        body.append(
            f'<text x="{x}" y="{cy + 106}" text-anchor="middle" font-size="17" fill="{cap}">{c1}</text>'
        )
        body.append(
            f'<text x="{x}" y="{cy + 128}" text-anchor="middle" font-size="17" fill="{cap}">{c2}</text>'
        )
        if kind == "run":
            body.append(
                f'<text x="{x}" y="{cy + 154}" text-anchor="middle" font-size="15" fill="{DIM}">In Progress</text>'
            )
    for i, color in enumerate(movers):
        x = (xs[i] + xs[i + 1]) / 2
        body.append(
            f'<path d="M{x - 6} {cy - 8} l8 8 -8 8" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    def card_kf(fn):
        stops = []
        for i in range(8):
            stops.append((arrive[i], fn(i, "at")))
            stops.append((leave[i], fn(i, "at")))
            if i < 7:
                stops.append((leave[i] + MOVE / 2, fn(i, "mid")))
        return stops

    def pos(i, phase):
        dx = xs[i] - xs[0]
        if phase == "mid":
            dx = (xs[i] + xs[i + 1]) / 2 - xs[0]
            return f"transform:translate({dx}px,-10px)"
        return f"transform:translate({dx}px,0)"

    mv = card_kf(pos)
    mv.append((T - 0.35, f"transform:translate({xs[7] - xs[0]}px,0)"))
    mv.append((T, f"transform:translate({xs[7] - xs[0]}px,0)"))
    s.append(
        keyframes("mv", mv, T)
        + f".mv{{animation:mv {T}s cubic-bezier(.5,0,.3,1) infinite}}"
    )
    fade = [
        (0, "opacity:0"),
        (0.35, "opacity:1"),
        (T - 0.4, "opacity:1"),
        (T, "opacity:0"),
    ]
    s.append(keyframes("cf", fade, T) + f".cf{{animation:cf {T}s linear infinite}}")

    def col(i, phase):
        return f"fill:{stripe[stripe_by[i]]}"

    stops = []
    for i in range(8):
        stops += [
            (arrive[i] + 0.1, col(i, "at")),
            (leave[i] + MOVE - 0.05, col(i, "at")),
        ]
    stops.append((T, col(7, "at")))
    stops.insert(0, (0, col(0, "at")))
    s.append(keyframes("st", stops, T) + f".st{{animation:st {T}s linear infinite}}")

    def window(name, ranges, prop_on, prop_off):
        st = [(0, prop_off)]
        for a, b in ranges:
            st += [
                (a - 0.01, prop_off),
                (a + 0.2, prop_on),
                (b, prop_on),
                (b + 0.2, prop_off),
            ]
        st.append((T, prop_off))
        return keyframes(name, st, T)

    s.append(
        window(
            "sp",
            [(arrive[2], leave[2]), (arrive[5], leave[5])],
            "opacity:1",
            "opacity:0",
        )
        + f".sp{{animation:sp {T}s linear infinite;opacity:0}}"
    )
    s.append(
        ".rot{animation:rot 1s linear infinite;transform-box:fill-box;transform-origin:center}@keyframes rot{to{transform:rotate(360deg)}}"
    )
    s.append(
        window("ck", [(arrive[7], T - 0.4)], "opacity:1", "opacity:0")
        + f".ck{{animation:ck {T}s linear infinite;opacity:0}}"
    )

    c0 = xs[0]
    card = [
        '<g class="mv"><g class="cf">',
        f'<rect x="{c0 - 54}" y="{cy - 33}" width="108" height="66" rx="11" fill="#1A2C4A" stroke="#3A5180" stroke-width="1.5"/>',
        f'<rect class="st" x="{c0 - 54}" y="{cy - 33}" width="6" height="66" rx="3" fill="#5A6F93"/>',
        f'<rect x="{c0 - 38}" y="{cy - 18}" width="58" height="9" rx="4.5" fill="#6479A0"/>',
        f'<rect x="{c0 - 38}" y="{cy}" width="76" height="7" rx="3.5" fill="#33476A"/>',
        f'<rect x="{c0 - 38}" y="{cy + 13}" width="44" height="7" rx="3.5" fill="#33476A"/>',
        f'<g class="sp"><circle class="rot" cx="{c0 + 36}" cy="{cy - 14}" r="8" fill="none" stroke="{AGENT}" stroke-width="2.5" stroke-dasharray="30 30" stroke-linecap="round"/></g>',
        f'<g class="ck"><circle cx="{c0 + 36}" cy="{cy - 14}" r="10" fill="{CLEAN}"/><path d="M{c0 + 31} {cy - 14} l3.5 3.5 6.5 -7" fill="none" stroke="{INK}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></g>',
    ]
    crew = {
        "foreman": (
            [(0, arrive[2]), (arrive[4], arrive[5])],
            [(0, arrive[4], "idle"), (arrive[4], T, "happy")],
        ),
        "probe": (
            [(arrive[2], arrive[4])],
            [(0, arrive[3], "work"), (arrive[3], T, "wait")],
        ),
        "rivet": (
            [(arrive[5], T)],
            [
                (0, arrive[6], "work"),
                (arrive[6], arrive[7], "wait"),
                (arrive[7], T, "happy"),
            ],
        ),
    }
    s.append(BOT_CSS)
    for kind, (vis, spans) in crew.items():
        ks, kcss = timeline(f"b{kind}", spans, T)
        s.append(kcss + win(f"v{kind}", vis, T))
        card.append(f'<g class="v{kind}">' + bot(kind, c0, cy - 52, 0.68, ks) + "</g>")
    card.append("</g></g>")
    body.extend(card)

    ga, gb = leave[3] - 1.1, arrive[4] + 0.5
    cur = [
        (0, f"transform:translate({xs[3] - xs[0]}px,26px);opacity:0"),
        (ga - 0.01, f"transform:translate({xs[3] - xs[0] + 26}px,40px);opacity:0"),
        (ga + 0.45, f"transform:translate({xs[3] - xs[0]}px,0);opacity:1"),
        (leave[3], f"transform:translate({xs[3] - xs[0]}px,0) scale(.88);opacity:1"),
        (
            leave[3] + MOVE / 2,
            f"transform:translate({(xs[3] + xs[4]) / 2 - xs[0]}px,-10px) scale(.88);opacity:1",
        ),
        (arrive[4], f"transform:translate({xs[4] - xs[0]}px,0) scale(.88);opacity:1"),
        (arrive[4] + 0.2, f"transform:translate({xs[4] - xs[0]}px,0);opacity:1"),
        (gb, f"transform:translate({xs[4] - xs[0] + 24}px,30px);opacity:0"),
        (T, f"transform:translate({xs[4] - xs[0] + 24}px,30px);opacity:0"),
    ]
    s.append(
        keyframes("cur", cur, T)
        + f".cur{{animation:cur {T}s cubic-bezier(.5,0,.3,1) infinite;opacity:0;transform-box:fill-box;transform-origin:center}}"
    )
    hx, hy = c0 + 14, cy + 4
    body.append(
        f'<g class="cur"><g transform="translate({hx} {hy})">'
        f'<path d="M0 0 L0 24 L6.5 18 L11 28 L15 26 L10.5 16.5 L19 16.5 Z" fill="{OWNER}" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>'
        f"</g></g>"
    )
    return frame(W, H, BOT_DEFS, "".join(s), "\n".join(body))


def crew():
    W, H, g, sc = 1280, 392, 262, 1.7
    order = ["foreman", "patch", "probe", "rivet", "ledger"]
    look = {
        "foreman": "work",
        "patch": "idle",
        "probe": "work",
        "rivet": "idle",
        "ledger": "idle",
    }
    gest = (
        ".g-foreman{animation:gf 4.5s ease-in-out infinite}@keyframes gf{0%,50%,100%{transform:rotate(0)}62%,84%{transform:rotate(105deg)}}"
        ".g-patch{animation:gp 1.4s ease-in-out infinite}@keyframes gp{0%,100%{transform:rotate(-12deg)}50%{transform:rotate(28deg)}}"
        ".g-probe{animation:gq 3.2s ease-in-out infinite alternate}@keyframes gq{0%{transform:rotate(8deg)}100%{transform:rotate(72deg)}}"
        ".g-rivet{animation:gr 1s cubic-bezier(.5,0,.2,1) infinite}@keyframes gr{0%,100%{transform:rotate(0)}55%{transform:rotate(70deg)}70%{transform:rotate(-6deg)}}"
        ".g-ledger{animation:gl .45s ease-in-out infinite alternate}@keyframes gl{to{transform:rotate(10deg)}}"
    )
    body = []
    for i, kind in enumerate(order):
        name, accent, _, role = CREW[kind]
        x = 168 + i * 236
        b = bot(
            kind,
            x,
            g,
            sc,
            {look[kind]: ""},
            arm=f"g-{kind}",
            spin=look[kind] != "idle",
            delay=-i * 0.7,
        )
        body.append(
            b.replace(
                'class="blink"',
                f'class="blink" style="animation-delay:{-i * 1.3:.1f}s"',
            )
        )
        body.append(
            f'<text x="{x}" y="{g + 52}" text-anchor="middle" font-size="25" font-weight="700" letter-spacing="-.4" fill="{TEXT}">{name}</text>'
        )
        body.append(
            f'<text x="{x}" y="{g + 80}" text-anchor="middle" font-size="18" fill="{MUTED}">{role}</text>'
        )
        body.append(
            f'<rect x="{x - 14}" y="{g + 94}" width="28" height="4" rx="2" fill="{accent}"/>'
        )
    return frame(W, H, BOT_DEFS, BOT_CSS + gest, "\n".join(body))


def states():
    W, H, g, sc = 1280, 352, 222, 1.45
    rows = [
        ("idle", "", False, "Idle", "between runs", MUTED),
        ("work", "", True, "Working", "on a task", AGENT),
        ("wait", "bob", True, "Waiting on you", "approve or review", OWNER),
        ("happy", "hop", True, "Clean", "merged and green", CLEAN),
        ("alarm", "shake", True, "Blocked", "failed twice or stuck", RED),
    ]
    body = []
    for i, (st, motion, spin, label, cap, color) in enumerate(rows):
        x = 168 + i * 236
        body.append(bot("foreman", x, g, sc, {st: ""}, motion=motion, spin=spin))
        body.append(
            f'<text x="{x}" y="{g + 56}" text-anchor="middle" font-size="23" font-weight="650" letter-spacing="-.3" fill="{color}">{label}</text>'
        )
        body.append(
            f'<text x="{x}" y="{g + 84}" text-anchor="middle" font-size="18" fill="{MUTED}">{cap}</text>'
        )
    return frame(W, H, BOT_DEFS, BOT_CSS, "\n".join(body))


def badges():
    motion = {"idle": "", "work": "", "wait": "bob", "happy": "hop", "alarm": "shake"}
    for kind in CREW:
        for st, mv in motion.items():
            body = bot(kind, 84, 124, 1.1, {st: ""}, motion=mv, spin=st != "idle")
            yield f"{kind}-{st}.svg", frame(168, 144, BOT_DEFS, BOT_CSS, body)


(OUT / "badges").mkdir(exist_ok=True)
for name, svg in badges():
    (OUT / "badges" / name).write_text(svg)
(OUT / "hero.svg").write_text(hero())
(OUT / "hourly-run.svg").write_text(loop())
(OUT / "card-journey.svg").write_text(board())
(OUT / "crew.svg").write_text(crew())
(OUT / "states.svg").write_text(states())
print("ok")
