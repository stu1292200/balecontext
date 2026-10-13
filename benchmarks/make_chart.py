"""Draw the results charts from the paired test results in benchmarks/results/.

docs/results-chart.svg is the summary on the README; docs/history-chart.svg shows
every test in the order it was run, with its cost saved or lost.

    python benchmarks/make_chart.py

Each group adds up the median whole-task tokens and median cost that
claude_pair.py wrote in the summary of the listed result files, so the chart
can be redrawn by anyone from the published files. No model calls.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
OUT = ROOT / "docs" / "results-chart.svg"
HISTORY_OUT = ROOT / "docs" / "history-chart.svg"

# Latest version of each kind of task. Older and negative runs are on docs/benchmarks.md.
GROUPS = [
    ("Long chats", "8 questions in a row", ["20261013T115226Z"]),
    ("Questions about code", "6 questions", ["20261012T222717Z", "20261012T225947Z"]),
    ("Bug fixes and features", "2 tasks", ["20261013T122703Z", "20261013T123701Z"]),
]


def summary(stamp: str) -> dict:
    text = (RESULTS / f"{stamp}.md").read_text(encoding="utf-8")
    match = re.search(r"## Summary\s*```json\s*(\{.*?\})\s*```", text, re.S)
    if not match:
        raise SystemExit(f"no summary JSON in {stamp}.md")
    return json.loads(match.group(1))


def totals(stamps: list[str]) -> dict:
    out = {"without": [0.0, 0.0], "hook": [0.0, 0.0]}
    for stamp in stamps:
        data = summary(stamp)
        for arm in out:
            out[arm][0] += data[arm]["median_total_tokens"]
            out[arm][1] += data[arm]["median_cost_usd_estimate"]
    return out


# Every published test in the order it was run: (result file, kind in plain words, note shown above it).
HISTORY = [
    ("20261012T115817Z", "Question", ""),
    ("20261012T143438Z", "Question", ""),
    ("20261012T153206Z", "Question", "From here on, all questions are asked in one chat"),
    ("20261012T154024Z", "Question", ""),
    ("20261012T190840Z", "Question", ""),
    ("20261012T191349Z", "Question", ""),
    ("20261012T205020Z", "Question", ""),
    ("20261012T211518Z", "Question", ""),
    ("20261012T222717Z", "Question", ""),
    ("20261012T225947Z", "Question", ""),
    ("20261012T232728Z", "Question", ""),
    ("20261013T114230Z", "Bug fix", "We start testing bug fixes and new features. These cost more at first"),
    ("20261013T114524Z", "Bug fix", ""),
    ("20261013T114752Z", "Feature", ""),
    ("20261013T115024Z", "Feature", ""),
    ("20261013T115226Z", "Long chat", ""),
    ("20261013T122703Z", "Feature", "We fixed it: shorter summaries for bug fixes and features"),
    ("20261013T123701Z", "Bug fix", ""),
]

STYLE = (
    "<style>"
    ".bg{fill:#ffffff}.ink{fill:#111111}.sub{fill:#5c5b57}.mute{fill:#8a8984}.acc{fill:#2a78d6}.card{fill:#f6f6f3}"
    ".good{fill:#0ca30c}.bad{fill:#d03b3b}.goodt{fill:#0a7d0a}.badt{fill:#b22e2e}"
    ".mark{fill:none;stroke:#ffffff;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}"
    "@media (prefers-color-scheme: dark){.bg{fill:#161b22}.ink{fill:#f0efec}.sub{fill:#b8b7b0}.mute{fill:#8f8e88}"
    ".acc{fill:#5598e7}.card{fill:#1f252e}.goodt{fill:#4cc24c}.badt{fill:#e66767}}"
    "</style>"
)
FONT = "Inter, -apple-system, Segoe UI, Helvetica, Arial, sans-serif"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, cls, size, weight=400, anchor="start"):
    return (f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}">{esc(s)}</text>')


def svg_open(width, height, title, desc):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="t d" font-family="{FONT}">',
        f'<title id="t">{esc(title)}</title>', f'<desc id="d">{esc(desc)}</desc>', STYLE,
        f'<rect class="bg" width="{width}" height="{height}" rx="16"/>',
    ]


def card(x, y, w, h):
    return f'<rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>'


def draw() -> str:
    width, pad, gap = 960, 44, 20
    card_w = (width - 2 * pad - 2 * gap) / 3
    card_y, card_h = 150, 220
    height = card_y + card_h + 80
    out = svg_open(width, height, "What BaleContext saves",
                   "Three numbers: how much less Claude uses with BaleContext in long chats, quick questions "
                   "and bug fixes, and how much cheaper each is.")
    out += [
        text(pad, 62, "Claude with BaleContext uses less and costs less", "ink", 28, 700),
        text(pad, 96, "We gave Claude the same tasks twice: once alone and once with BaleContext. "
             "Answers were just as good.", "sub", 15),
    ]
    for k, (name, sub, stamps) in enumerate(GROUPS):
        data = totals(stamps)
        use = 1 - data["hook"][0] / data["without"][0]
        cost = 1 - data["hook"][1] / data["without"][1]
        x = pad + k * (card_w + gap)
        cx = x + card_w / 2
        out += [
            card(x, card_y, card_w, card_h),
            text(cx, card_y + 42, name, "ink", 17, 600, "middle"),
            text(cx, card_y + 116, f"{use:.0%}", "acc", 64, 700, "middle"),
            text(cx, card_y + 146, "less usage", "ink", 17, 600, "middle"),
            text(cx, card_y + 190, f"and {cost:.0%} cheaper", "sub", 16, 400, "middle"),
        ]
    out += [
        text(pad, height - 34, "Less usage means you reach your Claude limit later.", "mute", 14),
        "</svg>",
    ]
    return "\n".join(out) + "\n"


def draw_history() -> str:
    width, pad = 960, 44
    per_row, cell_h, gap = 6, 118, 14
    cell_w = (width - 2 * pad - (per_row - 1) * gap) / per_row
    savings = [summary(stamp)["hook"]["cost_saving_percent"] for stamp, _, _ in HISTORY]
    saved = sum(1 for s in savings if s >= 0)
    top = 150
    rows = -(-len(HISTORY) // per_row)
    height = top + rows * (cell_h + gap) + 30
    out = svg_open(width, height, "Every test we ran",
                   f"{len(HISTORY)} tests in the order they were run. {saved} were cheaper with BaleContext, "
                   f"{len(HISTORY) - saved} cost more.")
    out += [
        text(pad, 62, f"We ran {len(HISTORY)} tests. {saved} were cheaper, {len(HISTORY) - saved} cost more.",
             "ink", 28, 700),
        text(pad, 96, "Bug fixes and new features cost more at first. We fixed that, and the tests after it "
             "were cheaper again.", "sub", 15),
    ]
    for n, ((stamp, kind, _), s) in enumerate(zip(HISTORY, savings)):
        x = pad + (n % per_row) * (cell_w + gap)
        y = top + (n // per_row) * (cell_h + gap)
        cx = x + cell_w / 2
        good = s >= 0
        out.append(card(x, y, cell_w, cell_h))
        out.append(text(cx, y + 26, f"Test {n + 1}", "ink", 14, 600, "middle"))
        out.append(f'<circle class="{"good" if good else "bad"}" cx="{cx:.1f}" cy="{y + 54}" r="15"/>')
        mark = (f'<path class="mark" d="M{cx - 7:.1f} {y + 54} l5 5 l9 -10"/>' if good else
                f'<path class="mark" d="M{cx - 6:.1f} {y + 48} l12 12 M{cx + 6:.1f} {y + 48} l-12 12"/>')
        out.append(mark)
        word = f"{abs(s):.0f}% cheaper" if good else f"{abs(s):.0f}% more"
        out.append(text(cx, y + 90, word, "goodt" if good else "badt", 14, 700, "middle"))
        out.append(text(cx, y + 108, kind, "mute", 12, 400, "middle"))
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    OUT.write_text(draw(), encoding="utf-8")
    HISTORY_OUT.write_text(draw_history(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} and {HISTORY_OUT.relative_to(ROOT)}")
