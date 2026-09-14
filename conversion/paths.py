"""Repository path contracts for canonical inputs and generated artifacts."""

from __future__ import annotations

from pathlib import Path

CONTENT_DIRECTORY = Path("content")
CRITERIA_DIRECTORY = CONTENT_DIRECTORY / "criteria"
REVISED_CRITERIA_DIRECTORY = CONTENT_DIRECTORY / "revisions" / "unix"
CANONICAL_ASSET_DIRECTORY = CONTENT_DIRECTORY / "assets"
CRITERION_ASSET_REFERENCE_DIRECTORY = Path("../..") / CANONICAL_ASSET_DIRECTORY.relative_to(
    CONTENT_DIRECTORY
)
SOURCE_DIRECTORY = CONTENT_DIRECTORY / "source"
SOURCE_DOCUMENT_PATH = SOURCE_DIRECTORY / "kisa-cce-criteria-2026.pdf"

DATA_DIRECTORY = Path("data")
SCHEMA_DIRECTORY = Path("schemas")
PROMPT_DIRECTORY = Path("conversion/prompts")

SITE_SOURCE_DIRECTORY = Path("site")
SITE_ASSET_DIRECTORY = SITE_SOURCE_DIRECTORY / "assets"
SITE_HOSTING_DIRECTORY = SITE_SOURCE_DIRECTORY / "hosting"
SITE_SKILL_DIRECTORY = SITE_SOURCE_DIRECTORY / "skill"
SITE_TEMPLATE_DIRECTORY = SITE_SOURCE_DIRECTORY / "templates"

ARTIFACT_DIRECTORY = Path(".artifacts")
BUILD_DIRECTORY = ARTIFACT_DIRECTORY / "build"
DISTRIBUTION_DIRECTORY = ARTIFACT_DIRECTORY / "dist"
WORK_DIRECTORY = ARTIFACT_DIRECTORY / "work"


def repository_root() -> Path:
    """Return the repository root independently of the current working directory."""

    return Path(__file__).resolve().parent.parent


def criterion_directory(root: Path, domain_identifier: str) -> Path:
    """Return the canonical directory for one criterion domain."""

    return root / CRITERIA_DIRECTORY / domain_identifier


def canonical_asset_directory(root: Path, criterion_slug: str) -> Path:
    """Return the canonical asset directory for one criterion."""

    return root / CANONICAL_ASSET_DIRECTORY / criterion_slug
