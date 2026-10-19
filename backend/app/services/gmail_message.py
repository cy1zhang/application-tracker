import re
from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

MAX_TEXT = 16_384
MAX_ENCODED_PART = 131_072


class MessageParseError(Exception):
    pass


@dataclass(frozen=True)
class ParsedGmailMessage:
    message_id: str
    thread_id: str
    received_at: datetime
    subject: str
    sender: str
    text: str
    is_forwarded: bool = False


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_tags: list[str] = []
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hidden_tags:
            if tag not in {"br", "img", "hr", "meta", "input", "link"}:
                self._hidden_tags.append(tag)
            return
        is_quote = any(
            name.lower() == "class" and value and "gmail_quote" in value.split()
            for name, value in attrs
        )
        if tag in {"script", "style", "head", "template", "blockquote"} or is_quote:
            self._hidden_tags.append(tag)
        elif tag in {"br", "div", "p", "li", "tr"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._hidden_tags:
            if tag in self._hidden_tags:
                while self._hidden_tags.pop() != tag:
                    pass
            return
        if tag in {"div", "p", "li", "tr"}:
            self._chunks.append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if not self._hidden_tags and tag == "br":
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_tags:
            self._chunks.append(data)

    def visible_text(self) -> str:
        return "\n".join(
            line for line in (part.strip() for part in "".join(self._chunks).splitlines())
            if line
        )


def parse_gmail_message(message: dict) -> ParsedGmailMessage:
    try:
        message_id = message["id"]
        thread_id = message["threadId"]
        received_ms = int(message["internalDate"])
        payload = message["payload"]
        if (
            not isinstance(message_id, str)
            or not message_id
            or not isinstance(thread_id, str)
            or not thread_id
            or received_ms < 0
            or not isinstance(payload, dict)
        ):
            raise ValueError
        received_at = datetime.fromtimestamp(received_ms / 1000, UTC)
        headers = payload.get("headers", [])
        if not isinstance(headers, list):
            raise TypeError
        values = {
            header["name"].lower(): header["value"]
            for header in headers
            if isinstance(header, dict)
            and isinstance(header.get("name"), str)
            and isinstance(header.get("value"), str)
        }
        plain_parts: list[str] = []
        html_parts: list[str] = []
        _collect_text(payload, plain_parts, html_parts)
        plain = next((_decode_part(value) for value in plain_parts if value), "")
        if plain.strip():
            text = plain
        elif html_parts:
            parser = _VisibleHTML()
            parser.feed(_decode_part(html_parts[0]))
            text = parser.visible_text()
        else:
            raise ValueError
        subject = values.get("subject", "")[:512]
        is_forwarded = bool(
            re.match(r"\s*fwd?:", subject, re.IGNORECASE)
            or re.search(r"^-{2,}\s*Forwarded message\s*-{2,}\s*$", text, re.IGNORECASE | re.MULTILINE)
        )
        text = _strip_quoted_reply(text)[:MAX_TEXT].strip()
        if not text:
            raise ValueError
        return ParsedGmailMessage(
            message_id=message_id,
            thread_id=thread_id,
            received_at=received_at,
            subject=subject,
            sender=values.get("from", "")[:512],
            text=text,
            is_forwarded=is_forwarded,
        )
    except (KeyError, TypeError, ValueError, OverflowError, Base64Error) as error:
        raise MessageParseError("Gmail message cannot be parsed") from error


def _collect_text(
    root: dict, plain_parts: list[str], html_parts: list[str]
) -> None:
    pending = [(root, 0)]
    seen = 0
    while pending:
        part, depth = pending.pop()
        seen += 1
        if seen > 100 or depth > 12 or not isinstance(part, dict):
            raise ValueError
        if part.get("filename"):
            continue
        headers = part.get("headers", [])
        if not isinstance(headers, list):
            raise TypeError
        if any(
            isinstance(header, dict)
            and str(header.get("name", "")).lower() == "content-disposition"
            and re.match(
                r"\s*attachment(?:\s*;|\s*$)",
                str(header.get("value", "")),
                re.IGNORECASE,
            )
            for header in headers
        ):
            continue
        body = part.get("body", {})
        if not isinstance(body, dict):
            raise TypeError
        if not body.get("attachmentId") and body.get("data") is not None:
            mime_type = str(part.get("mimeType", "")).lower()
            if mime_type in {"text/plain", "text/html"}:
                value = body["data"]
                if not isinstance(value, str) or len(value) > MAX_ENCODED_PART:
                    raise ValueError
                (plain_parts if mime_type == "text/plain" else html_parts).append(value)
        children = part.get("parts", [])
        if not isinstance(children, list):
            raise TypeError
        pending.extend((child, depth + 1) for child in reversed(children))


def _strip_quoted_reply(text: str) -> str:
    retained = []
    for line in text.splitlines():
        if re.match(r"^On .+ wrote:\s*$", line, re.IGNORECASE):
            break
        if re.match(r"^-{2,}\s*Original Message\s*-{2,}$", line, re.IGNORECASE):
            break
        if re.match(r"^-{2,}\s*Forwarded message\s*-{2,}$", line, re.IGNORECASE):
            break
        if line.lstrip().startswith(">"):
            break
        retained.append(line)
    return "\n".join(retained).strip()


def _decode_part(value: str) -> str:
    return b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    ).decode("utf-8", errors="replace")[: MAX_TEXT * 2]
