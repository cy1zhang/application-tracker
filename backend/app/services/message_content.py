from app.domain.messages import MessageContent
from app.services.ai_extraction import BODY_LIMIT
from app.services.local_review_ai import fetch_owner_message


class MessageNotFound(Exception):
    pass


class MessageContentService:
    def __init__(self, *, emails, connections, cipher, oauth, fetch) -> None:
        self._emails = emails
        self._connections = connections
        self._cipher = cipher
        self._oauth = oauth
        self._fetch = fetch

    def get(self, owner_id: str, message_id: str) -> MessageContent:
        # Only messages the owner has a processed item for may be fetched.
        if self._emails.get_outcome(owner_id, message_id) is None:
            raise MessageNotFound
        message, _ = fetch_owner_message(
            connections=self._connections, cipher=self._cipher, oauth=self._oauth,
            fetch=self._fetch, owner_id=owner_id, message_id=message_id,
        )
        return MessageContent(
            message_id=message.message_id, thread_id=message.thread_id,
            received_at=message.received_at, subject=message.subject,
            sender=message.sender, text=message.text[:BODY_LIMIT],
        )
