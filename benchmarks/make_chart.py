"""Draw docs/results-chart.svg from the paired test results in benchmarks/results/.

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

# Latest version of each kind of task. Older and negative runs are on docs/benchmarks.md.
GROUPS = [
    ("Long conversation", "8 questions about Django", ["20261013T115226Z"]),
    ("Questions about code", "6 Django questions", ["20261012T222717Z", "20261012T225947Z"]),
    ("Editing tasks", "fix a bug, add a feature", ["20261013T122703Z", "20261013T123701Z"]),
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


def tokens_label(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def draw() -> str:
    width, left, col_w, gap = 860, 230, 290, 30
    bar_h, bar_gap, row_h = 18, 4, 72
    top = 132
    height = top + row_h * len(GROUPS) + 70
    cols = [("Total tokens", 0, tokens_label), ("Cost (Claude Code estimate)", 1, lambda v: f"${v:.2f}")]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'role="img" aria-labelledby="t d" font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif">',
        "<title id=\"t\">BaleContext against plain Claude Code: whole-task tokens and cost</title>",
        "<desc id=\"d\">Grouped bars per kind of task, plain Claude Code against Claude Code with BaleContext.</desc>",
        "<style>",
        ".bg{fill:#fcfcfb}.t1{fill:#0b0b0b}.t2{fill:#52514e}.plain{fill:#8a8984}.bale{fill:#2a78d6}.rule{stroke:#e4e3df}",
        ".big{fill:#2a78d6}",
        "@media (prefers-color-scheme: dark){.bg{fill:#1a1a19}.t1{fill:#f0efec}.t2{fill:#c3c2b7}"
        ".plain{fill:#77766f}.bale{fill:#3987e5}.big{fill:#5598e7}.rule{stroke:#383835}}",
        "</style>",
        f'<rect class="bg" width="{width}" height="{height}" rx="12"/>',
    ]
    best = totals(GROUPS[0][2])
    tok_cut = 1 - best["hook"][0] / best["without"][0]
    cost_cut = 1 - best["hook"][1] / best["without"][1]
    parts += [
        f'<text class="big" x="24" y="52" font-size="34" font-weight="700">{tok_cut:.0%} fewer tokens · {cost_cut:.0%} lower cost</text>',
        f'<text class="t2" x="24" y="78" font-size="15">{esc(GROUPS[0][0])} ({esc(GROUPS[0][1])}), whole task, same model and prompts</text>',
        f'<rect class="plain" x="{left}" y="98" width="12" height="12" rx="2"/>',
        f'<text class="t1" x="{left + 18}" y="109" font-size="13">Plain Claude Code</text>',
        f'<rect class="bale" x="{left + 150}" y="98" width="12" height="12" rx="2"/>',
        f'<text class="t1" x="{left + 168}" y="109" font-size="13">With BaleContext</text>',
    ]
    for c, (name, _, _) in enumerate(cols):
        x = left + c * (col_w + gap)
        parts.append(f'<text class="t2" x="{x}" y="{top - 4}" font-size="12" font-weight="600">{esc(name.upper())}</text>')
    for r, (name, sub, stamps) in enumerate(GROUPS):
        y = top + 10 + r * row_h
        data = totals(stamps)
        parts.append(f'<line class="rule" x1="24" x2="{width - 24}" y1="{y - 8}" y2="{y - 8}"/>')
        parts.append(f'<text class="t1" x="24" y="{y + 16}" font-size="15" font-weight="600">{esc(name)}</text>')
        parts.append(f'<text class="t2" x="24" y="{y + 34}" font-size="12">{esc(sub)}</text>')
        for c, (_, i, fmt) in enumerate(cols):
            x = left + c * (col_w + gap)
            plain, bale = data["without"][i], data["hook"][i]
            span = col_w - 110
            for k, (cls, val) in enumerate((("plain", plain), ("bale", bale))):
                by = y + 4 + k * (bar_h + bar_gap)
                w = max(2.0, span * val / plain)
                parts.append(f'<rect class="{cls}" x="{x}" y="{by}" width="{w:.1f}" height="{bar_h}" rx="4"/>')
                label = fmt(val)
                if cls == "bale":
                    label += f"  ({1 - val / plain:.0%} less)" if val <= plain else f"  ({val / plain - 1:.0%} more)"
                parts.append(f'<text class="t1" x="{x + w + 6:.1f}" y="{by + 13}" font-size="12">{esc(label)}</text>')
    foot = top + row_h * len(GROUPS) + 18
    parts += [
        f'<text class="t2" x="24" y="{foot}" font-size="12">Claude Code with sonnet, totals of every model call. Answers and code changes were checked against the source;</text>',
        f'<text class="t2" x="24" y="{foot + 17}" font-size="12">one answer was thinner than plain Claude\'s. All runs, including weaker and negative ones: docs/benchmarks.md</text>',
        f'<text class="t2" x="24" y="{foot + 34}" font-size="11">Drawn by benchmarks/make_chart.py from benchmarks/results/.</text>',
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    OUT.write_text(draw(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
