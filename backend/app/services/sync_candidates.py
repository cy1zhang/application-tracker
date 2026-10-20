from app.repositories.applications import ApplicationRepository
from app.services.email_rules import ApplicationCandidate


def load_candidates(
    owner_id: str, applications: ApplicationRepository
) -> list[ApplicationCandidate]:
    candidates = []
    cursor = None
    while True:
        page = applications.list(owner_id=owner_id, limit=100, cursor=cursor)
        for application in page.items:
            events = applications.list_events(
                owner_id=owner_id, application_id=application.application_id
            )
            candidates.append(ApplicationCandidate(
                application=application,
                thread_ids=frozenset(event.thread_id for event in events if event.thread_id),
                requisition_ids=frozenset(
                    event.requisition_id for event in events if event.requisition_id
                ),
            ))
        if page.next_cursor is None:
            return candidates
        cursor = page.next_cursor
