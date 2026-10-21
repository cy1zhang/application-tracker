import logging

import pytest
from ai_support import (
    AMBIGUOUS_TEXT,
    MODELS,
    NOW,
    SYNTHETIC_KEY,
    Env,
    FakeCipher,
    StubProvider,
    error,
    extraction,
    interview_extraction,
    parsed,
)
from moto import mock_aws

from app.domain.ai import AiProvider
from app.domain.applications import ApplicationStatus
from app.repositories.dynamodb_ai import DAILY_LIMIT
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.ai_escalation import AiEscalator
from app.services.ai_reconcile import ESCALATION_REASONS
from app.services.email_rules import RuleDecision


@pytest.fixture
def env():
    with mock_aws():
        yield Env()


class Factory:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []

    def __call__(self, provider_name):
        self.calls.append(provider_name)
        return self.provider


def build(env, provider, owner="owner-a", models=MODELS, cipher=None):
    run = env.runs[owner]
    factory = Factory(provider)
    cipher = cipher or FakeCipher()
    escalator = AiEscalator(
        owner_id=owner, generation=run.generation, run_id=run.run_id,
        settings=env.ai, cipher=cipher, provider_factory=factory,
        models=models, clock=lambda: NOW,
    )
    return escalator, factory, cipher


def rule(reason="ambiguous_application", **kwargs):
    kwargs.setdefault("status", ApplicationStatus.INTERVIEWING)
    return RuleDecision(outcome="review_required", reason=reason, **kwargs)


def usage(env, owner="owner-a"):
    return int(env.user(owner).get("ai_requests_today", 0))


@pytest.mark.parametrize("reason", sorted(ESCALATION_REASONS))
def test_each_allowlisted_reason_calls_provider_once(env, reason) -> None:
    env.connect()
    env.app()
    provider = StubProvider(interview_extraction())
    escalator, _, _ = build(env, provider)

    result = escalator.escalate(
        parsed("m1", AMBIGUOUS_TEXT), rule(reason), env.candidates()
    )

    assert len(provider.calls) == 1
    assert result.origin == "ai"
    assert result.rule_reason == reason
    assert result.ai_call.provider == AiProvider.OPENAI
    assert result.ai_call.model == "synthetic-model"
    assert result.ai_call.rule_decision.reason == reason
    assert usage(env) == 1


@pytest.mark.parametrize("decision", [
    RuleDecision(outcome="review_required", reason="forwarded_message"),
    RuleDecision(outcome="review_required", reason="unparseable_message"),
    RuleDecision(outcome="review_required", reason="status_regression"),
    RuleDecision(outcome="review_required", reason="cycle_conflict"),
    RuleDecision(outcome="review_required", reason="missing_application"),
    RuleDecision(outcome="review_required", reason="conflicting_stages"),
    RuleDecision(outcome="review_required", reason="ambiguous_event_identity"),
    RuleDecision(outcome="applied", reason="explicit_event"),
    RuleDecision(outcome="ignored", reason="no_event"),
    RuleDecision(outcome="ignored", reason="no_change"),
    RuleDecision(outcome="applied", reason="ambiguous_application"),
])
def test_other_decisions_never_call_provider(env, decision) -> None:
    env.connect()
    provider = StubProvider(interview_extraction())
    escalator, factory, cipher = build(env, provider)

    result = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), decision, [])

    assert result is decision
    assert provider.calls == [] and factory.calls == [] and cipher.calls == []
    assert usage(env) == 0


@pytest.mark.parametrize("setup", ["no_key", "disabled", "no_model"])
def test_no_runtime_returns_rule_decision_without_note(env, setup) -> None:
    env.connect(ai=setup != "no_key", enabled=setup != "disabled")
    models = {} if setup == "no_model" else MODELS
    provider = StubProvider(interview_extraction())
    escalator, factory, cipher = build(env, provider, models=models)
    decision = rule()

    result = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), decision, [])

    assert result is decision and result.ai_note is None
    assert factory.calls == [] and cipher.calls == []


def test_runtime_and_key_are_loaded_once_per_page(env) -> None:
    env.connect()
    env.app()
    provider = StubProvider(interview_extraction())
    escalator, factory, cipher = build(env, provider)

    for index in range(3):
        escalator.escalate(
            parsed(f"m{index}", AMBIGUOUS_TEXT), rule(), env.candidates()
        )

    assert cipher.calls == [("owner-a", "ai_api_key")]
    assert factory.calls == [AiProvider.OPENAI]
    assert {call[0] for call in provider.calls} == {f"{SYNTHETIC_KEY}-owner-a"}
    assert usage(env) == 3


def test_provider_unavailable_retries_once_then_breaker_opens(env) -> None:
    env.connect()
    provider = StubProvider(error("provider_unavailable"))
    escalator, _, _ = build(env, provider)

    first = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])
    second = escalator.escalate(parsed("m2", AMBIGUOUS_TEXT), rule(), [])

    assert len(provider.calls) == 2
    assert usage(env) == 2
    for result in (first, second):
        assert result.reason == "ambiguous_application"
        assert result.outcome == "review_required"
        assert result.origin == "rules"
        assert result.ai_note == "ai_unavailable"


def test_retry_after_unavailable_can_succeed(env) -> None:
    env.connect()
    env.app()
    provider = StubProvider(error("provider_unavailable"), interview_extraction())
    escalator, _, _ = build(env, provider)

    result = escalator.escalate(
        parsed("m1", AMBIGUOUS_TEXT), rule(), env.candidates()
    )

    assert len(provider.calls) == 2 and usage(env) == 2
    assert result.outcome == "applied" and result.origin == "ai"


def test_rate_limited_is_not_retried_and_opens_breaker(env) -> None:
    env.connect()
    provider = StubProvider(error("rate_limited"))
    escalator, _, _ = build(env, provider)

    first = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])
    second = escalator.escalate(parsed("m2", AMBIGUOUS_TEXT), rule(), [])

    assert len(provider.calls) == 1 and usage(env) == 1
    assert first.ai_note == second.ai_note == "ai_unavailable"


def test_invalid_output_is_not_retried_and_keeps_breaker_closed(env) -> None:
    env.connect()
    provider = StubProvider(error("invalid_output"))
    escalator, _, _ = build(env, provider)

    first = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])
    escalator.escalate(parsed("m2", AMBIGUOUS_TEXT), rule(), [])

    assert len(provider.calls) == 2 and usage(env) == 2
    assert first.reason == "ambiguous_application" and first.origin == "rules"


def test_invalid_key_marks_key_invalid_and_stops_page(env) -> None:
    env.connect()
    provider = StubProvider(error("invalid_key"))
    escalator, _, _ = build(env, provider)

    first = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])
    second = escalator.escalate(parsed("m2", AMBIGUOUS_TEXT), rule(), [])

    assert len(provider.calls) == 1
    assert env.user()["ai_key_status"] == "invalid"
    assert first.ai_note == second.ai_note == "ai_key_invalid"
    assert first.reason == "ambiguous_application"


def test_stale_key_failure_does_not_invalidate_replaced_key(env) -> None:
    env.connect()
    provider = StubProvider(error("invalid_key"))
    escalator, _, _ = build(env, provider)
    original_extract = provider.extract

    def replace_then_fail(api_key, model, prompt_input):
        env.ai.save("owner-a", AiProvider.OPENAI, {"ciphertext": "x", "nonce": "y",
                    "encrypted_data_key": "z"}, "9999", True, NOW)
        return original_extract(api_key, model, prompt_input)

    provider.extract = replace_then_fail
    escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])

    assert env.user()["ai_key_status"] == "ok"


def test_exhausted_budget_leaves_messages_in_review(env) -> None:
    env.connect()
    env.client.update_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET ai_usage_day = :d, ai_requests_today = :n",
        ExpressionAttributeValues={
            ":d": {"S": NOW.date().isoformat()}, ":n": {"N": str(DAILY_LIMIT)},
        },
    )
    provider = StubProvider(interview_extraction())
    escalator, _, _ = build(env, provider)

    results = [
        escalator.escalate(parsed(f"m{i}", AMBIGUOUS_TEXT), rule(), [])
        for i in range(2)
    ]

    assert provider.calls == []
    assert [r.ai_note for r in results] == ["ai_budget_exhausted"] * 2
    assert all(r.reason == "ambiguous_application" for r in results)


def test_key_replaced_after_runtime_load_is_not_used(env) -> None:
    env.connect()
    provider = StubProvider(interview_extraction())
    escalator, _, _ = build(env, provider)
    escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])
    env.ai.delete("owner-a")

    result = escalator.escalate(parsed("m2", AMBIGUOUS_TEXT), rule(), [])

    assert len(provider.calls) == 1
    assert result.ai_note == "ai_unavailable" and result.origin == "rules"


def test_gmail_disconnect_raises_stale_run_without_provider_call(env) -> None:
    env.connect()
    provider = StubProvider(interview_extraction())
    escalator, _, _ = build(env, provider)
    env.gmail.disconnect("owner-a")

    with pytest.raises(StaleSyncRun):
        escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])

    assert provider.calls == []


def test_empty_validated_extraction_keeps_rule_decision(env) -> None:
    env.connect()
    fabricated = extraction(
        company="Evil Corp", status="offer",
        evidence={"company": "Evil Corp", "status": "pleased to offer you"},
    )
    provider = StubProvider(fabricated)
    escalator, _, _ = build(env, provider)
    decision = rule()

    result = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), decision, [])

    assert result is decision


@pytest.mark.parametrize("raw", [
    {"not": "the schema"},
    {**extraction(), "confidence": 0.99},
    {**extraction(), "year": "twenty"},
])
def test_malformed_output_routes_to_rule_review(env, raw) -> None:
    env.connect()
    escalator, _, _ = build(env, StubProvider(raw))

    result = escalator.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), [])

    assert result.origin == "rules" and result.outcome == "review_required"
    assert result.reason == "ambiguous_application"
    assert result.ai_proposal is None


def test_owner_key_and_counter_are_never_shared(env) -> None:
    env.connect("owner-a")
    env.connect("owner-b")
    env.app("owner-b", company="Bravo", role="Data Intern")
    provider_a = StubProvider(interview_extraction())
    provider_b = StubProvider(interview_extraction())
    esc_a, _, _ = build(env, provider_a, "owner-a")
    esc_b, _, _ = build(env, provider_b, "owner-b")

    esc_b.escalate(parsed("m1", AMBIGUOUS_TEXT), rule(), env.candidates("owner-b"))

    assert provider_a.calls == []
    assert provider_b.calls[0][0] == f"{SYNTHETIC_KEY}-owner-b"
    assert usage(env, "owner-a") == 0 and usage(env, "owner-b") == 1
    user_prompt = provider_b.calls[0][2].user
    assert "Bravo" not in user_prompt  # not mentioned in the email
    esc_a.escalate(parsed("m2", AMBIGUOUS_TEXT), rule(), [])
    assert provider_a.calls[0][0] == f"{SYNTHETIC_KEY}-owner-a"
    assert usage(env, "owner-a") == 1


def test_prompt_candidates_come_only_from_callers_applications(env) -> None:
    env.connect("owner-a")
    env.connect("owner-b")
    env.app("owner-a", company="Acme")
    env.app("owner-b", application_id="app-b", company="Acme Secret Division",
            role="Hidden Role")
    provider = StubProvider(interview_extraction())
    escalator, _, _ = build(env, provider, "owner-a")

    escalator.escalate(
        parsed("m1", AMBIGUOUS_TEXT), rule(), env.candidates("owner-a")
    )

    prompt = provider.calls[0][2]
    assert "Hidden Role" not in prompt.user and "Secret Division" not in prompt.user
    assert "app-b" not in prompt.user


def test_failure_logs_carry_no_text(env, caplog) -> None:
    env.connect()
    provider = StubProvider(error("provider_unavailable"))
    escalator, _, _ = build(env, provider)
    text = AMBIGUOUS_TEXT + " PRIVATE-EMAIL-BODY"

    with caplog.at_level(logging.DEBUG):
        escalator.escalate(parsed("m1", text), rule(), [])
        env.client.update_item(
            TableName="Users", Key={"owner_id": {"S": "owner-a"}},
            UpdateExpression="SET ai_key_status = :i",
            ExpressionAttributeValues={":i": {"S": "invalid"}},
        )

    assert "openai" in caplog.text
    assert "provider_unavailable" in caplog.text
    for secret in (SYNTHETIC_KEY, "PRIVATE-EMAIL-BODY", "Software Engineer Intern",
                   "invite you to interview"):
        assert secret not in caplog.text

