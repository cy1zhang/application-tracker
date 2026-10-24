from datetime import datetime

from pydantic import BaseModel


class MessageSummary(BaseModel):
    message_id: str
    outcome: str
    reason: str
    received_at: datetime | None


class MessagePage(BaseModel):
    items: list[MessageSummary]
    next_cursor: str | None


class MessageContent(BaseModel):
    message_id: str
    thread_id: str
    received_at: datetime
    subject: str
    sender: str
    text: str
