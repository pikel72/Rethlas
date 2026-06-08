from __future__ import annotations

import html
import socketserver
import webbrowser
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Iterable

from .config import RethlasConfig


@dataclass(frozen=True)
class ViewerBuild:
    output_dir: Path
    page_count: int


def build_results_viewer(config: RethlasConfig) -> ViewerBuild:
    generation_dir = config.paths.generation_dir
    results_dir = generation_dir / "results"
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

    index_html = _index_shell(pages)
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


def _index_shell(pages: list[tuple[str, str, str]]) -> str:
    if pages:
        items = "\n".join(
            f'<li><a href="{html.escape(href)}">{html.escape(problem_id)}</a> '
            f'<span>{html.escape(source)}</span></li>'
            for problem_id, href, source in pages
        )
    else:
        items = "<li>No generated results yet.</li>"
    body = f"<h1>Rethlas Results</h1><ul class=\"results\">{items}</ul>"
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
    ul.results {{ padding-left: 20px; }}
    ul.results li {{ margin: 10px 0; }}
    ul.results span {{ color: #6b7280; margin-left: 8px; font-size: 0.9em; }}
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
