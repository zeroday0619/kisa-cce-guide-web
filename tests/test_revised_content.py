"""Check revision isolation, strict input validation, and public rendering."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from conversion.paths import REVISED_CRITERIA_DIRECTORY, SITE_TEMPLATE_DIRECTORY, repository_root
from conversion.revised_content import (
    LICENSE_LABEL,
    REVISED_CRITERION_COUNT,
    build_revised_edition,
    load_revisions,
    validate_revised_dataset,
)


def _revision_fixture(root: Path) -> Path:
    shutil.copytree(repository_root() / SITE_TEMPLATE_DIRECTORY, root / SITE_TEMPLATE_DIRECTORY)
    (root / "schemas").mkdir()
    shutil.copyfile(
        repository_root() / "schemas/revised-dataset.schema.json",
        root / "schemas/revised-dataset.schema.json",
    )
    (root / "data").mkdir()
    shutil.copyfile(repository_root() / "data/taxonomy.yaml", root / "data/taxonomy.yaml")
    for name in ("criteria-manifest.yaml", "source-registry.yaml"):
        shutil.copyfile(repository_root() / "data" / name, root / "data" / name)
    shutil.copytree(repository_root() / "content/criteria/unix", root / "content/criteria/unix")
    directory = root / REVISED_CRITERIA_DIRECTORY
    directory.mkdir(parents=True)
    for number in range(1, REVISED_CRITERION_COUNT + 1):
        (directory / f"u-{number:02}.md").write_text(
            f"---\ncriterionCode: U-{number:02}\ntitle: 독립 개정\n"
            "platforms: [rhel-10, ubuntu-26.04, debian-13]\nstatus: final\n"
            "sources:\n  - title: 공식 문서\n    url: https://example.org/document\n---\n"
            "## 개요\n### 점검 내용\n설정 확인 여부 점검\n"
            "### 점검 목적\n접근 통제를 목적으로 함\n### 보안 위협\n비인가 접근 위험이 존재함\n"
            "## 점검 대상 및 판단 기준\n### 대상\nRHEL, Ubuntu, Debian\n"
            "### 판단 기준\n- **양호:** 제한된 경우\n- **취약:** 허용된 경우\n"
            "### 조치 방법\n설정 변경\n### 조치 시 영향\n접근 제한\n"
            "## 점검 및 조치 사례\n### RHEL 10\n1. 설정을 확인한다.\n"
            "\n```bash command\nprintf test\n```\n"
            "<script>alert(1)</script>\n"
            "\n| 설정 | 값 |\n| --- | --- |\n| 기능 | 제한 |\n"
            "### Ubuntu 26.04 LTS\n1. 설정을 확인한다.\n\n```bash command\nprintf test\n```\n"
            "### Debian 13\n1. 설정을 확인한다.\n\n```bash command\nprintf test\n```\n",
            encoding="utf-8",
        )
    return directory


def test_revised_pages_preserve_edition_and_base_path(tmp_path: Path) -> None:
    """Keep final content independent and escape embedded HTML."""

    _revision_fixture(tmp_path)
    output = tmp_path / "output"
    paths = build_revised_edition(repository=tmp_path, output_root=output, base_path="/guide/")
    assert len(paths) == REVISED_CRITERION_COUNT * 2 + 10
    page = (output / "site/revised/unix/u-01/index.html").read_text(encoding="utf-8")
    assert 'href="/guide/unix/u-01/"' in page
    assert 'href="/guide/revised/"' in page
    assert page.count(LICENSE_LABEL) == 1
    assert "초안" not in page
    assert "skill-document" not in page
    assert 'class="criterion__body"' in page
    assert 'class="pager"' in page
    assert 'href="/guide/revised/unix/u-02/"' in page
    assert "<script>" not in page
    assert 'data-block-reference="u-01:overview.heading:1"' in page
    assert 'id="theme-selector"' in page
    assert 'src="/guide/assets/theme-init.js"' in page
    assert 'class="table-scroll"' in page
    assert '<th scope="col">' in page
    dataset = json.loads((output / "site/revised/dataset.json").read_text(encoding="utf-8"))
    assert len(dataset["records"]) == REVISED_CRITERION_COUNT
    assert dataset["records"][0]["originalRoute"] == "/guide/unix/u-01/"
    before = {path: path.read_bytes() for path in paths}
    build_revised_edition(repository=tmp_path, output_root=output, base_path="/guide/")
    assert before == {path: path.read_bytes() for path in paths}
    assert not (output / "normalized").exists()
    search = json.loads((output / "site/revised/dataset/search-index.json").read_text())
    schema = json.loads((repository_root() / "schemas/search-index.schema.json").read_text())
    Draft202012Validator(schema).validate(search)
    assert len(search["records"]) == REVISED_CRITERION_COUNT
    assert all(record["route"].startswith("/revised/unix/") for record in search["records"])
    assert search["records"][0]["targetLabels"] == ["RHEL 10", "Ubuntu 26.04 LTS", "Debian 13"]
    search_page = (output / "site/revised/search/index.html").read_text()
    assert '<h1 id="search-heading">UNIX 개정판 검색</h1>' in search_page
    assert 'href="/guide/search/"' in search_page
    assert 'href="/guide/revised/search/" aria-current="page"' in search_page
    assert 'data-search-index-url="/guide/revised/dataset/search-index.json"' in search_page
    assert 'href="/guide/revised/search/"' in page
    assert 'data-source-physical-pages="12"' not in page
    assert "data-copy" in page
    assert "language-bash" in page


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("status: final", "status: approved"),
        ("status: final", "status: final\napproved: true"),
        ("criterionCode: U-01", "criterionCode: U-02"),
        ("ubuntu-26.04", "ubuntu-24.04"),
        ("https://example.org/document", "javascript:alert(1)"),
        ("## 점검 및 조치 사례", "# 조치"),
        ("### 조치 시 영향", "### 영향"),
        ("**양호:**", "**통과:**"),
        ("1. 설정을", "설정을"),
    ],
)
def test_invalid_revision_fails_before_output(tmp_path: Path, old: str, new: str) -> None:
    """Reject unsupported approval claims, platforms, and unsafe source URLs."""

    directory = _revision_fixture(tmp_path)
    path = directory / "u-01.md"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid revised"):
        build_revised_edition(repository=tmp_path, output_root=tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_revised_inventory_is_exact(tmp_path: Path) -> None:
    """A missing document cannot silently reduce revision coverage."""

    directory = _revision_fixture(tmp_path)
    (directory / "u-01.md").rename(directory / "unexpected.md")
    with pytest.raises(ValueError, match="inventory mismatch"):
        load_revisions(tmp_path)


def test_build_preflight_preserves_previous_output(tmp_path: Path) -> None:
    """Reject missing revisions before deleting an existing generated site."""

    from conversion.build_content import build  # noqa: PLC0415
    from conversion.paths import BUILD_DIRECTORY  # noqa: PLC0415

    output = tmp_path / BUILD_DIRECTORY / "site"
    output.mkdir(parents=True)
    previous = output / "index.html"
    previous.write_text("previous build", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory mismatch"):
        build(root=tmp_path)
    assert previous.read_text(encoding="utf-8") == "previous build"


@pytest.mark.parametrize("change", ["count", "route", "extra", "version"])
def test_revised_dataset_schema_rejects_contract_drift(tmp_path: Path, change: str) -> None:
    """Reject record loss, edition route drift, unknown fields, and schema changes."""

    _revision_fixture(tmp_path)
    output = tmp_path / "output"
    build_revised_edition(repository=tmp_path, output_root=output)
    dataset = json.loads((output / "site/revised/dataset.json").read_text(encoding="utf-8"))
    if change == "count":
        dataset["records"].pop()
    elif change == "route":
        dataset["records"][0]["route"] = "/unix/u-01/"
    elif change == "extra":
        dataset["records"][0]["approved"] = True
    else:
        dataset["schemaVersion"] = 1
    with pytest.raises(ValueError, match="revised dataset validation failed"):
        validate_revised_dataset(dataset, tmp_path)


@pytest.mark.parametrize("base_path", ["", "/kisa-cce-guide-web"])
def test_original_and_revised_counterparts_are_linked(tmp_path: Path, base_path: str) -> None:
    """Resolve both edition links against actual generated criterion pages."""

    from conversion.build_content import build  # noqa: PLC0415

    build(output_root=tmp_path, base_path=base_path)
    original_path = tmp_path / "site/unix/u-01/index.html"
    revised_path = tmp_path / "site/revised/unix/u-01/index.html"
    original = original_path.read_text(encoding="utf-8")
    revised = revised_path.read_text(encoding="utf-8")
    assert f'href="{base_path}/revised/unix/u-01/"' in original
    assert f'href="{base_path}/unix/u-01/"' in revised
    dataset = json.loads((tmp_path / "site/revised/dataset.json").read_text(encoding="utf-8"))
    first = dataset["records"][0]
    assert first["route"] == f"{base_path}/revised/unix/u-01/"
    assert first["originalRoute"] == f"{base_path}/unix/u-01/"
