import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GmailApiError(Exception):
    pass


class HistoryExpired(GmailApiError):
    pass


class MessageUnavailable(GmailApiError):
    pass


@dataclass(frozen=True)
class MessagePage:
    message_ids: list[str]
    next_page_token: str | None


@dataclass(frozen=True)
class HistoryPage:
    message_ids: list[str]
    next_page_token: str | None
    history_id: str


class GmailFetchClient:
    BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
    MAX_RESPONSE_BYTES = 2_000_000

    def __init__(self, *, urlopen: Callable[..., Any] = urlopen) -> None:
        self._urlopen = urlopen

    def list_import_page(
        self, access_token: str, boundary: datetime, page_token: str | None
    ) -> MessagePage:
        # Include the boundary second in Gmail's search; the worker checks each
        # message's exact received time before processing it.
        query = {
            "q": (
                f"after:{int(boundary.timestamp()) - 1} "
                "{application assessment interview offer rejection}"
            ),
            "maxResults": "10",
            "includeSpamTrash": "false",
        }
        if page_token is not None:
            query["pageToken"] = page_token
        response = self._fetch_json(
            f"{self.BASE_URL}/messages?{urlencode(query)}", access_token
        )
        if "error" in response:
            raise GmailApiError("Gmail request failed")
        messages = response.get("messages", [])
        next_token = response.get("nextPageToken")
        if (
            not isinstance(messages, list)
            or len(messages) > 10
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
                for item in messages
            )
            or (next_token is not None and (not isinstance(next_token, str) or not next_token))
        ):
            raise GmailApiError("Invalid Gmail response")
        return MessagePage(
            message_ids=[item["id"] for item in messages],
            next_page_token=next_token,
        )

    def get_message(self, access_token: str, message_id: str) -> dict:
        response = self._fetch_json(
            f"{self.BASE_URL}/messages/{quote(message_id, safe='')}?format=full",
            access_token,
            missing_message=True,
        )
        if response.get("id") != message_id:
            raise GmailApiError("Invalid Gmail response")
        return response

    def profile_history_id(self, access_token: str) -> str:
        response = self._fetch_json(f"{self.BASE_URL}/profile", access_token)
        history_id = response.get("historyId")
        if not isinstance(history_id, str) or not history_id.isdecimal():
            raise GmailApiError("Invalid Gmail response")
        return history_id

    def list_history_page(
        self, access_token: str, start_history_id: str, page_token: str | None
    ) -> HistoryPage:
        query = {
            "startHistoryId": start_history_id,
            "historyTypes": "messageAdded",
            "maxResults": "10",
        }
        if page_token is not None:
            query["pageToken"] = page_token
        response = self._fetch_json(
            f"{self.BASE_URL}/history?{urlencode(query)}", access_token,
            stale_history=True,
        )
        history_id = response.get("historyId")
        next_token = response.get("nextPageToken")
        records = response.get("history", [])
        if (
            not isinstance(history_id, str) or not history_id.isdecimal()
            or not isinstance(records, list) or len(records) > 10
            or (next_token is not None and (not isinstance(next_token, str) or not next_token))
        ):
            raise GmailApiError("Invalid Gmail response")
        message_ids = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("messagesAdded", []), list):
                raise GmailApiError("Invalid Gmail response")
            for added in record.get("messagesAdded", []):
                message_id = added.get("message", {}).get("id") if isinstance(added, dict) else None
                if not isinstance(message_id, str) or not message_id:
                    raise GmailApiError("Invalid Gmail response")
                if message_id not in message_ids:
                    message_ids.append(message_id)
        return HistoryPage(message_ids, next_token, history_id)

    def _fetch_json(
        self, url: str, access_token: str, *, stale_history=False, missing_message=False
    ) -> dict:
        request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
        try:
            with self._urlopen(request, timeout=10) as response:
                data = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(data) > self.MAX_RESPONSE_BYTES:
                raise ValueError("Gmail response too large")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise TypeError("Invalid Gmail response")
            return value
        except HTTPError as error:
            if stale_history and error.code == 404:
                raise HistoryExpired("Gmail history expired") from None
            if missing_message and error.code == 404:
                raise MessageUnavailable("Gmail message unavailable") from None
            raise GmailApiError("Gmail request failed") from None
        except (OSError, ValueError, TypeError):
            raise GmailApiError("Gmail request failed") from None
