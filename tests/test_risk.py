from __future__ import annotations

import pytest

from hoca.risk import (
    RiskClassification,
    RiskLevel,
    classify_description,
    classify_paths,
    classify_task,
)


class TestRiskLevel:
    def test_values(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.UNKNOWN.value == "unknown"

    def test_string_equality(self):
        assert RiskLevel.LOW == "low"
        assert RiskLevel.HIGH == "high"


class TestClassifyPaths:
    def test_empty_paths_returns_unknown(self):
        assert classify_paths([]) == RiskLevel.UNKNOWN

    @pytest.mark.parametrize(
        "paths",
        [
            ["README.md"],
            ["docs/setup.md", "CHANGELOG.md"],
            ["README.rst"],
            [".gitignore"],
            ["CONTRIBUTING.md"],
        ],
    )
    def test_doc_only_returns_low(self, paths: list[str]):
        assert classify_paths(paths) == RiskLevel.LOW

    @pytest.mark.parametrize(
        "paths",
        [
            ["tests/test_widget.py"],
            ["test/test_foo.py", "tests/test_bar.py"],
            ["src/utils/test_helpers.test.ts"],
            ["lib/widget_test.py"],
        ],
    )
    def test_test_only_returns_low(self, paths: list[str]):
        assert classify_paths(paths) == RiskLevel.LOW

    def test_example_only_returns_low(self):
        assert classify_paths(["examples/demo.py"]) == RiskLevel.LOW

    @pytest.mark.parametrize(
        "paths",
        [
            ["src/utils/format.py"],
            ["lib/widget.py"],
            ["app/components/Button.tsx"],
        ],
    )
    def test_source_changes_return_medium(self, paths: list[str]):
        assert classify_paths(paths) == RiskLevel.MEDIUM

    def test_mixed_doc_and_source_returns_medium(self):
        assert classify_paths(["README.md", "src/app.py"]) == RiskLevel.MEDIUM

    @pytest.mark.parametrize(
        "path",
        [
            "src/auth/middleware.py",
            "lib/authentication.py",
            "app/authorization/policies.py",
            "services/oauth/provider.py",
            "src/login_handler.py",
            "security/audit.py",
            "lib/encrypt_util.py",
            "core/crypto.py",
            "payments/stripe_webhook.py",
            "src/billing/invoice.py",
            "app/subscription_manager.py",
            "src/permissions/check.py",
            "lib/rbac/roles.py",
            "db/migrations/001_init.sql",
            "src/migrate_users.py",
            "alembic/versions/abc123.py",
            "scripts/destroy_old_data.sh",
            "tools/teardown_env.sh",
            "ops/nuke_cache.py",
            "package.json",
            "yarn.lock",
            "requirements.txt",
            "go.mod",
            "Cargo.lock",
            "poetry.lock",
            ".github/workflows/ci.yml",
            "Dockerfile",
            "infra/main.tf",
            "scripts/deploy_prod.sh",
        ],
    )
    def test_high_risk_paths(self, path: str):
        assert classify_paths([path]) == RiskLevel.HIGH

    def test_safe_source_not_high(self):
        assert classify_paths(["src/utils/format.py"]) != RiskLevel.HIGH


class TestClassifyDescription:
    def test_empty_returns_unknown(self):
        assert classify_description("") == RiskLevel.UNKNOWN
        assert classify_description("   ") == RiskLevel.UNKNOWN

    @pytest.mark.parametrize(
        "desc",
        [
            "Add auth middleware",
            "Fix payment processing bug",
            "Update billing logic",
            "Run database migration",
            "Rotate secret keys",
            "Add encryption to user data",
            "Update permission checks",
            "Deploy infrastructure changes",
            "Delete old user records",
        ],
    )
    def test_high_risk_descriptions(self, desc: str):
        assert classify_description(desc) == RiskLevel.HIGH

    def test_generic_description_returns_unknown(self):
        assert classify_description("Fix typo in README") == RiskLevel.UNKNOWN
        assert classify_description("Add unit tests") == RiskLevel.UNKNOWN


class TestClassifyTask:
    def test_no_inputs_returns_unknown(self):
        result = classify_task()
        assert result.level == RiskLevel.UNKNOWN
        assert result.auto_mergeable is False

    def test_doc_only_task(self):
        result = classify_task(
            description="Fix typo in README",
            changed_paths=["README.md"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    def test_source_change_task(self):
        result = classify_task(
            description="Refactor widget formatting",
            changed_paths=["src/widget.py"],
        )
        assert result.level == RiskLevel.MEDIUM
        assert result.auto_mergeable is False

    def test_high_risk_from_paths(self):
        result = classify_task(
            description="Update helper",
            changed_paths=["src/auth/middleware.py"],
        )
        assert result.level == RiskLevel.HIGH
        assert result.auto_mergeable is False

    def test_high_risk_from_description(self):
        result = classify_task(
            description="Fix auth token refresh",
            changed_paths=["README.md"],
        )
        assert result.level == RiskLevel.HIGH
        assert result.auto_mergeable is False

    def test_unknown_is_not_auto_mergeable(self):
        result = classify_task(description="Do something")
        assert result.level == RiskLevel.UNKNOWN
        assert result.auto_mergeable is False

    def test_medium_is_not_auto_mergeable(self):
        result = classify_task(changed_paths=["src/app.py"])
        assert result.level == RiskLevel.MEDIUM
        assert result.auto_mergeable is False

    def test_high_is_never_auto_mergeable(self):
        result = classify_task(changed_paths=["payments/charge.py"])
        assert result.level == RiskLevel.HIGH
        assert result.auto_mergeable is False

    def test_result_has_reason(self):
        result = classify_task(changed_paths=["README.md"])
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_classification_is_frozen(self):
        result = classify_task(changed_paths=["README.md"])
        with pytest.raises(AttributeError):
            result.level = RiskLevel.HIGH  # type: ignore[misc]

    def test_description_escalates_path_risk(self):
        result = classify_task(
            description="Add payment processing",
            changed_paths=["src/utils/format.py"],
        )
        assert result.level == RiskLevel.HIGH

    def test_test_additions_are_low_risk(self):
        result = classify_task(
            description="Add unit tests for widget",
            changed_paths=["tests/test_widget.py"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    # -- 34.2  Low-risk examples ------------------------------------------------

    def test_low_doc_only_changes(self):
        result = classify_task(
            description="Improve installation docs",
            changed_paths=["docs/install.md", "docs/quickstart.rst"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    def test_low_small_readme_update(self):
        result = classify_task(
            description="Add badge to README",
            changed_paths=["README.md"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    def test_low_simple_typo_fix_in_docs(self):
        result = classify_task(
            description="Fix typo in contributing guide",
            changed_paths=["CONTRIBUTING.md"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    def test_low_narrow_unit_test_addition(self):
        result = classify_task(
            description="Add test for edge case in parser",
            changed_paths=["tests/test_parser.py"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    def test_low_non_production_example_update(self):
        result = classify_task(
            description="Update example script",
            changed_paths=["examples/basic_usage.py", "examples/README.md"],
        )
        assert result.level == RiskLevel.LOW
        assert result.auto_mergeable is True

    # -- 34.3  Medium-risk examples ---------------------------------------------

    def test_medium_small_source_change(self):
        result = classify_task(
            description="Rename helper function",
            changed_paths=["src/utils/helpers.py"],
        )
        assert result.level == RiskLevel.MEDIUM
        assert result.auto_mergeable is False

    def test_medium_bug_fix_with_tests(self):
        result = classify_task(
            description="Fix off-by-one in pagination",
            changed_paths=["src/pagination.py", "tests/test_pagination.py"],
        )
        assert result.level == RiskLevel.MEDIUM
        assert result.auto_mergeable is False

    def test_medium_refactor_single_module(self):
        result = classify_task(
            description="Refactor formatting utilities",
            changed_paths=["src/utils/format.py"],
        )
        assert result.level == RiskLevel.MEDIUM
        assert result.auto_mergeable is False

    def test_medium_non_breaking_config_change(self):
        result = classify_task(
            description="Update linter settings",
            changed_paths=["pyproject.toml"],
        )
        assert result.level == RiskLevel.MEDIUM
        assert result.auto_mergeable is False
