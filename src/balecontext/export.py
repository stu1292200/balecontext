"""Offline, self-contained graph export. No CDN scripts or telemetry."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from .store import Project


def export(project: Project, output: Path | None = None) -> dict:
    graph = project.graph()
    target = output or project.state
    target.mkdir(parents=True, exist_ok=True)
    graph_path = target / "graph.json"
    viewer_path = target / "graph.html"
    report_path = target / "REPORT.md"
    graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    payload = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    viewer_path.write_text(files("balecontext").joinpath("viewer.html").read_text(encoding="utf-8").replace("__BALECONTEXT_DATA__", payload), encoding="utf-8")
    counts: dict[str, int] = {}
    for relation in graph["edges"]:
        counts[relation["evidence"]] = counts.get(relation["evidence"], 0) + 1
    degrees: dict[str, int] = {}
    for relation in graph["edges"]:
        if relation["relation"] not in {"defines", "contains"}:
            for identifier in (relation["source"], relation["target"]):
                degrees[identifier] = degrees.get(identifier, 0) + 1
    nodes = {n["id"]: n for n in graph["nodes"]}
    hubs = sorted(degrees, key=lambda n: (-degrees[n], nodes[n]["path"]))[:10]
    lines = [f"# BaleContext: {graph['project']}", "", f"{len(graph['nodes'])} nodes · {len(graph['edges'])} relationships · {graph['stats']['files']} files", "", "## Provenance", "", "EXTRACTED = syntax or containment found directly. INFERRED = static call-target resolution; verify before editing.", "", f"Evidence counts: {json.dumps(counts)}", "", "## Most connected symbols", ""]
    lines.extend(f"- `{nodes[n]['name']}` — `{nodes[n]['path']}:{nodes[n]['start']}` ({degrees[n]} links)" for n in hubs)
    lines.extend(["", "## Coverage", "", f"Parsers: {json.dumps(graph['stats']['parsers'])}", "", "Python uses the standard AST. JavaScript/TypeScript use optional tree-sitter parsers. Other supported text is chunked; it has no structural call graph.", "", "Groups in the viewer are connected components, not semantic communities. Dynamic calls, external packages, re-exports and aliases may be unresolved.", "", "No model-token savings or answer-quality improvements are claimed by this report."])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"graph": str(graph_path), "viewer": str(viewer_path), "report": str(report_path)}
