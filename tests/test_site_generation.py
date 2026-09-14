"""Static-site generation and link-integrity tests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import pytest
from jinja2 import StrictUndefined, UndefinedError

from conversion.build_content import build
from conversion.build_site import (
    _TABLE_OF_CONTENTS_MINIMUM_HEADING_COUNT,
    _inline_markup,
    _inline_renderer,
    _render_blocks,
    _render_table_of_contents,
    _template_environment,
)
from conversion.common import JsonValue, as_mapping, as_sequence, load_yaml, repository_root
from conversion.paths import SITE_SKILL_DIRECTORY
from conversion.site_validation import validate_site

EXPECTED_CRITERION_COUNT = 382
EXPECTED_HTML_PAGE_COUNT = 544
EXPECTED_TABLE_COUNT = 69
EXPECTED_LICENSE = "공공누리 - 공공저작물 자유이용허락"
HEX_SHORT_LENGTH = 3
SRGB_LINEAR_THRESHOLD = 0.04045
MINIMUM_TEXT_CONTRAST = 4.5
MINIMUM_UI_CONTRAST = 3.0


class PageInspector(HTMLParser):
    """Collect structural facts without executing site JavaScript."""

    def __init__(self) -> None:
        """Initialize collected structural facts."""

        super().__init__()
        self.html_language: str | None = None
        self.h1_count = 0
        self.identifiers: set[str] = set()
        self.links: list[str] = []
        self.tags: list[str] = []
        self.article_attributes: dict[str, str | None] = {}
        self.elements_by_identifier: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.note_attributes: list[dict[str, str | None]] = []
        self.table_header_scopes: list[str | None] = []
        self.skip_link_present = False
        self.table_of_contents_list_depth = 0
        self.maximum_table_of_contents_list_depth = 0
        self.table_of_contents_link_depths: list[tuple[int, str | None, str | None]] = []
        self._inside_table_of_contents = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Collect element, identifier, link, and landmark information."""

        self.tags.append(tag)
        attribute_map = dict(attrs)
        self._track_table_of_contents_start(tag, attribute_map)
        if tag == "html":
            self.html_language = attribute_map.get("lang")
        if tag == "h1":
            self.h1_count += 1
        identifier = attribute_map.get("id")
        if identifier is not None:
            self.identifiers.add(identifier)
            self.elements_by_identifier[identifier] = (tag, attribute_map)
        link = attribute_map.get("href")
        if link is not None:
            self.links.append(link)
            if link == "#main-content" and "skip-link" in (attribute_map.get("class") or ""):
                self.skip_link_present = True
        if tag == "article" and "criterion" in (attribute_map.get("class") or ""):
            self.article_attributes = {
                key: attribute_map.get(key)
                for key in (
                    "data-criterion-code",
                    "data-severity",
                    "data-content-model",
                    "data-source-document",
                )
            }
        if tag == "aside" and attribute_map.get("role") == "note":
            self.note_attributes.append(attribute_map)
        if tag == "th":
            self.table_header_scopes.append(attribute_map.get("scope"))

    def _track_table_of_contents_start(
        self,
        tag: str,
        attribute_map: dict[str, str | None],
    ) -> None:
        """Collect nested table-of-contents depth and heading metadata."""

        if tag == "nav" and "toc" in (attribute_map.get("class") or "").split():
            self._inside_table_of_contents = True
        if self._inside_table_of_contents and tag == "ul":
            self.table_of_contents_list_depth += 1
            self.maximum_table_of_contents_list_depth = max(
                self.maximum_table_of_contents_list_depth,
                self.table_of_contents_list_depth,
            )
        if self._inside_table_of_contents and tag == "a":
            self.table_of_contents_link_depths.append(
                (
                    self.table_of_contents_list_depth,
                    attribute_map.get("data-toc-heading-level"),
                    attribute_map.get("data-toc-depth"),
                )
            )

    def handle_endtag(self, tag: str) -> None:
        """Track nested table-of-contents list boundaries."""

        if self._inside_table_of_contents and tag == "ul":
            self.table_of_contents_list_depth -= 1
        if self._inside_table_of_contents and tag == "nav":
            self._inside_table_of_contents = False


def _inspect(path: Path) -> PageInspector:
    """Parse one generated HTML page."""

    inspector = PageInspector()
    inspector.feed(path.read_text(encoding="utf-8"))
    return inspector


def _theme_palette(stylesheet: str, selector: str) -> dict[str, str]:
    """Return hexadecimal custom properties from one theme selector."""

    declaration_block = stylesheet.partition(f"{selector} {{")[2].partition("}")[0]
    assert declaration_block
    return dict(
        re.findall(
            r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6});",
            declaration_block,
        )
    )


def _contrast_ratio(foreground: str, background: str) -> float:
    """Calculate a WCAG contrast ratio for two hexadecimal colors."""

    def relative_luminance(color: str) -> float:
        value = color.removeprefix("#")
        if len(value) == HEX_SHORT_LENGTH:
            value = "".join(character * 2 for character in value)
        channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
        linear_channels = [
            channel / 12.92
            if channel <= SRGB_LINEAR_THRESHOLD
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return (
            (0.2126 * linear_channels[0])
            + (0.7152 * linear_channels[1])
            + (0.0722 * linear_channels[2])
        )

    foreground_luminance = relative_luminance(foreground)
    background_luminance = relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _copy_site(generated_site: Path, destination: Path) -> Path:
    """Copy a generated site so a validation test can mutate it in isolation."""

    copied_site = destination / "site"
    shutil.copytree(generated_site, copied_site)
    return copied_site


def test_template_environment_is_strict_and_autoescapes(tmp_path: Path) -> None:
    """The shared Jinja environment must escape HTML and reject missing context."""

    template_path = tmp_path / "contract.html"
    template_path.write_text(
        "<p>{{ value }}</p><script>{{ payload | tojson }}</script>", encoding="utf-8"
    )
    environment = _template_environment(tmp_path)

    assert environment.undefined is StrictUndefined
    autoescape = environment.autoescape
    assert callable(autoescape)
    assert autoescape("contract.html")
    rendered = environment.get_template("contract.html").render(
        payload={"value": "</script><script>alert(1)</script>"},
        value='<strong data-test="escape">unsafe</strong>',
    )
    assert "&lt;strong data-test=&#34;escape&#34;&gt;unsafe&lt;/strong&gt;" in rendered
    assert "</script><script>" not in rendered
    assert "\\u003c/script\\u003e" in rendered

    with pytest.raises(UndefinedError):
        environment.get_template("contract.html").render(payload={})


def test_inline_markdown_trust_boundary_rejects_raw_html(tmp_path: Path) -> None:
    """Trusted Markdown markup must contain renderer tags but escape source HTML."""

    template_path = tmp_path / "markdown.html"
    template_path.write_text("<div>{{ content }}</div>", encoding="utf-8")
    environment = _template_environment(tmp_path)
    content = _inline_markup(
        '**강조** <script data-test="unsafe">alert(1)</script>',
        parser=_inline_renderer(),
    )

    rendered = environment.get_template("markdown.html").render(content=content)
    assert "<strong>강조</strong>" in rendered
    assert "<script" not in rendered
    assert "&lt;script data-test=&quot;unsafe&quot;&gt;" in rendered


def _issue_rule_identifiers(site_root: Path) -> set[str]:
    """Validate one mutated full site and return its failed rule identifiers."""

    manifest = load_yaml(repository_root() / "data/criteria-manifest.yaml")
    return {
        issue.rule_identifier
        for issue in validate_site(
            site_root=site_root,
            manifest=manifest,
            expected_html_page_count=EXPECTED_HTML_PAGE_COUNT,
        )
    }


def _source_attribute_values(block: dict[str, JsonValue]) -> tuple[str, str, str]:
    """Return ordered source-region, physical-page, and printed-page tokens."""

    source_spans = [
        as_mapping(value, location="block.sourceSpans[]")
        for value in as_sequence(block["sourceSpans"], location="block.sourceSpans")
    ]
    source_region_identifiers: list[str] = []
    source_physical_pages: list[str] = []
    source_printed_pages: list[str] = []
    for span in source_spans:
        region_identifier = span["pageRegionIdentifier"]
        physical_page = span["physicalPage"]
        printed_page = span["printedPage"]
        assert isinstance(region_identifier, str)
        assert isinstance(physical_page, int)
        assert isinstance(printed_page, str)
        source_region_identifiers.append(region_identifier)
        source_physical_pages.append(str(physical_page))
        source_printed_pages.append(printed_page)
    return (
        " ".join(dict.fromkeys(source_region_identifiers)),
        " ".join(dict.fromkeys(source_physical_pages)),
        " ".join(dict.fromkeys(source_printed_pages)),
    )


def _assert_highlight_contract(
    *,
    block: dict[str, JsonValue],
    pre_attributes: dict[str, str | None],
    code_attributes: dict[str, str | None],
) -> None:
    """Assert that syntax highlighting remains an optional explicit enhancement."""

    if block["codeContentType"] == "transcription" or block["codeLanguage"] in {
        "text",
        "plaintext",
    }:
        assert "data-highlight-language" not in pre_attributes
        assert "data-highlight-language" not in code_attributes
    else:
        assert pre_attributes["data-highlight-language"]
        assert code_attributes["data-highlight-language"]


def _assert_code_block_contract(
    *,
    inspector: PageInspector,
    block: dict[str, JsonValue],
    pre_attributes: dict[str, str | None],
) -> None:
    """Assert code metadata and optional highlighting attributes."""

    assert pre_attributes["data-code-content-type"] == block["codeContentType"]
    assert pre_attributes["data-code-language"] == block["codeLanguage"]
    code_tag, code_attributes = inspector.elements_by_identifier[f"code-{block['blockReference']}"]
    assert code_tag == "code"
    assert code_attributes["data-code-content-type"] == block["codeContentType"]
    assert code_attributes["data-code-language"] == block["codeLanguage"]
    _assert_highlight_contract(
        block=block,
        pre_attributes=pre_attributes,
        code_attributes=code_attributes,
    )


def _assert_table_block_contract(
    *,
    inspector: PageInspector,
    block: dict[str, JsonValue],
    attributes: dict[str, str | None],
) -> None:
    """Assert that a table keeps a source caption or semantic heading label."""

    block_reference = block["blockReference"]
    assert isinstance(block_reference, str)
    caption = block.get("caption")
    if isinstance(caption, str) and caption:
        caption_tag, _ = inspector.elements_by_identifier[f"caption-{block_reference}"]
        assert caption_tag == "caption"
        return
    labelled_by_identifier = attributes["aria-labelledby"]
    assert labelled_by_identifier is not None
    labelled_by_tag, _ = inspector.elements_by_identifier[labelled_by_identifier]
    assert labelled_by_tag in {"h2", "h3", "h4", "h5", "h6"}
    assert f"caption-{block_reference}" not in inspector.elements_by_identifier


def _assert_block_contract(
    inspector: PageInspector,
    block: dict[str, JsonValue],
) -> None:
    """Assert one normalized block's semantic element and machine attributes."""

    block_reference = block["blockReference"]
    block_type = block["blockType"]
    semantic_role = block["semanticRole"]
    publication_disposition = block["publicationDisposition"]
    assert isinstance(block_reference, str)
    assert isinstance(block_type, str)
    assert isinstance(semantic_role, str)
    assert isinstance(publication_disposition, str)
    semantic_path = as_sequence(block["semanticPath"], location="block.semanticPath")
    semantic_path_values = [value for value in semantic_path if isinstance(value, str)]
    source_regions, source_physical_pages, source_printed_pages = _source_attribute_values(block)

    tag, attributes = inspector.elements_by_identifier[block_reference]
    expected_tag = {
        "paragraph": "p",
        "listItem": "li",
        "noteLabel": "p",
        "noteContent": "p",
        "codeBlock": "pre",
        "table": "table",
        "image": "figure",
    }.get(block_type)
    if (
        block_type == "listItem"
        and semantic_role in {"good", "vulnerable"}
        and semantic_path_values == ["assessment", "judgment"]
    ):
        expected_tag = "section"
    if block_type == "heading":
        heading_level = block["headingLevel"]
        assert isinstance(heading_level, int)
        expected_tag = f"h{heading_level}"
        assert attributes["data-heading-level"] == str(heading_level)
    assert tag == expected_tag
    assert attributes["data-block-reference"] == block_reference
    assert attributes["data-block-type"] == block_type
    assert attributes["data-semantic-role"] == semantic_role
    assert attributes["data-semantic-path"] == "/".join(semantic_path_values)
    assert attributes["data-publication-disposition"] == publication_disposition
    assert attributes["data-source-region-identifiers"] == source_regions
    assert attributes["data-source-physical-pages"] == source_physical_pages
    assert attributes["data-source-printed-pages"] == source_printed_pages

    parent_reference = block.get("parentBlockReference")
    if isinstance(parent_reference, str):
        assert attributes["data-parent-block-reference"] == parent_reference
    else:
        assert "data-parent-block-reference" not in attributes

    if block_type == "listItem":
        assert attributes["data-list-type"] == block["listType"]
        assert attributes["data-list-depth"] == str(block["listDepth"])
    if block_type == "codeBlock":
        _assert_code_block_contract(
            inspector=inspector,
            block=block,
            pre_attributes=attributes,
        )
    if block_type == "table":
        _assert_table_block_contract(
            inspector=inspector,
            block=block,
            attributes=attributes,
        )
    if block_type == "image":
        caption_tag, _ = inspector.elements_by_identifier[f"caption-{block_reference}"]
        assert caption_tag == "figcaption"
        assert attributes["data-asset-type"] == block["assetType"]
        assert attributes["data-rendering-profile"] == block["renderingProfileIdentifier"]
        assert attributes["data-alternative-text-status"] == block["alternativeTextStatus"]


@pytest.fixture(scope="module")
def generated_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build one complete site for structural tests."""

    output_root = tmp_path_factory.mktemp("site-build")
    build(output_root=output_root)
    return output_root / "site"


def test_every_manifest_route_and_dataset_exist(generated_site: Path) -> None:
    """Every criterion must have an HTML route and JSON alternate."""

    manifest = load_yaml(repository_root() / "data/criteria-manifest.yaml")
    records = as_sequence(manifest["criteria"], location="manifest.criteria")
    assert len(records) == EXPECTED_CRITERION_COUNT
    for record_value in records:
        record = as_mapping(record_value, location="manifest.criteria[]")
        route = record["route"]
        domain_identifier = record["domainIdentifier"]
        slug = record["slug"]
        assert isinstance(route, str)
        assert isinstance(domain_identifier, str)
        assert isinstance(slug, str)
        assert (generated_site / route.strip("/") / "index.html").is_file()
        assert (
            generated_site / "dataset" / "criteria" / domain_identifier / f"{slug}.json"
        ).is_file()


def test_all_html_pages_have_required_landmarks(generated_site: Path) -> None:
    """Every route must expose Korean language, one H1, and core landmarks."""

    html_paths = sorted(generated_site.rglob("*.html"))
    assert len(html_paths) == EXPECTED_HTML_PAGE_COUNT
    for html_path in html_paths:
        inspector = _inspect(html_path)
        assert inspector.html_language == "ko", html_path
        assert inspector.h1_count == 1, html_path
        assert inspector.skip_link_present, html_path
        assert {"header", "nav", "main", "footer"} <= set(inspector.tags), html_path


def test_all_html_pages_display_public_license(generated_site: Path) -> None:
    """Every page must expose the canonical public license exactly once."""

    expected_text = f"라이선스: {EXPECTED_LICENSE}"
    for html_path in sorted(generated_site.rglob("*.html")):
        html_text = html_path.read_text(encoding="utf-8")
        assert html_text.count(expected_text) == 1, html_path


def test_revised_articles_share_original_document_components(generated_site: Path) -> None:
    """Every revised article must retain the original document layout and controls."""

    shared_components = (
        'class="page-shell page-shell--detail"',
        'class="criterion-meta"',
        'class="criterion__body"',
        'class="judgment-criteria"',
        'class="pager"',
        "data-table-of-contents",
        "data-copy-surface",
    )
    for number in range(1, 68):
        slug = f"u-{number:02}"
        original = (generated_site / "unix" / slug / "index.html").read_text(encoding="utf-8")
        revised = (generated_site / "revised" / "unix" / slug / "index.html").read_text(
            encoding="utf-8"
        )
        for component in shared_components:
            assert component in original, (slug, component)
            assert component in revised, (slug, component)
        original_styles = re.findall(r'<link rel="stylesheet" href="([^"]+)"', original)
        revised_styles = re.findall(r'<link rel="stylesheet" href="([^"]+)"', revised)
        assert "/assets/styles.css" in original_styles, slug
        assert "/assets/styles.css" in revised_styles, slug
        assert "/assets/highlight-init.js" in revised, slug
        for heading in ("개요", "점검 대상 및 판단 기준", "점검 및 조치 사례"):
            assert f">{heading}</h2>" in revised, slug
        assert 'href="/revised/search/"' in revised, slug
        assert f'href="/unix/{slug}/"' in revised, slug
        assert "skill-document" not in revised, slug
        assert "초안" not in revised, slug
        assert "Verification required" not in revised, slug


def test_shell_keeps_footer_at_viewport_bottom(generated_site: Path) -> None:
    """Short pages must push the footer to the viewport bottom without affecting print."""

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    body_styles = stylesheet.partition("body {")[2].partition("}")[0]
    footer_styles = stylesheet.partition(".site-footer {")[2].partition("}")[0]
    print_styles = stylesheet.partition("@media print {")[2]

    assert "display: flex;" in body_styles
    assert "flex-direction: column;" in body_styles
    assert "min-height: 100vh;" in body_styles
    assert "min-height: 100dvh;" in body_styles
    assert "margin-top: auto;" in footer_styles
    assert "body { display: block !important; min-height: 0 !important;" in print_styles


def test_primary_page_shells_share_desktop_container_width(generated_site: Path) -> None:
    """Home, listing, and criterion pages must share the desktop container width."""

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    root_styles = stylesheet.partition(":root {")[2].partition("}")[0]
    shell_styles = stylesheet.partition(".page-shell {")[2].partition("}")[0]
    shell_modifier_styles = stylesheet.partition(".page-shell--single")[2].partition(".content {")[
        0
    ]

    assert "--container-width: 1280px;" in root_styles
    assert "width: min(var(--container-width), calc(100% - 48px));" in shell_styles
    assert "1120px" not in shell_modifier_styles
    assert "max-width:" not in shell_modifier_styles
    assert ".skill-page { max-width: 1120px; }" in stylesheet


def test_theme_control_and_initialization_are_present_on_every_page(
    generated_site: Path,
) -> None:
    """Every page must expose one labeled selector and initialize its theme before CSS."""

    initialization_script = '<script src="/assets/theme-init.js"></script>'
    stylesheet_link = '<link rel="stylesheet" href="/assets/styles.css">'
    for html_path in sorted(generated_site.rglob("*.html")):
        html_text = html_path.read_text(encoding="utf-8")
        head = html_text.partition("<head>")[2].partition("</head>")[0]
        assert html_text.count("data-theme-selector") == 1, html_path
        assert html_text.count('for="theme-selector"') == 1, html_path
        assert html_text.count('id="theme-selector"') == 1, html_path
        assert html_text.count('<option value="system">시스템</option>') == 1, html_path
        assert html_text.count('<option value="light">화이트</option>') == 1, html_path
        assert html_text.count('<option value="dark">다크</option>') == 1, html_path
        assert html_text.count('<option value="oled">OLED 블랙</option>') == 1, html_path
        assert initialization_script in head, html_path
        assert "unsafe-inline" not in head, html_path
        assert head.index("Content-Security-Policy") < head.index(initialization_script), html_path
        assert head.index(initialization_script) < head.index(stylesheet_link), html_path
        assert "defer" not in initialization_script
        assert "async" not in initialization_script

    assert (generated_site / "assets" / "theme-init.js").is_file()


def test_theme_scripts_persist_and_synchronize_valid_preferences(
    generated_site: Path,
) -> None:
    """Theme scripts must validate storage and synchronize system and tab changes."""

    initialization_script = (generated_site / "assets" / "theme-init.js").read_text(
        encoding="utf-8"
    )
    site_script = (generated_site / "assets" / "site.js").read_text(encoding="utf-8")

    assert 'new Set(["system", "light", "dark", "oled"])' in initialization_script
    assert "window.localStorage.getItem(storageKey)" in initialization_script
    assert 'window.matchMedia?.("(prefers-color-scheme: dark)")' in initialization_script
    assert "root.dataset.themePreference = preference;" in initialization_script
    assert "root.dataset.theme = resolvedTheme;" in initialization_script
    assert "window.localStorage.setItem(themeStorageKey, preference);" in site_script
    assert 'systemTheme.addEventListener("change"' in site_script
    assert 'window.addEventListener("storage"' in site_script


def test_theme_palettes_meet_readability_contrast_contract(generated_site: Path) -> None:
    """Representative text, controls, boundaries, and statuses must meet WCAG contrast."""

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    palettes = {
        "light": _theme_palette(stylesheet, ":root"),
        "dark": _theme_palette(stylesheet, ':root[data-theme="dark"]'),
        "oled": _theme_palette(stylesheet, ':root[data-theme="oled"]'),
    }
    text_pairs = (
        ("body", "canvas"),
        ("muted", "canvas"),
        ("link", "canvas"),
        ("primary-text", "surface-soft"),
        ("on-dark", "surface-dark"),
        ("on-primary", "primary"),
        ("code-text", "code-surface"),
        ("badge-high-text", "badge-high-background"),
        ("badge-medium-text", "badge-medium-background"),
        ("success", "badge-low-background"),
        ("review", "badge-review-background"),
        ("body", "note-background"),
    )
    for theme_name, palette in palettes.items():
        for foreground_name, background_name in text_pairs:
            assert (
                _contrast_ratio(palette[foreground_name], palette[background_name])
                >= MINIMUM_TEXT_CONTRAST
            ), (theme_name, foreground_name, background_name)
        assert _contrast_ratio(palette["hairline"], palette["surface-soft"]) >= MINIMUM_UI_CONTRAST

    assert palettes["oled"]["canvas"] == "#000"
    assert palettes["oled"]["surface"] == "#000"
    assert ':root[data-theme="dark"] {\n  color-scheme: dark;' in stylesheet
    assert ':root[data-theme="oled"] {\n  color-scheme: dark;' in stylesheet
    print_styles = stylesheet.partition("@media print {")[2]
    assert ':root[data-theme="dark"]' in print_styles
    assert "color-scheme: light;" in print_styles


def test_home_links_llm_usage_to_separate_skill_page(generated_site: Path) -> None:
    """The home page must place LLM usage after domains and link its full instructions."""

    repository = repository_root()
    skill_source = (
        repository / SITE_SKILL_DIRECTORY / "kisa-cce-guide-explorer" / "SKILL.md"
    ).read_text(encoding="utf-8")
    public_skill = (generated_site / "SKILL.md").read_text(encoding="utf-8")
    home_html = (generated_site / "index.html").read_text(encoding="utf-8")
    skill_html = (generated_site / "skill" / "index.html").read_text(encoding="utf-8")

    assert public_skill == skill_source
    assert '<section class="surface llm-guide"' in home_html
    assert '<ol class="llm-guide__steps">' in home_html
    assert 'href="/skill/">본문 보기</a>' in home_html
    assert 'href="/SKILL.md" download' not in home_html
    assert "파일 다운로드" not in home_html
    assert 'data-copy-surface="llm-usage-prompt"' in home_html
    assert 'data-copy-control="llm-usage-prompt" hidden' in home_html
    assert 'class="copy-button"' not in home_html
    assert "INSTRUCTIONS" not in home_html
    assert "data-skill-document" not in home_html
    assert home_html.index('class="hero"') < home_html.index('class="surface domain-directory"')
    assert home_html.index('class="surface domain-directory"') < home_html.index(
        'class="surface llm-guide"'
    )

    assert '<main id="main-content" class="page-shell page-shell--single skill-page">' in skill_html
    assert '<h1 id="skill-page-heading">LLM 사용 지침</h1>' in skill_html
    assert 'href="/SKILL.md" download>SKILL.md 다운로드</a>' in skill_html
    assert '<div class="skill-document__body" data-skill-document>' in skill_html
    assert "<h2>KISA CCE Guide Explorer</h2>" in skill_html
    assert "<h3>When to Use</h3>" in skill_html
    assert "<h3>HTTP Usage</h3>" in skill_html
    assert "<h3>Page Types</h3>" in skill_html
    assert "<h3>Troubleshooting</h3>" in skill_html
    assert "description: Finds and reads" not in skill_html
    assert "dataset/search-index.json" not in skill_html


def test_llm_usage_and_skill_page_are_responsive(generated_site: Path) -> None:
    """The LLM usage chapter and separate skill article must fit narrow screens."""

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    llm_styles = stylesheet.partition(".llm-guide {")[2].partition(".domain-directory {")[0]
    compact_styles = stylesheet.partition("@media (max-width: 768px) {")[2].partition(
        "@media (max-width: 480px) {"
    )[0]

    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in llm_styles
    assert "max-width: 880px;" in llm_styles
    assert "overflow: auto;" in llm_styles
    assert "white-space: pre-wrap;" not in llm_styles
    assert ".llm-guide__prompt .code-block pre" not in llm_styles
    code_block_styles = stylesheet.partition(".code-block pre {")[2].partition("}")[0]
    assert "overflow: auto;" in code_block_styles
    assert "white-space: pre;" in code_block_styles
    assert "word-break: normal;" in code_block_styles
    assert "overflow-wrap: normal;" in code_block_styles
    assert ".llm-guide__steps { grid-template-columns: 1fr;" in compact_styles
    assert ".skill-document__body { margin-top: var(--space-6);" in compact_styles


def test_source_anomaly_pages_and_ui_are_not_published(generated_site: Path) -> None:
    """Source-review records must not be rendered in public HTML."""

    assert not (generated_site / "anomalies").exists()
    for html_path in generated_site.rglob("*.html"):
        html_text = html_path.read_text(encoding="utf-8")
        assert "/anomalies/" not in html_text, html_path
        assert "원문 이상" not in html_text, html_path
        assert 'class="annotations"' not in html_text, html_path


def test_unofficial_conversion_disclaimer_is_not_rendered(generated_site: Path) -> None:
    """Public pages must not render the removed unofficial-conversion disclaimer."""

    home_html = (generated_site / "index.html").read_text(encoding="utf-8")
    assert (
        '<p class="hero__lede">주요정보통신기반시설 기술적 취약점 분석·평가 방법 '
        "상세가이드</p>" in home_html
    )
    for html_path in generated_site.rglob("*.html"):
        html_text = html_path.read_text(encoding="utf-8")
        for removed_phrase in (
            "원문을 대체하지 않는 비공식 변환본",
            "비공식 웹 변환본",
        ):
            assert removed_phrase not in html_text, html_path


def test_source_attribution_section_is_not_rendered(generated_site: Path) -> None:
    """Criterion pages must not render the redundant source-attribution section."""

    for dataset_path in (generated_site / "dataset" / "criteria").rglob("*.json"):
        domain_identifier = dataset_path.parent.name
        detail_path = generated_site / domain_identifier / dataset_path.stem / "index.html"
        detail_html = detail_path.read_text(encoding="utf-8")
        assert "원문 및 출처" not in detail_html, detail_path
        assert "KISA 원문 게시물 보기" not in detail_html, detail_path
        assert 'class="provenance"' not in detail_html, detail_path


def test_document_status_and_json_button_are_not_rendered(generated_site: Path) -> None:
    """Criterion headers must omit internal status and the redundant dataset button."""

    for dataset_path in sorted((generated_site / "dataset" / "criteria").glob("*/*.json")):
        domain_identifier = dataset_path.parent.name
        detail_path = generated_site / domain_identifier / dataset_path.stem / "index.html"
        detail_html = detail_path.read_text(encoding="utf-8")
        criterion_header = detail_html.partition('<header class="criterion__header">')[2].partition(
            "</header>"
        )[0]
        assert "문서 상태" not in criterion_header, detail_path
        assert "구조화 문서" not in criterion_header, detail_path
        assert "자동 전사 · 검토 필요" not in criterion_header, detail_path
        assert "JSON 데이터 보기" not in criterion_header, detail_path
        assert '<link rel="alternate" type="application/json"' in detail_html, detail_path


def test_header_menu_contains_domain_exploration(generated_site: Path) -> None:
    """Every page must expose domain exploration through the primary header menu."""

    taxonomy = load_yaml(repository_root() / "data/taxonomy.yaml")
    domain_values = as_sequence(taxonomy["domains"], location="taxonomy.domains")
    domain_routes = []
    for domain_value in domain_values:
        domain = as_mapping(domain_value, location="taxonomy.domains[]")
        domain_identifier = domain["identifier"]
        assert isinstance(domain_identifier, str)
        domain_routes.append(f'href="/{domain_identifier}/"')

    for html_path in generated_site.rglob("*.html"):
        html_text = html_path.read_text(encoding="utf-8")
        navigation_html = html_text.partition('<nav id="site-navigation"')[2].partition("</nav>")[0]
        assert '<details class="site-nav__domains"' in navigation_html, html_path
        assert "<summary>분야</summary>" in navigation_html, html_path
        is_revised = html_path.relative_to(generated_site).parts[0] == "revised"
        home_route = "/revised/" if is_revised else "/"
        assert f'<li><a href="{home_route}">전체 분야</a></li>' in navigation_html, html_path
        expected_routes = ['href="/revised/unix/"'] if is_revised else domain_routes
        for domain_route in expected_routes:
            assert domain_route in navigation_html, html_path

    detail_html = (generated_site / "unix" / "u-01" / "index.html").read_text(encoding="utf-8")
    assert 'href="/unix/" aria-current="location"' in detail_html
    assert 'class="sidebar"' not in detail_html

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    base_styles = stylesheet.partition("@media (max-width: 1080px) {")[0]
    assert ".site-nav__domains { position: relative; display: block; }" in base_styles
    mobile_styles = stylesheet.partition("@media (max-width: 768px) {")[2].partition(
        "@media (max-width: 480px) {"
    )[0]
    assert ".site-nav__domains { position: static; display: block;" in mobile_styles
    assert "position: static;" in mobile_styles
    assert "width: auto;" in mobile_styles
    assert "max-height: calc(100vh - var(--header-height) - 16px);" in mobile_styles
    assert "max-height: calc(100dvh - var(--header-height) - 16px);" in mobile_styles

    site_script = (generated_site / "assets" / "site.js").read_text(encoding="utf-8")
    assert 'navigation.querySelector(".site-nav__domains")' in site_script
    assert 'window.matchMedia("(max-width: 768px)")' in site_script
    assert 'compactNavigation.addEventListener("change", synchronizeNavigation);' in site_script
    assert "navigation.contains(document.activeElement)" in site_script
    assert "navigationButton.focus();" in site_script
    assert 'domainNavigation?.querySelector("summary")?.focus();' in site_script
    assert "domainNavigation.open = false;" in site_script
    assert "sidebarDisclosure" not in site_script


def test_document_table_of_contents_uses_a_responsive_floating_panel(
    generated_site: Path,
) -> None:
    """Desktop TOCs must float at the upper right and remain collapsible."""

    detail_html = (generated_site / "unix" / "u-01" / "index.html").read_text(encoding="utf-8")
    assert detail_html.count('aria-label="문서 목차"') == 1
    assert '<nav class="toc" aria-label="문서 목차" data-table-of-contents>' in detail_html
    document_html = detail_html.partition(
        '<div class="criterion__document criterion__document--with-toc">'
    )[2].partition("</article>")[0]
    assert document_html.index('<nav class="toc"') < document_html.index(
        '<div class="criterion__body">'
    )
    assert (
        'aria-controls="table-of-contents-content" data-table-of-contents-toggle hidden>목차'
        in detail_html
    )
    assert '<div id="table-of-contents-content" data-table-of-contents-content>' in detail_html

    site_script = (generated_site / "assets" / "site.js").read_text(encoding="utf-8")
    assert 'window.matchMedia("(max-width: 1080px)")' in site_script
    assert "tableOfContents.dataset.expanded = String(expanded);" in site_script
    assert "tableOfContentsToggle.hidden = false;" in site_script
    assert "tableOfContentsTitle.hidden = true;" in site_script
    assert "setTableOfContentsExpanded(!compact);" in site_script
    assert 'addEventListener("change", synchronizeTableOfContents)' in site_script
    assert "setTableOfContentsExpanded(tableOfContentsContent.hidden);" in site_script

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    base_styles = stylesheet.partition("@media (min-width: 1081px) {")[0]
    desktop_styles = stylesheet.partition("@media (min-width: 1081px) {")[2].partition(".note {")[0]
    assert ".criterion__document--with-toc { display: flow-root; }" in base_styles
    assert 'toc[data-enhanced="true"] .toc__toggle { display: flex; }' in base_styles
    assert "float: right;" in desktop_styles
    assert "width: min(240px, 32%);" in desktop_styles
    assert "position: sticky;" not in desktop_styles
    assert "max-height: calc(100dvh - var(--header-height) - 48px);" in desktop_styles
    assert '.toc[data-expanded="false"]' in desktop_styles

    compact_styles = stylesheet.partition("@media (max-width: 1080px) {")[2].partition(
        "@media (max-width: 768px) {"
    )[0]
    assert "float: right;" not in compact_styles
    assert ".toc { margin-bottom: var(--space-8); }" in compact_styles
    assert '.toc__toggle[aria-expanded="true"]::after { content: "\N{MINUS SIGN}"; }' in base_styles


def test_narrow_layout_prevents_root_horizontal_overflow(generated_site: Path) -> None:
    """Narrow pages must contain hidden labels and allow hero statistics to shrink."""

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    visually_hidden_styles = stylesheet.partition(".visually-hidden {")[2].partition("}")[0]
    narrow_styles = stylesheet.partition("@media (max-width: 320px) {")[2].partition(
        "@media (prefers-reduced-motion: reduce) {"
    )[0]

    assert "inset: 0 auto auto 0 !important;" in visually_hidden_styles
    assert ".hero__stats > div" not in narrow_styles


def test_judgment_items_render_as_heading_sections(generated_site: Path) -> None:
    """Judgment labels must be H4 headings rather than list-item prefixes."""

    dataset_root = generated_site / "dataset" / "criteria"
    page_count = 0
    for dataset_path in sorted(dataset_root.glob("*/*.json")):
        normalized = as_mapping(
            json.loads(dataset_path.read_text(encoding="utf-8")),
            location=str(dataset_path),
        )
        judgment_blocks = [
            as_mapping(value, location=f"{dataset_path}.blocks[]")
            for value in as_sequence(normalized["blocks"], location=f"{dataset_path}.blocks")
            if isinstance(value, dict)
            and value.get("semanticRole") in {"good", "vulnerable"}
            and value.get("semanticPath") == ["assessment", "judgment"]
        ]
        assert [block["semanticRole"] for block in judgment_blocks] == ["good", "vulnerable"]

        relative_path = dataset_path.relative_to(dataset_root)
        detail_path = generated_site / relative_path.parent / relative_path.stem / "index.html"
        detail_html = detail_path.read_text(encoding="utf-8")
        inspector = _inspect(detail_path)
        assert detail_html.count('<div class="judgment-criteria">') == 1
        for block, label in zip(judgment_blocks, ("양호:", "취약:"), strict=True):
            block_reference = block["blockReference"]
            assert isinstance(block_reference, str)
            heading_identifier = f"{block_reference}-heading"
            group_tag, group_attributes = inspector.elements_by_identifier[block_reference]
            heading_tag, _ = inspector.elements_by_identifier[heading_identifier]
            assert group_tag == "section"
            assert group_attributes["aria-labelledby"] == heading_identifier
            assert heading_tag == "h4"
            assert f'<h4 id="{heading_identifier}">{label}</h4>' in detail_html
            assert f'<li id="{block_reference}"' not in detail_html
        page_count += 1

    assert page_count == EXPECTED_CRITERION_COUNT


def test_tables_only_force_horizontal_scroll_for_intrinsic_overflow(
    generated_site: Path,
) -> None:
    """Tables that fit their container must not cross a fixed minimum-width threshold."""

    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    table_scroll_styles = stylesheet.partition(".table-scroll {")[2].partition("}")[0]
    table_styles = stylesheet.partition("table {")[2].partition("}")[0]

    assert "overflow: auto;" in table_scroll_styles
    assert "min-width: min(640px, 100%);" in table_styles
    assert "min-width: 640px;" not in table_styles
    table_cell_styles = stylesheet.partition("th, td {")[2].partition("}")[0]
    assert "white-space: nowrap;" in table_cell_styles
    site_script = (generated_site / "assets" / "site.js").read_text(encoding="utf-8")
    assert 'document.querySelectorAll(".code-block pre, .table-scroll")' in site_script
    assert "updateHorizontalScrollRegion(region);" in site_script


def test_captionless_tables_use_semantic_heading_labels(generated_site: Path) -> None:
    """Tables without source captions must use their nearest semantic heading as a label."""

    table_count = 0
    for dataset_path in sorted((generated_site / "dataset" / "criteria").glob("*/*.json")):
        normalized = json.loads(dataset_path.read_text(encoding="utf-8"))
        blocks = [
            as_mapping(value, location=f"{dataset_path}.blocks[]")
            for value in as_sequence(normalized["blocks"], location=f"{dataset_path}.blocks")
        ]
        heading_identifiers_by_path = {
            tuple(as_sequence(block["semanticPath"], location="block.semanticPath")): block[
                "blockReference"
            ]
            for block in blocks
            if block["blockType"] == "heading"
        }
        table_blocks = [block for block in blocks if block["blockType"] == "table"]
        if not table_blocks:
            continue
        domain_identifier = dataset_path.parent.name
        detail_path = generated_site / domain_identifier / dataset_path.stem / "index.html"
        detail_html = detail_path.read_text(encoding="utf-8")
        inspector = _inspect(detail_path)
        assert "원문 표 table" not in detail_html
        assert "<caption" not in detail_html
        for block in table_blocks:
            block_reference = block["blockReference"]
            semantic_path = tuple(as_sequence(block["semanticPath"], location="block.semanticPath"))
            expected_heading_identifier = heading_identifiers_by_path[semantic_path]
            assert isinstance(block_reference, str)
            assert isinstance(expected_heading_identifier, str)
            table_tag, table_attributes = inspector.elements_by_identifier[block_reference]
            assert table_tag == "table"
            assert table_attributes["aria-labelledby"] == expected_heading_identifier
            assert (
                f'<div class="table-scroll" role="region" '
                f'aria-labelledby="{expected_heading_identifier}"' in detail_html
            )
            table_count += 1

    assert table_count == EXPECTED_TABLE_COUNT


def test_source_table_caption_remains_visible(generated_site: Path) -> None:
    """A real source caption must remain the visible accessible name."""

    normalized = json.loads(
        (generated_site / "dataset/criteria/web-service/web-16.json").read_text(encoding="utf-8")
    )
    blocks = [
        as_mapping(value, location="web-16.blocks[]")
        for value in as_sequence(normalized["blocks"], location="web-16.blocks")
    ]
    heading = next(
        block for block in blocks if block["blockReference"] == "web-16:remediation.linux.heading:1"
    )
    source_table = next(
        block for block in blocks if block["blockReference"] == "web-16:remediation.linux.table:2"
    )
    table = {**source_table, "caption": "ServerTokens 옵션별 반환 정보"}

    with pytest.raises(ValueError, match="table has no caption or semantic heading"):
        _render_blocks([source_table], base_path="")
    rendered = _render_blocks([heading, table], base_path="")

    assert (
        '<caption id="caption-web-16:remediation.linux.table:2">'
        "ServerTokens 옵션별 반환 정보</caption>" in rendered
    )
    assert 'role="region" aria-labelledby="caption-web-16:remediation.linux.table:2"' in rendered


def test_static_validation_rejects_unlabelled_table(
    generated_site: Path,
    tmp_path: Path,
) -> None:
    """A table without a source caption must retain its semantic heading label."""

    site_root = _copy_site(generated_site, tmp_path)
    detail_path = site_root / "web-service/web-16/index.html"
    detail_html = detail_path.read_text(encoding="utf-8")
    table_pattern = re.compile(
        r'(<table id="web-16:remediation\.linux\.table:2"[^>]*?) '
        r'aria-labelledby="web-16:remediation\.linux\.heading:1"'
    )
    mutated_html, replacement_count = table_pattern.subn(r"\1", detail_html, count=1)
    assert replacement_count == 1
    detail_path.write_text(mutated_html, encoding="utf-8")

    assert "site-table-accessibility" in _issue_rule_identifiers(site_root)

    non_heading_label, non_heading_label_count = table_pattern.subn(
        r'\1 aria-labelledby="web-16:remediation.linux.table:2"',
        detail_html,
        count=1,
    )
    assert non_heading_label_count == 1
    detail_path.write_text(non_heading_label, encoding="utf-8")
    assert "site-table-accessibility" in _issue_rule_identifiers(site_root)

    empty_caption, empty_caption_count = re.subn(
        r'(<table id="web-16:remediation\.linux\.table:2"[^>]*>)',
        r'\1<caption id="caption-web-16:remediation.linux.table:2"></caption>',
        mutated_html,
        count=1,
    )
    assert empty_caption_count == 1
    detail_path.write_text(empty_caption, encoding="utf-8")
    assert "site-table-accessibility" in _issue_rule_identifiers(site_root)


def test_code_blocks_are_buttonless_click_to_copy_surfaces(generated_site: Path) -> None:
    """Code blocks must expose one target without rendering separate copy buttons."""

    copy_surface_count = 0
    for html_path in sorted(generated_site.rglob("*.html")):
        html = html_path.read_text(encoding="utf-8")
        assert 'class="copy-button"' not in html, html_path
        copy_surfaces = re.findall(
            r'<div class="code-block[^"]*" data-copy-surface="([^"]+)">'
            r'<button class="code-copy-control"[^>]*data-copy-control="([^"]+)"',
            html,
        )
        for target_identifier, control_identifier in copy_surfaces:
            assert target_identifier, html_path
            assert control_identifier == target_identifier, html_path
            assert f'id="{target_identifier}"' in html, html_path
            copy_surface_count += 1
    assert copy_surface_count > 0

    base_html = (repository_root() / "site/templates/base.html").read_text(encoding="utf-8")
    assert 'role="status" aria-live="polite" aria-atomic="true" data-copy-status' in base_html
    stylesheet = (generated_site / "assets" / "styles.css").read_text(encoding="utf-8")
    assert '.code-block[data-copy-enabled="true"] { cursor: copy; }' in stylesheet
    assert ".copy-button" not in stylesheet
    assert ".code-copy-control:focus-visible" in stylesheet
    assert '.code-block[data-copy-state="success"]::after { content: "복사됨"; }' in stylesheet
    assert '.code-block[data-copy-state="error"]::after { content: "복사 실패"; }' in stylesheet
    site_script = (generated_site / "assets" / "site.js").read_text(encoding="utf-8")
    assert 'document.querySelectorAll("[data-copy-surface]")' in site_script
    assert 'surface.querySelector("[data-copy-control]")' in site_script
    assert 'surface.addEventListener("pointermove"' in site_script
    assert 'surface.addEventListener("dblclick"' in site_script
    assert "window.getSelection?.();" in site_script


def test_table_of_contents_preserves_heading_hierarchy(generated_site: Path) -> None:
    """Every criterion TOC must reproduce its complete normalized heading tree."""

    normalized_root = generated_site.parent / "normalized"
    table_of_contents_page_count = 0
    for normalized_path in sorted(normalized_root.rglob("*.json")):
        normalized = as_mapping(
            json.loads(normalized_path.read_text(encoding="utf-8")),
            location=str(normalized_path),
        )
        heading_blocks = [
            as_mapping(block, location=f"{normalized_path}.blocks[]")
            for block in as_sequence(normalized["blocks"], location=f"{normalized_path}.blocks")
            if isinstance(block, dict) and block.get("blockType") == "heading"
        ]
        classification = as_mapping(
            normalized["classification"],
            location=f"{normalized_path}.classification",
        )
        criterion = as_mapping(
            normalized["criterion"],
            location=f"{normalized_path}.criterion",
        )
        domain_identifier = classification["domainIdentifier"]
        slug = criterion["slug"]
        assert isinstance(domain_identifier, str)
        assert isinstance(slug, str)
        inspector = _inspect(generated_site / domain_identifier / slug / "index.html")

        if len(heading_blocks) < _TABLE_OF_CONTENTS_MINIMUM_HEADING_COUNT:
            assert inspector.table_of_contents_link_depths == []
            continue

        table_of_contents_page_count += 1
        ancestor_levels: list[int] = []
        expected_links: list[tuple[int, str, str]] = []
        for block in heading_blocks:
            heading_level = block["headingLevel"]
            assert isinstance(heading_level, int)
            while ancestor_levels and heading_level <= ancestor_levels[-1]:
                ancestor_levels.pop()
            ancestor_levels.append(heading_level)
            depth = len(ancestor_levels)
            expected_links.append((depth, str(heading_level), str(depth)))

        assert inspector.table_of_contents_link_depths == expected_links, normalized_path
        assert inspector.maximum_table_of_contents_list_depth == max(
            depth for depth, _, _ in expected_links
        )

    assert table_of_contents_page_count == EXPECTED_CRITERION_COUNT


def test_table_of_contents_collapses_skipped_heading_levels() -> None:
    """Skipped levels must nest below the nearest lower heading without placeholders."""

    heading_levels = [2, 4, 5, 3, 2, 3]
    heading_blocks: list[dict[str, JsonValue]] = [
        {
            "blockReference": f"heading-{index}",
            "content": f"Heading {index}",
            "headingLevel": heading_level,
        }
        for index, heading_level in enumerate(heading_levels, start=1)
    ]
    inspector = PageInspector()
    inspector.feed(_render_table_of_contents(heading_blocks))

    assert inspector.table_of_contents_link_depths == [
        (1, "2", "1"),
        (2, "4", "2"),
        (3, "5", "3"),
        (2, "3", "2"),
        (1, "2", "1"),
        (2, "3", "2"),
    ]


def test_github_pages_marker_is_generated(generated_site: Path) -> None:
    """GitHub Pages must bypass Jekyll processing for generated static assets."""

    assert (generated_site / ".nojekyll").is_file()


def test_generated_site_passes_static_validation(generated_site: Path) -> None:
    """The complete site must pass semantic, link, image, and search checks."""

    manifest = load_yaml(repository_root() / "data/criteria-manifest.yaml")
    sequential_issues = validate_site(
        site_root=generated_site,
        manifest=manifest,
        expected_html_page_count=EXPECTED_HTML_PAGE_COUNT,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        parallel_issues = validate_site(
            site_root=generated_site,
            manifest=manifest,
            expected_html_page_count=EXPECTED_HTML_PAGE_COUNT,
            executor=executor,
        )
    assert sequential_issues == parallel_issues == []
    assert not any(
        '<th scope="col"></th>' in path.read_text(encoding="utf-8")
        for path in generated_site.rglob("*.html")
    )


def test_search_page_keeps_a_script_independent_fallback(generated_site: Path) -> None:
    """Search must retain a visible criterion list when scripts or fetch fail."""

    search_html = (generated_site / "search" / "index.html").read_text(encoding="utf-8")
    assert "data-search-fallback" in search_html
    assert "<noscript>" not in search_html
    assert search_html.count('<li><a href="/') >= EXPECTED_CRITERION_COUNT
    assert 'placeholder="예: 리눅스에서 root 원격 로그인을 막고 싶어"' in search_html
    assert 'id="search-help" class="search-help"' in search_html
    natural_search_script = '<script src="/assets/search-core.js" defer></script>'
    interface_script = '<script src="/assets/search.js" defer></script>'
    assert natural_search_script in search_html
    assert search_html.index(natural_search_script) < search_html.index(interface_script)
    assert (generated_site / "assets" / "search-core.js").is_file()


def test_highlight_assets_are_self_hosted_and_checksum_pinned(generated_site: Path) -> None:
    """The approved Highlight.js distribution must be copied without alteration."""

    expected_checksums = {
        "highlight.min.js": "8ab71eb09c51f501e5e25157d9cff100e46cc29bcbfc744d0b746d451fca7f53",
        "github-dark.min.css": "9f208d022102b1d0c7aebfecd8e42ca7997d5de636649d2b31ea63093d809019",
        "LICENSE": "6c081431591d9df696c82dc598fe1423765b8a299b200ed00b281afd0f64c490",
        "languages/apache.min.js": (
            "9dc53948535832b25d4eb23d40fef70510317398fdaf15f8a3fcc3ae7e0c490c"
        ),
        "languages/dos.min.js": "e184a6f9cead550b7b39b6114d17cafd08904557317622d7ea488aed01cf31ee",
        "languages/http.min.js": "b09b4afc1ce71f37f4434baccae5800e0812c6eb1db2cb8978fe9e4d668f45a6",
        "languages/nginx.min.js": (
            "8c53cc63ce0cf4ec0ad80b77273de23dec6974ab069d46d14d5c75febe880e1f"
        ),
        "languages/powershell.min.js": (
            "fc298b3e0db362e531e6d58988b7a78f83da19df6b5d74db75bc961b2bfbfd3d"
        ),
        "languages/properties.min.js": (
            "8a987022cc566fa5bfcd79058ec4ba010920d576fff809b92317295827402590"
        ),
    }
    vendor_directory = generated_site / "assets" / "vendor" / "highlight.js"
    for relative_path, expected_checksum in expected_checksums.items():
        assert hashlib.sha256((vendor_directory / relative_path).read_bytes()).hexdigest() == (
            expected_checksum
        )


def test_highlighting_is_explicit_and_progressively_enhanced(generated_site: Path) -> None:
    """Only eligible explicit languages may load the self-hosted highlighter."""

    criterion_html = (generated_site / "web-application" / "ci" / "index.html").read_text(
        encoding="utf-8"
    )
    core_script = "/assets/vendor/highlight.js/highlight.min.js"
    http_script = "/assets/vendor/highlight.js/languages/http.min.js"
    initializer_script = "/assets/highlight-init.js"
    assert core_script in criterion_html
    assert http_script in criterion_html
    assert initializer_script in criterion_html
    assert criterion_html.index(core_script) < criterion_html.index(http_script)
    assert criterion_html.index(http_script) < criterion_html.index(initializer_script)
    assert "/assets/vendor/highlight.js/github-dark.min.css" in criterion_html
    assert 'data-code-language="html" data-highlight-language="xml"' in criterion_html
    assert 'data-code-language="velocity" data-highlight-language="xml"' in criterion_html

    plaintext_html = (generated_site / "unix" / "u-01" / "index.html").read_text(encoding="utf-8")
    assert core_script not in plaintext_html
    assert initializer_script not in plaintext_html
    assert "data-highlight-language" not in plaintext_html


def test_every_code_block_follows_the_highlighting_policy(generated_site: Path) -> None:
    """Every generated code block must opt in or remain untouched by policy."""

    for dataset_path in sorted((generated_site / "dataset" / "criteria").rglob("*.json")):
        normalized = json.loads(dataset_path.read_text(encoding="utf-8"))
        code_blocks = [
            as_mapping(value, location="normalized.blocks[]")
            for value in as_sequence(normalized["blocks"], location="normalized.blocks")
            if isinstance(value, dict) and value.get("blockType") == "codeBlock"
        ]
        if not code_blocks:
            continue
        domain_identifier = dataset_path.parent.name
        detail_path = generated_site / domain_identifier / dataset_path.stem / "index.html"
        detail_html = detail_path.read_text(encoding="utf-8")
        inspector = _inspect(detail_path)
        highlighted_block_count = 0
        for block in code_blocks:
            block_reference = block["blockReference"]
            assert isinstance(block_reference, str)
            _, pre_attributes = inspector.elements_by_identifier[block_reference]
            _, code_attributes = inspector.elements_by_identifier[f"code-{block_reference}"]
            _assert_highlight_contract(
                block=block,
                pre_attributes=pre_attributes,
                code_attributes=code_attributes,
            )
            highlighted_block_count += int("data-highlight-language" in code_attributes)
        if highlighted_block_count:
            assert "/assets/vendor/highlight.js/highlight.min.js" in detail_html
            assert "/assets/highlight-init.js" in detail_html
        else:
            assert "/assets/vendor/highlight.js/highlight.min.js" not in detail_html
            assert "/assets/highlight-init.js" not in detail_html


def test_static_validation_rejects_page_contract_regressions(
    generated_site: Path,
    tmp_path: Path,
) -> None:
    """Static validation must reject inaccessible or stale detail-page contracts."""

    site_root = _copy_site(generated_site, tmp_path)
    u_01_path = site_root / "unix" / "u-01" / "index.html"
    u_01_html = u_01_path.read_text(encoding="utf-8")
    assert 'data-criterion-code="U-01"' in u_01_html
    assert 'rel="alternate" type="application/json"' in u_01_html
    assert "<code " in u_01_html
    u_01_html = u_01_html.replace(
        'data-criterion-code="U-01"',
        'data-criterion-code="U-02"',
        1,
    ).replace(
        'rel="alternate" type="application/json"',
        'rel="related" type="application/json"',
    )
    u_01_html = u_01_html.replace("<code ", "<span ", 1).replace(
        "</code>",
        "</span>",
        1,
    )
    u_01_html = u_01_html.replace(
        "</article>",
        "<table><caption>보조 표</caption><tbody><tr><td>값</td></tr></tbody></table></article>",
        1,
    )
    u_01_path.write_text(u_01_html, encoding="utf-8")

    u_02_path = site_root / "unix" / "u-02" / "index.html"
    u_02_html = u_02_path.read_text(encoding="utf-8")
    assert "/dataset/criteria/unix/u-02.json" in u_02_html
    u_02_path.write_text(
        u_02_html.replace(
            "/dataset/criteria/unix/u-02.json",
            "/dataset/criteria/unix/u-01.json",
        ),
        encoding="utf-8",
    )

    home_path = site_root / "index.html"
    home_html = home_path.read_text(encoding="utf-8")
    assert 'aria-controls="site-navigation"' in home_html
    home_path.write_text(
        home_html.replace(
            'aria-controls="site-navigation"',
            'aria-controls="missing-navigation"',
            1,
        ),
        encoding="utf-8",
    )
    (site_root / "unix" / "u-03" / "index.html").unlink()

    rule_identifiers = _issue_rule_identifiers(site_root)
    assert {
        "site-aria-reference",
        "site-criterion-alternate",
        "site-criterion-attributes",
        "site-criterion-route",
        "site-pre-code",
        "site-table-accessibility",
    } <= rule_identifiers


def test_static_validation_rejects_search_index_contract_regressions(
    generated_site: Path,
    tmp_path: Path,
) -> None:
    """Search validation must reject stale, duplicate, and malformed records."""

    site_root = _copy_site(generated_site, tmp_path)
    search_path = site_root / "dataset" / "search-index.json"
    search_index = json.loads(search_path.read_text(encoding="utf-8"))
    records = search_index["records"]
    records[2]["route"] = "/missing/"
    records[3]["code"] = records[2]["code"]
    records[4].pop("searchSections")
    records[5]["targetLabels"] = records[5]["targetLabels"][:-1]
    search_path.write_text(
        json.dumps(search_index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    rule_identifiers = _issue_rule_identifiers(site_root)
    assert {
        "site-search-completeness",
        "site-search-route",
        "site-search-schema",
    } <= rule_identifiers


def test_detail_pages_expose_machine_attributes_and_block_ids(
    generated_site: Path,
) -> None:
    """Representative detail pages must expose metadata and stable anchors."""

    for relative_path in (
        "unix/u-01/index.html",
        "windows/w-01/index.html",
        "web-application/ci/index.html",
    ):
        detail_path = generated_site / relative_path
        detail_html = detail_path.read_text(encoding="utf-8")
        inspector = _inspect(detail_path)
        assert all(inspector.article_attributes.values())
        assert inspector.article_attributes["data-content-model"] in {
            "systemCriterion",
            "webApplicationCriterion",
        }
        assert any(identifier.endswith(".heading:1") for identifier in inspector.identifiers)
        assert not any(
            identifier.endswith(".transcription:1") for identifier in inspector.identifiers
        )
        assert '<link rel="canonical" href="/' in detail_html
        assert '<link rel="alternate" type="application/json" href="/' in detail_html
        assert '<dl class="criterion-meta">' in detail_html
        structured_data_text = detail_html.partition('<script type="application/ld+json">')[
            2
        ].partition("</script>")[0]
        structured_data = json.loads(structured_data_text)
        assert structured_data["@type"] == "TechArticle"
        assert structured_data["identifier"] == inspector.article_attributes["data-criterion-code"]
        assert structured_data["mainEntityOfPage"] == structured_data["url"]
        assert "additionalProperty" not in structured_data
        assert structured_data["articleSection"]
        assert structured_data["keywords"]
        assert structured_data["pagination"]


def test_internal_links_resolve(generated_site: Path) -> None:
    """All generated root-hosting links must resolve to a file or block."""

    for html_path in sorted(generated_site.rglob("*.html")):
        inspector = _inspect(html_path)
        for link in inspector.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            if not parsed.path:
                if parsed.fragment:
                    assert parsed.fragment in inspector.identifiers, (html_path, link)
                continue
            target_path = parsed.path
            if target_path.endswith("/"):
                resolved = generated_site / target_path.lstrip("/") / "index.html"
            else:
                resolved = generated_site / target_path.lstrip("/")
            assert resolved.exists(), (html_path, link)
            if parsed.fragment and resolved.suffix == ".html":
                target_inspector = _inspect(resolved)
                assert parsed.fragment in target_inspector.identifiers, (html_path, link)


def test_static_validation_resolves_relative_links_from_the_current_page(
    generated_site: Path,
    tmp_path: Path,
) -> None:
    """Relative links must resolve from the source page without escaping the site."""

    site_root = _copy_site(generated_site, tmp_path)
    detail_path = site_root / "unix" / "u-01" / "index.html"
    detail_html = detail_path.read_text(encoding="utf-8")
    detail_path.write_text(
        detail_html.replace("</article>", '<a href="../">분야</a></article>', 1),
        encoding="utf-8",
    )
    assert _issue_rule_identifiers(site_root) == set()

    root_path = site_root / "index.html"
    root_html = root_path.read_text(encoding="utf-8")
    root_path.unlink()
    assert "site-link" in _issue_rule_identifiers(site_root)
    root_path.write_text(root_html, encoding="utf-8")

    outside_path = tmp_path / "outside.html"
    outside_path.write_text("<h1>outside</h1>", encoding="utf-8")
    detail_path.write_text(
        detail_html.replace(
            "</article>",
            '<a href="../../../outside.html">외부 파일</a></article>',
            1,
        ),
        encoding="utf-8",
    )
    assert "site-link" in _issue_rule_identifiers(site_root)


def test_subpath_build_prefixes_links() -> None:
    """A repository-subpath build must prefix public links without changing files."""

    with TemporaryDirectory() as directory:
        output_root = Path(directory)
        build(output_root=output_root, base_path="/kisa-cce-guide-web")
        inspector = _inspect(output_root / "site" / "index.html")
        assert "/kisa-cce-guide-web/search/" in inspector.links
        detail_inspector = _inspect(output_root / "site" / "unix" / "u-01" / "index.html")
        assert "/kisa-cce-guide-web/unix/" in detail_inspector.links
        detail_html = (output_root / "site" / "unix" / "u-01" / "index.html").read_text(
            encoding="utf-8"
        )
        assert '<script src="/kisa-cce-guide-web/assets/theme-init.js"></script>' in detail_html
        mobile_navigation_html = detail_html.partition('<nav id="site-navigation"')[2].partition(
            "</nav>"
        )[0]
        assert 'href="/kisa-cce-guide-web/windows/"' in mobile_navigation_html
        manifest = load_yaml(repository_root() / "data/criteria-manifest.yaml")
        assert (
            validate_site(
                site_root=output_root / "site",
                manifest=manifest,
                expected_html_page_count=EXPECTED_HTML_PAGE_COUNT,
                base_path="/kisa-cce-guide-web",
            )
            == []
        )
        index_path = output_root / "site" / "index.html"
        index_html = index_path.read_text(encoding="utf-8")
        assert "/kisa-cce-guide-web/search/" in index_html
        index_path.write_text(
            index_html.replace("/kisa-cce-guide-web/search/", "/search/", 1),
            encoding="utf-8",
        )
        issues = validate_site(
            site_root=output_root / "site",
            manifest=manifest,
            expected_html_page_count=EXPECTED_HTML_PAGE_COUNT,
            base_path="/kisa-cce-guide-web",
        )
        assert "site-base-path" in {issue.rule_identifier for issue in issues}


@pytest.mark.parametrize(
    ("domain_identifier", "slug"),
    [("unix", "u-02"), ("windows", "w-01"), ("web-application", "ci")],
)
def test_typed_blocks_expose_semantic_elements_and_machine_attributes(
    generated_site: Path,
    domain_identifier: str,
    slug: str,
) -> None:
    """Structured blocks must retain semantic and provenance contracts."""

    inspector = _inspect(generated_site / domain_identifier / slug / "index.html")
    normalized = json.loads(
        (generated_site / "dataset" / "criteria" / domain_identifier / f"{slug}.json").read_text(
            encoding="utf-8"
        )
    )
    blocks = [
        as_mapping(value, location="normalized.blocks[]")
        for value in as_sequence(normalized["blocks"], location="normalized.blocks")
    ]
    assert {block["blockReference"] for block in blocks} <= inspector.identifiers
    for block in blocks:
        _assert_block_contract(inspector, block)

    if slug == "u-02":
        assert {"ol", "ul", "li", "aside", "table", "thead", "tbody", "th", "pre", "code"} <= set(
            inspector.tags
        )
        assert inspector.note_attributes
        assert all(attributes.get("aria-labelledby") for attributes in inspector.note_attributes)
        assert inspector.table_header_scopes
        assert set(inspector.table_header_scopes) == {"col"}
    else:
        assert {"ol", "li", "aside"} <= set(inspector.tags)
        assert inspector.note_attributes


def test_legacy_source_images_and_source_pdf_are_not_published(
    generated_site: Path,
) -> None:
    """A structured corpus must not publish obsolete source crops or the source PDF."""

    source_images = sorted((generated_site / "assets").rglob("*-source-region.png"))
    assert source_images == []
    assert not (generated_site / "source" / "kisa-cce-criteria-2026.pdf").exists()
    inspector = _inspect(generated_site / "windows" / "w-01" / "index.html")
    assert "figure" not in inspector.tags
    assert "img" not in inspector.tags
