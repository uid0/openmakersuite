"""AC-11 (oms-2gi): CI/CD release gating.

These tests assert the structural invariants in our GitHub Actions workflows
that prevent image and chart publishing when CI checks fail. They are
intentionally pure YAML assertions — there is no Django state involved — but
they live here because the backend test suite is what CI runs and what
pre-commit hooks gate.

If you change `.github/workflows/{ci,docker-build,release}.yml`, run this
suite to make sure you haven't accidentally re-introduced an unsafe publish
path.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.exists(), f"workflow not found: {path}"
    with path.open() as fh:
        # PyYAML interprets the bare key `on:` as the boolean True. Load and
        # then normalize so callers can use the string key.
        data = yaml.safe_load(fh)
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


@pytest.fixture(scope="module")
def docker_build_yaml():
    return _load_workflow("docker-build.yml")


@pytest.fixture(scope="module")
def release_yaml():
    return _load_workflow("release.yml")


@pytest.fixture(scope="module")
def ci_yaml():
    return _load_workflow("ci.yml")


class TestDockerBuildGating:
    """`docker-build.yml` must only publish when CI succeeded."""

    def test_only_workflow_run_trigger(self, docker_build_yaml):
        # Image/chart publishing must NOT have a `push` or `pull_request`
        # trigger — those bypass CI entirely. The only allowed trigger is
        # `workflow_run`, which we then gate on conclusion == 'success'.
        triggers = docker_build_yaml["on"]
        assert set(triggers.keys()) == {"workflow_run"}, (
            "docker-build.yml must only trigger on workflow_run; found triggers: "
            f"{sorted(triggers.keys())}"
        )

    def test_workflow_run_targets_ci(self, docker_build_yaml):
        wfr = docker_build_yaml["on"]["workflow_run"]
        assert wfr["workflows"] == [
            "CI"
        ], f"workflow_run must depend on the CI workflow, got {wfr['workflows']}"
        assert "completed" in wfr["types"]

    def test_determine_tags_requires_ci_success(self, docker_build_yaml):
        # The first job (which gates everything via `needs:`) must require
        # workflow_run.conclusion == 'success'. Any failure / cancellation /
        # timeout of CI must skip the entire publish chain.
        jobs = docker_build_yaml["jobs"]
        gate_job = jobs["determine-tags"]
        if_clause = gate_job["if"].strip()
        assert "workflow_run.conclusion == 'success'" in if_clause, (
            "determine-tags job must gate on workflow_run.conclusion == 'success'; "
            f"got: {if_clause!r}"
        )
        # Defensive: no path that skips the conclusion check.
        assert "github.event_name == 'push'" not in if_clause, (
            "determine-tags must not have a push-event escape hatch — that "
            "bypasses CI gating (AC-11)."
        )

    def test_publish_jobs_depend_on_determine_tags(self, docker_build_yaml):
        # Every job that pushes images or charts must transitively depend on
        # determine-tags so the CI gate cascades.
        jobs = docker_build_yaml["jobs"]
        for job_name in ("build-backend", "build-frontend", "publish-helm"):
            job = jobs[job_name]
            needs = job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            assert "determine-tags" in needs, (
                f"job {job_name} must `needs: determine-tags` so the CI gate "
                f"cascades to it; got needs={needs}"
            )


class TestReleaseGating:
    """`release.yml` must only cut releases when CI succeeded."""

    def test_check_prerequisites_gates_workflow_run(self, release_yaml):
        gate = release_yaml["jobs"]["check-prerequisites"]
        if_clause = gate["if"]
        assert "workflow_run.conclusion == 'success'" in if_clause, (
            "release.yml's check-prerequisites must gate workflow_run paths on "
            f"conclusion == 'success'; got: {if_clause!r}"
        )

    def test_workflow_dispatch_verifies_ci(self, release_yaml):
        # Manual dispatch is an allowed trigger, but it must verify CI is green
        # on main before proceeding (otherwise an admin could ship a broken build).
        steps = release_yaml["jobs"]["check-prerequisites"]["steps"]
        verify_steps = [
            s for s in steps if "Verify" in s.get("name", "") and "CI" in s.get("name", "")
        ]
        assert verify_steps, (
            "release.yml workflow_dispatch path must include a step that verifies "
            "CI is green on main before releasing (AC-11)."
        )
        verify = verify_steps[0]
        assert verify.get("if") == "github.event_name == 'workflow_dispatch'"
        run_script = verify.get("run", "")
        # The check must hard-fail when CI isn't green.
        assert "exit 1" in run_script, (
            "workflow_dispatch CI verification must exit non-zero when CI is "
            "not green; otherwise the gate is decorative."
        )


class TestCIWorkflow:
    """`ci.yml`'s aggregator job must reflect every gate AC-11 enumerates."""

    REQUIRED_GATES = {
        # AC-11 enumerates: linting, unit tests, coverage gates, frontend build,
        # Docker build, manifest validation, Helm lint, template rendering.
        # The first three live in backend-tests; frontend build lives in
        # frontend-tests; Docker build lives in docker-build; security covers
        # bandit/secret/dep scans and is part of the "all checks" gate.
        "backend-tests",
        "frontend-tests",
        "docker-build",
        "security",
    }

    def test_ci_complete_aggregates_required_gates(self, ci_yaml):
        ci_complete = ci_yaml["jobs"]["ci-complete"]
        needs = ci_complete.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        missing = self.REQUIRED_GATES - set(needs)
        assert not missing, (
            f"ci-complete must depend on every gating job; missing: {sorted(missing)}. "
            "If a check is removed, AC-11 must be re-evaluated."
        )

    def test_ci_complete_fails_when_any_gate_fails(self, ci_yaml):
        # The aggregator runs with `if: always()`; the protective check must
        # explicitly verify every needed job's `result == 'success'` so a
        # failed gate is propagated to consumers (release.yml, docker-build).
        ci_complete = ci_yaml["jobs"]["ci-complete"]
        run_steps = [s for s in ci_complete["steps"] if "run" in s]
        joined = "\n".join(s["run"] for s in run_steps)
        for job in self.REQUIRED_GATES:
            assert f"needs.{job}.result" in joined, (
                f"ci-complete's check script must inspect needs.{job}.result so "
                f"a {job} failure blocks publishing."
            )
