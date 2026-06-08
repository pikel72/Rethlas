from __future__ import annotations

import html
import json
import socketserver
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .config import RethlasConfig
from .events import event_path, iter_events


@dataclass(frozen=True)
class ViewerBuild:
    output_dir: Path
    page_count: int


@dataclass(frozen=True)
class ResultStatus:
    problem_id: str
    source: Optional[Path]
    badge: str  # "verified" | "draft" | "missing"
    latest_event_type: Optional[str]
    latest_event_at: Optional[str]
    last_update_display: str

    @property
    def has_events(self) -> bool:
        return self.latest_event_type is not None


def _problem_status_for_viewer(
    problem_id: str, results_dir: Path, logs_dir: Path
) -> ResultStatus:
    result_dir = results_dir / problem_id
    source = _best_result_file(result_dir)
    if source is not None and source.name == "blueprint_verified.md":
        badge = "verified"
    elif source is not None and source.name == "blueprint.md":
        badge = "draft"
    else:
        badge = "missing"

    log_dir = logs_dir / problem_id
    events_path = event_path(log_dir)
    latest_type: Optional[str] = None
    latest_at: Optional[str] = None
    if events_path.is_file():
        for event in iter_events(log_dir):
            latest_type = event.get("event_type")
            latest_at = event.get("timestamp_utc")
    last_update_display = _format_last_update(latest_at, log_dir, result_dir)
    return ResultStatus(
        problem_id=problem_id,
        source=source,
        badge=badge,
        latest_event_type=latest_type,
        latest_event_at=latest_at,
        last_update_display=last_update_display,
    )


def _format_last_update(
    latest_event_at: Optional[str], log_dir: Path, result_dir: Path
) -> str:
    """Return a short human-readable "last update" string for the index.

    We prefer the latest event timestamp; if there are no events yet we
    fall back to the most recent mtime across the result and log
    directories. Returns ``"never"`` when nothing exists.
    """
    if latest_event_at:
        return _short_timestamp(latest_event_at)
    candidates: List[Path] = []
    for directory in (log_dir, result_dir):
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file():
                    candidates.append(path)
    if not candidates:
        return "never"
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return _format_mtime(latest.stat().st_mtime)


def _short_timestamp(iso_timestamp: str) -> str:
    if not isinstance(iso_timestamp, str) or "T" not in iso_timestamp:
        return iso_timestamp or ""
    time_part = iso_timestamp.split("T", 1)[1]
    return time_part[:8]


def _format_mtime(mtime: float) -> str:
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return dt.strftime("%H:%M:%S UTC")


def build_results_viewer(config: RethlasConfig) -> ViewerBuild:
    generation_dir = config.paths.generation_dir
    results_dir = generation_dir / "results"
    logs_dir = generation_dir / "logs"
    output_dir = generation_dir / "viewer"
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    _clean_generated_pages(pages_dir)

    pages: list[tuple[str, str, str]] = []
    for result_dir in _iter_result_dirs(results_dir):
        source = _best_result_file(result_dir)
        if source is None:
            continue
        problem_id = result_dir.relative_to(results_dir).as_posix()
        slug = _slug(problem_id)
        title = problem_id
        body = _render_markdown(source.read_text(encoding="utf-8", errors="replace"))
        page_html = _page_shell(title=title, body=body, home_href="../index.html")
        (pages_dir / f"{slug}.html").write_text(page_html, encoding="utf-8")
        pages.append((problem_id, f"pages/{slug}.html", source.name))

    statuses = [
        _problem_status_for_viewer(problem_id, results_dir, logs_dir)
        for problem_id, _href, _source in pages
    ]
    index_html = _index_shell(pages, statuses)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    return ViewerBuild(output_dir=output_dir, page_count=len(pages))


def serve_results_viewer(
    config: RethlasConfig,
    *,
    port: int = 3264,
    open_browser: bool = False,
) -> None:
    build = build_results_viewer(config)
    url = f"http://127.0.0.1:{port}/"
    print(f"Synced {build.page_count} result page(s) into {build.output_dir}")
    print(f"Serving results at {url}")
    if open_browser:
        webbrowser.open(url)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(build.output_dir), **kwargs)

    with socketserver.TCPServer(("127.0.0.1", port), Handler) as server:
        server.serve_forever()


def _iter_result_dirs(results_dir: Path) -> Iterable[Path]:
    if not results_dir.is_dir():
        return []
    return sorted(path for path in results_dir.rglob("*") if path.is_dir())


def _best_result_file(result_dir: Path) -> Path | None:
    verified = result_dir / "blueprint_verified.md"
    if verified.is_file():
        return verified
    draft = result_dir / "blueprint.md"
    if draft.is_file():
        return draft
    return None


def _clean_generated_pages(pages_dir: Path) -> None:
    for path in pages_dir.glob("*.html"):
        path.unlink()


def _slug(problem_id: str) -> str:
    safe = []
    for char in problem_id:
        if char.isalnum() or char in {"-", "_"}:
            safe.append(char)
        else:
            safe.append("-")
    return "".join(safe).strip("-") or "result"


def _render_markdown(markdown: str) -> str:
    text = _strip_outer_markdown_fence(markdown.strip())
    html_parts: list[str] = []
    paragraph: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            html_parts.append("<p>" + "<br>".join(html.escape(line) for line in paragraph) + "</p>")
            paragraph.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines.clear()
                in_code = False
            else:
                flush_paragraph()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            continue
        if line.startswith("#"):
            flush_paragraph()
            level = min(len(line) - len(line.lstrip("#")), 3)
            title = line[level:].strip()
            html_parts.append(f"<h{level}>{html.escape(title)}</h{level}>")
            continue
        if line.startswith("- "):
            flush_paragraph()
            html_parts.append(f"<ul><li>{html.escape(line[2:].strip())}</li></ul>")
            continue
        paragraph.append(line)
    flush_paragraph()
    if in_code:
        html_parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(html_parts)


def _strip_outer_markdown_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip() in {"```", "```markdown", "```md"} and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _index_shell(
    pages: list[tuple[str, str, str]],
    statuses: Optional[List[ResultStatus]] = None,
) -> str:
    statuses = statuses or []
    by_problem = {status.problem_id: status for status in statuses}
    if pages:
        rows = []
        for problem_id, href, source in pages:
            status = by_problem.get(problem_id)
            badge = status.badge if status else "missing"
            latest_event = status.latest_event_type if status else None
            last_update = status.last_update_display if status else "never"
            events_href = f"../logs/{problem_id}/events.jsonl"
            events_link = (
                f'<a class="events" href="{html.escape(events_href)}">events.jsonl</a>'
                if status and status.has_events
                else ""
            )
            rows.append(
                "<li>"
                f'<a class="title" href="{html.escape(href)}">{html.escape(problem_id)}</a> '
                f'<span class="badge badge-{html.escape(badge)}">{html.escape(badge)}</span> '
                f'<span class="meta">last update: {html.escape(last_update)}</span> '
                f'<span class="meta">latest event: {html.escape(latest_event or "—")}</span> '
                f"{events_link}"
                "</li>"
            )
        items = "\n".join(rows)
    else:
        items = "<li>No generated results yet.</li>"
    body = (
        "<h1>Rethlas Results</h1>"
        f'<p class="hint">Showing {len(pages)} result(s). '
        "Badges reflect the latest status on disk. The list is regenerated on every "
        "build; live-tail progress with <code>python -m rethlas.cli watch &lt;problem&gt;</code>.</p>"
        f'<ul class="results">{items}</ul>'
    )
    return _page_shell(title="Rethlas Results", body=body, home_href=None)


def _page_shell(title: str, body: str, home_href: str | None) -> str:
    home = f'<a class="home" href="{home_href}">Results</a>' if home_href else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <script>
    window.MathJax = {{tex: {{inlineMath: [['$', '$'], ['\\\\(', '\\\\)']]}}}};
  </script>
  <script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; color: #1f2933; background: #f7f8fa; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 32px 24px 64px; background: #fff; min-height: 100vh; box-shadow: 0 0 0 1px #e5e7eb; }}
    .home {{ display: inline-block; margin-bottom: 24px; color: #2563eb; text-decoration: none; }}
    h1, h2, h3 {{ line-height: 1.25; }}
    p {{ line-height: 1.7; }}
    pre {{ overflow: auto; padding: 16px; background: #111827; color: #f9fafb; border-radius: 6px; }}
    ul.results {{ list-style: none; padding-left: 0; }}
    ul.results li {{ margin: 14px 0; padding: 12px 16px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fafbfc; }}
    ul.results .title {{ font-weight: 600; color: #111827; text-decoration: none; margin-right: 10px; }}
    ul.results .title:hover {{ text-decoration: underline; }}
    ul.results .meta {{ color: #6b7280; margin-left: 12px; font-size: 0.88em; }}
    ul.results .events {{ color: #2563eb; margin-left: 12px; font-size: 0.85em; }}
    ul.results .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; margin-right: 6px; vertical-align: middle; }}
    .badge-verified {{ background: #d1fae5; color: #065f46; }}
    .badge-draft {{ background: #fef3c7; color: #92400e; }}
    .badge-missing {{ background: #e5e7eb; color: #374151; }}
    .hint {{ color: #4b5563; font-size: 0.95em; margin-bottom: 16px; }}
  </style>
</head>
<body>
  <main>
    {home}
    {body}
  </main>
</body>
</html>
"""
