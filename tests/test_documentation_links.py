"""Keep the Markdown documentation graph navigable and locally valid."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _documents() -> set[Path]:
    documents = set(ROOT.glob("*.md"))
    for directory in ("docs", "character-work", "assets"):
        documents.update((ROOT / directory).rglob("*.md"))
    # Raw evidence notes remain append-only private records, not public-facing
    # documentation. Only the machine-neutral governance index joins the graph.
    documents.add(ROOT / "evidence" / "README.md")
    return {path.resolve() for path in documents if path.is_file()}


def _local_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    lowered = target.lower()
    if lowered.startswith(("http://", "https://", "mailto:")):
        return None
    path_part = unquote(target.split("#", 1)[0])
    return (source.parent / path_part).resolve()


def test_relative_document_links_exist():
    missing: list[str] = []
    escaped: list[str] = []
    for source in sorted(_documents()):
        text = source.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = _local_target(source, raw_target)
            if target is None:
                continue
            if not target.is_relative_to(ROOT):
                escaped.append(
                    f"{source.relative_to(ROOT)} -> {raw_target}")
            elif not target.exists():
                missing.append(
                    f"{source.relative_to(ROOT)} -> {raw_target}")
    assert missing == [], "missing local Markdown targets:\n" + "\n".join(missing)
    assert escaped == [], "local Markdown targets outside repository:\n" + "\n".join(escaped)


def test_every_markdown_document_is_reachable_from_document_hub():
    documents = _documents()
    graph = {path: set() for path in documents}
    for source in documents:
        text = source.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = _local_target(source, raw_target)
            if target in documents and target != source:
                graph[source].add(target)

    hub = (ROOT / "docs" / "README.md").resolve()
    visited: set[Path] = set()
    pending = [hub]
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        pending.extend(graph[source] - visited)

    unreachable = [
        str(path.relative_to(ROOT)) for path in sorted(documents - visited)
    ]
    assert unreachable == [], (
        "Markdown documents unreachable from docs/README.md:\n"
        + "\n".join(unreachable)
    )
