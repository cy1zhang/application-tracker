from pydantic import BaseModel, ConfigDict


class TrackerIdentity(BaseModel):
    owner_id: str
    username: str
    email: str


class GmailConnectionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    email: str | None
