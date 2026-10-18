from app.repositories.dynamodb_email import DynamoEmailRepository, StoredOutcome
from app.services.email_rules import ApplicationCandidate, classify_message
from app.services.gmail_message import MessageParseError, parse_gmail_message


def process_message(
    owner_id: str,
    gmail_message: dict,
    applications: list[ApplicationCandidate],
    repository: DynamoEmailRepository,
) -> StoredOutcome:
    try:
        message = parse_gmail_message(gmail_message)
    except MessageParseError:
        message_id = gmail_message.get("id") if isinstance(gmail_message, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise
        return repository.record_unparseable(owner_id=owner_id, message_id=message_id)

    decision = classify_message(message, applications)
    version = next(
        (
            candidate.application.version
            for candidate in applications
            if candidate.application.application_id == decision.application_id
        ),
        None,
    )
    return repository.record(
        owner_id=owner_id,
        message=message,
        decision=decision,
        application_version=version,
    )
