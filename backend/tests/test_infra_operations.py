"""Offline guards for the deletion, alarm, retention, and notification wiring."""

import json

from app.deletion_handler import DEADLINE_BUFFER_SECONDS
from app.domain.account import LEASE_MINUTES
from app.observability import LOG_EVENTS
from tests.test_infra_timing import _actions, _template

DELETION = "AccountDeletionFunction"
ERROR_ALARMS = {
    "SyncWorkerFunction": "SyncWorkerErrorsAlarm",
    "SyncDispatcherFunction": "SyncDispatcherErrorsAlarm",
    DELETION: "AccountDeletionErrorsAlarm",
}
FILTERS = {
    "SyncWorkerFailureFilter": ("SyncWorkerLogGroup", "sync_worker_failure", 3),
    "SyncRunFailedFilter": ("SyncWorkerLogGroup", "sync_run_failed", 3),
    "ApiUnhandledErrorFilter": ("ApiLogGroup", "api_unhandled_error", 3),
    "AccountDeletionFailedFilter": ("AccountDeletionLogGroup", "deletion_job_failed", 1),
    "AccountDeletionEnqueueFailedFilter": ("ApiLogGroup", "deletion_enqueue_failed", 1),
}


def _resources(kind: str) -> dict:
    return {
        name: resource
        for name, resource in _template()["Resources"].items()
        if resource["Type"] == kind
    }


def _statements(function: str) -> list[dict]:
    properties = _template()["Resources"][function]["Properties"]
    return [s for policy in properties["Policies"] for s in policy["Statement"]]


def _alarms() -> dict:
    return _resources("AWS::CloudWatch::Alarm")


def test_every_function_has_a_named_14_day_log_group() -> None:
    groups = _resources("AWS::Logs::LogGroup")
    for function in _resources("AWS::Serverless::Function"):
        matches = [
            g for g in groups.values()
            if g["Properties"]["LogGroupName"] == {"Sub": f"/aws/lambda/${{{function}}}"}
        ]
        assert len(matches) == 1, function
        assert matches[0]["Properties"]["RetentionInDays"] == 14


def test_dead_letter_queues_retain_14_days_and_every_redrive_has_an_alarm() -> None:
    queues = _resources("AWS::SQS::Queue")
    alarms = _alarms().values()
    for name, queue in queues.items():
        redrive = queue["Properties"].get("RedrivePolicy")
        if not redrive:
            continue
        dlq = redrive["deadLetterTargetArn"]["GetAtt"].split(".")[0]
        assert queues[dlq]["Properties"]["MessageRetentionPeriod"] == 1209600
        assert any(
            a["Properties"].get("MetricName") == "ApproximateNumberOfMessagesVisible"
            and a["Properties"]["Dimensions"][0]["Value"]
            == {"GetAtt": f"{dlq}.QueueName"}
            for a in alarms
        ), name


def test_functions_have_error_alarms() -> None:
    alarms = _alarms()
    for function, alarm in ERROR_ALARMS.items():
        properties = alarms[alarm]["Properties"]
        assert properties["Namespace"] == "AWS/Lambda"
        assert properties["MetricName"] == "Errors"
        assert properties["Dimensions"] == [
            {"Name": "FunctionName", "Value": {"Ref": function}}
        ]
        assert properties["Threshold"] == 1
        assert properties["TreatMissingData"] == "notBreaching"


def test_log_metric_filters_use_exact_event_names_and_alarm_on_threshold() -> None:
    filters = _resources("AWS::Logs::MetricFilter")
    alarms = _alarms().values()
    assert set(filters) == set(FILTERS)
    for name, (group, event, threshold) in FILTERS.items():
        assert event in LOG_EVENTS
        properties = filters[name]["Properties"]
        assert properties["LogGroupName"] == {"Ref": group}
        assert properties["FilterPattern"] == f'"event={event}"'
        transformation = properties["MetricTransformations"][0]
        assert transformation["MetricValue"] == "1"
        metric = transformation["MetricName"]
        alarm = next(a["Properties"] for a in alarms if a["Properties"].get("MetricName") == metric)
        assert alarm["Namespace"] == transformation["MetricNamespace"]
        assert alarm["Statistic"] == "Sum"
        assert alarm["Threshold"] == threshold
        assert alarm["Period"] == 900
        assert alarm["TreatMissingData"] == "notBreaching"


def test_deletion_queue_and_function_timing() -> None:
    resources = _template()["Resources"]
    function = resources[DELETION]["Properties"]
    timeout = function["Timeout"]
    queue = resources["AccountDeletionQueue"]["Properties"]
    assert function["Handler"] == "app.deletion_handler.handler"
    assert queue["VisibilityTimeout"] >= 6 * timeout
    assert LEASE_MINUTES * 60 > timeout
    assert timeout > DEADLINE_BUFFER_SECONDS
    assert queue["RedrivePolicy"]["maxReceiveCount"] == 5
    mapping = function["Events"]["DeletionJobs"]["Properties"]
    assert mapping["BatchSize"] == 1
    assert mapping["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert mapping["Queue"] == {"GetAtt": "AccountDeletionQueue.Arn"}


def test_deletion_role_is_least_privilege_without_wildcards() -> None:
    statements = _statements(DELETION)
    assert "*" not in json.dumps(statements)
    by_resource = {}
    for statement in statements:
        resource = json.dumps(statement["Resource"], sort_keys=True)
        by_resource.setdefault(resource, set()).update(_actions(statement))
        assert not any(a.endswith("*") for a in _actions(statement))
        assert "secretsmanager" not in json.dumps(statement)
    expected = {
        '{"GetAtt": "ApplicationsTable.Arn"}': {
            "dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:DeleteItem"},
        '{"GetAtt": "EventsTable.Arn"}': {
            "dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:DeleteItem"},
        '{"GetAtt": "SyncRunsTable.Arn"}': {
            "dynamodb:Query", "dynamodb:BatchWriteItem"},
        '{"GetAtt": "OAuthStatesTable.Arn"}': {
            "dynamodb:Scan", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"},
        '{"GetAtt": "UsersTable.Arn"}': {
            "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem"},
        '{"GetAtt": "GmailKmsKey.Arn"}': {"kms:Decrypt"},
        '{"GetAtt": "UserPool.Arn"}': {"cognito-idp:AdminDeleteUser"},
        '{"GetAtt": "AccountDeletionQueue.Arn"}': {
            "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
            "sqs:SendMessage"},
    }
    assert by_resource == expected
    kms = next(s for s in statements if "kms:Decrypt" in _actions(s))
    assert kms["Condition"] == {
        "StringEquals": {"kms:EncryptionContext:credential_type": "gmail_refresh_token"}
    }


def test_destructive_actions_stay_off_other_roles() -> None:
    functions = _resources("AWS::Serverless::Function")
    for function in functions:
        if function == DELETION:
            continue
        for statement in _statements(function):
            actions = set(_actions(statement))
            assert "cognito-idp:AdminDeleteUser" not in actions, function
            assert "dynamodb:BatchWriteItem" not in actions, function
            if "dynamodb:DeleteItem" in actions:
                # Pre-existing: the API consumes OAuth states on callback.
                assert function == "ApiFunction"
                assert statement["Resource"] == {"GetAtt": "OAuthStatesTable.Arn"}


def test_queue_access_and_environment_for_api_dispatcher_and_deletion() -> None:
    queue_ref = {"Ref": "AccountDeletionQueue"}
    queue_arn = {"GetAtt": "AccountDeletionQueue.Arn"}
    resources = _template()["Resources"]
    for function in ("ApiFunction", "SyncDispatcherFunction"):
        variables = resources[function]["Properties"]["Environment"]["Variables"]
        assert variables["DELETION_QUEUE_URL"] == queue_ref
        sends = [
            s for s in _statements(function)
            if s["Resource"] == queue_arn
        ]
        assert [_actions(s) for s in sends] == [["sqs:SendMessage"]]
    worker = resources["SyncWorkerFunction"]["Properties"]
    assert "DELETION_QUEUE_URL" not in worker["Environment"]["Variables"]
    assert queue_arn not in [s["Resource"] for s in _statements("SyncWorkerFunction")]
    variables = resources[DELETION]["Properties"]["Environment"]["Variables"]
    assert set(variables) == {
        "USERS_TABLE", "APPLICATIONS_TABLE", "EVENTS_TABLE", "SYNC_RUNS_TABLE",
        "OAUTH_STATES_TABLE", "GMAIL_KMS_KEY_ARN", "USER_POOL_ID", "DELETION_QUEUE_URL",
    }
    assert variables["USER_POOL_ID"] == {"Ref": "UserPool"}


def test_users_table_expires_tombstones_with_ttl() -> None:
    users = _template()["Resources"]["UsersTable"]["Properties"]
    assert users["TimeToLiveSpecification"] == {
        "AttributeName": "expires_at", "Enabled": True,
    }


def test_alarm_notifications_default_off_and_gate_every_sns_resource() -> None:
    template = _template()
    parameter = template["Parameters"]["AlarmNotifications"]
    assert parameter["Default"] == "false"
    assert parameter["AllowedValues"] == ["true", "false"]
    assert template["Conditions"]["SendAlarmEmail"] == {
        "Equals": [{"Ref": "AlarmNotifications"}, "true"]
    }
    sns = {
        name: r for name, r in template["Resources"].items()
        if r["Type"].startswith("AWS::SNS::")
    }
    assert sns, "expected an SNS topic"
    for resource in sns.values():
        assert resource["Condition"] == "SendAlarmEmail"
    topic = next(r for r in sns.values() if r["Type"] == "AWS::SNS::Topic")
    assert topic["Properties"]["Subscription"] == [
        {"Protocol": "email", "Endpoint": {"Ref": "BudgetEmail"}}
    ]
    guarded = {"If": ["SendAlarmEmail", [{"Ref": "AlarmTopic"}], {"Ref": "AWS::NoValue"}]}
    alarms = _alarms()
    assert alarms
    for name, alarm in alarms.items():
        assert "Condition" not in alarm
        assert alarm["Properties"]["AlarmActions"] == guarded, name
        assert alarm["Properties"]["OKActions"] == guarded, name
    # No other place may reference the topic outside the guarded alarm actions.
    others = {k: v for k, v in template["Resources"].items() if k not in alarms and k != "AlarmTopic"}
    assert "AlarmTopic" not in json.dumps(others)


def test_only_self_continuing_functions_allow_recursive_loop() -> None:
    allowed = {
        name
        for name, resource in _resources("AWS::Serverless::Function").items()
        if resource["Properties"].get("RecursiveLoop") == "Allow"
    }
    assert allowed == {"SyncWorkerFunction", DELETION}


def _granted(function: str, table: str) -> set[str]:
    arn = {"GetAtt": f"{table}.Arn"}
    granted: set[str] = set()
    for statement in _statements(function):
        resource = statement["Resource"]
        resources = resource if isinstance(resource, list) else [resource]
        if arn in resources:
            granted |= set(_actions(statement))
    return granted


def test_functions_hold_every_dynamodb_action_the_email_repository_calls() -> None:
    # (operation -> table) the email, review, and application repositories issue,
    # directly or as transaction items, from the API routes and the sync worker.
    required = {
        "EventsTable": {
            "dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem",
            "dynamodb:UpdateItem",
        },
        "ApplicationsTable": {
            "dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem",
        },
    }
    for function in ("ApiFunction", "SyncWorkerFunction"):
        for table, actions in required.items():
            assert actions <= _granted(function, table), (function, table)
    assert "dynamodb:ConditionCheckItem" in _granted("ApiFunction", "ApplicationsTable")
    for function in ("ApiFunction", "SyncWorkerFunction"):
        for table in ("EventsTable", "ApplicationsTable"):
            granted = _granted(function, table)
            assert not granted & {"dynamodb:DeleteItem", "dynamodb:Scan"}, (function, table)
