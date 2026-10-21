"""Offline guards tying SAM timing to the sync lease and AI/KMS wiring."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra/template.yaml"
SYNC_REPOSITORY = ROOT / "backend/app/repositories/dynamodb_sync.py"


class _Loader(yaml.SafeLoader):
    pass


def _intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _intrinsic)


def _template() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=_Loader)


def _lease_seconds() -> int:
    match = re.search(
        r"\(now \+ timedelta\(minutes=(\d+)\)\)", SYNC_REPOSITORY.read_text()
    )
    assert match, "lease duration not found in DynamoSyncRepository.claim"
    return int(match.group(1)) * 60


def test_lease_outlasts_worker_and_queue_visibility_has_headroom() -> None:
    resources = _template()["Resources"]
    timeout = resources["SyncWorkerFunction"]["Properties"]["Timeout"]
    visibility = resources["SyncQueue"]["Properties"]["VisibilityTimeout"]

    assert _lease_seconds() > timeout
    assert visibility >= 6 * timeout


def test_model_id_parameters_default_blank_and_reach_both_functions() -> None:
    template = _template()
    for parameter in ("OpenAiModelId", "AnthropicModelId", "GeminiModelId"):
        assert template["Parameters"][parameter]["Default"] == ""
    for function in ("ApiFunction", "SyncWorkerFunction"):
        variables = template["Resources"][function]["Properties"]["Environment"][
            "Variables"
        ]
        assert variables["AI_MODEL_OPENAI"] == {"Ref": "OpenAiModelId"}
        assert variables["AI_MODEL_ANTHROPIC"] == {"Ref": "AnthropicModelId"}
        assert variables["AI_MODEL_GEMINI"] == {"Ref": "GeminiModelId"}


def test_cors_allows_put() -> None:
    cors = _template()["Resources"]["HttpApi"]["Properties"]["CorsConfiguration"]
    assert "PUT" in cors["AllowMethods"]


def _kms_statements(function: str) -> list[dict]:
    policy = _template()["Resources"][function]["Properties"]["Policies"][0]
    return [
        statement
        for statement in policy["Statement"]
        if any(a.startswith("kms:") for a in _actions(statement))
    ]


def _actions(statement: dict) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


def test_kms_access_is_scoped_by_credential_type() -> None:
    expected = {
        "ApiFunction": {"kms:GenerateDataKey", "kms:Decrypt"},
        "SyncWorkerFunction": {"kms:Decrypt"},
    }
    for function, actions in expected.items():
        statements = _kms_statements(function)
        types = set()
        for statement in statements:
            assert set(_actions(statement)) == actions
            condition = statement["Condition"]["StringEquals"]
            types.add(condition["kms:EncryptionContext:credential_type"])
        assert types == {"gmail_refresh_token", "ai_api_key"}
    dispatcher = _template()["Resources"]["SyncDispatcherFunction"]["Properties"]
    assert "kms" not in str(dispatcher["Policies"])
