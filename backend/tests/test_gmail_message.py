from base64 import urlsafe_b64encode
from datetime import UTC, datetime

import pytest

from app.services.gmail_message import MessageParseError, parse_gmail_message


def _part(text: str, mime_type: str = "text/plain") -> dict:
    return {
        "mimeType": mime_type,
        "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
    }


def _message(payload: dict) -> dict:
    return {
        "id": "message-1",
        "threadId": "thread-1",
        "internalDate": "1790000000000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Interview invitation"},
                {"name": "From", "value": "Recruiting <jobs@example.test>"},
            ],
            **payload,
        },
    }


def test_plain_text_and_quoted_reply_are_bounded() -> None:
    message = _message(
        _part("We invite you to interview.\n\nOn Monday, Taylor wrote:\n> old mail")
    )

    parsed = parse_gmail_message(message)

    assert parsed.message_id == "message-1"
    assert parsed.thread_id == "thread-1"
    assert parsed.received_at == datetime.fromtimestamp(1790000000, UTC)
    assert parsed.subject == "Interview invitation"
    assert parsed.sender == "Recruiting <jobs@example.test>"
    assert parsed.text == "We invite you to interview."


def test_nested_alternative_prefers_plain_and_ignores_attachments() -> None:
    message = _message(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        _part("Plain recruiting message"),
                        _part("<b>Different HTML</b>", "text/html"),
                    ],
                },
                {
                    **_part("ATTACHMENT SECRET"),
                    "filename": "notes.txt",
                },
            ],
        }
    )

    assert parse_gmail_message(message).text == "Plain recruiting message"


def test_html_fallback_only_uses_visible_text() -> None:
    message = _message(
        _part(
            "<div>Offer &amp; details</div><script>steal()</script>"
            "<style>.secret{}</style><p>See you soon</p>",
            "text/html",
        )
    )

    assert parse_gmail_message(message).text == "Offer & details\nSee you soon"


def test_html_quoted_block_is_excluded() -> None:
    message = _message(
        _part(
            "<p>We invite you to interview.</p>"
            "<blockquote>Thank you for applying.</blockquote>",
            "text/html",
        )
    )

    assert parse_gmail_message(message).text == "We invite you to interview."


def test_gmail_quote_container_is_excluded() -> None:
    message = _message(
        _part(
            '<div>Thanks for the update.</div><div class="gmail_quote">'
            "<div>Acme invites you to interview for Software Engineer Intern.</div>"
            "</div>",
            "text/html",
        )
    )

    assert parse_gmail_message(message).text == "Thanks for the update."


def test_empty_plain_part_falls_back_to_html() -> None:
    message = _message(
        {
            "mimeType": "multipart/alternative",
            "parts": [_part(""), _part("<p>Visible interview</p>", "text/html")],
        }
    )

    assert parse_gmail_message(message).text == "Visible interview"


def test_malformed_html_does_not_block_valid_plain_text() -> None:
    message = _message(
        {
            "mimeType": "multipart/alternative",
            "parts": [_part("Plain interview"), {"mimeType": "text/html", "body": {"data": "@@@"}}],
        }
    )

    assert parse_gmail_message(message).text == "Plain interview"


def test_external_attachment_is_never_fetched() -> None:
    message = _message(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                _part("Visible message"),
                {
                    "mimeType": "text/plain",
                    "filename": "secret.txt",
                    "body": {"attachmentId": "external-attachment"},
                },
            ],
        }
    )

    assert parse_gmail_message(message).text == "Visible message"


def test_content_disposition_attachment_is_excluded_without_filename() -> None:
    message = _message(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    **_part("ATTACHMENT SECRET"),
                    "headers": [
                        {"name": "Content-Disposition", "value": "attachment"}
                    ],
                },
                _part("Visible message"),
            ],
        }
    )

    assert parse_gmail_message(message).text == "Visible message"


@pytest.mark.parametrize(
    "change",
    [
        lambda message: message.update(id=""),
        lambda message: message.update(internalDate="invalid"),
        lambda message: message["payload"]["body"].update(data="@@@"),
    ],
)
def test_invalid_message_is_rejected(change) -> None:
    message = _message(_part("Text"))
    change(message)

    with pytest.raises(MessageParseError):
        parse_gmail_message(message)


def test_missing_inline_text_is_rejected() -> None:
    message = _message(
        {"mimeType": "multipart/mixed", "parts": [{"mimeType": "application/pdf"}]}
    )

    with pytest.raises(MessageParseError):
        parse_gmail_message(message)


def test_oversized_text_is_capped_before_classification() -> None:
    message = _message(_part("x" * 20_000))

    assert len(parse_gmail_message(message).text) == 16_384
