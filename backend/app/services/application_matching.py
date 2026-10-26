import re
from dataclasses import dataclass

from app.domain.applications import RoleType, Season
from app.services.email_rules import ApplicationCandidate

_COMPANY_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co"}
_ROLE_NOISE = re.compile(
    r"\b(?:intern|internship|coop|summer|fall|spring|winter|\d{4})\b"
)


_INTERN = re.compile(r"\b(?:intern|internship)\b")
_CO_OP = re.compile(r"\bco-?op\b")
_FULL_TIME = re.compile(r"\b(?:new[- ]grad|full[- ]time)\b")


@dataclass(frozen=True)
class Matched:
    candidate: ApplicationCandidate


@dataclass(frozen=True)
class NoMatch:
    pass


@dataclass(frozen=True)
class Ambiguous:
    count: int


MatchResult = Matched | NoMatch | Ambiguous


def normalize_company(name: str) -> str:
    text = re.sub(r"[.']", "", name.lower())
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    if len(tokens) > 3 and tokens[-3:] == ["talent", "acquisition", "team"]:
        tokens = tokens[:-3]
    while len(tokens) > 1 and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_role(role: str) -> str:
    text = re.sub(r"\bco-op\b", "coop", role.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\bswe\b", "software engineering", text)
    text = re.sub(r"\bsoftware engineer\b", "software engineering", text)
    text = _ROLE_NOISE.sub(" ", text)
    return " ".join(text.split())


def _outcome(found: list[ApplicationCandidate]) -> MatchResult | None:
    if len(found) == 1:
        return Matched(found[0])
    if found:
        return Ambiguous(len(found))
    return None


def _role_type(explicit: RoleType | None, role: str | None) -> RoleType | None:
    if explicit is not None and explicit != RoleType.UNKNOWN:
        return explicit
    text = (role or "").lower()
    if _CO_OP.search(text):
        return RoleType.CO_OP
    if _INTERN.search(text):
        return RoleType.INTERNSHIP
    if _FULL_TIME.search(text):
        return RoleType.FULL_TIME_NEW_GRAD
    return None


def _compatible(left, right) -> bool:
    return left is None or right is None or left == right


def match_application(
    candidates: list[ApplicationCandidate],
    *,
    thread_id: str | None,
    requisition_id: str | None,
    company: str | None,
    role: str | None,
    season: Season | None,
    year: int | None,
    role_type: RoleType | None = None,
) -> MatchResult:
    requisition = requisition_id.strip().lower() if requisition_id else ""
    if requisition:
        by_requisition = [
            c
            for c in candidates
            if requisition in {r.strip().lower() for r in c.requisition_ids}
        ]
        if by_requisition:
            email_name = normalize_company(company) if company else None
            compatible = [
                c
                for c in by_requisition
                if _compatible(
                    normalize_company(c.application.company)
                    if c.application.company
                    else None,
                    email_name or None,
                )
            ]
            return _outcome(compatible) or Ambiguous(1)

    if thread_id:
        by_thread = [c for c in candidates if thread_id in c.thread_ids]
        if requisition and any(
            c.requisition_ids
            and requisition not in {r.strip().lower() for r in c.requisition_ids}
            for c in by_thread
        ):
            return Ambiguous(len(by_thread))
        result = _outcome(by_thread)
        if result:
            return result

    email_company = normalize_company(company) if company else ""
    if not email_company:
        return NoMatch()
    email_type = _role_type(role_type, role)
    same_company = [
        c
        for c in candidates
        if c.application.company
        and normalize_company(c.application.company) == email_company
        and _compatible(
            _role_type(c.application.role_type, c.application.role), email_type
        )
    ]
    email_role = normalize_role(role) if role else ""
    if not email_role:
        return _outcome(same_company) or NoMatch()

    result = _outcome(
        [
            c
            for c in same_company
            if c.application.role
            and normalize_role(c.application.role) == email_role
            and _compatible(c.application.season, season)
            and _compatible(c.application.year, year)
        ]
    )
    return result or NoMatch()
