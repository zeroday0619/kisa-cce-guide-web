"""Validate and build the independently authored modern Linux edition."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from markdown_it import MarkdownIt
from markupsafe import Markup

from conversion.build_site import _render_page, _taxonomy_maps, _template_environment
from conversion.common import CriterionDocument, JsonValue, load_criterion, load_json, load_yaml
from conversion.paths import REVISED_CRITERIA_DIRECTORY, SCHEMA_DIRECTORY, SITE_TEMPLATE_DIRECTORY

PLATFORMS = ("rhel-10", "ubuntu-26.04", "debian-13")
REVISED_CRITERION_COUNT = 67
REVISED_HTML_PAGE_COUNT = REVISED_CRITERION_COUNT + 1
_MINIMUM_SECTION_PARTS = 3
LICENSE_LABEL = "라이선스: 공공누리 - 공공저작물 자유이용허락"


def _validate_document(document: CriterionDocument) -> None:
    """Reject incomplete metadata and sections before publication."""

    metadata = document.metadata
    valid = (
        set(metadata) == {"criterionCode", "title", "platforms", "status", "sources"}
        and metadata.get("criterionCode") == document.path.stem.upper()
        and isinstance(metadata.get("title"), str)
        and bool(str(metadata.get("title", "")).strip())
        and metadata.get("platforms") == list(PLATFORMS)
        and metadata.get("status") == "draft"
    )
    sources = metadata.get("sources")
    if not isinstance(sources, list) or not sources:
        valid = False
    else:
        for source in sources:
            if not isinstance(source, dict):
                valid = False
                continue
            valid = valid and set(source) == {"title", "url"}
            title, url = source.get("title"), source.get("url")
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
                valid = False
                continue
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc:
                valid = False
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(document.body)
    sections = re.split(r"^## .+$", document.body, flags=re.MULTILINE)
    if len(sections) < _MINIMUM_SECTION_PARTS or any(
        not section.strip() for section in sections[1:]
    ):
        valid = False
    if any(token.type == "heading_open" and token.tag == "h1" for token in tokens):
        valid = False
    if any(token.type == "fence" and not token.info.strip() for token in tokens):
        valid = False
    if not valid:
        msg = f"{document.path}: invalid revised metadata or incomplete Markdown sections"
        raise ValueError(msg)


def load_revisions(repository: Path) -> list[CriterionDocument]:
    """Load the exact UNIX revision inventory in criterion order."""

    directory = repository / REVISED_CRITERIA_DIRECTORY
    expected = {f"u-{number:02}.md" for number in range(1, REVISED_CRITERION_COUNT + 1)}
    actual = {path.name for path in directory.glob("*.md")}
    if actual != expected:
        msg = (
            "revised UNIX inventory mismatch: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
        raise ValueError(msg)
    documents = [load_criterion(directory / name) for name in sorted(expected)]
    for document in documents:
        _validate_document(document)
    return documents


def _page(title: str, body: str, base_path: str, repository: Path) -> str:
    """Render revised content through the shared theme and navigation shell."""

    domains, _, _ = _taxonomy_maps(load_yaml(repository / "data/taxonomy.yaml"))
    return _render_page(
        environment=_template_environment(repository / SITE_TEMPLATE_DIRECTORY),
        template_name="revised.html",
        title=f"{title} | UNIX 개정판",
        description="RHEL 10, Ubuntu 26.04 LTS, Debian 13 기준 UNIX 독립 개정 초안",
        base_path=base_path,
        domains=domains,
        current_domain=None,
        license_label=LICENSE_LABEL.removeprefix("라이선스: "),
        json_alternate_url=("/" + base_path.strip("/")).rstrip("/") + "/revised/dataset.json",
        page_context={"revision_title": title, "revision_body": Markup(body)},  # noqa: S704
    )


def _body_html(document: CriterionDocument) -> str:
    """Render inert Markdown with named tables and scrollable table containers."""

    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(document.body)
    heading_number = 0
    for token in tokens:
        if token.type == "heading_open":
            heading_number += 1
            token.attrSet("id", f"{document.path.stem}-section-{heading_number}")
    rendered = parser.renderer.render(tokens, parser.options, {})
    table_number = 0

    def table_open(_match: re.Match[str]) -> str:
        nonlocal table_number
        table_number += 1
        return (
            '<div class="table-scroll"><table>'
            f"<caption>{escape(str(document.metadata['title']))} — 표 {table_number}</caption>"
        )

    rendered = re.sub(r"<table>", table_open, rendered)
    return rendered.replace("</table>", "</table></div>").replace("<th>", '<th scope="col">')


def validate_revised_dataset(document: dict[str, JsonValue], repository: Path) -> None:
    """Validate the public revision dataset against its versioned contract."""

    schema = load_json(repository / SCHEMA_DIRECTORY / "revised-dataset.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: str(error.json_path))
    if errors:
        details = "; ".join(f"{error.json_path}: {error.message}" for error in errors)
        msg = f"revised dataset validation failed: {details}"
        raise ValueError(msg)


def build_revised_edition(
    *, repository: Path, output_root: Path, base_path: str = ""
) -> list[Path]:
    """Build independent revision pages and deterministic machine-readable content."""

    documents = load_revisions(repository)
    edition_root = output_root / "site" / "revised"
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    paths: list[Path] = []
    records: list[JsonValue] = []
    entries: list[str] = []
    for document in documents:
        slug = document.path.stem
        title = f"{document.metadata['criterionCode']} {document.metadata['title']}"
        route = f"{base}/revised/unix/{slug}/"
        original = f"{base}/unix/{slug}/"
        sources = cast("list[dict[str, str]]", document.metadata["sources"])
        references = "".join(
            f'<li><a href="{escape(source["url"], quote=True)}">{escape(source["title"])}</a></li>'
            for source in sources
        )
        body = (
            f'<p><a href="{escape(original, quote=True)}">{slug.upper()} 원본 대조</a></p>'
            f"<article>{_body_html(document)}</article>"
            '<section aria-label="공식 참고 자료"><h2>공식 참고 자료</h2>'
            f"<ul>{references}</ul></section>"
        )
        path = edition_root / "unix" / slug / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_page(title, body, base_path, repository), encoding="utf-8")
        paths.append(path)
        entries.append(f'<li><a href="{escape(route, quote=True)}">{escape(title)}</a></li>')
        records.append(
            {**document.metadata, "route": route, "originalRoute": original, "body": document.body}
        )
    index_path = edition_root / "index.html"
    index_path.write_text(
        _page(
            "UNIX 최신 Linux 배포판 개정판",
            "<ul>" + "".join(entries) + "</ul>",
            base_path,
            repository,
        ),
        encoding="utf-8",
    )
    dataset_path = edition_root / "dataset.json"
    dataset: dict[str, JsonValue] = {"schemaVersion": 1, "edition": "revised", "records": records}
    validate_revised_dataset(dataset, repository)
    dataset_path.write_bytes(rfc8785.dumps(dataset))
    return [*paths, index_path, dataset_path]
