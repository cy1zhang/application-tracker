from dataclasses import dataclass
from enum import StrEnum

TOMBSTONE_TTL_HOURS = 24
LEASE_MINUTES = 10


class AccountState(StrEnum):
    ACTIVE = "active"
    DELETING = "deleting"
    DELETED = "deleted"


class DeletionPhase(StrEnum):
    REVOKE_GMAIL = "revoke_gmail"
    DELETE_PARTITIONS = "delete_partitions"
    WIPE_PROFILE = "wipe_profile"
    DELETE_LOGIN = "delete_login"


class AccountDeleting(Exception):
    """The account is being deleted; the write was not applied."""


@dataclass(frozen=True)
class AccountStatus:
    state: AccountState
    phase: str | None = None
