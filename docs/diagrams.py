"""Generate the README diagrams (light + dark) from Concord's design tokens.

    python docs/diagrams.py
"""
from pathlib import Path

OUT = Path(__file__).parent / "media"

THEMES = {
    "light": dict(paper="#FCFCFD", sunk="#F4F5F7", ink="#1A1D23", soft="#5B616E", line="#E4E6EB",
                  accent="#2C3E66", concord="#3B6B57", conflict="#8E3B46", review="#9A6B2F", card="#FFFFFF"),
    "dark": dict(paper="#14161A", sunk="#1C1F25", ink="#E6E8EC", soft="#99A0AD", line="#2A2E36",
                 accent="#9FB2D9", concord="#7FB39B", conflict="#D58A94", review="#D2A86C", card="#181A1F"),
}
SERIF = "Newsreader, 'Iowan Old Style', Georgia, 'Times New Roman', serif"
SANS = "Geist, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'Geist Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Svg:
    def __init__(self, w: int, h: int, t: dict):
        self.w, self.h, self.t, self.parts = w, h, t, []

    def add(self, s: str):
        self.parts.append(s)

    def text(self, x, y, s, size=13, color="ink", font=SANS, weight=400, anchor="start", italic=False, spacing=0):
        style = ' font-style="italic"' if italic else ""
        ls = f' letter-spacing="{spacing}"' if spacing else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{weight}" '
                 f'fill="{self.t[color]}" text-anchor="{anchor}"{style}{ls}>{esc(s)}</text>')

    def rect(self, x, y, w, h, fill="card", stroke="line", r=6, sw=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        fillv = "none" if fill == "none" else self.t[fill]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fillv}" '
                 f'stroke="{self.t[stroke]}" stroke-width="{sw}"{d}/>')

    def line(self, x1, y1, x2, y2, color="soft", sw=1.25, arrow=True, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = ' marker-end="url(#arrow)"' if arrow else ""
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{self.t[color]}" stroke-width="{sw}"{d}{m}/>')

    def path(self, d, color="soft", sw=1.25, arrow=True, dash=None):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        m = ' marker-end="url(#arrow)"' if arrow else ""
        self.add(f'<path d="{d}" fill="none" stroke="{self.t[color]}" stroke-width="{sw}"{da}{m}/>')

    def dot(self, x, y, r, color):
        self.add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{self.t[color]}"/>')

    def render(self) -> str:
        t = self.t
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}">'
                f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                f'orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9" fill="none" stroke="{t["soft"]}" '
                f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>'
                f'<rect width="100%" height="100%" rx="12" fill="{t["paper"]}"/>')
        return head + "".join(self.parts) + "</svg>"


# ---------------------------------------------------------------- architecture

def chip(s: Svg, x, y, w, title, sub=None, fill="sunk", title_color="ink"):
    h = 44 if sub else 30
    s.rect(x, y, w, h, fill=fill, stroke=fill, r=4)
    s.text(x + 12, y + (19 if sub else 19), title, 13, title_color, weight=500)
    if sub:
        s.text(x + 12, y + 35, sub, 11.5, "soft", font=MONO)
    return y + h


def stage(s: Svg, x, y, w, h, n, title, caption):
    s.rect(x, y, w, h)
    s.text(x + 18, y + 30, n, 12, "soft", font=MONO)
    s.text(x + 18, y + 56, title, 20, "ink", font=SERIF)
    s.text(x + 18, y + h - 18, caption, 12, "soft", italic=True, font=SERIF)


def architecture(theme: str) -> str:
    t = THEMES[theme]
    s = Svg(1280, 850, t)

    s.text(48, 64, "How Concord checks an upload", 30, "ink", font=SERIF)
    s.text(48, 92, "Every claim is compared only with the few passages it could contradict. "
                   "Each stage is cheaper than the next, and passes on less.", 14.5, "soft")

    # ---- lane: application
    ly = 124
    s.text(48, ly + 14, "Application", 12.5, "soft", weight=500)
    s.line(48, ly + 24, 1232, ly + 24, "line", 1, arrow=False)
    by = ly + 44
    s.rect(48, by, 250, 64)
    s.text(66, by + 27, "Console", 16, "ink", font=SERIF)
    s.text(66, by + 48, "Next.js on :3000", 12.5, "soft", font=MONO)
    s.path(f"M 298 {by + 22} L 418 {by + 22}")
    s.text(358, by + 14, "upload file", 11.5, "soft", anchor="middle")
    s.path(f"M 420 {by + 44} L 300 {by + 44}")
    s.text(358, by + 60, "poll progress, verdict", 11.5, "soft", anchor="middle")
    s.rect(420, by, 330, 64)
    s.text(438, by + 27, "API and audit job", 16, "ink", font=SERIF)
    s.text(438, by + 48, "FastAPI on :8000, one worker thread", 12.5, "soft", font=MONO)
    s.path(f"M 750 {by + 32} L 858 {by + 32}")
    s.rect(860, by, 372, 64)
    s.text(878, by + 27, "Decide", 16, "ink", font=SERIF)
    s.text(878, by + 48, "commit, replace, or override with a reason", 12.5, "soft")

    # ---- lane: pipeline
    py = 270
    s.text(48, py + 14, "The audit, per claim", 12.5, "soft", weight=500)
    s.line(48, py + 24, 1232, py + 24, "line", 1, arrow=False)
    top, h = py + 52, 338
    s.path(f"M 585 {by + 64} L 585 {py + 38} L 133 {py + 38} L 133 {top - 2}", dash="3 4")
    s.text(593, py + 20, "for each claim", 11.5, "soft")

    # 1 read
    x1, w1 = 48, 170
    stage(s, x1, top, w1, h, "01", "Read", "structure first")
    y = chip(s, x1 + 14, top + 78, w1 - 28, "Sections", "headings, pages")
    y = chip(s, x1 + 14, y + 10, w1 - 28, "Claims", "one per sentence")
    y = chip(s, x1 + 14, y + 10, w1 - 28, "Provenance", "section, page, span")
    s.rect(x1 + 14, y + 16, w1 - 28, 44, fill="none", stroke="conflict", r=4, dash="3 3")
    s.text(x1 + 26, y + 35, "Duplicate?", 13, "conflict", weight=500)
    s.text(x1 + 26, y + 51, "hash or claims", 11.5, "soft", font=MONO)
    dup_y = y + 38

    # 2 retrieve
    x2, w2 = x1 + w1 + 30, 250
    s.line(x1 + w1 + 2, top + 150, x2 - 2, top + 150)
    stage(s, x2, top, w2, h, "02", "Retrieve", "recall first, precision later")
    half = (w2 - 28 - 10) // 2
    chip(s, x2 + 14, top + 78, half, "Dense", "bge-small")
    chip(s, x2 + 14 + half + 10, top + 78, half, "Keyword", "BM25")
    cx = x2 + w2 // 2
    s.line(x2 + 14 + half // 2, top + 124, cx - 6, top + 142, arrow=False)
    s.line(x2 + 24 + half * 3 // 2, top + 124, cx + 6, top + 142, arrow=False)
    y = chip(s, x2 + 14, top + 144, w2 - 28, "Reciprocal rank fusion", "top 20")
    s.line(cx, y + 1, cx, y + 13)
    y = chip(s, x2 + 14, y + 14, w2 - 28, "Cross-encoder rerank", "bge-reranker-base")
    s.line(cx, y + 1, cx, y + 13)
    chip(s, x2 + 14, y + 14, w2 - 28, "Top 6 passages per claim", fill="sunk", title_color="accent")

    # 3 gate
    x3, w3 = x2 + w2 + 30, 250
    s.line(x2 + w2 + 2, top + 150, x3 - 2, top + 150)
    stage(s, x3, top, w3, h, "03", "Check", "many cheap local calls")
    y = chip(s, x3 + 14, top + 78, w3 - 28, "Same subject", "cosine ≥ 0.62")
    s.text(x3 + w3 // 2, y + 19, "and", 12.5, "soft", anchor="middle", italic=True, font=SERIF)
    y = chip(s, x3 + 14, y + 28, w3 - 28, "Laya: contradicts", "noul ≥ 0.75, batched")
    s.text(x3 + w3 // 2, y + 19, "or", 12.5, "soft", anchor="middle", italic=True, font=SERIF)
    y = chip(s, x3 + 14, y + 28, w3 - 28, "Numbers and dates differ", "deterministic parser")
    s.text(x3 + 14, y + 26, "Gray zone goes to Review", 12, "review", weight=500)

    # 4 llm
    x4, w4 = x3 + w3 + 30, 200
    s.line(x3 + w3 + 2, top + 150, x4 - 2, top + 150)
    stage(s, x4, top, w4, h, "04", "Confirm", "flagged pairs only")
    y = chip(s, x4 + 14, top + 78, w4 - 28, "Local LLM", "Ollama qwen2.5:3b")
    y = chip(s, x4 + 14, y + 10, w4 - 28, "Names both subjects", "then rules")
    y = chip(s, x4 + 14, y + 10, w4 - 28, "Explains the conflict", "quotes real values")
    s.text(x4 + 14, y + 26, "Only confirmed pairs block", 12, "soft", weight=500)

    # 5 verdict
    x5 = x4 + w4 + 30
    w5 = 1232 - x5
    s.line(x4 + w4 + 2, top + 150, x5 - 2, top + 150)
    s.text(x5, top + 30, "05", 12, "soft", font=MONO)
    s.text(x5, top + 56, "Verdict", 20, "ink", font=SERIF)
    pills = [("Conflict found", "conflict", "blocked"), ("Needs review", "review", "confirm to add"),
             ("In concord", "concord", "add to record"), ("Already in the record", "conflict", "rejected")]
    for i, (label, color, sub) in enumerate(pills):
        yy = top + 80 + i * 56
        s.rect(x5, yy, w5, 46, fill="card", stroke="line", r=4)
        s.dot(x5 + 16, yy + 18, 4, color)
        s.text(x5 + 28, yy + 22, label, 13, color, weight=500)
        s.text(x5 + 28, yy + 38, sub, 11.5, "soft")
    # duplicate shortcut, along the bottom
    s.path(f"M {x1 + w1 - 14} {dup_y} L {x1 + w1 + 16} {dup_y} L {x1 + w1 + 16} {top + h + 18} "
           f"L {x5 + w5 // 2} {top + h + 18} L {x5 + w5 // 2} {top + 80 + 3 * 56 + 48}",
           color="conflict", dash="3 4")
    s.text(x5 - 12, top + h + 13, "duplicates exit early", 11, "conflict", anchor="end")

    # ---- lane: stores
    sy = 718
    s.text(48, sy + 14, "Local stores, all files under backend/data", 12.5, "soft", weight=500)
    s.line(48, sy + 24, 1232, sy + 24, "line", 1, arrow=False)
    stores = [("Chroma", "claim vectors, cosine"), ("SQLite", "documents, claims, reports, overrides"),
              ("BM25", "in-memory keyword index"), ("Model cache", "Laya, bge, reranker, Ollama")]
    sw_ = (1232 - 48 - 3 * 16) // 4
    for i, (name, sub) in enumerate(stores):
        xx = 48 + i * (sw_ + 16)
        s.rect(xx, sy + 40, sw_, 58, fill="sunk", stroke="sunk", r=4)
        s.text(xx + 16, sy + 64, name, 15, "ink", font=SERIF)
        s.text(xx + 16, sy + 84, sub, 12, "soft")
    # retrieval reads Chroma (dense) and BM25 (keyword)
    rx = x2 + w2 // 2
    for i in (0, 2):
        cx_ = 48 + i * (sw_ + 16) + sw_ - 36
        s.path(f"M {cx_} {sy + 38} L {cx_} {top + h + 40} L {rx} {top + h + 40}", dash="3 4", arrow=False)
    s.path(f"M {rx} {top + h + 40} L {rx} {top + h + 2}", dash="3 4")
    s.text(rx + 8, top + h + 56, "retrieval reads the vector and keyword indexes", 11, "soft")
    return s.render()


# ---------------------------------------------------------------- banner

def banner(theme: str) -> str:
    t = THEMES[theme]
    s = Svg(1280, 300, t)
    s.text(64, 128, "Concord", 76, "ink", font=SERIF)
    s.text(66, 172, "Nothing enters the record that contradicts it.", 22, "soft", font=SERIF, italic=True)
    s.text(66, 226, "A local consistency gate for a knowledge base. Retrieval, Laya, and a local LLM,", 15, "soft")
    s.text(66, 248, "on CPU, on one machine.", 15, "soft")
    # a small verdict card, echoing the product's hero moment
    x, y, w = 800, 64, 416
    s.rect(x, y, w, 176, fill="card")
    s.dot(x + 28, y + 42, 5, "conflict")
    s.text(x + 44, y + 50, "Conflict found", 26, "conflict", font=SERIF)
    s.rect(x + 24, y + 72, 176, 60, fill="sunk", stroke="sunk", r=4)
    s.text(x + 36, y + 92, "In the record", 11, "soft")
    s.text(x + 36, y + 116, "retained for 90 days", 14.5, "ink", font=SERIF)
    s.rect(x + 216, y + 72, 176, 60, fill="sunk", stroke="sunk", r=4)
    s.text(x + 228, y + 92, "This upload", 11, "soft")
    s.text(x + 228, y + 116, "kept for 30 days", 14.5, "ink", font=SERIF)
    s.text(x + 24, y + 158, "data-retention-policy.md, §3.2", 12, "accent")
    s.text(x + w - 24, y + 158, "confidence 1.00", 12, "soft", font=MONO, anchor="end")
    return s.render()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (OUT / f"architecture-{theme}.svg").write_text(architecture(theme), encoding="utf-8")
        (OUT / f"banner-{theme}.svg").write_text(banner(theme), encoding="utf-8")
    print("wrote", sorted(p.name for p in OUT.glob("*.svg")))
