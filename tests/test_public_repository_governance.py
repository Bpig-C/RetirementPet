"""Freeze the public collaboration and licensing files kept in Git."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_project_license_is_detectable_mit_text():
    license_text = _text("LICENSE")
    assert license_text.startswith("MIT License\n\nCopyright (c) 2026 Bpig-C\n")
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text
    asset_licenses = _text("assets/LICENSE.md")
    assert "icons/retirement_pet.png" in asset_licenses
    assert "包内独立许可" in asset_licenses


def test_fork_ci_is_read_only_secret_free_and_immutable_action_pinned():
    workflow = _text(".github/workflows/tests.yml")
    assert re.search(r"(?m)^\s*pull_request:\s*$", workflow)
    assert "pull_request_target" not in workflow
    assert "secrets." not in workflow
    assert re.search(r"(?m)^permissions:\s*\n\s+contents:\s*read\s*$", workflow)
    assert re.search(r"(?m)^\s{2}tests:\s*\n\s{4}name:\s*tests\s*$", workflow)
    pinned_actions = re.findall(r"(?m)^\s*uses:\s*[^@\s]+@([^\s#]+)", workflow)
    assert pinned_actions
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in pinned_actions)
    assert "persist-credentials: false" in workflow


def test_contribution_files_freeze_review_test_privacy_and_rights_requirements():
    contributing = _text("CONTRIBUTING.md")
    pull_request = _text(".github/PULL_REQUEST_TEMPLATE.md")
    character_form = _text(".github/ISSUE_TEMPLATE/character_pack.yml")
    codeowners = _text(".github/CODEOWNERS")

    assert "`main` 只接受 Pull Request，不直接推送" in contributing
    assert "合并前必须通过所需检查和代码审阅" in contributing
    assert "原始来源" in contributing and "再分发" in contributing
    assert "个人路径" in contributing and "tasks.db" in contributing
    assert "自提交时起按项目" in contributing
    assert "MIT License 许可给项目及所有接收者" in contributing
    assert "内容资产与许可" in pull_request
    assert "我已运行相关测试" in pull_request
    assert "我没有上传 `tasks.db`" in pull_request
    assert "按项目 MIT License 许可" in pull_request
    for required_id in ("source", "license", "ai_disclosure", "inventory"):
        assert f"id: {required_id}" in character_form
    assert character_form.count("required: true") >= 10
    assert codeowners.strip() == (
        "# Repository-wide review ownership. Enforcement requires the main branch\n"
        "# ruleset to require pull requests and Code Owner approval.\n"
        "* @Bpig-C"
    )


def test_private_reporting_and_public_export_policy_have_documented_entry_points():
    security = _text("SECURITY.md")
    document_hub = _text("docs/README.md")
    notices = _text("THIRD_PARTY_NOTICES.md")

    assert "/security/advisories/new" in security
    assert "CONTRIBUTING.md" in document_hub
    assert "SECURITY.md" in document_hub
    assert "LICENSING.md" in document_hub
    assert "THIRD_PARTY_NOTICES.md" in document_hub
    assert "PySide6" in notices and "Qt" in notices
    spec = _text("RetirementPet.spec")
    assert "ROOT / 'LICENSE'" in spec
    assert "ROOT / 'THIRD_PARTY_NOTICES.md'" in spec
    assert "ROOT / 'docs' / 'LICENSING.md'" in spec
