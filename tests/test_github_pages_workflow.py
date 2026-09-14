"""Regression tests for the GitHub Pages deployment workflow."""

from __future__ import annotations

from conversion.common import as_mapping, as_sequence, load_yaml, repository_root


def test_github_pages_workflow_builds_and_deploys_public_site() -> None:
    """The workflow must package and deploy the generated site through Pages."""

    workflow_path = repository_root() / ".github" / "workflows" / "pages-build.yml"
    workflow = load_yaml(workflow_path)
    permissions = as_mapping(workflow["permissions"], location="workflow.permissions")
    assert permissions == {"contents": "read"}
    jobs = as_mapping(workflow["jobs"], location="workflow.jobs")
    assert set(jobs) == {"build", "deploy"}
    build_job = as_mapping(jobs["build"], location="workflow.jobs.build")
    assert as_mapping(build_job["permissions"], location="workflow.jobs.build.permissions") == {
        "contents": "read",
        "pages": "read",
    }
    step_values = as_sequence(build_job["steps"], location="workflow.jobs.build.steps")
    steps = [as_mapping(value, location="workflow.jobs.build.steps[]") for value in step_values]
    action_identifiers = {
        action_identifier
        for step in steps
        if isinstance((action_identifier := step.get("uses")), str)
    }
    assert "actions/checkout@v6" in action_identifiers
    assert "actions/configure-pages@v5" in action_identifiers
    assert "actions/upload-pages-artifact@v5" in action_identifiers
    validation_step = next(
        step for step in steps if step.get("name") == "Validate canonical content"
    )
    assert validation_step.get("run") == "uv run --locked python -m conversion.validate_content"
    workflow_directory = repository_root() / ".github" / "workflows"
    assert all(
        "--release" not in workflow_file.read_text(encoding="utf-8")
        for workflow_file in workflow_directory.glob("*.yml")
    )
    build_step = next(step for step in steps if step.get("name") == "Build GitHub Pages artifact")
    build_command = build_step.get("run")
    assert isinstance(build_command, str)
    assert "--base-path" in build_command
    assert "steps.pages.outputs.base_path" in build_command
    edition_step = next(
        step for step in steps if step.get("name") == "Verify original and revised editions"
    )
    edition_command = edition_step.get("run")
    assert isinstance(edition_command, str)
    assert ".artifacts/build/site/unix/u-01/index.html" in edition_command
    assert ".artifacts/build/site/revised/index.html" in edition_command
    assert ".artifacts/build/site/revised/search/index.html" in edition_command
    assert ".artifacts/build/site/revised/unix/u-67/index.html" in edition_command
    assert ".artifacts/build/site/revised/dataset.json" in edition_command
    upload_step = next(step for step in steps if step.get("name") == "Upload GitHub Pages artifact")
    assert upload_step.get("with") == {
        "path": ".artifacts/build/site",
        "include-hidden-files": True,
    }

    deploy_job = as_mapping(jobs["deploy"], location="workflow.jobs.deploy")
    assert as_mapping(
        deploy_job["permissions"],
        location="workflow.jobs.deploy.permissions",
    ) == {
        "pages": "write",
        "id-token": "write",
    }
    assert deploy_job["needs"] == "build"
    assert deploy_job["environment"] == {
        "name": "github-pages",
        "url": "${{ steps.deployment.outputs.page_url }}",
    }
    deploy_step_values = as_sequence(
        deploy_job["steps"],
        location="workflow.jobs.deploy.steps",
    )
    deploy_steps = [
        as_mapping(value, location="workflow.jobs.deploy.steps[]") for value in deploy_step_values
    ]
    assert any(step.get("uses") == "actions/deploy-pages@v5" for step in deploy_steps)
