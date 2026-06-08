"""Dev-only dummy Person seeding (gated on BITSWAN_AUTOMATION_STAGE).

The auto-seed populates the roster in dev environments where there are
no real OIDC users to log in and grow it organically. It must never
run outside of the dev stage.

Tests drive `seed_dev_dummies_into(session)` against the per-test
async engine in conftest so they don't bind the app's module-level
engine to a transient asyncio.run loop (which would break every
subsequent TestClient-based test).
"""
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.main import (
    DEV_DUMMY_PEOPLE,
    _is_dev_stage,
    _seed_dev_dummies,
    seed_dev_dummies_into,
)
from app.models import Person
from tests.conftest import clear_table, db_run


def _reset_persons():
    clear_table(Person)


def _seed_via_session() -> int:
    return db_run(seed_dev_dummies_into)


def _list_dummy_persons() -> list[tuple[str, str]]:
    async def _do(session):
        emails = [e for _, e in DEV_DUMMY_PEOPLE]
        rows = (
            await session.execute(
                select(Person.display_name, Person.email).where(
                    Person.email.in_(emails)
                )
            )
        ).all()
        return [(name, email) for (name, email) in rows]

    return db_run(_do)


def test_seed_inserts_all_dummies():
    _reset_persons()
    try:
        added = _seed_via_session()
        assert added == len(DEV_DUMMY_PEOPLE)
        got = {email for (_, email) in _list_dummy_persons()}
        expected = {email for (_, email) in DEV_DUMMY_PEOPLE}
        assert got == expected
    finally:
        _reset_persons()


def test_seed_is_idempotent():
    _reset_persons()
    try:
        _seed_via_session()
        added_again = _seed_via_session()
        assert added_again == 0
        rows = _list_dummy_persons()
        assert len(rows) == len(DEV_DUMMY_PEOPLE)
    finally:
        _reset_persons()


def test_seed_preserves_existing_email_row():
    """If a Person with one of the dummy emails already exists (e.g. a
    user with the same email actually logged in), the seed must leave
    that row alone — no duplicate, no overwrite of display_name."""
    _reset_persons()
    try:
        target_email = DEV_DUMMY_PEOPLE[0][1]
        custom_name = "Real Human (not the dummy)"

        async def _seed_real(session):
            session.add(
                Person(
                    display_name=custom_name,
                    email=target_email,
                    first_seen_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        db_run(_seed_real)

        added = _seed_via_session()
        # All dummies minus the one already present.
        assert added == len(DEV_DUMMY_PEOPLE) - 1

        async def _check(session):
            return (
                await session.execute(
                    select(Person.display_name).where(Person.email == target_email)
                )
            ).scalars().all()

        names = db_run(_check)
        assert names == [custom_name]
    finally:
        _reset_persons()


@pytest.mark.parametrize("stage", ["live-dev", "dev"])
def test_is_dev_stage_for_dev_values(monkeypatch, stage):
    monkeypatch.setenv("BITSWAN_AUTOMATION_STAGE", stage)
    assert _is_dev_stage() is True


@pytest.mark.parametrize("stage", ["production", "staging", "", "prod-dev-lookalike"])
def test_is_dev_stage_rejects_non_dev_values(monkeypatch, stage):
    monkeypatch.setenv("BITSWAN_AUTOMATION_STAGE", stage)
    assert _is_dev_stage() is False


def test_startup_wrapper_short_circuits_when_disabled(monkeypatch):
    """The lifespan wrapper must not run the seed when the disable flag
    is set, even on a dev stage."""
    monkeypatch.setenv("BITSWAN_AUTOMATION_STAGE", "live-dev")
    monkeypatch.setenv("BITSWAN_DISABLE_DEV_SEED", "1")
    _reset_persons()
    try:
        import asyncio
        asyncio.run(_seed_dev_dummies())
        assert _list_dummy_persons() == []
    finally:
        _reset_persons()


def test_startup_wrapper_short_circuits_outside_dev(monkeypatch):
    monkeypatch.setenv("BITSWAN_AUTOMATION_STAGE", "production")
    monkeypatch.delenv("BITSWAN_DISABLE_DEV_SEED", raising=False)
    _reset_persons()
    try:
        import asyncio
        asyncio.run(_seed_dev_dummies())
        assert _list_dummy_persons() == []
    finally:
        _reset_persons()
