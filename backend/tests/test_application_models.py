from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.applications import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationStatus,
    Season,
)


def test_create_requires_a_non_empty_company() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(
            company="  ",
            role="Engineer",
            status=ApplicationStatus.APPLIED,
        )


def test_create_does_not_default_status() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="Acme", role="Engineer")


def test_patch_distinguishes_null_from_omitted_fields() -> None:
    patch = ApplicationPatch(expected_version=3, season=None)

    assert patch.updates() == {"season": None}


def test_patch_rejects_no_changes() -> None:
    with pytest.raises(ValidationError):
        ApplicationPatch(expected_version=3)


def test_cycle_fields_are_independently_nullable() -> None:
    payload = ApplicationCreate(
        company="Acme",
        role="Engineer",
        status=ApplicationStatus.APPLIED,
        season=Season.SUMMER,
        year=None,
        application_date=date(2026, 9, 24),
    )

    assert payload.season is Season.SUMMER
    assert payload.year is None
