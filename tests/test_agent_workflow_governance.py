"""Cycle handoff governance (CR-001 / RR2-01..04).

契约见 docs/agent-workflow/WORKFLOW.md 第 4 节。三类检查：

1. 身份校验（纯函数，Git 查询可注入）：``implementation_head`` 与
   ``handoff_head`` 必须存在、完整 40 位、可解析，且 implementation_head
   是 handoff_head 的祖先。
2. 登记信封（RR2-02 / RR3-01）：READY_FOR_REVIEW 的登记态要求 HEAD 的
   直接父提交恰为 handoff_head（尾部恰一个登记提交），且
   ``handoff_head..HEAD`` 只能触碰 CURRENT_STATE 与其中指向的本周期
   execution report 两个精确文件。
3. 一致性（RR2-03）：执行报告首个 YAML 头的身份必须与 CURRENT_STATE
   相同。

公开快照（根目录存在 ``PUBLIC_SNAPSHOT_MANIFEST.json``）不含内部提交
对象：身份 ancestry 集成检查显式 SKIP；纯校验器反例仍然运行。所有
隔离仓库反例自带本地 Git 身份，不依赖调用环境配置。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "docs" / "agent-workflow" / "CURRENT_STATE.md"
SNAPSHOT_MARKER = ROOT / "PUBLIC_SNAPSHOT_MANIFEST.json"
PROBE_IDENTITY = ("-c", "user.name=governance-probe",
                  "-c", "user.email=probe@invalid")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


def _parse_yaml_fields(text: str, keys: tuple[str, ...]) -> dict:
    match = re.search(r"```yaml\n(.*?)```", text, re.S)
    assert match, "expected a fenced yaml block"
    block = match.group(1)
    fields = {}
    for key in keys:
        found = re.search(rf"^{key}:\s*(\S+)", block, re.M)
        fields[key] = found.group(1) if found else None
    return fields


def _execution_report_path() -> Path:
    state = _parse_yaml_fields(
        STATE_PATH.read_text(encoding="utf-8"), ("execution_report",))
    rel = state["execution_report"]
    assert rel, "CURRENT_STATE must point at the cycle execution report"
    return (ROOT / rel).resolve()


def _identity_problems(fields: dict, repo: Path) -> list[str]:
    """Field validity + ancestry; no envelope checks (pure identity)."""
    problems: list[str] = []
    resolved: dict[str, str] = {}
    for key in ("implementation_head", "handoff_head"):
        value = fields.get(key)
        if not value or value == "null":
            problems.append(f"{key} missing")
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            problems.append(f"{key} not 40-hex: {value!r}")
            continue
        if _git(repo, "cat-file", "-e", f"{value}^{{commit}}").returncode != 0:
            problems.append(f"{key} unresolvable: {value}")
            continue
        resolved[key] = value
    if len(resolved) == 2 and _git(
            repo, "merge-base", "--is-ancestor", resolved["implementation_head"],
            resolved["handoff_head"]).returncode != 0:
        problems.append("implementation_head is not an ancestor of "
                        "handoff_head")
    return problems


def _envelope_problems(
        handoff_head: str, execution_report: str | None,
        repo: Path) -> list[str]:
    """Registration envelope with an exact two-file allowlist."""
    problems: list[str] = []
    parent = _git(repo, "rev-parse", "HEAD^").stdout.strip()
    if parent != handoff_head:
        problems.append(
            f"HEAD's direct parent {parent[:12]} is not handoff_head "
            f"{handoff_head[:12]} (expected exactly one registration commit)")
    if not execution_report or not re.fullmatch(
            r"docs/agent-workflow/cycles/[A-Za-z0-9._-]+/"
            r"EXECUTION(?:-[A-Za-z0-9._-]+)?\.md", execution_report):
        problems.append(
            f"execution_report is not a safe cycle report path: "
            f"{execution_report!r}")
        return problems
    allowed = {
        "docs/agent-workflow/CURRENT_STATE.md",
        execution_report,
    }
    diff = _git(repo, "diff", "--name-only", f"{handoff_head}..HEAD")
    changed = {line for line in diff.stdout.splitlines() if line}
    if "docs/agent-workflow/CURRENT_STATE.md" not in changed:
        problems.append("registration commit does not update CURRENT_STATE.md")
    smuggled = sorted(changed - allowed)
    if smuggled:
        problems.append(
            "registration commit touches paths outside the exact allowlist: "
            + ", ".join(smuggled))
    return problems


def _registration_problems(fields: dict, repo: Path) -> list[str]:
    identity = _identity_problems(fields, repo)
    if identity:
        return identity
    return identity + _envelope_problems(
        fields["handoff_head"], fields.get("execution_report"), repo)


# -- isolated scenario repos (explicit local identity; no global config) ------


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    assert _git(path, "init", "-q").returncode == 0
    _commit(path, "base", {"README.md": "probe\n"})


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    result = _git(repo, *PROBE_IDENTITY, "commit", "-q", "-m", message)
    assert result.returncode == 0, result.stderr
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def registered_repo(tmp_path):
    """A minimal repo in the fully registered READY_FOR_REVIEW shape."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "implementation", {"src/feature.py": "print('v1')\n"})
    handoff = _commit(repo, "handoff", {
        "docs/agent-workflow/cycles/001/EXECUTION.md": "# report\n"})
    _commit(repo, "registration", {
        "docs/agent-workflow/CURRENT_STATE.md": "status: READY_FOR_REVIEW\n"})
    implementation = _git(repo, "rev-parse", "HEAD^^").stdout.strip()
    return repo, {"implementation_head": implementation,
                  "handoff_head": handoff,
                  "execution_report":
                      "docs/agent-workflow/cycles/001/EXECUTION.md"}


# -- the real repository gates -------------------------------------------------


def test_current_state_registration_is_machine_verifiable():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    if SNAPSHOT_MARKER.exists():
        pytest.skip("public snapshot carries no internal commit objects")
    fields = _parse_yaml_fields(
        STATE_PATH.read_text(encoding="utf-8"),
        ("status", "implementation_head", "handoff_head",
         "execution_report"))
    if fields["status"] != "READY_FOR_REVIEW":
        pytest.skip("registration identity is enforced only at "
                    f"READY_FOR_REVIEW (current status: {fields['status']})")
    assert _registration_problems(fields, ROOT) == []


def test_execution_report_identity_matches_current_state():
    state = _parse_yaml_fields(
        STATE_PATH.read_text(encoding="utf-8"),
        ("status", "implementation_head", "handoff_head"))
    if state["status"] != "READY_FOR_REVIEW":
        pytest.skip("report consistency is enforced only at "
                    f"READY_FOR_REVIEW (current status: {state['status']})")
    report = _parse_yaml_fields(
        _execution_report_path().read_text(encoding="utf-8"),
        ("implementation_commit", "handoff_head"))
    assert report["implementation_commit"] == state["implementation_head"]
    assert report["handoff_head"] == state["handoff_head"]


# -- validator counterexamples on isolated repos -------------------------------


def test_identity_validator_rejects_missing_malformed_unresolvable(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert _identity_problems({"implementation_head": None,
                               "handoff_head": None}, repo) == [
        "implementation_head missing", "handoff_head missing"]
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    malformed = {"implementation_head": head[:7], "handoff_head": head}
    problems = _identity_problems(malformed, repo)
    assert f"implementation_head not 40-hex: {head[:7]!r}" in problems
    fake = {"implementation_head": "0" * 40, "handoff_head": "1" * 40}
    assert all("unresolvable" in p for p in _identity_problems(fake, repo))


def test_identity_validator_rejects_non_ancestor(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, "implementation", {"src/x.py": "1\n"})
    handoff = _commit(repo, "handoff", {"docs/report.md": "r\n"})
    # a real, resolvable commit that is NOT on the branch (explicit identity:
    # never depends on the caller's global git config)
    dangling = _git(repo, *PROBE_IDENTITY, "commit-tree",
                    f"{handoff}^{{tree}}", "-m", "not-on-branch"
                    ).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", dangling)
    problems = _identity_problems(
        {"implementation_head": dangling, "handoff_head": handoff}, repo)
    assert problems == ["implementation_head is not an ancestor of "
                        "handoff_head"]


def test_registration_envelope_accepts_pure_documentation_tail(
        registered_repo):
    repo, fields = registered_repo
    assert _registration_problems(fields, repo) == []


def test_registration_envelope_rejects_smuggled_production_change(
        registered_repo):
    repo, fields = registered_repo
    handoff = fields["handoff_head"]
    # reset the tail and rebuild it smuggling src/
    _git(repo, "reset", "-q", "--hard", handoff)
    smuggled = _commit(repo, "registration-with-src", {
        "docs/agent-workflow/CURRENT_STATE.md": "status: READY_FOR_REVIEW\n",
        "src/smuggled.py": "print('unreviewed')\n"})
    assert smuggled != handoff
    problems = _registration_problems(fields, repo)
    assert any("outside the exact allowlist" in p for p in problems), problems


def test_registration_envelope_rejects_workflow_rule_rewrite(
        registered_repo):
    repo, fields = registered_repo
    handoff = fields["handoff_head"]
    _git(repo, "reset", "-q", "--hard", handoff)
    _commit(repo, "registration-with-rule-rewrite", {
        "docs/agent-workflow/CURRENT_STATE.md":
            "status: READY_FOR_REVIEW\n",
        "docs/agent-workflow/WORKFLOW.md": "weakened\n",
        "docs/agent-workflow/skills/controller/SKILL.md": "weakened\n",
    })
    problems = _registration_problems(fields, repo)
    assert any("outside the exact allowlist" in p for p in problems), problems
    assert any("WORKFLOW.md" in p for p in problems), problems
    assert any("skills/controller/SKILL.md" in p for p in problems), problems


def test_registration_envelope_rejects_extra_tail_commit(registered_repo):
    repo, fields = registered_repo
    _commit(repo, "extra-tail", {
        "docs/agent-workflow/CURRENT_STATE.md":
            "status: READY_FOR_REVIEW\n# extra commit\n"})
    problems = _registration_problems(fields, repo)
    assert any("not handoff_head" in p for p in problems), problems
