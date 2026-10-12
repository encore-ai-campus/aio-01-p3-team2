"""CI promotion blocks incomplete, mixed-run, skipped and altered evidence."""

import pytest
from infra.ci import gate


def test_operational_dispatch_does_not_replace_pending_pipeline():
    import yaml

    workflow = yaml.safe_load((gate.ROOT / ".github/workflows/ikonly-ci.yml").read_text())
    group = workflow["concurrency"]["group"]
    assert "inputs.operation == 'recover'" in group
    assert "inputs.operation == 'oidc-check'" in group
    assert "'operations' || 'pipeline'" in group
    assert workflow["concurrency"]["cancel-in-progress"] is False
    for job in ("deploy", "operations"):
        assert workflow["jobs"][job]["concurrency"] == {
            "group": "ikonly-demo-lifecycle",
            "cancel-in-progress": False,
        }


def test_profile_selection_preserves_pytest_builtin_ignore():
    from types import SimpleNamespace

    import conftest
    from backend.testing.test_profiles import ROOT

    path = ROOT / "backend/tests/integration/test_production_images.py"
    assert (
        conftest.pytest_ignore_collect(path, SimpleNamespace(getoption=lambda _: "postgres"))
        is None
    )
    assert conftest.pytest_ignore_collect(path, SimpleNamespace(getoption=lambda _: "core")) is True


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    for name, value in {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(name, value)
    images = {}
    for kind in ("application", "web"):
        file = tmp_path / (kind + ".tar")
        file.write_bytes(kind.encode())
        images[kind] = {
            "tar": file.name,
            "tag": f"ikonly-{kind}:ci",
            "image_id": "sha256:" + "b" * 64,
            "tar_hash": gate.digest(file),
        }
    gate.write(
        tmp_path / "candidate.json",
        {"version": 1, **gate.identity(), "images": images, "fingerprints": gate.fingerprints()},
    )
    for suite in ("quality-core", "postgres", "ui", "verify-images"):
        file = tmp_path / (suite + ".xml")
        file.write_text(
            '<testsuites><testsuite><testcase classname="sample" name="test_ok"/></testsuite></testsuites>'
        )
        gate.write(
            tmp_path / (suite + ".json"),
            {
                "version": 1,
                **gate.identity(),
                "suite": suite,
                "passed_cases": 1,
                "reports": {file.name: gate.digest(file)},
                "candidate_hash": gate.digest(tmp_path / "candidate.json"),
            },
        )
    gate.write(
        tmp_path / "deployment-impact.json",
        {
            "head_sha": "a" * 40,
            "ci_required": True,
            "manual_review_required": True,
            "targets": ["main-api"],
        },
    )
    return tmp_path


def test_complete_ci_evidence_is_verified_without_deployment_authority(candidate):
    gate.approve(
        candidate, {job: {"result": "success"} for job in gate.JOBS}, candidate / "verified.json"
    )
    result = gate.read(candidate / "verified.json")
    assert result["ci_verified"] is True
    assert result["deployment_authorized"] is False
    assert result["manual_review_required"] is True


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "missing"])
def test_required_job_must_succeed(candidate, result):
    jobs = {job: {"result": "success"} for job in gate.JOBS}
    if result == "missing":
        del jobs["postgres"]
    else:
        jobs["postgres"]["result"] = result
    with pytest.raises(ValueError, match="Every required"):
        gate.approve(candidate, jobs, candidate / "verified.json")


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("candidate.json", "source_sha", "c" * 40),
        ("candidate.json", "ci_run_id", "999"),
        ("candidate.json", "ci_attempt", 2),
        ("ui.json", "source_sha", "c" * 40),
        ("ui.json", "passed_cases", 0),
        ("deployment-impact.json", "head_sha", "c" * 40),
    ],
)
def test_changed_identity_or_empty_evidence_is_rejected(candidate, target, field, value):
    item = gate.read(candidate / target)
    item[field] = value
    gate.write(candidate / target, item)
    with pytest.raises(ValueError):
        gate.approve(
            candidate,
            {job: {"result": "success"} for job in gate.JOBS},
            candidate / "verified.json",
        )


@pytest.mark.parametrize("file", ["application.tar", "web.tar", "ui.xml"])
def test_changed_artifact_or_report_is_rejected(candidate, file):
    (candidate / file).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        gate.approve(
            candidate,
            {job: {"result": "success"} for job in gate.JOBS},
            candidate / "verified.json",
        )


def test_candidate_paths_cannot_escape_bundle(candidate):
    item = gate.read(candidate / "candidate.json")
    item["images"]["web"]["tar"] = "../web.tar"
    gate.write(candidate / "candidate.json", item)
    with pytest.raises(ValueError, match="artifact path"):
        gate.verify_bundle(candidate)


@pytest.mark.parametrize(
    "content",
    ["", '<testcase name="x"><skipped/></testcase>', '<testcase name="x"><failure/></testcase>'],
)
def test_empty_skip_failed_reports_cannot_be_evidence(tmp_path, content):
    file = tmp_path / "report.xml"
    file.write_text(f"<testsuites><testsuite>{content}</testsuite></testsuites>")
    with pytest.raises(ValueError):
        gate.report_cases(file)


def test_function_coverage_is_required(tmp_path, monkeypatch):
    file = tmp_path / "report.xml"
    file.write_text('<testsuites><testcase classname="sample" name="test_ok"/></testsuites>')
    with pytest.raises(ValueError, match="functions missing"):
        gate.evidence("quality-core", [file], tmp_path / "quality-core.json")


def test_missing_parameter_is_rejected_even_when_function_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    gate.write(
        tmp_path / "infra/test-suites.json",
        {"python": [{"file": "sample.py", "suite": "core", "test_functions": ["test_value"]}]},
    )
    gate.write(
        tmp_path / "quality-core-expected.json",
        [["sample", "test_value[1]"], ["sample", "test_value[2]"]],
    )
    file = tmp_path / "report.xml"
    file.write_text('<testsuites><testcase classname="sample" name="test_value[1]"/></testsuites>')
    with pytest.raises(ValueError, match="parameterized cases missing"):
        gate.evidence("quality-core", [file], tmp_path / "quality-core.json")
