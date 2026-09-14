"""Regression tests for logging across executable conversion tools."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from conversion import (
    build_content,
    build_sites_bundle,
    codex_agent_candidate_applier,
    codex_agent_pipeline,
    codex_bulk_runner,
    codex_candidate_applier,
    codex_result_importer,
    codex_runner,
    codex_task_builder,
    generate_corpus,
    qa_reports,
    serve_site,
    validate_content,
)

TEST_WORKER_COUNT = 3

EXECUTABLE_TOOL_NAMES = {
    "build_content.py",
    "build_sites_bundle.py",
    "codex_agent_candidate_applier.py",
    "codex_agent_pipeline.py",
    "codex_bulk_runner.py",
    "codex_candidate_applier.py",
    "codex_result_importer.py",
    "codex_runner.py",
    "codex_task_builder.py",
    "generate_corpus.py",
    "qa_reports.py",
    "serve_site.py",
    "validate_content.py",
}
ARGUMENT_PARSER_CASES: tuple[tuple[ModuleType, list[str]], ...] = (
    (build_content, []),
    (build_sites_bundle, []),
    (codex_agent_candidate_applier, ["u-03"]),
    (codex_agent_pipeline, []),
    (codex_bulk_runner, []),
    (codex_candidate_applier, ["u-03"]),
    (codex_result_importer, ["u-03"]),
    (codex_runner, ["u-03"]),
    (codex_task_builder, ["u-03"]),
    (generate_corpus, []),
    (qa_reports, ["validate"]),
    (serve_site, []),
    (validate_content, []),
)


def _log_events(log_directory: Path) -> list[str]:
    """Read event names from the one command log created by a test."""

    log_paths = list(log_directory.glob("*.jsonl"))
    assert len(log_paths) == 1
    return [
        cast("str", json.loads(line)["event"])
        for line in log_paths[0].read_text(encoding="utf-8").splitlines()
    ]


def test_executable_conversion_tool_inventory_is_explicit() -> None:
    """Every Python conversion entry point must remain in the logging inventory."""

    conversion_directory = Path(__file__).parents[1] / "conversion"
    executable_tools = {
        path.name
        for path in conversion_directory.glob("*.py")
        if 'if __name__ == "__main__":' in path.read_text(encoding="utf-8")
    }
    assert executable_tools == EXECUTABLE_TOOL_NAMES


@pytest.mark.parametrize(("module", "required_arguments"), ARGUMENT_PARSER_CASES)
def test_conversion_tool_parsers_accept_shared_logging_options(
    module: ModuleType,
    required_arguments: list[str],
    tmp_path: Path,
) -> None:
    """Every argparse-based tool must expose the shared logging controls."""

    parser_factory = cast(
        "Callable[[], argparse.ArgumentParser]",
        vars(module)["_argument_parser"],
    )
    arguments = parser_factory().parse_args(
        [
            *required_arguments,
            "--log-level",
            "DEBUG",
            "--log-directory",
            str(tmp_path),
        ]
    )
    assert arguments.log_level == "DEBUG"
    assert arguments.log_directory == tmp_path


def test_generate_corpus_main_preserves_stdout_and_logs_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A successful command must retain stdout while recording lifecycle events."""

    monkeypatch.setattr(generate_corpus, "generate", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate-corpus", "--log-directory", str(tmp_path)],
    )

    assert generate_corpus.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "generated 382 criterion packages and the 873-page inventory\n"
    assert _log_events(tmp_path) == ["command.started", "command.completed"]


def test_generate_corpus_main_logs_an_unhandled_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The runtime logger context must record exceptions that retain traceback behavior."""

    def fail_generation() -> None:
        message = "source PDF is unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(generate_corpus, "generate", fail_generation)
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate-corpus", "--log-directory", str(tmp_path)],
    )

    with pytest.raises(RuntimeError, match="source PDF is unavailable"):
        generate_corpus.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert _log_events(tmp_path) == ["command.started", "runtime.unhandled_exception"]


@pytest.mark.parametrize("origin", ["", "https://guide.example"])
def test_build_content_main_logs_handled_failure(
    origin: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An error converted to exit code one must be written as a failed command event."""

    def fail_build(*, base_path: str, workers: int, site_origin: str) -> list[Path]:
        assert site_origin == origin
        assert workers == TEST_WORKER_COUNT
        message = f"invalid base path: {base_path}"
        raise ValueError(message)

    monkeypatch.setattr(build_content, "build", fail_build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-content",
            "--base-path",
            "/invalid",
            *(["--site-origin", origin] if origin else []),
            "--workers",
            str(TEST_WORKER_COUNT),
            "--log-directory",
            str(tmp_path),
        ],
    )

    assert build_content.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid base path: /invalid" in captured.err
    assert _log_events(tmp_path) == ["command.started", "command.failed"]


@pytest.mark.parametrize("origin", ["", "https://guide.example"])
def test_build_sites_bundle_main_uses_shared_logging_configuration(
    origin: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bundle command must honor shared logging arguments."""

    forwarded_workers: list[int] = []

    def fake_build_sites_bundle(*, workers: int, site_origin: str) -> list[Path]:
        assert site_origin == origin
        forwarded_workers.append(workers)
        return [tmp_path / "index.html"]

    monkeypatch.setattr(
        build_sites_bundle,
        "build_sites_bundle",
        fake_build_sites_bundle,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-sites-bundle",
            *(["--site-origin", origin] if origin else []),
            "--workers",
            str(TEST_WORKER_COUNT),
            "--log-directory",
            str(tmp_path),
        ],
    )

    assert build_sites_bundle.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "generated 1 Sites deployment artifacts\n"
    assert forwarded_workers == [TEST_WORKER_COUNT]
    assert _log_events(tmp_path) == ["command.started", "command.completed"]


def test_validate_content_main_forwards_worker_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The validation command must pass its exact worker count to the validator."""

    forwarded_workers: list[int] = []

    def fake_validate_repository(*, root: Path, release: bool, workers: int) -> list[object]:
        assert root.is_dir()
        assert not release
        forwarded_workers.append(workers)
        return []

    monkeypatch.setattr(validate_content, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(validate_content, "validate_repository", fake_validate_repository)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate-content",
            "--workers",
            str(TEST_WORKER_COUNT),
            "--report",
            str(tmp_path / "report.json"),
            "--log-directory",
            str(tmp_path / "logs"),
        ],
    )

    assert validate_content.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "canonical corpus validation passed\n"
    assert forwarded_workers == [TEST_WORKER_COUNT]
