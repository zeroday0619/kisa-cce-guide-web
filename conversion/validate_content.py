"""Validate canonical content and registry consistency."""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import Executor
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from markdown_it.token import Token
from PIL import Image, UnidentifiedImageError

from conversion.common import (
    ALLOWED_CODE_CONTENT_TYPES,
    ALLOWED_NOTE_LABELS,
    CANONICAL_FORMAT_CONTENT_MODELS,
    EXTRACTED_LEVEL_TWO_HEADINGS,
    REQUIRED_ASSESSMENT_LEVEL_THREE_HEADINGS,
    REQUIRED_LEVEL_TWO_HEADINGS,
    REQUIRED_OVERVIEW_LEVEL_THREE_HEADINGS,
    SUPPLEMENTARY_GUIDANCE_HEADING,
    JsonValue,
    LeafBlock,
    as_mapping,
    as_sequence,
    canonical_corpus_checksum,
    criterion_source_checksum,
    extract_leaf_blocks,
    flatten_block_references,
    heading_identifiers,
    is_nfc,
    load_criterion,
    load_json,
    load_yaml,
    markdown_parser,
    provenance_by_reference,
    region_source_checksum,
    repository_root,
    sha256_file,
)
from conversion.parallel import (
    default_worker_count,
    parse_worker_count,
    process_executor,
    validate_worker_count,
)
from conversion.paths import (
    BUILD_DIRECTORY,
    CRITERION_ASSET_REFERENCE_DIRECTORY,
    canonical_asset_directory,
    criterion_directory,
)
from conversion.runtime_logging import add_logging_arguments, configure_runtime_logging

SCHEMA_BINDINGS = {
    "data/source-registry.yaml": "schemas/source-registry.schema.json",
    "data/taxonomy.yaml": "schemas/taxonomy.schema.json",
    "data/criteria-manifest.yaml": "schemas/criteria-manifest.schema.json",
    "data/page-region-inventory.yaml": "schemas/page-region-inventory.schema.json",
    "data/review-registry.yaml": "schemas/review-registry.schema.json",
    "data/source-annotations.yaml": "schemas/source-annotations.schema.json",
    "data/policy-exceptions.yaml": "schemas/policy-exceptions.schema.json",
    "data/test-profile.yaml": "schemas/test-profile.schema.json",
}
EXPECTED_CRITERION_COUNT = 382
EXPECTED_PHYSICAL_PAGE_COUNT = 873
NUMERIC_CODE_COUNTS = {
    "U": 67,
    "W": 64,
    "WEB": 26,
    "S": 23,
    "N": 38,
    "C": 51,
    "PC": 18,
    "D": 26,
    "M": 4,
    "HV": 25,
    "CA": 19,
}
WEB_APPLICATION_CODES = frozenset(
    {
        "CI",
        "SI",
        "DI",
        "EP",
        "IL",
        "XS",
        "CF",
        "SF",
        "BF",
        "IA",
        "IN",
        "PR",
        "PV",
        "FU",
        "FD",
        "IS",
        "SN",
        "CC",
        "AE",
        "AU",
        "WM",
    }
)
# The canonical exemplar content/criteria/unix/u-01.md writes judgment items with the colon inside
# the strong
# span and exactly one space after it, so the validator matches that literal notation instead
# of accepting any emphasis arrangement.
JUDGMENT_ITEM_PATTERN = re.compile(r"^\*\*(양호|취약):\*\* (?=\S)")
NOTE_LABEL_PATTERN = re.compile(r"^\*\*([^*]+)\*\*$")
HEADING_TOKEN_SPAN = 3
EXPECTED_JUDGMENT_LABELS = ("양호", "취약")


@dataclass(frozen=True)
class ValidationIssue:
    """A stable validation failure record."""

    rule_identifier: str
    location: str
    message: str


@dataclass(frozen=True)
class _AnnotationOccurrence:
    """Record where a criterion introduced one annotation identifier."""

    identifier: str
    issue_offset: int
    location: str


@dataclass(frozen=True)
class _CriterionValidationResult:
    """Contain one criterion's independent validation result."""

    issues: tuple[ValidationIssue, ...]
    annotation_occurrences: tuple[_AnnotationOccurrence, ...]


@dataclass(frozen=True)
class _CriterionValidationJob:
    """Contain shared context and an ordered criterion batch for one worker."""

    root: Path
    manifest_records: tuple[dict[str, JsonValue], ...]
    criterion_schema: dict[str, JsonValue]
    provenance_schema: dict[str, JsonValue]
    taxonomy: dict[str, JsonValue]
    page_regions: dict[str, dict[str, JsonValue]]
    source_document_identifiers: set[str]
    review_records: dict[tuple[str, str], dict[str, JsonValue]]
    release: bool


def _numeric_bounding_box(value: JsonValue) -> tuple[float, float, float, float] | None:
    """Narrow a JSON array to four numeric coordinates."""

    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(coordinate, int | float) for coordinate in value):
        return None
    numeric_coordinates = cast("list[int | float]", value)
    x_minimum, y_minimum, x_maximum, y_maximum = numeric_coordinates
    return (
        float(x_minimum),
        float(y_minimum),
        float(x_maximum),
        float(y_maximum),
    )


def _json_path(parts: list[object]) -> str:
    """Render a JSON Schema error path."""

    if not parts:
        return "$"
    return "$." + ".".join(str(part) for part in parts)


def _schema_issues(
    *,
    document: dict[str, JsonValue],
    schema: dict[str, JsonValue],
    location: str,
    rule_identifier: str,
) -> list[ValidationIssue]:
    """Validate a document against a Draft 2020-12 schema."""

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        ValidationIssue(
            rule_identifier=rule_identifier,
            location=f"{location}:{_json_path(list(error.absolute_path))}",
            message=error.message,
        )
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    ]


def _load_and_validate_bound_documents(
    root: Path,
) -> tuple[
    dict[str, dict[str, JsonValue]],
    list[ValidationIssue],
]:
    """Load canonical registry documents and validate their schemas."""

    documents: dict[str, dict[str, JsonValue]] = {}
    issues: list[ValidationIssue] = []
    for document_path_text, schema_path_text in SCHEMA_BINDINGS.items():
        document_path = root / document_path_text
        schema_path = root / schema_path_text
        try:
            document = load_yaml(document_path)
            schema = load_json(schema_path)
            Draft202012Validator.check_schema(schema)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            issues.append(
                ValidationIssue(
                    rule_identifier="registry-load",
                    location=document_path_text,
                    message=str(error),
                )
            )
            continue
        documents[document_path_text] = document
        issues.extend(
            _schema_issues(
                document=document,
                schema=schema,
                location=document_path_text,
                rule_identifier="registry-schema",
            )
        )
    return documents, issues


def _validate_all_schema_documents(root: Path) -> list[ValidationIssue]:
    """Check every schema file, including schemas not bound to a source document."""

    issues: list[ValidationIssue] = []
    for schema_path in sorted((root / "schemas").glob("*.json")):
        try:
            Draft202012Validator.check_schema(load_json(schema_path))
        except (ValueError, json.JSONDecodeError) as error:
            issues.append(
                ValidationIssue(
                    rule_identifier="schema-document",
                    location=str(schema_path.relative_to(root)),
                    message=str(error),
                )
            )
    return issues


def _validate_source_document(
    root: Path,
    source_registry: dict[str, JsonValue],
) -> list[ValidationIssue]:
    """Verify the immutable source PDF byte properties."""

    issues: list[ValidationIssue] = []
    documents = as_sequence(source_registry["documents"], location="sourceRegistry.documents")
    for document_value in documents:
        document = as_mapping(document_value, location="sourceRegistry.documents[]")
        repository_path = document.get("repositoryPath")
        expected_checksum = document.get("checksumValue")
        expected_size = document.get("fileSizeBytes")
        if not isinstance(repository_path, str):
            continue
        source_path = root / repository_path
        if not source_path.is_file():
            issues.append(
                ValidationIssue("source-file", repository_path, "source document is missing")
            )
            continue
        if isinstance(expected_checksum, str) and sha256_file(source_path) != expected_checksum:
            issues.append(
                ValidationIssue("source-checksum", repository_path, "SHA-256 checksum mismatch")
            )
        if isinstance(expected_size, int) and source_path.stat().st_size != expected_size:
            issues.append(ValidationIssue("source-size", repository_path, "file size mismatch"))
    return issues


def _validate_derived_inventory(
    *,
    root: Path,
    manifest: dict[str, JsonValue],
    taxonomy: dict[str, JsonValue],
) -> list[ValidationIssue]:
    """Validate the machine-derived inventory used to regenerate the corpus."""

    issues: list[ValidationIssue] = []
    try:
        inventory = load_json(root / "data/derived/authoritative-inventory.json")
        authoritative_taxonomy = load_json(root / "data/derived/authoritative-taxonomy.json")
        anomaly_inventory = load_json(root / "data/derived/inventory-anomalies.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [
            ValidationIssue(
                "derived-inventory-load",
                "data/derived",
                str(error),
            )
        ]

    inventory_values = as_sequence(
        inventory.get("criteria"),
        location="authoritativeInventory.criteria",
    )
    manifest_values = as_sequence(manifest["criteria"], location="manifest.criteria")
    if (
        inventory.get("completionStatus") != "complete"
        or inventory.get("expectedCriterionCount") != EXPECTED_CRITERION_COUNT
        or inventory.get("registeredCriterionCount") != len(inventory_values)
        or inventory.get("sourcePhysicalPageCount") != EXPECTED_PHYSICAL_PAGE_COUNT
        or len(inventory_values) != EXPECTED_CRITERION_COUNT
    ):
        issues.append(
            ValidationIssue(
                "derived-inventory-count",
                "data/derived/authoritative-inventory.json",
                "authoritative inventory counts are incomplete",
            )
        )
    comparable_fields = (
        "code",
        "slug",
        "title",
        "severityLevel",
        "severitySourceLabel",
        "domainIdentifier",
        "categoryIdentifier",
        "sourceStartRegionIdentifier",
        "sourceEndRegionIdentifier",
        "route",
    )
    for inventory_value, manifest_value in zip(
        inventory_values,
        manifest_values,
        strict=False,
    ):
        inventory_record = as_mapping(
            inventory_value,
            location="authoritativeInventory.criteria[]",
        )
        manifest_record = as_mapping(manifest_value, location="manifest.criteria[]")
        if any(
            inventory_record.get(field_name) != manifest_record.get(field_name)
            for field_name in comparable_fields
        ):
            issues.append(
                ValidationIssue(
                    "derived-inventory-manifest",
                    "data/derived/authoritative-inventory.json",
                    f"inventory and manifest differ for {inventory_record.get('code')}",
                )
            )

    for collection_name in ("domains", "categories"):
        authoritative_values = as_sequence(
            authoritative_taxonomy.get(collection_name),
            location=f"authoritativeTaxonomy.{collection_name}",
        )
        canonical_values = as_sequence(
            taxonomy[collection_name],
            location=f"taxonomy.{collection_name}",
        )
        identifier_fields = (
            ("domainIdentifier", "identifier")
            if collection_name == "categories"
            else ("identifier",)
        )
        authoritative_keys = {
            tuple(record.get(field_name) for field_name in identifier_fields)
            for value in authoritative_values
            if isinstance(value, dict) and (record := value)
        }
        canonical_keys = {
            tuple(record.get(field_name) for field_name in identifier_fields)
            for value in canonical_values
            if isinstance(value, dict) and (record := value)
        }
        if authoritative_keys != canonical_keys:
            issues.append(
                ValidationIssue(
                    "derived-inventory-taxonomy",
                    "data/derived/authoritative-taxonomy.json",
                    f"authoritative and canonical {collection_name} differ",
                )
            )

    anomaly_values = as_sequence(
        anomaly_inventory.get("anomalies"),
        location="inventoryAnomalies.anomalies",
    )
    validation = as_mapping(
        inventory.get("validation"),
        location="authoritativeInventory.validation",
    )
    if validation.get("anomalyRecordCount") != len(anomaly_values):
        issues.append(
            ValidationIssue(
                "derived-inventory-anomalies",
                "data/derived/inventory-anomalies.json",
                "anomaly count differs from authoritative inventory validation",
            )
        )
    return issues


def _validate_release_generated_outputs(
    *,
    repository: Path,
    expected_codes: set[str],
    criteria_values: list[JsonValue],
    test_profile_version: JsonValue,
    workers: int,
    executor: Executor | None,
) -> list[ValidationIssue]:
    """Run two clean builds and require searchable, semantic, and QA artifacts."""

    # The lazy import avoids a module cycle during ordinary corpus builds.
    from conversion.build_content import _build_validated  # noqa: PLC0415

    issues: list[ValidationIssue] = []
    with TemporaryDirectory() as first_directory, TemporaryDirectory() as second_directory:
        first_root = Path(first_directory)
        second_root = Path(second_directory)
        first_paths = _build_validated(
            root=repository,
            output_root=first_root,
            workers=workers,
            executor=executor,
        )
        second_paths = _build_validated(
            root=repository,
            output_root=second_root,
            workers=workers,
            executor=executor,
        )
        first_outputs = {path.relative_to(first_root): path.read_bytes() for path in first_paths}
        second_outputs = {path.relative_to(second_root): path.read_bytes() for path in second_paths}
        if first_outputs != second_outputs:
            issues.append(
                ValidationIssue(
                    "release-deterministic-build",
                    BUILD_DIRECTORY.as_posix(),
                    "two clean builds produced different file sets or bytes",
                )
            )

        normalized_paths = sorted((first_root / "normalized").glob("*/*.json"))
        if len(normalized_paths) != len(expected_codes):
            issues.append(
                ValidationIssue(
                    "release-normalized-count",
                    (BUILD_DIRECTORY / "normalized").as_posix(),
                    "normalized criterion count differs from the exact code allowlist",
                )
            )
        search_path = first_root / "search" / "search-index.json"
        if not search_path.is_file():
            issues.append(
                ValidationIssue(
                    "release-search-index",
                    (BUILD_DIRECTORY / "search").as_posix(),
                    "search index is missing",
                )
            )
        else:
            search_index = json.loads(search_path.read_text(encoding="utf-8"))
            search_records = search_index.get("records", [])
            indexed_codes = {
                record.get("code") for record in search_records if isinstance(record, dict)
            }
            if indexed_codes != expected_codes:
                issues.append(
                    ValidationIssue(
                        "release-search-index",
                        (BUILD_DIRECTORY / "search/search-index.json").as_posix(),
                        "search index code set differs from the allowlist",
                    )
                )

        for criterion_value in criteria_values:
            criterion = as_mapping(criterion_value, location="manifest.criteria[]")
            route = criterion.get("route")
            if not isinstance(route, str):
                continue
            site_path = first_root / "site" / route.strip("/") / "index.html"
            if not site_path.is_file():
                issues.append(
                    ValidationIssue(
                        "release-semantic-html",
                        str(site_path.relative_to(first_root)),
                        "semantic HTML artifact is missing",
                    )
                )

        from conversion.site_validation import validate_site  # noqa: PLC0415

        site_issues = validate_site(
            site_root=first_root / "site",
            manifest={"criteria": criteria_values},
            expected_html_page_count=537,
            executor=executor,
        )
        issues.extend(
            ValidationIssue(
                rule_identifier="release-" + site_issue.rule_identifier,
                location=site_issue.location,
                message=site_issue.message,
            )
            for site_issue in site_issues
        )

        manifest_records = [
            as_mapping(value, location="manifest.criteria[]") for value in criteria_values
        ]
        expected_corpus_checksum = canonical_corpus_checksum(
            manifest_records,
            root=repository,
        )
        qa_report_schema = load_json(repository / "schemas/qa-report.schema.json")
        for report_name in ("accessibility", "responsive", "print"):
            report_path = repository / "data" / "qa-reports" / f"{report_name}.json"
            if not report_path.is_file():
                issues.append(
                    ValidationIssue(
                        f"release-{report_name}-report",
                        str(report_path.relative_to(repository)),
                        f"{report_name} report is missing",
                    )
                )
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            issues.extend(
                _schema_issues(
                    document=report,
                    schema=qa_report_schema,
                    location=str(report_path.relative_to(repository)),
                    rule_identifier=f"release-{report_name}-report",
                )
            )
            if report.get("reportType") != report_name or (
                report.get("passed") is not True
                or report.get("validationStatus") != "passed"
                or report.get("canonicalCorpusChecksum") != expected_corpus_checksum
                or report.get("testProfileVersion") != test_profile_version
            ):
                issues.append(
                    ValidationIssue(
                        f"release-{report_name}-report",
                        str(report_path.relative_to(repository)),
                        f"{report_name} QA report is incomplete or stale",
                    )
                )
    return issues


def _taxonomy_identifiers(
    taxonomy: dict[str, JsonValue],
    collection_name: str,
) -> set[str]:
    """Return registered identifiers from one taxonomy collection."""

    collection = as_sequence(taxonomy[collection_name], location=f"taxonomy.{collection_name}")
    identifiers: set[str] = set()
    for item_value in collection:
        item = as_mapping(item_value, location=f"taxonomy.{collection_name}[]")
        identifier = item.get("identifier")
        if isinstance(identifier, str):
            identifiers.add(identifier)
    return identifiers


def _valid_system_semantic_path(
    semantic_path: tuple[str, ...],
    *,
    taxonomy: dict[str, JsonValue],
) -> bool:
    """Return whether a path is allowed by the systemCriterion content model."""

    if not semantic_path:
        return False
    if semantic_path[0] == "overview":
        return len(semantic_path) == 1 or (
            len(semantic_path) == 2
            and semantic_path[1]
            in {"inspectionContent", "inspectionPurpose", "securityThreat", "reference"}
        )
    if semantic_path[0] == "assessment":
        return len(semantic_path) == 1 or (
            len(semantic_path) == 2
            and semantic_path[1] in {"target", "judgment", "remediationMethod", "remediationImpact"}
        )
    if semantic_path[0] != "remediation":
        return False
    if len(semantic_path) == 1:
        return True
    if semantic_path[1] == "supplementaryGuidance":
        return len(semantic_path) == 2 or (
            len(semantic_path) == 3
            and semantic_path[2] in {"inappropriatePasswordTypes", "passwordManagementMethods"}
        )
    product_family_identifiers = _taxonomy_identifiers(taxonomy, "productFamilies")
    target_identifiers = _taxonomy_identifiers(taxonomy, "targets") | product_family_identifiers
    if semantic_path[1] not in target_identifiers:
        return False
    if len(semantic_path) == 2:
        return True
    detail_identifiers = _taxonomy_identifiers(taxonomy, "protocols") | product_family_identifiers
    return len(semantic_path) == 3 and semantic_path[2] in detail_identifiers


def _validate_assets(
    *,
    root: Path,
    criterion_directory: Path,
    slug: str,
    relative_criterion_path: str,
    provenance: dict[str, JsonValue],
    leaf_blocks: list[LeafBlock],
    release: bool,
) -> list[ValidationIssue]:
    """Validate image references, canonical files, and asset provenance."""

    issues: list[ValidationIssue] = []
    image_blocks = [block for block in leaf_blocks if getattr(block, "block_type", None) == "image"]
    image_paths = [
        getattr(block, "asset_path", None)
        for block in image_blocks
        if isinstance(getattr(block, "asset_path", None), str)
    ]
    asset_values = as_sequence(
        provenance.get("assets"),
        location=f"{relative_criterion_path}.provenance.assets",
    )
    assets = [
        as_mapping(asset_value, location=f"{relative_criterion_path}.provenance.assets[]")
        for asset_value in asset_values
    ]
    asset_paths = [asset.get("path") for asset in assets if isinstance(asset.get("path"), str)]
    if image_paths != asset_paths:
        issues.append(
            ValidationIssue(
                "asset-reference-match",
                relative_criterion_path,
                "Markdown image paths and provenance asset paths differ",
            )
        )
    if len(asset_paths) != len(set(asset_paths)):
        issues.append(
            ValidationIssue(
                "asset-path-unique",
                relative_criterion_path,
                "provenance contains duplicate asset paths",
            )
        )

    expected_asset_directory = canonical_asset_directory(root, slug).resolve()
    expected_asset_prefix = (*CRITERION_ASSET_REFERENCE_DIRECTORY.parts, slug)
    image_blocks_by_path = {
        block.asset_path: block for block in image_blocks if isinstance(block.asset_path, str)
    }
    for asset in assets:
        declared_path = asset.get("path")
        if not isinstance(declared_path, str):
            continue
        declared_parts = Path(declared_path).parts
        if (
            Path(declared_path).is_absolute()
            or "\\" in declared_path
            or len(declared_parts) < len(expected_asset_prefix) + 1
            or declared_parts[: len(expected_asset_prefix)] != expected_asset_prefix
        ):
            issues.append(
                ValidationIssue(
                    "asset-path-format",
                    relative_criterion_path,
                    "asset path must be "
                    f"{CRITERION_ASSET_REFERENCE_DIRECTORY.as_posix()}/"
                    f"{slug}/<filename>: {declared_path}",
                )
            )
            continue
        resolved_path = (criterion_directory / declared_path).resolve()
        if not resolved_path.is_relative_to(expected_asset_directory):
            issues.append(
                ValidationIssue(
                    "asset-path-boundary",
                    relative_criterion_path,
                    f"asset path leaves assets/{slug}: {declared_path}",
                )
            )
            continue
        if not resolved_path.is_file():
            issues.append(
                ValidationIssue(
                    "asset-file",
                    relative_criterion_path,
                    f"asset file is missing: {declared_path}",
                )
            )
            continue
        expected_checksum = asset.get("checksumValue")
        if isinstance(expected_checksum, str) and sha256_file(resolved_path) != expected_checksum:
            issues.append(
                ValidationIssue(
                    "asset-checksum",
                    relative_criterion_path,
                    f"asset checksum differs: {declared_path}",
                )
            )
        try:
            with Image.open(resolved_path) as image:
                image_dimensions = list(image.size)
                image_format = image.format
                image_mode = image.mode
        except (OSError, UnidentifiedImageError) as error:
            issues.append(
                ValidationIssue(
                    "asset-image",
                    relative_criterion_path,
                    f"asset cannot be decoded: {declared_path}: {error}",
                )
            )
            continue
        if image_format != "PNG" or image_mode != "P":
            issues.append(
                ValidationIssue(
                    "asset-image-profile",
                    relative_criterion_path,
                    f"asset must use indexed PNG profile: {declared_path}",
                )
            )
        if image_dimensions != asset.get("outputPixelDimensions"):
            issues.append(
                ValidationIssue(
                    "asset-dimensions",
                    relative_criterion_path,
                    f"asset dimensions differ: {declared_path}",
                )
            )
        if asset.get("assetType") == "sourcePageCrop" and image_dimensions != asset.get(
            "originalPixelDimensions"
        ):
            issues.append(
                ValidationIssue(
                    "asset-original-dimensions",
                    relative_criterion_path,
                    f"source crop dimensions differ: {declared_path}",
                )
            )
        block = image_blocks_by_path.get(declared_path)
        if block is not None and (
            getattr(block, "alternative_text", None) != asset.get("alternativeText")
            or getattr(block, "alternative_text_status", None) != asset.get("alternativeTextStatus")
        ):
            issues.append(
                ValidationIssue(
                    "asset-alternative-text",
                    relative_criterion_path,
                    f"Markdown and provenance alternative text differ: {declared_path}",
                )
            )
        source_spans = as_sequence(
            asset.get("sourceSpans"),
            location=f"{relative_criterion_path}.asset.sourceSpans",
        )
        if (
            not source_spans
            or not isinstance(source_spans[0], dict)
            or source_spans[0].get("sourceBoundingBox") != asset.get("sourceCrop")
        ):
            issues.append(
                ValidationIssue(
                    "asset-source-crop",
                    relative_criterion_path,
                    f"asset crop and source span differ: {declared_path}",
                )
            )
        if release and asset.get("alternativeTextStatus") != "reviewed":
            issues.append(
                ValidationIssue(
                    "release-image-alternative-text",
                    relative_criterion_path,
                    f"asset alternative text is not reviewed: {declared_path}",
                )
            )
    registered_files = {
        (criterion_directory / declared_path).resolve()
        for declared_path in asset_paths
        if isinstance(declared_path, str)
    }
    if expected_asset_directory.is_dir():
        actual_files = {
            path.resolve() for path in expected_asset_directory.rglob("*") if path.is_file()
        }
        if actual_files != registered_files:
            issues.append(
                ValidationIssue(
                    "asset-file-coverage",
                    relative_criterion_path,
                    "canonical asset files and provenance records differ",
                )
            )
    return issues


def _level_three_headings_by_section(tokens: list[Token]) -> dict[str, list[str]]:
    """Group H3 headings under the H2 section that introduces them."""

    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    for token_index, token in enumerate(tokens):
        if token.type != "heading_open" or token_index + 1 >= len(tokens):
            continue
        heading_text = tokens[token_index + 1].content
        if token.tag == "h2":
            current_section = heading_text
            sections.setdefault(current_section, [])
        elif token.tag == "h3" and current_section is not None:
            sections[current_section].append(heading_text)
    return sections


def _section_token_span(tokens: list[Token], *, level: int, heading_text: str) -> list[Token]:
    """Return the tokens that belong to one heading section, excluding its heading."""

    for token_index, token in enumerate(tokens):
        if (
            token.type != "heading_open"
            or token.tag != f"h{level}"
            or token_index + 1 >= len(tokens)
            or tokens[token_index + 1].content != heading_text
        ):
            continue
        start_index = token_index + HEADING_TOKEN_SPAN
        for end_index in range(start_index, len(tokens)):
            candidate = tokens[end_index]
            if candidate.type == "heading_open" and int(candidate.tag[1:]) <= level:
                return tokens[start_index:end_index]
        return tokens[start_index:]
    return []


def _validate_judgment_section(
    tokens: list[Token],
    *,
    relative_path: str,
) -> list[ValidationIssue]:
    """Validate that the judgment section uses one 양호 item and one 취약 item."""

    section_tokens = _section_token_span(tokens, level=3, heading_text="판단 기준")
    if not section_tokens:
        return []
    issues: list[ValidationIssue] = []
    if not any(token.type == "bullet_list_open" for token in section_tokens):
        issues.append(
            ValidationIssue(
                "markdown-judgment-notation",
                relative_path,
                "판단 기준 requires an unordered list of judgment items",
            )
        )
        return issues
    if any(token.type == "ordered_list_open" for token in section_tokens):
        issues.append(
            ValidationIssue(
                "markdown-judgment-notation",
                relative_path,
                "판단 기준 must not use an ordered list",
            )
        )
    observed_labels: list[str] = []
    for token_index, token in enumerate(section_tokens):
        if token.type != "list_item_open":
            continue
        inline_token = next(
            (candidate for candidate in section_tokens[token_index:] if candidate.type == "inline"),
            None,
        )
        if inline_token is None:
            continue
        label_match = JUDGMENT_ITEM_PATTERN.match(inline_token.content)
        if label_match is None:
            issues.append(
                ValidationIssue(
                    "markdown-judgment-notation",
                    relative_path,
                    (
                        "판단 기준 item must use '- **양호:** ' or '- **취약:** ' notation: "
                        f"{inline_token.content[:40]!r}"
                    ),
                )
            )
            continue
        observed_labels.append(label_match.group(1))
    if tuple(observed_labels) != EXPECTED_JUDGMENT_LABELS:
        issues.append(
            ValidationIssue(
                "markdown-judgment-notation",
                relative_path,
                (
                    "판단 기준 requires exactly one 양호 item followed by one 취약 item: "
                    f"{tuple(observed_labels)!r}"
                ),
            )
        )
    return issues


def _validate_note_blockquotes(
    tokens: list[Token],
    *,
    body_lines: list[str],
    relative_path: str,
) -> list[ValidationIssue]:
    """Validate the label line, blank quote line, and allowed labels of note blockquotes."""

    issues: list[ValidationIssue] = []
    for token_index, token in enumerate(tokens):
        if token.type != "blockquote_open":
            continue
        label_token = next(
            (candidate for candidate in tokens[token_index:] if candidate.type == "inline"),
            None,
        )
        if label_token is None:
            continue
        label_match = NOTE_LABEL_PATTERN.match(label_token.content)
        if label_match is None:
            issues.append(
                ValidationIssue(
                    "markdown-note-profile",
                    relative_path,
                    (
                        "note blockquote must open with a strong label line: "
                        f"{label_token.content[:40]!r}"
                    ),
                )
            )
            continue
        label = label_match.group(1)
        if label not in ALLOWED_NOTE_LABELS:
            issues.append(
                ValidationIssue(
                    "markdown-note-profile",
                    relative_path,
                    f"unsupported note label: {label!r}",
                )
            )
        # The contract requires a bare quote marker between the label and the note body so
        # that the label renders as its own block rather than merging into the first sentence.
        if label_token.map is None:
            continue
        separator_index = label_token.map[0] + 1
        separator_line = body_lines[separator_index] if separator_index < len(body_lines) else ""
        if separator_line != ">":
            issues.append(
                ValidationIssue(
                    "markdown-note-profile",
                    relative_path,
                    (
                        "note blockquote label must be followed by a bare '>' line: "
                        f"{separator_line[:40]!r}"
                    ),
                )
            )
    return issues


def _validate_canonical_format_headings(
    tokens: list[Token],
    *,
    relative_path: str,
) -> list[ValidationIssue]:
    """Validate the fixed heading composition of the canonical format contract."""

    issues: list[ValidationIssue] = []
    sections = _level_three_headings_by_section(tokens)
    expected_level_three_headings = {
        "개요": REQUIRED_OVERVIEW_LEVEL_THREE_HEADINGS,
        "점검 대상 및 판단 기준": REQUIRED_ASSESSMENT_LEVEL_THREE_HEADINGS,
    }
    for section_name, expected_headings in expected_level_three_headings.items():
        observed_headings = tuple(sections.get(section_name, ()))
        if observed_headings != expected_headings:
            issues.append(
                ValidationIssue(
                    "markdown-section-headings",
                    relative_path,
                    (
                        f"{section_name} requires H3 sequence {expected_headings!r} "
                        f"but found {observed_headings!r}"
                    ),
                )
            )
    remediation_headings = tuple(sections.get("점검 및 조치 사례", ()))
    if not remediation_headings:
        issues.append(
            ValidationIssue(
                "markdown-section-headings",
                relative_path,
                "점검 및 조치 사례 requires at least one target H3",
            )
        )
    elif (
        SUPPLEMENTARY_GUIDANCE_HEADING in remediation_headings
        and remediation_headings[-1] != SUPPLEMENTARY_GUIDANCE_HEADING
    ):
        issues.append(
            ValidationIssue(
                "markdown-section-headings",
                relative_path,
                (
                    f"{SUPPLEMENTARY_GUIDANCE_HEADING} must be the last H3 under "
                    f"점검 및 조치 사례: {remediation_headings!r}"
                ),
            )
        )
    return issues


def _validate_markdown_structure(
    *,
    criterion_path: Path,
    body: str,
    content_model: str,
) -> list[ValidationIssue]:
    """Validate constrained Markdown structure and fenced code profiles."""

    issues: list[ValidationIssue] = []
    relative_path = criterion_path.as_posix()
    if "\ufffd" in body:
        issues.append(
            ValidationIssue("markdown-replacement-character", relative_path, "contains U+FFFD")
        )
    if not is_nfc(body):
        issues.append(ValidationIssue("markdown-nfc", relative_path, "body is not Unicode NFC"))
    if content_model != "extractedCriterion" and re.search(
        r"^Step\s+[0-9]+\)",
        body,
        flags=re.MULTILINE,
    ):
        issues.append(
            ValidationIssue(
                "markdown-step-paragraph",
                relative_path,
                "contains a Step number outside an ordered list",
            )
        )

    tokens = markdown_parser().parse(body)
    level_two_headings: list[str] = []
    heading_levels: list[int] = []
    for token_index, token in enumerate(tokens):
        if token.type == "heading_open":
            heading_level = int(token.tag[1:])
            heading_levels.append(heading_level)
            if heading_level == 1:
                issues.append(
                    ValidationIssue(
                        "markdown-source-h1",
                        relative_path,
                        "source Markdown must not contain H1",
                    )
                )
            if heading_level == 2 and token_index + 1 < len(tokens):
                level_two_headings.append(tokens[token_index + 1].content)
        if token.type == "fence":
            information_parts = token.info.split()
            if len(information_parts) != 2:
                issues.append(
                    ValidationIssue(
                        "markdown-fence-profile",
                        relative_path,
                        f"fence must have language and content type: {token.info!r}",
                    )
                )
            elif information_parts[1] not in ALLOWED_CODE_CONTENT_TYPES:
                issues.append(
                    ValidationIssue(
                        "markdown-fence-profile",
                        relative_path,
                        f"unsupported fence content type: {information_parts[1]}",
                    )
                )

    # extractedCriterion is an intermediate transcription state rather than a completed
    # conversion, so it keeps its own reduced heading contract.
    expected_level_two_headings = (
        EXTRACTED_LEVEL_TWO_HEADINGS
        if content_model == "extractedCriterion"
        else REQUIRED_LEVEL_TWO_HEADINGS
    )
    if tuple(level_two_headings) != expected_level_two_headings:
        issues.append(
            ValidationIssue(
                "markdown-required-headings",
                relative_path,
                (
                    f"expected H2 sequence {expected_level_two_headings!r} "
                    f"but found {tuple(level_two_headings)!r}"
                ),
            )
        )
    if content_model in CANONICAL_FORMAT_CONTENT_MODELS:
        issues.extend(_validate_canonical_format_headings(tokens, relative_path=relative_path))
        issues.extend(_validate_judgment_section(tokens, relative_path=relative_path))
        issues.extend(
            _validate_note_blockquotes(
                tokens,
                body_lines=body.splitlines(),
                relative_path=relative_path,
            )
        )
    for previous_level, current_level in pairwise(heading_levels):
        if current_level > previous_level + 1:
            issues.append(
                ValidationIssue(
                    "markdown-heading-level",
                    relative_path,
                    f"heading level jumps from H{previous_level} to H{current_level}",
                )
            )
    return issues


def _validate_criterion(
    *,
    root: Path,
    manifest_record: dict[str, JsonValue],
    criterion_schema: dict[str, JsonValue],
    provenance_schema: dict[str, JsonValue],
    taxonomy: dict[str, JsonValue],
    page_regions: dict[str, dict[str, JsonValue]],
    source_document_identifiers: set[str],
    review_records: dict[tuple[str, str], dict[str, JsonValue]],
    release: bool,
) -> _CriterionValidationResult:
    """Validate one criterion package and its cross-registry references."""

    issues: list[ValidationIssue] = []
    annotation_occurrences: list[_AnnotationOccurrence] = []
    slug = manifest_record.get("slug")
    code = manifest_record.get("code")
    if not isinstance(slug, str) or not isinstance(code, str):
        return _CriterionValidationResult(
            issues=(ValidationIssue("criterion-manifest", "criteria[]", "missing code or slug"),),
            annotation_occurrences=(),
        )

    domain_identifier = manifest_record.get("domainIdentifier")
    if not isinstance(domain_identifier, str):
        return _CriterionValidationResult(
            issues=(
                ValidationIssue(
                    "criterion-manifest",
                    "criteria[]",
                    "missing domainIdentifier",
                ),
            ),
            annotation_occurrences=(),
        )
    criterion_source_directory = criterion_directory(root, domain_identifier)
    criterion_path = criterion_source_directory / f"{slug}.md"
    provenance_path = criterion_source_directory / f"{slug}.provenance.yaml"
    relative_criterion_path = criterion_path.relative_to(root).as_posix()
    try:
        criterion = load_criterion(criterion_path)
        provenance = load_yaml(provenance_path)
    except (OSError, ValueError) as error:
        return _CriterionValidationResult(
            issues=(ValidationIssue("criterion-load", relative_criterion_path, str(error)),),
            annotation_occurrences=(),
        )

    issues.extend(
        _schema_issues(
            document=criterion.metadata,
            schema=criterion_schema,
            location=relative_criterion_path,
            rule_identifier="criterion-schema",
        )
    )
    issues.extend(
        _schema_issues(
            document=provenance,
            schema=provenance_schema,
            location=provenance_path.relative_to(root).as_posix(),
            rule_identifier="provenance-schema",
        )
    )
    content_model_value = criterion.metadata.get("contentModel")
    content_model = content_model_value if isinstance(content_model_value, str) else ""
    issues.extend(
        _validate_markdown_structure(
            criterion_path=Path(relative_criterion_path),
            body=criterion.body,
            content_model=content_model,
        )
    )

    criterion_metadata = as_mapping(
        criterion.metadata.get("criterion"),
        location=f"{relative_criterion_path}.criterion",
    )
    classification = as_mapping(
        criterion.metadata.get("classification"),
        location=f"{relative_criterion_path}.classification",
    )
    expected_pairs = {
        "code": criterion_metadata.get("code"),
        "slug": criterion_metadata.get("slug"),
        "title": criterion_metadata.get("title"),
        "contentModel": criterion.metadata.get("contentModel"),
        "contentModelVersion": criterion.metadata.get("contentModelVersion"),
        "domainIdentifier": classification.get("domainIdentifier"),
        "categoryIdentifier": classification.get("categoryIdentifier"),
    }
    for field_name, actual_value in expected_pairs.items():
        expected_value = manifest_record.get(field_name)
        if actual_value != expected_value:
            issues.append(
                ValidationIssue(
                    "criterion-manifest-match",
                    relative_criterion_path,
                    f"{field_name} differs from manifest",
                )
            )

    severity = as_mapping(
        criterion_metadata.get("severity"),
        location=f"{relative_criterion_path}.criterion.severity",
    )
    if severity.get("level") != manifest_record.get("severityLevel"):
        issues.append(
            ValidationIssue(
                "criterion-manifest-match",
                relative_criterion_path,
                "severity level differs from manifest",
            )
        )
    if severity.get("sourceLabel") != manifest_record.get("severitySourceLabel"):
        issues.append(
            ValidationIssue(
                "criterion-manifest-match",
                relative_criterion_path,
                "source severity differs from manifest",
            )
        )

    metadata_provenance = as_mapping(
        criterion.metadata.get("provenance"),
        location=f"{relative_criterion_path}.provenance",
    )
    source_document_identifier = metadata_provenance.get("sourceDocumentIdentifier")
    if source_document_identifier not in source_document_identifiers:
        issues.append(
            ValidationIssue(
                "criterion-source-document",
                relative_criterion_path,
                f"unknown source document {source_document_identifier!r}",
            )
        )
    source_page_range_values = as_sequence(
        metadata_provenance.get("sourcePageRanges"),
        location=f"{relative_criterion_path}.provenance.sourcePageRanges",
    )
    source_page_ranges: list[tuple[int, int]] = []
    for source_page_range_value in source_page_range_values:
        source_page_range = as_mapping(
            source_page_range_value,
            location=f"{relative_criterion_path}.provenance.sourcePageRanges[]",
        )
        start_page = source_page_range.get("physicalPageStart")
        end_page = source_page_range.get("physicalPageEnd")
        if isinstance(start_page, int) and isinstance(end_page, int):
            source_page_ranges.append((start_page, end_page))
            if start_page > end_page:
                issues.append(
                    ValidationIssue(
                        "criterion-source-range",
                        relative_criterion_path,
                        "source page range is reversed",
                    )
                )

    start_region_identifier = manifest_record.get("sourceStartRegionIdentifier")
    end_region_identifier = manifest_record.get("sourceEndRegionIdentifier")
    start_region = (
        page_regions.get(start_region_identifier)
        if isinstance(start_region_identifier, str)
        else None
    )
    end_region = (
        page_regions.get(end_region_identifier) if isinstance(end_region_identifier, str) else None
    )
    for boundary_name, boundary_region in (
        ("start", start_region),
        ("end", end_region),
    ):
        if (
            boundary_region is None
            or boundary_region.get("role") != "criterion"
            or boundary_region.get("ownerIdentifier") != slug
            or boundary_region.get("publicationDisposition") == "excluded"
        ):
            issues.append(
                ValidationIssue(
                    "criterion-source-boundary",
                    relative_criterion_path,
                    f"invalid {boundary_name} page region",
                )
            )
    if source_page_ranges and start_region is not None and end_region is not None:
        expected_start_page = min(start_page for start_page, _ in source_page_ranges)
        expected_end_page = max(end_page for _, end_page in source_page_ranges)
        if (
            start_region.get("physicalPage") != expected_start_page
            or end_region.get("physicalPage") != expected_end_page
        ):
            issues.append(
                ValidationIssue(
                    "criterion-source-boundary",
                    relative_criterion_path,
                    "manifest boundaries differ from metadata page ranges",
                )
            )

    if code.lower() != slug:
        issues.append(
            ValidationIssue(
                "criterion-code-slug",
                relative_criterion_path,
                f"slug {slug!r} is not lowercase code {code.lower()!r}",
            )
        )

    domain_identifiers = _taxonomy_identifiers(taxonomy, "domains")
    category_identifiers = _taxonomy_identifiers(taxonomy, "categories")
    target_identifiers = _taxonomy_identifiers(taxonomy, "targets")
    if classification.get("domainIdentifier") not in domain_identifiers:
        issues.append(
            ValidationIssue("criterion-taxonomy", relative_criterion_path, "unknown domain")
        )
    if classification.get("categoryIdentifier") not in category_identifiers:
        issues.append(
            ValidationIssue("criterion-taxonomy", relative_criterion_path, "unknown category")
        )
    for target_value in as_sequence(
        criterion.metadata.get("targetIdentifiers"),
        location=f"{relative_criterion_path}.targetIdentifiers",
    ):
        if target_value not in target_identifiers:
            issues.append(
                ValidationIssue(
                    "criterion-taxonomy",
                    relative_criterion_path,
                    f"unknown target {target_value!r}",
                )
            )

    try:
        leaf_blocks = extract_leaf_blocks(
            criterion.body,
            criterion_slug=slug,
            heading_identifier_mapping=heading_identifiers(taxonomy),
        )
    except ValueError as error:
        issues.append(
            ValidationIssue(
                "content-model-heading",
                relative_criterion_path,
                str(error),
            )
        )
        return _CriterionValidationResult(
            issues=tuple(issues),
            annotation_occurrences=tuple(annotation_occurrences),
        )
    references = flatten_block_references(provenance)
    if len(leaf_blocks) != len(references):
        issues.append(
            ValidationIssue(
                "provenance-leaf-coverage",
                relative_criterion_path,
                f"{len(leaf_blocks)} Markdown leaves but {len(references)} provenance references",
            )
        )
    if len(references) != len(set(references)):
        issues.append(
            ValidationIssue(
                "provenance-reference-unique",
                relative_criterion_path,
                "duplicate block reference",
            )
        )
    generated_references = [block.block_reference for block in leaf_blocks]
    if len(generated_references) == len(references) and generated_references != references:
        mismatch_index = next(
            index
            for index, (generated, declared) in enumerate(
                zip(generated_references, references, strict=True)
            )
            if generated != declared
        )
        issues.append(
            ValidationIssue(
                "provenance-reference-match",
                relative_criterion_path,
                (
                    f"block {mismatch_index} generated {generated_references[mismatch_index]!r} "
                    f"but sidecar declares {references[mismatch_index]!r}"
                ),
            )
        )
    issues.extend(
        _validate_assets(
            root=root,
            criterion_directory=criterion_source_directory,
            slug=slug,
            relative_criterion_path=relative_criterion_path,
            provenance=provenance,
            leaf_blocks=leaf_blocks,
            release=release,
        )
    )
    criterion_content_model = criterion.metadata.get("contentModel")
    if criterion_content_model in CANONICAL_FORMAT_CONTENT_MODELS:
        # Both canonical content models share the fixed overview, assessment, and remediation
        # semantic roles of the canonical format contract, so they share one path validator.
        for block in leaf_blocks:
            if not _valid_system_semantic_path(block.semantic_path, taxonomy=taxonomy):
                issues.append(
                    ValidationIssue(
                        "content-model-semantic-path",
                        relative_criterion_path,
                        (f"unsupported {criterion_content_model} path {block.semantic_path!r}"),
                    )
                )
    if criterion.metadata.get("contentModel") == "extractedCriterion":
        for block in leaf_blocks:
            semantic_path = block.semantic_path
            if not (
                semantic_path == ("sourceTranscription",)
                or (
                    len(semantic_path) == 2
                    and semantic_path[0] == "sourceTranscription"
                    and re.fullmatch(r"sourcePage[0-9]+", semantic_path[1]) is not None
                )
            ):
                issues.append(
                    ValidationIssue(
                        "content-model-semantic-path",
                        relative_criterion_path,
                        f"unsupported extractedCriterion path {semantic_path!r}",
                    )
                )
    indexed_provenance = provenance_by_reference(provenance)
    leaf_blocks_by_reference = {block.block_reference: block for block in leaf_blocks}
    for block_reference, source_spans in indexed_provenance.items():
        for source_span in source_spans:
            page_region_identifier = source_span.get("pageRegionIdentifier")
            page_region = (
                page_regions.get(page_region_identifier)
                if isinstance(page_region_identifier, str)
                else None
            )
            if page_region is None:
                issues.append(
                    ValidationIssue(
                        "provenance-page-region",
                        relative_criterion_path,
                        f"{block_reference} references unknown region {page_region_identifier!r}",
                    )
                )
                continue
            if source_span.get("physicalPage") != page_region.get("physicalPage"):
                issues.append(
                    ValidationIssue(
                        "provenance-physical-page",
                        relative_criterion_path,
                        f"{block_reference} physical page differs from region",
                    )
                )
            if source_span.get("printedPage") != page_region.get("printedPage"):
                issues.append(
                    ValidationIssue(
                        "provenance-printed-page",
                        relative_criterion_path,
                        f"{block_reference} printed page differs from region",
                    )
                )
            if page_region.get("ownerIdentifier") != slug:
                issues.append(
                    ValidationIssue(
                        "provenance-region-owner",
                        relative_criterion_path,
                        f"{block_reference} region is not owned by {slug}",
                    )
                )
            if (
                page_region.get("role") != "criterion"
                or page_region.get("publicationDisposition") == "excluded"
            ):
                issues.append(
                    ValidationIssue(
                        "provenance-region-role",
                        relative_criterion_path,
                        f"{block_reference} references a non-content region",
                    )
                )
            physical_page = source_span.get("physicalPage")
            if isinstance(physical_page, int) and not any(
                start_page <= physical_page <= end_page
                for start_page, end_page in source_page_ranges
            ):
                issues.append(
                    ValidationIssue(
                        "provenance-source-range",
                        relative_criterion_path,
                        f"{block_reference} falls outside criterion page ranges",
                    )
                )
            raw_source_bounding_box = source_span.get("sourceBoundingBox")
            source_bounding_box = _numeric_bounding_box(raw_source_bounding_box)
            region_bounding_box = _numeric_bounding_box(page_region.get("boundingBox"))
            if raw_source_bounding_box is not None and source_bounding_box is None:
                issues.append(
                    ValidationIssue(
                        "provenance-bounding-box-type",
                        relative_criterion_path,
                        f"{block_reference} has a non-numeric bounding box",
                    )
                )
            elif source_bounding_box is not None and region_bounding_box is not None:
                source_x_minimum, source_y_minimum, source_x_maximum, source_y_maximum = (
                    source_bounding_box
                )
                region_x_minimum, region_y_minimum, region_x_maximum, region_y_maximum = (
                    region_bounding_box
                )
                if not (
                    region_x_minimum <= source_x_minimum <= source_x_maximum <= region_x_maximum
                    and region_y_minimum <= source_y_minimum <= source_y_maximum <= region_y_maximum
                ):
                    issues.append(
                        ValidationIssue(
                            "provenance-bounding-box-containment",
                            relative_criterion_path,
                            f"{block_reference} source box is outside its page region",
                        )
                    )
    annotation_values = as_sequence(
        criterion.metadata.get("sourceAnnotations"),
        location=f"{relative_criterion_path}.sourceAnnotations",
    )
    for annotation_value in annotation_values:
        annotation = as_mapping(
            annotation_value,
            location=f"{relative_criterion_path}.sourceAnnotations[]",
        )
        annotation_identifier = annotation.get("annotationIdentifier")
        if isinstance(annotation_identifier, str):
            annotation_occurrences.append(
                _AnnotationOccurrence(
                    identifier=annotation_identifier,
                    issue_offset=len(issues),
                    location=relative_criterion_path,
                )
            )
        target_type = annotation.get("targetType")
        target_reference = annotation.get("targetReference")
        source_location = annotation.get("sourceLocation")
        if isinstance(source_location, dict):
            annotation_region_identifier = source_location.get("pageRegionIdentifier")
            annotation_region = (
                page_regions.get(annotation_region_identifier)
                if isinstance(annotation_region_identifier, str)
                else None
            )
            if annotation_region is None:
                issues.append(
                    ValidationIssue(
                        "annotation-source-region",
                        relative_criterion_path,
                        f"unknown annotation source region {annotation_region_identifier!r}",
                    )
                )
            elif source_location.get("physicalPage") != annotation_region.get(
                "physicalPage"
            ) or source_location.get("printedPage") != annotation_region.get("printedPage"):
                issues.append(
                    ValidationIssue(
                        "annotation-source-region",
                        relative_criterion_path,
                        "annotation page differs from its page region",
                    )
                )
            else:
                annotation_physical_page = source_location.get("physicalPage")
                if (
                    annotation_region.get("role") != "criterion"
                    or annotation_region.get("ownerIdentifier") != slug
                    or not isinstance(annotation_physical_page, int)
                    or not any(
                        start_page <= annotation_physical_page <= end_page
                        for start_page, end_page in source_page_ranges
                    )
                ):
                    issues.append(
                        ValidationIssue(
                            "annotation-source-range",
                            relative_criterion_path,
                            "annotation source lies outside the criterion boundary",
                        )
                    )
        if target_type == "astNode" and target_reference not in indexed_provenance:
            issues.append(
                ValidationIssue(
                    "annotation-target",
                    relative_criterion_path,
                    f"unknown AST target {target_reference!r}",
                )
            )
        if target_type == "astNode" and isinstance(target_reference, str):
            target_block = leaf_blocks_by_reference.get(target_reference)
            source_text = annotation.get("sourceText")
            if (
                target_block is not None
                and isinstance(source_text, str)
                and source_text not in target_block.content
            ):
                issues.append(
                    ValidationIssue(
                        "annotation-source-text",
                        relative_criterion_path,
                        f"{source_text!r} is absent from target {target_reference}",
                    )
                )
        if target_type == "pageRegion" and target_reference not in page_regions:
            issues.append(
                ValidationIssue(
                    "annotation-target",
                    relative_criterion_path,
                    f"unknown page region target {target_reference!r}",
                )
            )

    current_checksum = criterion_source_checksum(
        slug,
        domain_identifier,
        root=root,
    )
    review_record = review_records.get(("criterion", slug))
    if review_record is None:
        issues.append(
            ValidationIssue("review-record", relative_criterion_path, "missing review record")
        )
    elif review_record.get("subjectSourceChecksum") != current_checksum:
        issues.append(
            ValidationIssue(
                "review-source-checksum",
                relative_criterion_path,
                "review record does not reference current criterion checksum",
            )
        )
    elif review_record.get("unresolvedSourceAnomalyCount") != len(annotation_values):
        issues.append(
            ValidationIssue(
                "review-anomaly-count",
                relative_criterion_path,
                "review record anomaly count differs from sourceAnnotations",
            )
        )
    if (
        review_record is not None
        and criterion.metadata.get("contentModel") == "extractedCriterion"
        and review_record.get("workflowStatus") != "extracted"
    ):
        issues.append(
            ValidationIssue(
                "extracted-review-state",
                relative_criterion_path,
                "extractedCriterion must remain in extracted workflow status",
            )
        )
    return _CriterionValidationResult(
        issues=tuple(issues),
        annotation_occurrences=tuple(annotation_occurrences),
    )


def _run_criterion_validation_job(
    job: _CriterionValidationJob,
) -> tuple[_CriterionValidationResult, ...]:
    """Validate an ordered criterion batch without shared mutable state."""

    return tuple(
        _validate_criterion(
            root=job.root,
            manifest_record=manifest_record,
            criterion_schema=job.criterion_schema,
            provenance_schema=job.provenance_schema,
            taxonomy=job.taxonomy,
            page_regions=job.page_regions,
            source_document_identifiers=job.source_document_identifiers,
            review_records=job.review_records,
            release=job.release,
        )
        for manifest_record in job.manifest_records
    )


def _criterion_validation_jobs(
    *,
    repository: Path,
    manifest_records: list[dict[str, JsonValue]],
    criterion_schema: dict[str, JsonValue],
    provenance_schema: dict[str, JsonValue],
    taxonomy: dict[str, JsonValue],
    page_regions: dict[str, dict[str, JsonValue]],
    source_document_identifiers: set[str],
    review_records: dict[tuple[str, str], dict[str, JsonValue]],
    release: bool,
    workers: int,
) -> list[_CriterionValidationJob]:
    """Partition criteria into stable batches that balance process overhead."""

    if not manifest_records:
        return []
    batch_count = min(len(manifest_records), workers * 4)
    batch_size = max(1, (len(manifest_records) + batch_count - 1) // batch_count)
    return [
        _CriterionValidationJob(
            root=repository,
            manifest_records=tuple(manifest_records[start : start + batch_size]),
            criterion_schema=criterion_schema,
            provenance_schema=provenance_schema,
            taxonomy=taxonomy,
            page_regions=page_regions,
            source_document_identifiers=source_document_identifiers,
            review_records=review_records,
            release=release,
        )
        for start in range(0, len(manifest_records), batch_size)
    ]


def _merge_criterion_validation_results(
    results: list[_CriterionValidationResult],
) -> list[ValidationIssue]:
    """Restore cross-criterion annotation checks in manifest order."""

    annotation_identifiers: set[str] = set()
    merged_issues: list[ValidationIssue] = []
    for result in results:
        criterion_issues = list(result.issues)
        inserted_issue_count = 0
        for occurrence in result.annotation_occurrences:
            if occurrence.identifier in annotation_identifiers:
                criterion_issues.insert(
                    occurrence.issue_offset + inserted_issue_count,
                    ValidationIssue(
                        "annotation-identifier-unique",
                        occurrence.location,
                        f"duplicate annotation identifier {occurrence.identifier}",
                    ),
                )
                inserted_issue_count += 1
            else:
                annotation_identifiers.add(occurrence.identifier)
        merged_issues.extend(criterion_issues)
    return merged_issues


def validate_repository(
    *,
    root: Path | None = None,
    release: bool = False,
    workers: int = 1,
    executor: Executor | None = None,
) -> list[ValidationIssue]:
    """Validate the canonical corpus or the full release gate."""

    worker_count = validate_worker_count(workers)
    if executor is None and worker_count > 1:
        with process_executor(worker_count) as owned_executor:
            return validate_repository(
                root=root,
                release=release,
                workers=worker_count,
                executor=owned_executor,
            )
    repository = root or repository_root()
    documents, issues = _load_and_validate_bound_documents(repository)
    issues.extend(_validate_all_schema_documents(repository))
    required_documents = set(SCHEMA_BINDINGS)
    if set(documents) != required_documents:
        return issues

    source_registry = documents["data/source-registry.yaml"]
    taxonomy = documents["data/taxonomy.yaml"]
    manifest = documents["data/criteria-manifest.yaml"]
    page_inventory = documents["data/page-region-inventory.yaml"]
    review_registry = documents["data/review-registry.yaml"]
    issues.extend(_validate_source_document(repository, source_registry))
    source_document_values = as_sequence(
        source_registry["documents"],
        location="sourceRegistry.documents",
    )
    source_document_checksums: dict[str, str] = {}
    for source_document_value in source_document_values:
        source_document = as_mapping(
            source_document_value,
            location="sourceRegistry.documents[]",
        )
        source_document_identifier = source_document.get("sourceDocumentIdentifier")
        source_document_checksum = source_document.get("checksumValue")
        if isinstance(source_document_identifier, str) and isinstance(
            source_document_checksum,
            str,
        ):
            source_document_checksums[source_document_identifier] = source_document_checksum

    criteria_values = as_sequence(manifest["criteria"], location="manifest.criteria")
    issues.extend(
        _validate_derived_inventory(
            root=repository,
            manifest=manifest,
            taxonomy=taxonomy,
        )
    )
    registered_count = manifest.get("registeredCriterionCount")
    if registered_count != len(criteria_values):
        issues.append(
            ValidationIssue(
                "manifest-count",
                "data/criteria-manifest.yaml",
                "registeredCriterionCount does not match criteria length",
            )
        )
    if manifest.get("expectedCriterionCount") != EXPECTED_CRITERION_COUNT:
        issues.append(
            ValidationIssue(
                "manifest-expected-count",
                "data/criteria-manifest.yaml",
                f"expectedCriterionCount must be {EXPECTED_CRITERION_COUNT}",
            )
        )
    manifest_identity_sets = {
        field_name: [
            criterion_value.get(field_name)
            for criterion_value in criteria_values
            if isinstance(criterion_value, dict)
        ]
        for field_name in ("code", "slug", "route")
    }
    for field_name, values in manifest_identity_sets.items():
        if len(values) != len(set(values)):
            issues.append(
                ValidationIssue(
                    "manifest-identity-unique",
                    "data/criteria-manifest.yaml",
                    f"duplicate {field_name}",
                )
            )

    page_region_values = as_sequence(
        page_inventory["pageRegions"],
        location="pageRegionInventory.pageRegions",
    )
    page_regions: dict[str, dict[str, JsonValue]] = {}
    physical_pages: set[int] = set()
    for region_value in page_region_values:
        region = as_mapping(region_value, location="pageRegionInventory.pageRegions[]")
        region_identifier = region.get("pageRegionIdentifier")
        if isinstance(region_identifier, str):
            if region_identifier in page_regions:
                issues.append(
                    ValidationIssue(
                        "page-region-identifier-unique",
                        "data/page-region-inventory.yaml",
                        f"duplicate page region {region_identifier}",
                    )
                )
            page_regions[region_identifier] = region
        physical_page = region.get("physicalPage")
        if isinstance(physical_page, int):
            physical_pages.add(physical_page)
        bounding_box = region.get("boundingBox")
        if isinstance(bounding_box, list) and len(bounding_box) == 4:
            x_minimum, y_minimum, x_maximum, y_maximum = bounding_box
            if (
                isinstance(x_minimum, int | float)
                and isinstance(x_maximum, int | float)
                and x_minimum >= x_maximum
            ) or (
                isinstance(y_minimum, int | float)
                and isinstance(y_maximum, int | float)
                and y_minimum >= y_maximum
            ):
                issues.append(
                    ValidationIssue(
                        "page-region-bounds",
                        "data/page-region-inventory.yaml",
                        f"invalid bounding box for {region.get('pageRegionIdentifier')}",
                    )
                )

    if page_inventory.get("registeredPhysicalPageCount") != len(physical_pages):
        issues.append(
            ValidationIssue(
                "page-region-registered-count",
                "data/page-region-inventory.yaml",
                "registeredPhysicalPageCount differs from unique physical pages",
            )
        )
    if page_inventory.get("expectedPhysicalPageCount") != EXPECTED_PHYSICAL_PAGE_COUNT:
        issues.append(
            ValidationIssue(
                "page-region-expected-count",
                "data/page-region-inventory.yaml",
                f"expectedPhysicalPageCount must be {EXPECTED_PHYSICAL_PAGE_COUNT}",
            )
        )

    review_values = as_sequence(review_registry["records"], location="reviewRegistry.records")
    review_records: dict[tuple[str, str], dict[str, JsonValue]] = {}
    for review_value in review_values:
        if not isinstance(review_value, dict):
            continue
        review_record = review_value
        subject_type = review_record.get("subjectType")
        subject_identifier = review_record.get("subjectIdentifier")
        if isinstance(subject_type, str) and isinstance(subject_identifier, str):
            review_key = (subject_type, subject_identifier)
            if review_key in review_records:
                issues.append(
                    ValidationIssue(
                        "review-subject-unique",
                        "data/review-registry.yaml",
                        f"duplicate review subject {review_key}",
                    )
                )
            review_records[review_key] = review_record
            source_document_identifier = review_record.get("sourceDocumentIdentifier")
            if not isinstance(source_document_identifier, str) or review_record.get(
                "sourceDocumentChecksum"
            ) != source_document_checksums.get(source_document_identifier):
                issues.append(
                    ValidationIssue(
                        "review-source-document-checksum",
                        "data/review-registry.yaml",
                        f"{review_key} does not reference the current source document",
                    )
                )

    criterion_schema = load_json(repository / "schemas/criterion-metadata.schema.json")
    provenance_schema = load_json(repository / "schemas/provenance-sidecar.schema.json")
    criterion_records = [
        as_mapping(criterion_value, location="manifest.criteria[]")
        for criterion_value in criteria_values
    ]
    validation_jobs = _criterion_validation_jobs(
        repository=repository,
        manifest_records=criterion_records,
        criterion_schema=criterion_schema,
        provenance_schema=provenance_schema,
        taxonomy=taxonomy,
        page_regions=page_regions,
        source_document_identifiers=set(source_document_checksums),
        review_records=review_records,
        release=release,
        workers=worker_count,
    )
    if executor is None:
        validation_batches = [_run_criterion_validation_job(job) for job in validation_jobs]
    else:
        validation_batches = list(executor.map(_run_criterion_validation_job, validation_jobs))
    criterion_results = [
        result for validation_batch in validation_batches for result in validation_batch
    ]
    issues.extend(_merge_criterion_validation_results(criterion_results))

    if release:
        expected_codes = {
            f"{prefix}-{number:02d}"
            for prefix, count in NUMERIC_CODE_COUNTS.items()
            for number in range(1, count + 1)
        } | WEB_APPLICATION_CODES
        registered_codes = {
            code for code in manifest_identity_sets["code"] if isinstance(code, str)
        }
        if (
            manifest.get("completionStatus") != "complete"
            or len(criteria_values) != EXPECTED_CRITERION_COUNT
            or registered_codes != expected_codes
        ):
            issues.append(
                ValidationIssue(
                    "release-manifest-complete",
                    "data/criteria-manifest.yaml",
                    "release requires the exact 382-code allowlist",
                )
            )
        extracted_codes = [
            criterion_value.get("code")
            for criterion_value in criteria_values
            if isinstance(criterion_value, dict)
            and criterion_value.get("contentModel") == "extractedCriterion"
        ]
        if extracted_codes:
            issues.append(
                ValidationIssue(
                    "release-structured-corpus",
                    "data/criteria-manifest.yaml",
                    (
                        "release requires zero extractedCriterion records; "
                        f"found {len(extracted_codes)}"
                    ),
                )
            )
        expected_pages = set(range(1, EXPECTED_PHYSICAL_PAGE_COUNT + 1))
        if (
            page_inventory.get("completionStatus") != "complete"
            or page_inventory.get("registeredPhysicalPageCount") != len(physical_pages)
            or physical_pages != expected_pages
            or page_inventory.get("unclassifiedContentBlockCount") != 0
        ):
            issues.append(
                ValidationIssue(
                    "release-page-inventory-complete",
                    "data/page-region-inventory.yaml",
                    "release requires exact physical page coverage 1 through 873",
                )
            )
        required_review_subjects = {
            ("criterion", slug) for slug in manifest_identity_sets["slug"] if isinstance(slug, str)
        } | {
            ("pageRegion", region_identifier)
            for region_identifier, region in page_regions.items()
            if region.get("publicationDisposition") in {"published", "derived"}
        }
        test_profile = documents["data/test-profile.yaml"]
        current_test_profile_version = test_profile.get("profileVersion")
        manifest_records_by_slug = {
            slug: as_mapping(criterion_value, location="manifest.criteria[]")
            for criterion_value in criteria_values
            if isinstance(criterion_value, dict)
            and isinstance((slug := criterion_value.get("slug")), str)
        }
        if (
            review_registry.get("completionStatus") != "complete"
            or set(review_records) != required_review_subjects
        ):
            issues.append(
                ValidationIssue(
                    "release-review-subjects",
                    "data/review-registry.yaml",
                    "release requires exact criterion and published-region review subjects",
                )
            )
        for review_key in sorted(required_review_subjects):
            review_record = review_records.get(review_key)
            if review_record is None:
                continue
            if review_key[0] == "pageRegion":
                region = page_regions[review_key[1]]
                owner_identifier = region.get("ownerIdentifier")
                owner_manifest = (
                    manifest_records_by_slug.get(owner_identifier)
                    if isinstance(owner_identifier, str)
                    else None
                )
                if owner_manifest is not None:
                    owner_domain_identifier = owner_manifest.get("domainIdentifier")
                    if not isinstance(owner_domain_identifier, str):
                        owner_source_checksum = ""
                    else:
                        owner_source_checksum = criterion_source_checksum(
                            review_key[1]
                            if region.get("ownerType") == "pageRegion"
                            else cast("str", owner_identifier),
                            owner_domain_identifier,
                            root=repository,
                        )
                else:
                    source_document_identifier = review_record.get("sourceDocumentIdentifier")
                    owner_source_checksum = (
                        source_document_checksums.get(source_document_identifier, "")
                        if isinstance(source_document_identifier, str)
                        else ""
                    )
                expected_region_checksum = region_source_checksum(
                    region,
                    owner_source_checksum=owner_source_checksum,
                )
                if review_record.get("subjectSourceChecksum") != expected_region_checksum:
                    issues.append(
                        ValidationIssue(
                            "release-region-source-checksum",
                            "data/review-registry.yaml",
                            f"{review_key} does not reference the current region state",
                        )
                    )
            approval_failures = [
                review_record.get("workflowStatus") != "approved",
                review_record.get("transcriptionStatus") != "visuallyVerified",
                review_record.get("sourceAnomalyStatus") == "reviewRequired",
                review_record.get("automatedValidationResult") != "passed",
                review_record.get("unresolvedConversionErrorCount") != 0,
                not review_record.get("reviewers"),
                not isinstance(review_record.get("reviewedAt"), str),
                not review_record.get("visualEvidenceIdentifiers"),
                review_record.get("testProfileVersion") != current_test_profile_version,
                not isinstance(review_record.get("validationReportIdentifier"), str),
            ]
            if any(approval_failures):
                issues.append(
                    ValidationIssue(
                        "release-review-approved",
                        "data/review-registry.yaml",
                        f"{review_key} does not satisfy approval preconditions",
                    )
                )
            if review_key[0] == "criterion":
                criterion_manifest = manifest_records_by_slug.get(review_key[1])
                if criterion_manifest is None:
                    continue
                criterion_domain_identifier = criterion_manifest.get("domainIdentifier")
                if not isinstance(criterion_domain_identifier, str):
                    continue
                criterion_document = load_criterion(
                    criterion_directory(repository, criterion_domain_identifier)
                    / f"{review_key[1]}.md"
                )
                annotation_values = as_sequence(
                    criterion_document.metadata["sourceAnnotations"],
                    location=f"{review_key[1]}.sourceAnnotations",
                )
                annotations = [
                    as_mapping(
                        annotation_value,
                        location=f"{review_key[1]}.sourceAnnotations[]",
                    )
                    for annotation_value in annotation_values
                ]
                anomaly_status = review_record.get("sourceAnomalyStatus")
                if anomaly_status == "none" and annotations:
                    issues.append(
                        ValidationIssue(
                            "release-annotation-status",
                            "data/review-registry.yaml",
                            f"{review_key} declares no anomalies but annotations exist",
                        )
                    )
                if anomaly_status == "reviewedWithOpenAnnotations" and any(
                    annotation.get("reviewStatus") != "reviewed"
                    or not annotation.get("reviewedBy")
                    or not annotation.get("reviewedAt")
                    for annotation in annotations
                ):
                    issues.append(
                        ValidationIssue(
                            "release-annotation-status",
                            "data/review-registry.yaml",
                            f"{review_key} has unreviewed open annotations",
                        )
                    )
                if anomaly_status == "resolved" and any(
                    annotation.get("reviewStatus") != "reviewed"
                    or annotation.get("disposition") == "unresolved"
                    for annotation in annotations
                ):
                    issues.append(
                        ValidationIssue(
                            "release-annotation-status",
                            "data/review-registry.yaml",
                            f"{review_key} has unresolved annotation records",
                        )
                    )
        if test_profile.get("completionStatus") != "complete":
            issues.append(
                ValidationIssue(
                    "release-test-profile-complete",
                    "data/test-profile.yaml",
                    "release requires a complete visual and accessibility test profile",
                )
            )
        policy_exceptions = documents["data/policy-exceptions.yaml"]
        exception_values = as_sequence(
            policy_exceptions["exceptions"],
            location="policyExceptions.exceptions",
        )
        if any(
            isinstance(exception_value, dict) and exception_value.get("status") != "active"
            for exception_value in exception_values
        ):
            issues.append(
                ValidationIssue(
                    "release-policy-exceptions",
                    "data/policy-exceptions.yaml",
                    "release contains expired or revoked policy exceptions",
                )
            )
        issues.extend(
            _validate_release_generated_outputs(
                repository=repository,
                expected_codes=expected_codes,
                criteria_values=criteria_values,
                test_profile_version=current_test_profile_version,
                workers=worker_count,
                executor=executor,
            )
        )
    return issues


def write_report(
    issues: list[ValidationIssue],
    *,
    report_path: Path,
    release: bool,
) -> None:
    """Write a deterministic machine-readable validation report."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schemaVersion": 1,
        "validationScope": "release" if release else "canonicalSubset",
        "passed": not issues,
        "issueCount": len(issues),
        "issues": [asdict(issue) for issue in issues],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="apply full release gates")
    parser.add_argument(
        "--report",
        type=Path,
        default=BUILD_DIRECTORY / "reports" / "content-validation.json",
        help="validation report output path",
    )
    parser.add_argument(
        "--workers",
        type=parse_worker_count,
        default=default_worker_count(),
        help="parallel worker processes for criterion and site validation",
    )
    add_logging_arguments(parser)
    return parser


def main() -> int:
    """Run scoped or release validation."""

    arguments = _argument_parser().parse_args()
    with configure_runtime_logging(
        "validate_content",
        level=arguments.log_level,
        log_directory=arguments.log_directory,
    ) as logger:
        scope = "release" if arguments.release else "canonical corpus"
        logger.info(
            "Content validation started",
            event="command.started",
            validation_scope=scope,
        )
        root = repository_root()
        issues = validate_repository(
            root=root,
            release=arguments.release,
            workers=arguments.workers,
        )
        report_path = arguments.report
        if not report_path.is_absolute():
            report_path = root / report_path
        write_report(issues, report_path=report_path, release=arguments.release)
        logger.info(
            "Validation report written",
            event="validation.report_written",
            issue_count=len(issues),
            report_path=str(report_path),
            workers=arguments.workers,
        )
        if issues:
            logger.error(
                "Content validation failed",
                event="command.failed",
                issue_count=len(issues),
                validation_scope=scope,
            )
            for issue in issues:
                print(
                    f"{issue.rule_identifier}: {issue.location}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
        logger.info(
            "Content validation completed",
            event="command.completed",
            issue_count=0,
            validation_scope=scope,
        )
        print(f"{scope} validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
