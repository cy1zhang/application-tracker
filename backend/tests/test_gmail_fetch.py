import json
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.gmail_fetch import GmailApiError, GmailFetchClient, HistoryExpired

BOUNDARY = datetime(2026, 7, 20, 12, tzinfo=UTC)


def test_import_page_has_fixed_epoch_boundary_and_pagination() -> None:
    requests = []

    def open_request(request, *, timeout):
        assert timeout <= 10
        requests.append(request)
        return BytesIO(json.dumps({
            "messages": [{"id": "msg-1", "threadId": "thread-1"}],
            "nextPageToken": "page-2",
        }).encode())

    client = GmailFetchClient(urlopen=open_request)

    page = client.list_import_page("synthetic-access", BOUNDARY, "page-1")

    assert page.message_ids == ["msg-1"]
    assert page.next_page_token == "page-2"
    request = requests[0]
    assert urlparse(request.full_url).path == "/gmail/v1/users/me/messages"
    query = parse_qs(urlparse(request.full_url).query)
    assert query["pageToken"] == ["page-1"]
    assert query["maxResults"] == ["10"]
    assert query["includeSpamTrash"] == ["false"]
    assert query["q"] == [
        "after:1784548799 {application assessment interview offer rejection}"
    ]
    assert "synthetic-access" not in request.full_url
    assert request.headers["Authorization"] == "Bearer synthetic-access"


def test_get_message_requests_full_payload() -> None:
    requests = []

    def open_request(request, *, timeout):
        requests.append(request)
        return BytesIO(json.dumps({"id": "msg-1", "threadId": "thread-1", "payload": {}}).encode())

    message = GmailFetchClient(urlopen=open_request).get_message(
        "synthetic-access", "msg-1"
    )

    assert message["id"] == "msg-1"
    assert urlparse(requests[0].full_url).path == "/gmail/v1/users/me/messages/msg-1"
    assert parse_qs(urlparse(requests[0].full_url).query) == {"format": ["full"]}


@pytest.mark.parametrize("payload", [
    b'{"error":"private provider content"}',
    b"x" * 2_000_001,
])
def test_invalid_or_oversized_response_is_sanitized(payload: bytes) -> None:
    def invalid_response(request, *, timeout):
        return BytesIO(payload)

    with pytest.raises(GmailApiError) as error:
        GmailFetchClient(urlopen=invalid_response).list_import_page(
            "synthetic-access", BOUNDARY, None
        )

    assert "private provider content" not in str(error.value)


def test_history_page_extracts_added_messages_and_checkpoint() -> None:
    requests = []

    def open_request(request, *, timeout):
        requests.append(request)
        return BytesIO(json.dumps({
            "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]},
                {"messagesAdded": [{"message": {"id": "m1"}}]},
            ],
            "nextPageToken": "next", "historyId": "110",
        }).encode())

    page = GmailFetchClient(urlopen=open_request).list_history_page(
        "synthetic-access", "100", "current"
    )

    assert page.message_ids == ["m1", "m2"]
    assert page.next_page_token == "next"
    assert page.history_id == "110"
    query = parse_qs(urlparse(requests[0].full_url).query)
    assert query == {"startHistoryId": ["100"], "pageToken": ["current"],
                     "historyTypes": ["messageAdded"], "maxResults": ["10"]}


def test_stale_history_is_distinguished_without_provider_body() -> None:
    from urllib.error import HTTPError

    def open_request(request, *, timeout):
        raise HTTPError(request.full_url, 404, "private detail", {}, BytesIO(b"private"))

    with pytest.raises(HistoryExpired) as error:
        GmailFetchClient(urlopen=open_request).list_history_page(
            "synthetic-access", "100", None
        )
    assert "private" not in str(error.value)


def test_profile_history_id_is_validated() -> None:
    def open_request(request, *, timeout):
        return BytesIO(b'{"historyId":"120"}')

    assert GmailFetchClient(urlopen=open_request).profile_history_id("access") == "120"
