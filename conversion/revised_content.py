"""Validate and build the independently authored modern Linux edition."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from markdown_it import MarkdownIt

from conversion.build_site import (
    _criterion_list_view,
    _detail_page,
    _render_page,
    _taxonomy_maps,
    _template_environment,
)
from conversion.common import (
    CriterionDocument,
    JsonValue,
    as_mapping,
    as_sequence,
    extract_leaf_blocks,
    heading_identifiers,
    load_criterion,
    load_json,
    load_yaml,
)
from conversion.paths import REVISED_CRITERIA_DIRECTORY, SCHEMA_DIRECTORY, SITE_TEMPLATE_DIRECTORY

PLATFORMS = ("rhel-10", "ubuntu-26.04", "debian-13")
REVISED_CRITERION_COUNT = 67
REVISED_HTML_PAGE_COUNT = REVISED_CRITERION_COUNT + 8
_MINIMUM_SECTION_PARTS = 3
LICENSE_LABEL = "라이선스: 공공누리 - 공공저작물 자유이용허락"


def _validate_structure(body: str) -> bool:
    """Require the original section hierarchy and complete distribution procedures."""

    sections = re.split(r"^## (.+)$", body, flags=re.MULTILINE)
    if sections[1::2] != ["개요", "점검 대상 및 판단 기준", "점검 및 조치 사례"]:
        return False
    required = (
        ["점검 내용", "점검 목적", "보안 위협"],
        ["대상", "판단 기준", "조치 방법", "조치 시 영향"],
        ["RHEL 10", "Ubuntu 26.04 LTS", "Debian 13"],
    )
    for section, labels in zip(sections[2::2], required, strict=True):
        subsections = re.split(r"^### (.+)$", section, flags=re.MULTILINE)
        headings = subsections[1::2]
        if [label for label in headings if label != "참고"] != labels:
            return False
        if any(not text.strip() for text in subsections[2::2]):
            return False
    judgments = sections[4]
    if not all(
        re.search(rf"^- \*\*{label}:\*\*\s+\S", judgments, re.MULTILINE)
        for label in ("양호", "취약")
    ):
        return False
    procedures = re.split(r"^### .+$", sections[6], flags=re.MULTILINE)[1:]
    return all(
        re.search(r"^1\. \S", text, re.MULTILINE)
        and re.search(r"^[ \t]*```(?:bash|text|sh)\b", text, re.MULTILINE)
        for text in procedures
    )


def _validate_document(document: CriterionDocument) -> None:
    """Reject incomplete metadata and sections before publication."""

    metadata = document.metadata
    valid = (
        set(metadata) == {"criterionCode", "title", "platforms", "status", "sources"}
        and metadata.get("criterionCode") == document.path.stem.upper()
        and isinstance(metadata.get("title"), str)
        and bool(str(metadata.get("title", "")).strip())
        and metadata.get("platforms") == list(PLATFORMS)
        and metadata.get("status") == "final"
        and _validate_structure(document.body)
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
    headings = [
        tokens[index + 1].content
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2"
    ]
    valid = valid and headings == ["개요", "점검 대상 및 판단 기준", "점검 및 조치 사례"]
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


def validate_revised_dataset(document: dict[str, JsonValue], repository: Path) -> None:
    """Validate the public revision dataset against its versioned contract."""

    schema = load_json(repository / SCHEMA_DIRECTORY / "revised-dataset.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: str(error.json_path))
    if errors:
        details = "; ".join(f"{error.json_path}: {error.message}" for error in errors)
        msg = f"revised dataset validation failed: {details}"
        raise ValueError(msg)


def _normalized_revision(
    document: CriterionDocument, repository: Path, taxonomy: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Adapt editorial Markdown to shared semantic rendering without PDF source claims."""

    from conversion.paths import criterion_directory  # noqa: PLC0415

    original = load_criterion(criterion_directory(repository, "unix") / document.path.name)
    blocks: list[JsonValue] = []
    for block in extract_leaf_blocks(
        document.body,
        criterion_slug=document.path.stem,
        heading_identifier_mapping={
            **heading_identifiers(taxonomy),
            "RHEL 10": "rhel10",
            "Ubuntu 26.04 LTS": "ubuntu2604",
            "Debian 13": "debian13",
        },
    ):
        record: dict[str, JsonValue] = {
            "blockReference": block.block_reference,
            "blockType": block.block_type,
            "content": block.content,
            "semanticRole": block.semantic_role,
            "semanticPath": list(block.semantic_path),
            "sourceSpans": [],
            "technicalLiterals": list(block.technical_literals),
            "publicationDisposition": "included",
        }
        for key, value in {
            "headingLevel": block.heading_level,
            "listType": block.list_type,
            "listDepth": block.list_depth,
            "codeLanguage": block.code_language,
            "codeContentType": block.code_content_type,
            "parentBlockReference": block.parent_block_reference,
            "tableHeaders": list(block.table_headers) if block.table_headers is not None else None,
            "tableRows": [list(row) for row in block.table_rows]
            if block.table_rows is not None
            else None,
        }.items():
            if value is not None:
                record[key] = cast("JsonValue", value)
        blocks.append(record)
    return {
        "edition": "revised",
        "contentModel": "systemCriterion",
        "criterion": original.metadata["criterion"],
        "classification": original.metadata["classification"],
        "targetIdentifiers": list(PLATFORMS),
        "sourceTargetText": "RHEL 10, Ubuntu 26.04 LTS, Debian 13",
        "basedOn": original.metadata["provenance"],
        "blocks": blocks,
    }


def build_revised_edition(
    *, repository: Path, output_root: Path, base_path: str = ""
) -> list[Path]:
    """Build the final edition with the original detail, browsing, and search components."""

    from conversion.build_content import _search_record  # noqa: PLC0415

    documents = load_revisions(repository)
    taxonomy = load_yaml(repository / "data/taxonomy.yaml")
    domains, categories, targets = _taxonomy_maps(taxonomy)
    domains = {"unix": domains["unix"]}
    targets.update(dict(zip(PLATFORMS, ("RHEL 10", "Ubuntu 26.04 LTS", "Debian 13"), strict=True)))
    manifest = load_yaml(repository / "data/criteria-manifest.yaml")
    records = [
        {**record, "route": "/revised" + str(record["route"])}
        for record in as_sequence(manifest["criteria"], location="manifest.criteria")
        if isinstance(record, dict) and record["domainIdentifier"] == "unix"
    ]
    source_registry = load_yaml(repository / "data/source-registry.yaml")
    source = as_mapping(
        as_sequence(source_registry["documents"], location="documents")[0], location="source"
    )
    environment = _template_environment(repository / SITE_TEMPLATE_DIRECTORY)
    edition_root = output_root / "site" / "revised"
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    paths: list[Path] = []
    dataset_records: list[JsonValue] = []
    search_records: list[JsonValue] = []

    def write(relative: str, body: str | bytes) -> None:
        path = edition_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
        paths.append(path)

    for index, (document, record) in enumerate(zip(documents, records, strict=True)):
        normalized = _normalized_revision(document, repository, taxonomy)
        write(
            f"unix/{document.path.stem}/index.html",
            _detail_page(
                environment=environment,
                normalized=normalized,
                previous_record=records[index - 1] if index else None,
                next_record=records[index + 1] if index + 1 < len(records) else None,
                domains=domains,
                categories=categories,
                targets=targets,
                source_document=source,
                license_label=LICENSE_LABEL.removeprefix("라이선스: "),
                base_path=base_path,
            ),
        )
        record_data: dict[str, JsonValue] = {
            **document.metadata,
            "route": base + str(record["route"]),
            "originalRoute": f"{base}/unix/{document.path.stem}/",
            "body": document.body,
        }
        dataset_records.append(record_data)
        write(f"dataset/criteria/unix/{document.path.stem}.json", rfc8785.dumps(record_data))
        search_record = _search_record(
            manifest_record=record,
            normalized_document=normalized,
            taxonomy=taxonomy,
            record_order=index + 1,
        )
        search_record["targetLabels"] = [targets[platform] for platform in PLATFORMS]
        search_records.append(search_record)

    def page(
        template: str, title: str, context: dict[str, object], *, scripts: tuple[str, ...] = ()
    ) -> str:
        return _render_page(
            environment=environment,
            template_name=template,
            title=title,
            description=title,
            base_path=base_path,
            domains=domains,
            current_domain="unix",
            edition_prefix="/revised",
            current_navigation="search" if template == "pages/search.html" else "domains",
            domain_navigation_current=template != "pages/search.html",
            license_label=LICENSE_LABEL.removeprefix("라이선스: "),
            extra_scripts=scripts,
            page_context=context,
        )

    sections: list[dict[str, object]] = []
    for (domain_identifier, category_identifier), category in categories.items():
        if domain_identifier != "unix":
            continue
        selected = [
            record for record in records if record["categoryIdentifier"] == category_identifier
        ]
        views = _criterion_list_view(selected, base_path=base_path)
        label = str(category["label"])
        sections.append(
            {"label": label, "records": views, "url": f"{base}/revised/unix/{category_identifier}/"}
        )
        write(
            f"unix/{category_identifier}/index.html",
            page(
                "pages/listing.html",
                f"UNIX · {label}",
                {"heading": f"UNIX · {label}", "records": views, "sections": []},
            ),
        )
    listing = page(
        "pages/listing.html",
        "UNIX · Linux 개정판",
        {"heading": "UNIX · Linux 개정판", "records": [], "sections": sections},
    )
    write("index.html", listing)
    write("unix/index.html", listing)
    write(
        "search/index.html",
        page(
            "pages/search.html",
            "검색 · Linux 개정판",
            {
                "normalized_base_path": base,
                "records": _criterion_list_view(records, base_path=base_path),
                "search_index_url": f"{base}/revised/dataset/search-index.json",
            },
            scripts=("/assets/search-core.js", "/assets/search.js"),
        ),
    )
    write(
        "dataset/search-index.json",
        rfc8785.dumps(
            {
                "schemaVersion": 2,
                "tokenizerVersion": "unicode-nfc-korean-sections-v2",
                "caseFoldingVersion": "unicode-default-v1",
                "canonicalCorpusChecksum": hashlib.sha256(
                    rfc8785.dumps(dataset_records)
                ).hexdigest(),
                "records": search_records,
            }
        ),
    )
    dataset: dict[str, JsonValue] = {
        "schemaVersion": 2,
        "edition": "revised",
        "records": dataset_records,
    }
    validate_revised_dataset(dataset, repository)
    write("dataset.json", rfc8785.dumps(dataset))
    return paths
