"""Build the self-contained static website from normalized JSON."""

from __future__ import annotations

import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import cast

import rfc8785
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markdown_it import MarkdownIt
from markupsafe import Markup

from conversion.common import JsonValue, as_mapping, as_sequence
from conversion.paths import (
    CANONICAL_ASSET_DIRECTORY,
    SITE_ASSET_DIRECTORY,
    SITE_SKILL_DIRECTORY,
    SITE_TEMPLATE_DIRECTORY,
    repository_root,
)

_HIGHLIGHT_LANGUAGE_ALIASES = {
    "asp": "xml",
    "batch": "dos",
    "console": "shell",
    "csh": "bash",
    "freemarker": "xml",
    "html": "xml",
    "sh": "bash",
    "shell": "bash",
    "velocity": "xml",
}
_HIGHLIGHT_COMMON_LANGUAGES = frozenset(
    {
        "bash",
        "c",
        "cpp",
        "csharp",
        "css",
        "diff",
        "go",
        "graphql",
        "ini",
        "java",
        "javascript",
        "json",
        "kotlin",
        "less",
        "lua",
        "makefile",
        "markdown",
        "objectivec",
        "perl",
        "php",
        "php-template",
        "plaintext",
        "python",
        "python-repl",
        "r",
        "ruby",
        "rust",
        "scss",
        "shell",
        "sql",
        "swift",
        "typescript",
        "vbnet",
        "wasm",
        "xml",
        "yaml",
    }
)
_HIGHLIGHT_ADDITIONAL_LANGUAGE_SCRIPTS = {
    language: f"/assets/vendor/highlight.js/languages/{language}.min.js"
    for language in ("apache", "dos", "http", "nginx", "powershell", "properties")
}
_HIGHLIGHT_SUPPORTED_LANGUAGES = _HIGHLIGHT_COMMON_LANGUAGES | frozenset(
    _HIGHLIGHT_ADDITIONAL_LANGUAGE_SCRIPTS
)
_TABLE_OF_CONTENTS_MINIMUM_HEADING_COUNT = 6
_JUDGMENT_ROLES = ("good", "vulnerable")
_JUDGMENT_LABEL_BY_ROLE = {
    "good": "양호:",
    "vulnerable": "취약:",
}


@dataclass(slots=True)
class _TableOfContentsNode:
    """Represent one heading and its ordered descendants."""

    identifier: str
    text: str
    heading_level: int
    children: list[_TableOfContentsNode] = field(default_factory=list)


def _text(value: JsonValue, *, location: str) -> str:
    """Narrow a JSON value to text."""

    if not isinstance(value, str):
        msg = f"{location} must be a string"
        raise TypeError(msg)
    return value


def _site_url(path: str, *, base_path: str) -> str:
    """Prefix a canonical route with an optional hosting base path."""

    normalized_base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    normalized_path = "/" + path.lstrip("/")
    return normalized_base + normalized_path


def _inline_renderer() -> MarkdownIt:
    """Create a raw-HTML-disabled CommonMark inline renderer."""

    return MarkdownIt("commonmark", {"html": False})


def _inline_html(value: str, *, parser: MarkdownIt) -> str:
    """Render constrained inline Markdown."""

    return parser.renderInline(value)


def _inline_markup(value: str, *, parser: MarkdownIt) -> Markup:
    """Mark raw-HTML-disabled Markdown output as trusted template content."""

    # Raw HTML is disabled, so only renderer-owned inline tags cross this boundary.
    return Markup(_inline_html(value, parser=parser))  # noqa: S704


def _rendered_markup(value: str) -> Markup:
    """Mark controlled renderer output for composition in another template."""

    # The value has already passed through Jinja or raw-HTML-disabled Markdown.
    return Markup(value)  # noqa: S704


def _render_skill_document(value: str) -> str:
    """Render a skill body as nested document HTML without exposing source markup."""

    frontmatter_end = value.find("\n---", 3)
    if not value.startswith("---\n") or frontmatter_end < 0:
        msg = "skill document must contain YAML frontmatter"
        raise ValueError(msg)
    body = value[frontmatter_end + len("\n---") :].lstrip()
    parser = MarkdownIt("commonmark", {"html": False})
    tokens = parser.parse(body)
    for token in tokens:
        if token.type not in {"heading_open", "heading_close"}:
            continue
        level = int(token.tag[1:])
        token.tag = f"h{min(level + 1, 6)}"
    return parser.renderer.render(tokens, parser.options, {})


def _highlight_language(block: Mapping[str, JsonValue]) -> str | None:
    """Return the explicit Highlight.js language for one eligible code block."""

    if block.get("blockType") != "codeBlock":
        return None
    content_type = _text(
        block.get("codeContentType", "literal"),
        location="block.codeContentType",
    )
    language = _text(block.get("codeLanguage", "text"), location="block.codeLanguage")
    if content_type == "transcription" or language in {"text", "plaintext"}:
        return None
    highlight_language = _HIGHLIGHT_LANGUAGE_ALIASES.get(language, language)
    return highlight_language if highlight_language in _HIGHLIGHT_SUPPORTED_LANGUAGES else None


@cache
def _template_environment(template_directory: Path) -> Environment:
    """Create one strict deterministic Jinja environment per template directory."""

    environment = Environment(
        loader=FileSystemLoader(template_directory),
        autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=True),
        undefined=StrictUndefined,
        auto_reload=False,
        trim_blocks=False,
        lstrip_blocks=False,
        newline_sequence="\n",
        keep_trailing_newline=True,
    )
    environment.policies["json.dumps_kwargs"] = {
        "ensure_ascii": False,
        "separators": (",", ":"),
        "sort_keys": True,
    }
    return environment


def _default_template_environment() -> Environment:
    """Return the repository template environment for direct renderer tests."""

    return _template_environment(repository_root() / SITE_TEMPLATE_DIRECTORY)


def _render_page(
    *,
    environment: Environment,
    template_name: str,
    title: str,
    description: str,
    base_path: str,
    domains: Mapping[str, dict[str, JsonValue]],
    current_domain: str | None,
    extra_scripts: Sequence[str] = (),
    extra_stylesheets: Sequence[str] = (),
    canonical_url: str | None = None,
    current_navigation: str | None = None,
    domain_navigation_current: bool = False,
    license_label: str,
    json_alternate_url: str | None = None,
    structured_data: Mapping[str, JsonValue] | None = None,
    page_context: Mapping[str, object] | None = None,
    edition_prefix: str = "",
) -> str:
    """Render one complete page through the shared Jinja template shell."""

    context: dict[str, object] = {
        "canonical_url": canonical_url,
        "current_navigation": current_navigation,
        "description": description,
        "domain_navigation_current": domain_navigation_current,
        "domain_navigation_items": _domain_navigation_view(
            domains=domains,
            current_domain=current_domain,
            base_path=base_path,
        ),
        "home_url": _site_url("/", base_path=base_path),
        "original_url": _site_url("/", base_path=base_path),
        "revised_url": _site_url("/revised/", base_path=base_path),
        "json_alternate_url": json_alternate_url,
        "license_label": license_label,
        "script_urls": [
            _site_url(script, base_path=base_path) for script in ["/assets/site.js", *extra_scripts]
        ],
        "search_url": _site_url("/search/", base_path=base_path),
        "site_stylesheet_url": _site_url("/assets/styles.css", base_path=base_path),
        "structured_data": structured_data,
        "stylesheet_urls": [
            _site_url(stylesheet, base_path=base_path) for stylesheet in extra_stylesheets
        ],
        "theme_initialization_url": _site_url(
            "/assets/theme-init.js",
            base_path=base_path,
        ),
        "title": title,
    }
    if edition_prefix:
        context["home_url"] = _site_url(f"{edition_prefix}/", base_path=base_path)
        context["search_url"] = _site_url(f"{edition_prefix}/search/", base_path=base_path)
        context["domain_navigation_items"] = _domain_navigation_view(
            domains=domains,
            current_domain=current_domain,
            base_path=base_path.rstrip("/") + edition_prefix,
        )
    if page_context is not None:
        context.update(page_context)
    return environment.get_template(template_name).render(context)


def _taxonomy_maps(
    taxonomy: Mapping[str, JsonValue],
) -> tuple[
    dict[str, dict[str, JsonValue]],
    dict[tuple[str, str], dict[str, JsonValue]],
    dict[str, str],
]:
    """Create domain, category, and target lookup maps."""

    domains = {
        _text(record["identifier"], location="taxonomy.domain.identifier"): record
        for record_value in as_sequence(taxonomy["domains"], location="taxonomy.domains")
        if isinstance(record_value, dict) and (record := record_value)
    }
    categories = {
        (
            _text(record["domainIdentifier"], location="taxonomy.category.domainIdentifier"),
            _text(record["identifier"], location="taxonomy.category.identifier"),
        ): record
        for record_value in as_sequence(
            taxonomy["categories"],
            location="taxonomy.categories",
        )
        if isinstance(record_value, dict) and (record := record_value)
    }
    targets = {
        _text(record["identifier"], location="taxonomy.target.identifier"): _text(
            record["label"],
            location="taxonomy.target.label",
        )
        for record_value in as_sequence(taxonomy["targets"], location="taxonomy.targets")
        if isinstance(record_value, dict) and (record := record_value)
    }
    return domains, categories, targets


def _domain_navigation_view(
    *,
    domains: Mapping[str, dict[str, JsonValue]],
    current_domain: str | None,
    base_path: str,
) -> list[dict[str, object]]:
    """Build ordered domain navigation items for the shared template."""

    items: list[dict[str, object]] = []
    for domain_identifier, domain in sorted(
        domains.items(),
        key=lambda item: int(cast("int", item[1]["order"])),
    ):
        label = _text(domain["label"], location="taxonomy.domain.label")
        route = _site_url(f"/{domain_identifier}/", base_path=base_path)
        items.append(
            {
                "current": domain_identifier == current_domain,
                "label": label,
                "url": route,
            }
        )
    return items


def _block_identifier(block: Mapping[str, JsonValue]) -> str:
    """Return one normalized block reference."""

    return _text(block["blockReference"], location="block.blockReference")


def _render_table_of_contents_outline(
    heading_blocks: Sequence[dict[str, JsonValue]],
    *,
    environment: Environment | None = None,
) -> str:
    """Render the document heading outline with the recursive Jinja partial."""

    if len(heading_blocks) < _TABLE_OF_CONTENTS_MINIMUM_HEADING_COUNT:
        return ""

    roots: list[_TableOfContentsNode] = []
    ancestors: list[_TableOfContentsNode] = []
    for block in heading_blocks:
        heading_level = int(cast("int", block["headingLevel"]))
        node = _TableOfContentsNode(
            identifier=_block_identifier(block),
            text=_text(block["content"], location="block.content"),
            heading_level=heading_level,
        )
        while ancestors and heading_level <= ancestors[-1].heading_level:
            ancestors.pop()
        if ancestors:
            ancestors[-1].children.append(node)
        else:
            roots.append(node)
        ancestors.append(node)
    active_environment = environment or _default_template_environment()
    return active_environment.get_template("partials/table-of-contents.html").render(
        nodes=roots,
        outline_only=True,
    )


def _render_table_of_contents(
    heading_blocks: Sequence[dict[str, JsonValue]],
    *,
    environment: Environment | None = None,
) -> str:
    """Render the collapsible document outline through Jinja."""

    if len(heading_blocks) < _TABLE_OF_CONTENTS_MINIMUM_HEADING_COUNT:
        return ""
    roots: list[_TableOfContentsNode] = []
    ancestors: list[_TableOfContentsNode] = []
    for block in heading_blocks:
        heading_level = int(cast("int", block["headingLevel"]))
        node = _TableOfContentsNode(
            identifier=_block_identifier(block),
            text=_text(block["content"], location="block.content"),
            heading_level=heading_level,
        )
        while ancestors and heading_level <= ancestors[-1].heading_level:
            ancestors.pop()
        if ancestors:
            ancestors[-1].children.append(node)
        else:
            roots.append(node)
        ancestors.append(node)
    active_environment = environment or _default_template_environment()
    return active_environment.get_template("partials/table-of-contents.html").render(
        nodes=roots,
        outline_only=False,
    )


def _block_attributes(
    block: Mapping[str, JsonValue],
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build deterministic semantic and provenance attributes for one block."""

    identifier = _block_identifier(block)
    semantic_path = as_sequence(block["semanticPath"], location="block.semanticPath")
    source_spans = as_sequence(block["sourceSpans"], location="block.sourceSpans")
    source_region_identifiers: list[str] = []
    source_physical_pages: list[str] = []
    source_printed_pages: list[str] = []
    for span_value in source_spans:
        span = as_mapping(span_value, location="block.sourceSpans[]")
        region_identifier = _text(
            span["pageRegionIdentifier"],
            location="block.sourceSpans[].pageRegionIdentifier",
        )
        physical_page = str(int(cast("int", span["physicalPage"])))
        printed_page = _text(
            span["printedPage"],
            location="block.sourceSpans[].printedPage",
        )
        if region_identifier not in source_region_identifiers:
            source_region_identifiers.append(region_identifier)
        if physical_page not in source_physical_pages:
            source_physical_pages.append(physical_page)
        if printed_page not in source_printed_pages:
            source_printed_pages.append(printed_page)

    attributes = {
        "id": identifier,
        "data-block-reference": identifier,
        "data-block-type": _text(block["blockType"], location="block.blockType"),
        "data-semantic-role": _text(block["semanticRole"], location="block.semanticRole"),
        "data-semantic-path": "/".join(
            _text(value, location="block.semanticPath[]") for value in semantic_path
        ),
        "data-publication-disposition": _text(
            block["publicationDisposition"],
            location="block.publicationDisposition",
        ),
        "data-source-region-identifiers": " ".join(source_region_identifiers),
        "data-source-physical-pages": " ".join(source_physical_pages),
        "data-source-printed-pages": " ".join(source_printed_pages),
    }
    parent_reference = block.get("parentBlockReference")
    if isinstance(parent_reference, str):
        attributes["data-parent-block-reference"] = parent_reference
    if extra is not None:
        attributes.update(extra)
    return attributes


def _render_blocks(
    block_values: list[JsonValue],
    *,
    base_path: str,
    environment: Environment | None = None,
) -> str:
    """Build typed block views and render them with the recursive Jinja partial."""

    parser = _inline_renderer()
    blocks = [
        as_mapping(block_value, location="normalized.blocks[]") for block_value in block_values
    ]
    heading_identifier_by_semantic_path = {
        tuple(
            _text(value, location="block.semanticPath[]")
            for value in as_sequence(block["semanticPath"], location="block.semanticPath")
        ): _block_identifier(block)
        for block in blocks
        if block.get("blockType") == "heading"
    }
    children: dict[str, list[dict[str, JsonValue]]] = defaultdict(list)
    top_level: list[dict[str, JsonValue]] = []
    for block in blocks:
        parent = block.get("parentBlockReference")
        if isinstance(parent, str):
            children[parent].append(block)
        else:
            top_level.append(block)

    def build_list_item(block: dict[str, JsonValue]) -> dict[str, object]:
        identifier = _block_identifier(block)
        list_type = _text(block["listType"], location="block.listType")
        list_depth = str(int(cast("int", block["listDepth"])))
        content = _inline_markup(
            _text(block["content"], location="block.content"),
            parser=parser,
        )
        return {
            "attributes": _block_attributes(
                block,
                extra={"data-list-type": list_type, "data-list-depth": list_depth},
            ),
            "children": build_sequence(children.get(identifier, [])),
            "content": content,
        }

    def build_judgment_item(block: dict[str, JsonValue]) -> dict[str, object]:
        identifier = _block_identifier(block)
        semantic_role = _text(block["semanticRole"], location="block.semanticRole")
        label = _JUDGMENT_LABEL_BY_ROLE.get(semantic_role)
        if label is None:
            msg = f"unsupported judgment semantic role: {semantic_role}"
            raise ValueError(msg)
        source_content = _text(block["content"], location="block.content")
        content_prefix = f"**{label}** "
        if not source_content.startswith(content_prefix):
            msg = f"{identifier} judgment content must start with {content_prefix!r}"
            raise ValueError(msg)
        list_type = _text(block["listType"], location="block.listType")
        list_depth = str(int(cast("int", block["listDepth"])))
        return {
            "attributes": _block_attributes(
                block,
                extra={"data-list-type": list_type, "data-list-depth": list_depth},
            ),
            "content": _inline_markup(source_content.removeprefix(content_prefix), parser=parser),
            "heading_identifier": f"{identifier}-heading",
            "label": label,
        }

    def build_single(block: dict[str, JsonValue]) -> dict[str, object]:
        block_type = _text(block["blockType"], location="block.blockType")
        content = _text(block["content"], location="block.content")
        if block_type == "heading":
            heading_level = int(cast("int", block["headingLevel"]))
            return {
                "attributes": _block_attributes(
                    block,
                    extra={"data-heading-level": str(heading_level)},
                ),
                "content": _inline_markup(content, parser=parser),
                "heading_level": heading_level,
                "kind": "heading",
            }
        if block_type in {"paragraph", "noteContent", "noteLabel"}:
            return {
                "attributes": _block_attributes(block),
                "content": _inline_markup(content, parser=parser),
                "css_class": "note__label" if block_type == "noteLabel" else None,
                "kind": "paragraph",
            }
        if block_type == "codeBlock":
            identifier = _block_identifier(block)
            language = _text(block.get("codeLanguage", "text"), location="block.codeLanguage")
            content_type = _text(
                block.get("codeContentType", "literal"),
                location="block.codeContentType",
            )
            highlight_language = _highlight_language(block)
            code_attributes = {
                "data-code-content-type": content_type,
                "data-code-language": language,
            }
            if highlight_language is not None:
                code_attributes["data-highlight-language"] = highlight_language
            return {
                "attributes": _block_attributes(block, extra=code_attributes),
                "code_attributes": code_attributes,
                "code_identifier": f"code-{identifier}",
                "content": content,
                "content_label": {
                    "command": "명령",
                    "configuration": "설정",
                    "output": "출력",
                    "literal": "리터럴",
                    "transcription": "원문 전사",
                }.get(content_type, content_type),
                "kind": "code",
                "rendered_language": highlight_language or language,
                "transcription_class": (
                    " code-block--transcription" if content_type == "transcription" else ""
                ),
            }
        if block_type == "table":
            identifier = _block_identifier(block)
            headers = as_sequence(block["tableHeaders"], location="block.tableHeaders")
            rows = as_sequence(block["tableRows"], location="block.tableRows")
            rendered_headers: list[dict[str, Markup | None]] = []
            for value in headers:
                header_text = _text(value, location="table.header")
                rendered_headers.append(
                    {
                        "content": (
                            _inline_markup(header_text, parser=parser) if header_text else None
                        )
                    }
                )
            rendered_rows: list[list[Markup]] = []
            for row_value in rows:
                row = as_sequence(row_value, location="table.row")
                if len(row) != len(headers):
                    msg = (
                        f"{identifier} table row has {len(row)} cells; "
                        f"expected {len(headers)} cells"
                    )
                    raise ValueError(msg)
                rendered_rows.append(
                    [
                        _inline_markup(_text(value, location="table.cell"), parser=parser)
                        for value in row
                    ]
                )
            caption_value = block.get("caption")
            caption = caption_value if isinstance(caption_value, str) and caption_value else None
            if caption is not None:
                label_identifier = f"caption-{identifier}"
            else:
                semantic_path = tuple(
                    _text(value, location="block.semanticPath[]")
                    for value in as_sequence(block["semanticPath"], location="block.semanticPath")
                )
                label_identifier = heading_identifier_by_semantic_path.get(semantic_path)
                if label_identifier is None:
                    msg = f"{identifier} table has no caption or semantic heading"
                    raise ValueError(msg)
            return {
                "attributes": _block_attributes(
                    block,
                    extra={
                        "data-table-column-count": str(len(headers)),
                        "data-table-row-count": str(len(rows)),
                    },
                ),
                "caption": caption,
                "headers": rendered_headers,
                "kind": "table",
                "label_identifier": label_identifier,
                "rows": rendered_rows,
            }
        if block_type == "image":
            identifier = _block_identifier(block)
            asset_path = _text(block["assetPath"], location="block.assetPath")
            alternative_text = _text(
                block["alternativeText"],
                location="block.alternativeText",
            )
            alternative_text_status = _text(
                block["alternativeTextStatus"],
                location="block.alternativeTextStatus",
            )
            pixel_dimensions = as_sequence(
                block["outputPixelDimensions"],
                location="block.outputPixelDimensions",
            )
            caption_value = block.get("caption")
            return {
                "alternative_text": alternative_text,
                "asset_url": _site_url("/" + asset_path.lstrip("/"), base_path=base_path),
                "attributes": _block_attributes(
                    block,
                    extra={
                        "data-asset-type": _text(block["assetType"], location="block.assetType"),
                        "data-rendering-profile": _text(
                            block["renderingProfileIdentifier"],
                            location="block.renderingProfileIdentifier",
                        ),
                        "data-alternative-text-status": alternative_text_status,
                    },
                ),
                "caption": caption_value if isinstance(caption_value, str) else alternative_text,
                "caption_identifier": f"caption-{identifier}",
                "kind": "image",
                "pixel_height": int(cast("int", pixel_dimensions[1])),
                "pixel_width": int(cast("int", pixel_dimensions[0])),
                "verification_required": alternative_text_status == "verificationRequired",
            }
        if block_type == "listItem":
            return build_list_item(block)
        msg = f"unsupported normalized block type: {block_type}"
        raise ValueError(msg)

    def build_sequence(sequence: Sequence[dict[str, JsonValue]]) -> list[dict[str, object]]:
        parts: list[dict[str, object]] = []
        index = 0
        while index < len(sequence):
            block = sequence[index]
            block_type = block.get("blockType")
            if block_type == "listItem":
                list_type = block.get("listType")
                semantic_path = block.get("semanticPath")
                grouped = []
                while index < len(sequence):
                    candidate = sequence[index]
                    if (
                        candidate.get("blockType") != "listItem"
                        or candidate.get("listType") != list_type
                        or candidate.get("semanticPath") != semantic_path
                    ):
                        break
                    grouped.append(candidate)
                    index += 1
                if semantic_path == ["assessment", "judgment"]:
                    semantic_roles = tuple(
                        _text(item["semanticRole"], location="block.semanticRole")
                        for item in grouped
                    )
                    if list_type != "unordered" or semantic_roles != _JUDGMENT_ROLES:
                        msg = (
                            "assessment judgment must contain ordered good and vulnerable "
                            "unordered items"
                        )
                        raise ValueError(msg)
                    parts.append(
                        {
                            "items": [build_judgment_item(item) for item in grouped],
                            "kind": "judgments",
                        }
                    )
                    continue
                tag = "ol" if list_type == "ordered" else "ul"
                first_list_depth = str(int(cast("int", grouped[0]["listDepth"])))
                parts.append(
                    {
                        "items": [build_list_item(item) for item in grouped],
                        "kind": "list",
                        "list_depth": first_list_depth,
                        "list_type": _text(list_type, location="block.listType"),
                        "tag": tag,
                    }
                )
                continue
            if block_type == "noteLabel":
                note_blocks = [block]
                index += 1
                while index < len(sequence) and sequence[index].get("blockType") not in {
                    "heading",
                    "noteLabel",
                }:
                    note_blocks.append(sequence[index])
                    index += 1
                note_label_identifier = _block_identifier(note_blocks[0])
                parts.append(
                    {
                        "children": [
                            build_single(note_blocks[0]),
                            *build_sequence(note_blocks[1:]),
                        ],
                        "kind": "note",
                        "label_identifier": note_label_identifier,
                    }
                )
                continue
            parts.append(build_single(block))
            index += 1
        return parts

    active_environment = environment or _default_template_environment()
    return active_environment.get_template("partials/blocks.html").render(
        blocks=build_sequence(top_level)
    )


def _criterion_list_view(
    records: Sequence[Mapping[str, JsonValue]],
    *,
    base_path: str,
) -> list[dict[str, str]]:
    """Build ordered static criterion-list items for Jinja templates."""

    items: list[dict[str, str]] = []
    for record in records:
        route = _text(record["route"], location="manifest.route")
        items.append(
            {
                "code": _text(record["code"], location="manifest.code"),
                "severity": _text(
                    record["severitySourceLabel"],
                    location="manifest.severity",
                ),
                "title": _text(record["title"], location="manifest.title"),
                "url": _site_url(route, base_path=base_path),
            }
        )
    return items


def _detail_page(
    *,
    environment: Environment,
    normalized: dict[str, JsonValue],
    previous_record: dict[str, JsonValue] | None,
    next_record: dict[str, JsonValue] | None,
    domains: Mapping[str, dict[str, JsonValue]],
    categories: Mapping[tuple[str, str], dict[str, JsonValue]],
    targets: Mapping[str, str],
    source_document: Mapping[str, JsonValue],
    license_label: str,
    base_path: str,
) -> str:
    """Render one criterion detail page."""

    edition_prefix = "/revised" if normalized.get("edition") == "revised" else ""

    criterion = as_mapping(normalized["criterion"], location="normalized.criterion")
    classification = as_mapping(
        normalized["classification"],
        location="normalized.classification",
    )
    provenance = as_mapping(
        normalized["basedOn"] if edition_prefix else normalized["provenance"],
        location="normalized.provenance",
    )
    code = _text(criterion["code"], location="criterion.code")
    title = _text(criterion["title"], location="criterion.title")
    severity = as_mapping(criterion["severity"], location="criterion.severity")
    severity_level = _text(severity["level"], location="severity.level")
    severity_source = _text(severity["sourceLabel"], location="severity.sourceLabel")
    domain_identifier = _text(
        classification["domainIdentifier"],
        location="classification.domainIdentifier",
    )
    category_identifier = _text(
        classification["categoryIdentifier"],
        location="classification.categoryIdentifier",
    )
    domain = domains[domain_identifier]
    category = categories[(domain_identifier, category_identifier)]
    domain_label = _text(domain["label"], location="domain.label")
    category_label = _text(category["label"], location="category.label")
    target_identifiers = as_sequence(
        normalized["targetIdentifiers"],
        location="normalized.targetIdentifiers",
    )
    target_labels = [
        targets.get(_text(value, location="targetIdentifier"), "확인 필요")
        for value in target_identifiers
    ]
    blocks = as_sequence(normalized["blocks"], location="normalized.blocks")
    highlight_languages = sorted(
        {
            language
            for block_value in blocks
            if isinstance(block_value, dict)
            and (language := _highlight_language(block_value)) is not None
        }
    )
    highlight_scripts: tuple[str, ...] = ()
    highlight_stylesheets: tuple[str, ...] = ()
    if highlight_languages:
        highlight_scripts = (
            "/assets/vendor/highlight.js/highlight.min.js",
            *(
                _HIGHLIGHT_ADDITIONAL_LANGUAGE_SCRIPTS[language]
                for language in highlight_languages
                if language in _HIGHLIGHT_ADDITIONAL_LANGUAGE_SCRIPTS
            ),
            "/assets/highlight-init.js",
        )
        highlight_stylesheets = ("/assets/vendor/highlight.js/github-dark.min.css",)
    heading_blocks = [
        as_mapping(block, location="normalized.blocks[]")
        for block in blocks
        if isinstance(block, dict) and block.get("blockType") == "heading"
    ]
    toc = _render_table_of_contents(heading_blocks, environment=environment)
    document_class = (
        "criterion__document criterion__document--with-toc" if toc else "criterion__document"
    )
    content_model = _text(normalized["contentModel"], location="normalized.contentModel")
    source_ranges = as_sequence(
        provenance["sourcePageRanges"],
        location="provenance.sourcePageRanges",
    )
    source_range = as_mapping(source_ranges[0], location="provenance.sourcePageRanges[0]")
    first_page = int(cast("int", source_range["physicalPageStart"]))
    last_page = int(cast("int", source_range["physicalPageEnd"]))
    pdf_url = _text(
        source_document["sourceUrl"],
        location="source.sourceUrl",
    )
    source_title = _text(source_document["title"], location="source.title")
    source_publisher = _text(source_document["publisher"], location="source.publisher")
    dataset_url = _site_url(
        f"{edition_prefix}/dataset/criteria/{domain_identifier}/{_text(criterion['slug'], location='criterion.slug')}.json",
        base_path=base_path,
    )
    criterion_url = _site_url(
        f"{edition_prefix}/{domain_identifier}/{_text(criterion['slug'], location='criterion.slug')}/",
        base_path=base_path,
    )
    structured_data: dict[str, JsonValue] = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "identifier": code,
        "name": f"{code} {title}",
        "description": f"{code} {title} 점검항목",
        "mainEntityOfPage": criterion_url,
        "url": criterion_url,
        "about": [domain_label, category_label, *target_labels],
        "isPartOf": {
            "@type": "CreativeWork",
            "name": "KISA CCE 가이드 2026",
        },
        "isBasedOn": {
            "@type": "CreativeWork",
            "name": source_title,
            "publisher": {
                "@type": "Organization",
                "name": source_publisher,
            },
            "url": pdf_url,
        },
        "articleSection": f"{domain_label} / {category_label}",
        "keywords": [severity_source, *target_labels],
        "pagination": f"{first_page}-{last_page}",
    }
    if edition_prefix:
        structured_data.pop("pagination")
    previous_item = (
        {
            "code": _text(previous_record["code"], location="previous.code"),
            "url": _site_url(
                _text(previous_record["route"], location="previous.route"),
                base_path=base_path,
            ),
        }
        if previous_record is not None
        else None
    )
    next_item = (
        {
            "code": _text(next_record["code"], location="next.code"),
            "url": _site_url(
                _text(next_record["route"], location="next.route"),
                base_path=base_path,
            ),
        }
        if next_record is not None
        else None
    )
    return _render_page(
        environment=environment,
        template_name="pages/criterion.html",
        edition_prefix=edition_prefix,
        title=f"{code} {title} · KISA CCE 가이드 2026",
        description=f"{code} {title} 점검항목",
        base_path=base_path,
        domains=domains,
        current_domain=domain_identifier,
        extra_scripts=highlight_scripts,
        extra_stylesheets=highlight_stylesheets,
        canonical_url=criterion_url,
        current_navigation="domains",
        domain_navigation_current=True,
        license_label=license_label,
        json_alternate_url=dataset_url,
        structured_data=structured_data,
        page_context={
            "category_label": category_label,
            "category_url": _site_url(
                f"{edition_prefix}/{domain_identifier}/{category_identifier}/",
                base_path=base_path,
            ),
            "code": code,
            "content_model": content_model,
            "criterion_title": title,
            "revised_criterion_url": (
                _site_url(f"/revised/unix/{code.lower()}/", base_path=base_path)
                if domain_identifier == "unix" and not edition_prefix
                else None
            ),
            "original_criterion_url": _site_url(f"/unix/{code.lower()}/", base_path=base_path)
            if edition_prefix
            else None,
            "edition_label": "Linux 개정판" if edition_prefix else None,
            "document_class": document_class,
            "domain_label": domain_label,
            "domain_url": _site_url(f"{edition_prefix}/{domain_identifier}/", base_path=base_path),
            "first_page": first_page,
            "last_page": last_page,
            "next_item": next_item,
            "previous_item": previous_item,
            "rendered_blocks": _rendered_markup(
                _render_blocks(
                    blocks,
                    base_path=base_path,
                    environment=environment,
                )
            ),
            "severity_level": severity_level,
            "severity_source": severity_source,
            "source_document_identifier": _text(
                provenance["sourceDocumentIdentifier"],
                location="provenance.sourceDocumentIdentifier",
            ),
            "table_of_contents": _rendered_markup(toc),
            "target_labels": target_labels,
        },
    )


def build_site(
    *,
    repository: Path,
    output_root: Path,
    manifest: dict[str, JsonValue],
    taxonomy: dict[str, JsonValue],
    source_registry: dict[str, JsonValue],
    normalized_documents: Sequence[dict[str, JsonValue]],
    search_index: dict[str, JsonValue],
    base_path: str = "",
) -> list[Path]:
    """Build every static route and public dataset."""

    site_root = output_root / "site"
    if site_root.exists():
        shutil.rmtree(site_root)
    site_root.mkdir(parents=True)
    environment = _template_environment(repository / SITE_TEMPLATE_DIRECTORY)
    nojekyll_path = site_root / ".nojekyll"
    nojekyll_path.write_text("", encoding="utf-8")
    domains, categories, targets = _taxonomy_maps(taxonomy)
    manifest_values = as_sequence(manifest["criteria"], location="manifest.criteria")
    manifest_records = [
        as_mapping(value, location="manifest.criteria[]") for value in manifest_values
    ]
    source_documents = as_sequence(
        source_registry["documents"],
        location="sourceRegistry.documents",
    )
    source_document = as_mapping(
        source_documents[0],
        location="sourceRegistry.documents[0]",
    )
    license_label = _text(
        source_document["license"],
        location="sourceRegistry.documents[0].license",
    )
    generated_paths: list[Path] = [nojekyll_path]

    skill_source_path = repository / SITE_SKILL_DIRECTORY / "kisa-cce-guide-explorer" / "SKILL.md"
    skill_document = skill_source_path.read_text(encoding="utf-8")
    rendered_skill_document = _render_skill_document(skill_document)
    public_skill_path = site_root / "SKILL.md"
    public_skill_path.write_text(skill_document, encoding="utf-8")
    generated_paths.append(public_skill_path)

    asset_directory = site_root / "assets"
    asset_directory.mkdir()
    for asset_name in (
        "styles.css",
        "theme-init.js",
        "site.js",
        "search-core.js",
        "search.js",
        "highlight-init.js",
    ):
        source_path = repository / SITE_ASSET_DIRECTORY / asset_name
        target_path = asset_directory / asset_name
        shutil.copy2(source_path, target_path)
        generated_paths.append(target_path)
    canonical_asset_directory = repository / CANONICAL_ASSET_DIRECTORY
    if canonical_asset_directory.is_dir():
        shutil.copytree(
            canonical_asset_directory,
            asset_directory,
            dirs_exist_ok=True,
        )
        generated_paths.extend(
            path
            for child_directory in asset_directory.iterdir()
            if child_directory.is_dir()
            for path in child_directory.rglob("*")
            if path.is_file()
        )
    vendor_asset_source = repository / SITE_ASSET_DIRECTORY / "vendor"
    vendor_asset_target = asset_directory / "vendor"
    shutil.copytree(vendor_asset_source, vendor_asset_target)
    generated_paths.extend(
        path for path in sorted(vendor_asset_target.rglob("*")) if path.is_file()
    )

    dataset_root = site_root / "dataset"
    criteria_dataset_root = dataset_root / "criteria"
    for normalized in normalized_documents:
        criterion = as_mapping(normalized["criterion"], location="normalized.criterion")
        classification = as_mapping(
            normalized["classification"],
            location="normalized.classification",
        )
        domain_identifier = _text(
            classification["domainIdentifier"],
            location="classification.domainIdentifier",
        )
        slug = _text(criterion["slug"], location="criterion.slug")
        dataset_path = criteria_dataset_root / domain_identifier / f"{slug}.json"
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path.write_bytes(rfc8785.dumps(normalized))
        generated_paths.append(dataset_path)
    search_dataset_path = dataset_root / "search-index.json"
    search_dataset_path.parent.mkdir(parents=True, exist_ok=True)
    search_dataset_path.write_bytes(rfc8785.dumps(search_index))
    taxonomy_dataset_path = dataset_root / "taxonomy.json"
    taxonomy_dataset_path.write_bytes(rfc8785.dumps(taxonomy))
    generated_paths.extend([search_dataset_path, taxonomy_dataset_path])

    records_by_domain: dict[str, list[dict[str, JsonValue]]] = defaultdict(list)
    records_by_category: dict[tuple[str, str], list[dict[str, JsonValue]]] = defaultdict(list)
    for record in manifest_records:
        domain_identifier = _text(record["domainIdentifier"], location="manifest.domain")
        category_identifier = _text(record["categoryIdentifier"], location="manifest.category")
        records_by_domain[domain_identifier].append(record)
        records_by_category[(domain_identifier, category_identifier)].append(record)

    domain_cards: list[dict[str, object]] = []
    for domain_identifier, domain in sorted(
        domains.items(),
        key=lambda item: int(cast("int", item[1]["order"])),
    ):
        label = _text(domain["label"], location="domain.label")
        count = len(records_by_domain[domain_identifier])
        domain_cards.append(
            {
                "count": count,
                "label": label,
                "url": _site_url(f"/{domain_identifier}/", base_path=base_path),
            }
        )
    skill_url = _site_url("/SKILL.md", base_path=base_path)
    skill_page_url = _site_url("/skill/", base_path=base_path)
    skill_usage_prompt = (
        "이 사이트의 SKILL.md 지침을 따라, 빌드된 KISA CCE 가이드 웹페이지만 탐색하고 "
        "[대상 환경과 보안 질문]에 관련된 점검항목을 찾아 "
        "판단 기준, 조치 방법, 적용 대상과 근거 링크를 정리해줘."
    )
    root_path = site_root / "index.html"
    root_path.write_text(
        _render_page(
            environment=environment,
            template_name="pages/home.html",
            title="KISA CCE 가이드 2026",
            description="KISA CCE 2026 점검항목 검색 및 탐색",
            base_path=base_path,
            domains=domains,
            current_domain=None,
            canonical_url=_site_url("/", base_path=base_path),
            current_navigation="domains",
            domain_navigation_current=True,
            license_label=license_label,
            page_context={
                "domain_cards": domain_cards,
                "skill_page_url": skill_page_url,
                "skill_usage_prompt": skill_usage_prompt,
            },
        ),
        encoding="utf-8",
    )
    generated_paths.append(root_path)

    skill_page_path = site_root / "skill" / "index.html"
    skill_page_path.parent.mkdir(parents=True)
    skill_page_path.write_text(
        _render_page(
            environment=environment,
            template_name="pages/skill.html",
            title="LLM 사용 지침 · KISA CCE 가이드 2026",
            description="KISA CCE 가이드 웹페이지를 탐색하는 LLM용 SKILL.md 사용 지침",
            base_path=base_path,
            domains=domains,
            current_domain=None,
            canonical_url=skill_page_url,
            license_label=license_label,
            page_context={
                "rendered_skill_document": _rendered_markup(rendered_skill_document),
                "skill_url": skill_url,
            },
        ),
        encoding="utf-8",
    )
    generated_paths.append(skill_page_path)

    for domain_identifier in records_by_domain:
        domain = domains[domain_identifier]
        domain_label = _text(domain["label"], location="domain.label")
        sections: list[dict[str, object]] = []
        domain_categories = [
            (category_identifier, category)
            for (candidate_domain, category_identifier), category in categories.items()
            if candidate_domain == domain_identifier
        ]
        for category_identifier, category in sorted(
            domain_categories,
            key=lambda item: int(cast("int", item[1]["order"])),
        ):
            category_records = records_by_category[(domain_identifier, category_identifier)]
            category_label = _text(category["label"], location="category.label")
            category_route = _site_url(
                f"/{domain_identifier}/{category_identifier}/",
                base_path=base_path,
            )
            category_record_views = _criterion_list_view(
                category_records,
                base_path=base_path,
            )
            sections.append(
                {
                    "label": category_label,
                    "records": category_record_views,
                    "url": category_route,
                }
            )
            category_path = site_root / domain_identifier / category_identifier / "index.html"
            category_path.parent.mkdir(parents=True, exist_ok=True)
            category_path.write_text(
                _render_page(
                    environment=environment,
                    template_name="pages/listing.html",
                    title=f"{category_label} · {domain_label}",
                    description=f"{domain_label} {category_label} 점검항목",
                    base_path=base_path,
                    domains=domains,
                    current_domain=domain_identifier,
                    canonical_url=category_route,
                    current_navigation="domains",
                    domain_navigation_current=True,
                    license_label=license_label,
                    page_context={
                        "heading": f"{domain_label} · {category_label}",
                        "records": category_record_views,
                        "sections": [],
                    },
                ),
                encoding="utf-8",
            )
            generated_paths.append(category_path)
        domain_path = site_root / domain_identifier / "index.html"
        domain_path.parent.mkdir(parents=True, exist_ok=True)
        domain_path.write_text(
            _render_page(
                environment=environment,
                template_name="pages/listing.html",
                title=f"{domain_label} · KISA CCE 가이드 2026",
                description=f"{domain_label} 점검항목",
                base_path=base_path,
                domains=domains,
                current_domain=domain_identifier,
                canonical_url=_site_url(f"/{domain_identifier}/", base_path=base_path),
                current_navigation="domains",
                domain_navigation_current=True,
                license_label=license_label,
                page_context={
                    "heading": domain_label,
                    "records": [],
                    "sections": sections,
                },
            ),
            encoding="utf-8",
        )
        generated_paths.append(domain_path)

    for record_index, (record, normalized) in enumerate(
        zip(manifest_records, normalized_documents, strict=True)
    ):
        previous_record = manifest_records[record_index - 1] if record_index > 0 else None
        next_record = (
            manifest_records[record_index + 1] if record_index + 1 < len(manifest_records) else None
        )
        route = _text(record["route"], location="manifest.route")
        detail_path = site_root / route.strip("/") / "index.html"
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(
            _detail_page(
                environment=environment,
                normalized=normalized,
                previous_record=previous_record,
                next_record=next_record,
                domains=domains,
                categories=categories,
                targets=targets,
                source_document=source_document,
                license_label=license_label,
                base_path=base_path,
            ),
            encoding="utf-8",
        )
        generated_paths.append(detail_path)

    search_path = site_root / "search" / "index.html"
    search_path.parent.mkdir()
    search_path.write_text(
        _render_page(
            environment=environment,
            template_name="pages/search.html",
            title="검색 · KISA CCE 가이드 2026",
            description="KISA CCE 점검항목 검색",
            base_path=base_path,
            domains=domains,
            current_domain=None,
            canonical_url=_site_url("/search/", base_path=base_path),
            extra_scripts=("/assets/search-core.js", "/assets/search.js"),
            current_navigation="search",
            license_label=license_label,
            page_context={
                "normalized_base_path": (
                    "/" + base_path.strip("/") if base_path.strip("/") else ""
                ),
                "records": _criterion_list_view(manifest_records, base_path=base_path),
                "search_index_url": _site_url(
                    "/dataset/search-index.json",
                    base_path=base_path,
                ),
            },
        ),
        encoding="utf-8",
    )
    generated_paths.append(search_path)

    not_found_path = site_root / "404.html"
    not_found_path.write_text(
        _render_page(
            environment=environment,
            template_name="pages/not-found.html",
            title="페이지를 찾을 수 없습니다",
            description="404",
            base_path=base_path,
            domains=domains,
            current_domain=None,
            license_label=license_label,
        ),
        encoding="utf-8",
    )
    generated_paths.append(not_found_path)
    return generated_paths
