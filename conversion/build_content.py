"""Build deterministic normalized JSON and a search index."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import unicodedata
from concurrent.futures import Executor
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator

from conversion import GENERATOR_VERSION
from conversion.common import (
    JsonValue,
    as_mapping,
    as_sequence,
    canonical_corpus_checksum,
    criterion_source_checksum,
    extract_leaf_blocks,
    flatten_block_references,
    heading_identifiers,
    load_criterion,
    load_json,
    load_yaml,
    provenance_attributes_by_reference,
    provenance_by_reference,
    repository_root,
)
from conversion.parallel import (
    default_worker_count,
    parse_worker_count,
    process_executor,
    validate_worker_count,
)
from conversion.paths import BUILD_DIRECTORY, criterion_directory
from conversion.revised_content import build_revised_edition, load_revisions
from conversion.runtime_logging import add_logging_arguments, configure_runtime_logging
from conversion.validate_content import validate_repository


def _plain_search_text(value: str) -> str:
    """Remove Markdown punctuation without changing searchable words."""

    without_markup = re.sub(r"[\x60*#>|[\]{}()]", " ", value)
    without_table_rules = re.sub(r"(?:^|\s)-{3,}(?:\s|$)", " ", without_markup)
    collapsed = re.sub(r"\s+", " ", without_table_rules).strip()
    return unicodedata.normalize("NFC", collapsed)


_SEARCH_SECTION_NAMES = (
    "inspection",
    "purpose",
    "threat",
    "judgment",
    "action",
    "impact",
    "guidance",
    "reference",
)


@dataclass(frozen=True)
class _NormalizationJob:
    """Contain the immutable inputs for one normalized criterion."""

    root: Path
    manifest_record: dict[str, JsonValue]
    heading_identifier_mapping: dict[str, str]


def _search_section_name(block: dict[str, JsonValue]) -> str:
    """Map one article block to its structured search section."""

    semantic_path = block.get("semanticPath")
    if not isinstance(semantic_path, list):
        return "inspection"
    path = [value for value in semantic_path if isinstance(value, str)]
    section_by_path = {
        ("overview", "inspectionContent"): "inspection",
        ("overview", "inspectionPurpose"): "purpose",
        ("overview", "securityThreat"): "threat",
        ("assessment", "judgment"): "judgment",
        ("assessment", "remediationMethod"): "action",
        ("assessment", "remediationImpact"): "impact",
        ("overview", "reference"): "reference",
    }
    if path[:1] == ["remediation"]:
        return "guidance"
    return section_by_path.get(tuple(path[:2]), "inspection")


def _search_sections(blocks: list[JsonValue]) -> dict[str, JsonValue]:
    """Derive weighted search sections from one normalized article JSON document."""

    section_parts: dict[str, list[str]] = {name: [] for name in _SEARCH_SECTION_NAMES}
    for block_value in blocks:
        if (
            not isinstance(block_value, dict)
            or block_value.get("publicationDisposition") == "excluded"
        ):
            continue
        semantic_path = block_value.get("semanticPath")
        if not isinstance(semantic_path, list) or not semantic_path:
            continue
        if block_value.get("blockType") == "heading" and semantic_path[0] != "remediation":
            continue
        if semantic_path[:2] == ["assessment", "target"]:
            continue
        content_value = block_value.get("content")
        if not isinstance(content_value, str):
            continue
        section_parts[_search_section_name(block_value)].append(content_value)
    return {
        name: _plain_search_text(" ".join(section_parts[name])) for name in _SEARCH_SECTION_NAMES
    }


def _write_canonical_json(path: Path, value: JsonValue) -> None:
    """Write RFC 8785 canonical JSON without a trailing newline."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rfc8785.dumps(value))


def _validate_generated_document(
    *,
    document: dict[str, JsonValue],
    validator: Validator,
    schema_name: str,
) -> None:
    """Reject generated output that does not satisfy its public schema."""

    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    if errors:
        messages = "; ".join(error.message for error in errors)
        msg = f"{schema_name}: generated output is invalid: {messages}"
        raise ValueError(msg)


def _generated_document_validator(schema_path: Path) -> Validator:
    """Compile one generated-document schema for reuse throughout a build."""

    return Draft202012Validator(load_json(schema_path), format_checker=FormatChecker())


def _normalized_criterion(
    *,
    root: Path,
    manifest_record: dict[str, JsonValue],
    heading_identifier_mapping: dict[str, str],
) -> dict[str, JsonValue]:
    """Build one normalized criterion from Markdown and provenance."""

    slug_value = manifest_record.get("slug")
    if not isinstance(slug_value, str):
        msg = "manifest criterion slug must be a string"
        raise ValueError(msg)
    domain_identifier = manifest_record.get("domainIdentifier")
    if not isinstance(domain_identifier, str):
        msg = "manifest criterion domainIdentifier must be a string"
        raise TypeError(msg)
    criterion_source_directory = criterion_directory(root, domain_identifier)
    criterion_document = load_criterion(criterion_source_directory / f"{slug_value}.md")
    provenance = load_yaml(criterion_source_directory / f"{slug_value}.provenance.yaml")
    leaf_blocks = extract_leaf_blocks(
        criterion_document.body,
        criterion_slug=slug_value,
        heading_identifier_mapping=heading_identifier_mapping,
    )
    block_references = flatten_block_references(provenance)
    if len(leaf_blocks) != len(block_references):
        msg = f"{slug_value}: leaf and provenance counts differ"
        raise ValueError(msg)
    generated_references = [block.block_reference for block in leaf_blocks]
    if generated_references != block_references:
        mismatch_index = next(
            index
            for index, (generated, declared) in enumerate(
                zip(generated_references, block_references, strict=True)
            )
            if generated != declared
        )
        msg = (
            f"{slug_value}: provenance reference mismatch at {mismatch_index}: "
            f"{generated_references[mismatch_index]} != {block_references[mismatch_index]}"
        )
        raise ValueError(msg)
    source_spans = provenance_by_reference(provenance)
    provenance_attributes = provenance_attributes_by_reference(provenance)
    assets_by_path = {
        asset["path"]: asset
        for asset_value in as_sequence(
            provenance["assets"],
            location=f"{slug_value}.provenance.assets",
        )
        if isinstance(asset_value, dict)
        and (asset := asset_value)
        and isinstance(asset.get("path"), str)
    }
    normalized_blocks: list[JsonValue] = []
    for block in leaf_blocks:
        block_record: dict[str, JsonValue] = {
            "blockReference": block.block_reference,
            "blockType": block.block_type,
            "content": block.content,
            "semanticRole": block.semantic_role,
            "semanticPath": cast("JsonValue", list(block.semantic_path)),
            "sourceSpans": cast("JsonValue", source_spans[block.block_reference]),
            "technicalLiterals": cast("JsonValue", list(block.technical_literals)),
            "publicationDisposition": provenance_attributes[block.block_reference][
                "publicationDisposition"
            ],
        }
        optional_properties: dict[str, JsonValue] = {
            "headingLevel": block.heading_level,
            "listType": block.list_type,
            "listDepth": block.list_depth,
            "codeLanguage": block.code_language,
            "codeContentType": block.code_content_type,
            "parentBlockReference": block.parent_block_reference,
            "tableHeaders": (
                cast("JsonValue", list(block.table_headers))
                if block.table_headers is not None
                else None
            ),
            "tableRows": (
                cast("JsonValue", [list(row) for row in block.table_rows])
                if block.table_rows is not None
                else None
            ),
            "assetPath": (
                block.asset_path.removeprefix("../") if block.asset_path is not None else None
            ),
            "alternativeText": block.alternative_text,
            "alternativeTextStatus": block.alternative_text_status,
        }
        block_record.update(
            {
                property_name: property_value
                for property_name, property_value in optional_properties.items()
                if property_value is not None
            }
        )
        if block.asset_path is not None:
            asset = assets_by_path.get(block.asset_path)
            if asset is None:
                msg = f"{slug_value}: image block has no asset record: {block.asset_path}"
                raise ValueError(msg)
            block_record.update(
                {
                    "assetType": asset["assetType"],
                    "renderingProfileIdentifier": asset["renderingProfileIdentifier"],
                    "outputPixelDimensions": asset["outputPixelDimensions"],
                    "caption": asset.get("caption"),
                }
            )
        derivation_type = provenance_attributes[block.block_reference].get("derivationType")
        if isinstance(derivation_type, str):
            block_record["derivationType"] = derivation_type
        normalized_blocks.append(block_record)

    return {
        "schemaVersion": 1,
        "contentModel": criterion_document.metadata["contentModel"],
        "contentModelVersion": criterion_document.metadata["contentModelVersion"],
        "criterion": as_mapping(
            criterion_document.metadata["criterion"],
            location=f"{slug_value}.criterion",
        ),
        "classification": as_mapping(
            criterion_document.metadata["classification"],
            location=f"{slug_value}.classification",
        ),
        "targetScope": criterion_document.metadata["targetScope"],
        "targetIdentifiers": criterion_document.metadata["targetIdentifiers"],
        "sourceTargetText": criterion_document.metadata["sourceTargetText"],
        "provenance": as_mapping(
            criterion_document.metadata["provenance"],
            location=f"{slug_value}.provenance",
        ),
        "annotations": as_sequence(
            criterion_document.metadata["sourceAnnotations"],
            location=f"{slug_value}.sourceAnnotations",
        ),
        "blocks": normalized_blocks,
        "criterionSourceChecksum": criterion_source_checksum(
            slug_value,
            domain_identifier,
            root=root,
        ),
        "generatorVersion": GENERATOR_VERSION,
    }


def _run_normalization_job(job: _NormalizationJob) -> dict[str, JsonValue]:
    """Normalize one criterion in an isolated worker process."""

    return _normalized_criterion(
        root=job.root,
        manifest_record=job.manifest_record,
        heading_identifier_mapping=job.heading_identifier_mapping,
    )


def _search_record(
    *,
    manifest_record: dict[str, JsonValue],
    normalized_document: dict[str, JsonValue],
    taxonomy: dict[str, JsonValue],
    record_order: int,
) -> dict[str, JsonValue]:
    """Build one search record from normalized content."""

    criterion = as_mapping(
        normalized_document["criterion"],
        location="normalizedCriterion.criterion",
    )
    classification = as_mapping(
        normalized_document["classification"],
        location="normalizedCriterion.classification",
    )
    domain_identifier = classification["domainIdentifier"]
    category_identifier = classification["categoryIdentifier"]
    domain_records = as_sequence(taxonomy["domains"], location="taxonomy.domains")
    category_records = as_sequence(taxonomy["categories"], location="taxonomy.categories")
    target_records = as_sequence(taxonomy["targets"], location="taxonomy.targets")
    domain_label = next(
        record["label"]
        for record in domain_records
        if isinstance(record, dict)
        and record.get("identifier") == domain_identifier
        and isinstance(record.get("label"), str)
    )
    category_label = next(
        record["label"]
        for record in category_records
        if isinstance(record, dict)
        and record.get("identifier") == category_identifier
        and record.get("domainIdentifier") == domain_identifier
        and isinstance(record.get("label"), str)
    )
    target_identifiers = as_sequence(
        normalized_document["targetIdentifiers"],
        location="normalizedCriterion.targetIdentifiers",
    )
    target_labels = [
        record["label"]
        for target_identifier in target_identifiers
        for record in target_records
        if isinstance(record, dict)
        and record.get("identifier") == target_identifier
        and isinstance(record.get("label"), str)
    ]
    blocks = as_sequence(
        normalized_document["blocks"],
        location="normalizedCriterion.blocks",
    )
    search_sections = _search_sections(blocks)
    title_value = criterion["title"]
    if not isinstance(title_value, str):
        msg = "normalized criterion title must be a string"
        raise TypeError(msg)
    source_target_text = normalized_document["sourceTargetText"]
    if not isinstance(source_target_text, str):
        msg = "normalized sourceTargetText must be a string"
        raise TypeError(msg)
    exact_terms = sorted(
        {
            literal
            for block_value in blocks
            if isinstance(block_value, dict)
            and block_value.get("publicationDisposition") != "excluded"
            for literal_value in [block_value.get("technicalLiterals")]
            if isinstance(literal_value, list)
            for literal in literal_value
            if isinstance(literal, str)
        }
    )
    return {
        "order": record_order,
        "code": criterion["code"],
        "route": manifest_record["route"],
        "title": title_value,
        "severityLevel": as_mapping(
            criterion["severity"],
            location="normalizedCriterion.criterion.severity",
        )["level"],
        "severitySourceLabel": as_mapping(
            criterion["severity"],
            location="normalizedCriterion.criterion.severity",
        )["sourceLabel"],
        "domainIdentifier": domain_identifier,
        "domainLabel": domain_label,
        "categoryIdentifier": category_identifier,
        "categoryLabel": category_label,
        "targetIdentifiers": target_identifiers,
        "targetLabels": target_labels,
        "sourceTargetText": source_target_text,
        "searchSections": search_sections,
        "exactTerms": cast("JsonValue", exact_terms),
    }


def _build(  # noqa: PLR0913
    *,
    repository: Path,
    output_directory: Path,
    base_path: str = "",
    workers: int,
    executor: Executor | None,
    validate_corpus: bool,
) -> list[Path]:
    """Build the canonical corpus and return generated artifact paths."""

    load_revisions(repository)
    if output_directory == repository / BUILD_DIRECTORY:
        # Replacing generated directories prevents removed criteria from leaving stale files.
        for generated_directory_name in ("normalized", "search", "site"):
            shutil.rmtree(
                output_directory / generated_directory_name,
                ignore_errors=True,
            )
    if validate_corpus:
        issues = validate_repository(
            root=repository,
            release=False,
            workers=workers,
            executor=executor,
        )
        if issues:
            joined = "\n".join(
                f"{issue.rule_identifier}: {issue.location}: {issue.message}" for issue in issues
            )
            msg = f"canonical corpus validation failed:\n{joined}"
            raise ValueError(msg)

    manifest = load_yaml(repository / "data/criteria-manifest.yaml")
    criteria = as_sequence(manifest["criteria"], location="manifest.criteria")
    taxonomy = load_yaml(repository / "data/taxonomy.yaml")
    heading_identifier_mapping = heading_identifiers(taxonomy)
    normalized_schema_path = repository / "schemas/normalized-criterion.schema.json"
    search_schema_path = repository / "schemas/search-index.schema.json"
    normalized_validator = _generated_document_validator(normalized_schema_path)
    search_validator = _generated_document_validator(search_schema_path)
    generated_paths: list[Path] = []
    manifest_records = [
        as_mapping(criterion_value, location="manifest.criteria[]") for criterion_value in criteria
    ]
    normalization_jobs = [
        _NormalizationJob(
            root=repository,
            manifest_record=manifest_record,
            heading_identifier_mapping=heading_identifier_mapping,
        )
        for manifest_record in manifest_records
    ]
    if executor is None:
        normalized_documents = [_run_normalization_job(job) for job in normalization_jobs]
    else:
        normalized_documents = list(executor.map(_run_normalization_job, normalization_jobs))

    for manifest_record, normalized_document in zip(
        manifest_records,
        normalized_documents,
        strict=True,
    ):
        _validate_generated_document(
            document=normalized_document,
            validator=normalized_validator,
            schema_name=normalized_schema_path.name,
        )
        slug_value = manifest_record["slug"]
        if not isinstance(slug_value, str):
            msg = "manifest criterion slug must be a string"
            raise ValueError(msg)
        domain_identifier = manifest_record["domainIdentifier"]
        if not isinstance(domain_identifier, str):
            msg = "manifest criterion domainIdentifier must be a string"
            raise TypeError(msg)
        output_path = output_directory / "normalized" / domain_identifier / f"{slug_value}.json"
        _write_canonical_json(output_path, normalized_document)
        generated_paths.append(output_path)
    corpus_checksum = canonical_corpus_checksum(manifest_records, root=repository)
    search_records: list[JsonValue] = [
        _search_record(
            manifest_record=as_mapping(
                manifest_value,
                location="manifest.criteria[]",
            ),
            normalized_document=normalized_document,
            taxonomy=taxonomy,
            record_order=record_order,
        )
        for record_order, (manifest_value, normalized_document) in enumerate(
            zip(criteria, normalized_documents, strict=True),
            start=1,
        )
    ]

    search_index: dict[str, JsonValue] = {
        "schemaVersion": 2,
        "tokenizerVersion": "unicode-nfc-korean-sections-v2",
        "caseFoldingVersion": "unicode-default-v1",
        "canonicalCorpusChecksum": corpus_checksum,
        "records": search_records,
    }
    _validate_generated_document(
        document=search_index,
        validator=search_validator,
        schema_name=search_schema_path.name,
    )
    search_path = output_directory / "search" / "search-index.json"
    _write_canonical_json(search_path, search_index)
    generated_paths.append(search_path)
    source_registry = load_yaml(repository / "data/source-registry.yaml")
    from conversion.build_site import build_site  # noqa: PLC0415

    generated_paths.extend(
        build_site(
            repository=repository,
            output_root=output_directory,
            manifest=manifest,
            taxonomy=taxonomy,
            source_registry=source_registry,
            normalized_documents=normalized_documents,
            search_index=search_index,
            base_path=base_path,
        )
    )
    generated_paths.extend(
        build_revised_edition(
            repository=repository, output_root=output_directory, base_path=base_path
        )
    )
    return generated_paths


def _build_validated(
    *,
    root: Path,
    output_root: Path,
    base_path: str = "",
    workers: int = 1,
    executor: Executor | None = None,
) -> list[Path]:
    """Build a corpus that the current caller has already validated."""

    return _build(
        repository=root,
        output_directory=output_root,
        base_path=base_path,
        workers=validate_worker_count(workers),
        executor=executor,
        validate_corpus=False,
    )


def build(
    *,
    root: Path | None = None,
    output_root: Path | None = None,
    base_path: str = "",
    workers: int = 1,
    executor: Executor | None = None,
) -> list[Path]:
    """Build the canonical corpus and return generated artifact paths."""

    worker_count = validate_worker_count(workers)
    repository = root or repository_root()
    output_directory = output_root or repository / BUILD_DIRECTORY
    if executor is not None or worker_count == 1:
        return _build(
            repository=repository,
            output_directory=output_directory,
            base_path=base_path,
            workers=worker_count,
            executor=executor,
            validate_corpus=True,
        )
    with process_executor(worker_count) as owned_executor:
        return _build(
            repository=repository,
            output_directory=output_directory,
            base_path=base_path,
            workers=worker_count,
            executor=owned_executor,
            validate_corpus=True,
        )


def _argument_parser() -> argparse.ArgumentParser:
    """Build the corpus-builder command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-path",
        default="",
        help="optional hosting path prefix such as /kisa-cce",
    )
    parser.add_argument(
        "--workers",
        type=parse_worker_count,
        default=default_worker_count(),
        help="parallel worker processes for validation and normalization",
    )
    add_logging_arguments(parser)
    return parser


def main() -> int:
    """Run the deterministic corpus build."""

    arguments = _argument_parser().parse_args()
    with configure_runtime_logging(
        "build_content",
        level=arguments.log_level,
        log_directory=arguments.log_directory,
    ) as logger:
        logger.info(
            "Corpus build started",
            event="command.started",
            base_path=arguments.base_path,
            workers=arguments.workers,
        )
        try:
            generated_paths = build(
                base_path=arguments.base_path,
                workers=arguments.workers,
            )
        except ValueError as error:
            logger.exception(
                "Corpus build failed",
                event="command.failed",
                error=error,
                base_path=arguments.base_path,
                workers=arguments.workers,
            )
            print(str(error), file=sys.stderr)
            return 1
        root = repository_root()
        output_roots = sorted(
            {
                str(path.relative_to(root).parts[0])
                for path in generated_paths
                if path.is_relative_to(root)
            }
        )
        logger.info(
            "Corpus build completed",
            event="command.completed",
            artifact_count=len(generated_paths),
            output_roots=output_roots,
            workers=arguments.workers,
        )
        print(f"generated {len(generated_paths)} artifacts under {', '.join(output_roots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
