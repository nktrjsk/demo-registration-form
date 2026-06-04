"""Internal endpoints for the live meeting form."""
from datetime import date, datetime, time

from fastapi import Body, Depends, HTTPException
from sqlalchemy import desc, select, cast, Date
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_email_from_request, require_admin
from app.database import get_db
from app.models import (
    MeetingInstance,
    MeetingEntry,
    ProjectEntry,
    Project,
    UserRoster,
)
from app.routers.internal import router


def _project_to_dict(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "leader": p.leader}


def _meeting_to_dict(m: MeetingInstance, projects: list[Project]) -> dict:
    return {
        "id": m.id,
        "meeting_date": m.meeting_date.isoformat(),
        "projects": [_project_to_dict(p) for p in projects],
    }


async def _load_current_meeting(db: AsyncSession) -> tuple[MeetingInstance, list[Project]] | None:
    result = await db.execute(
        select(MeetingInstance).order_by(desc(MeetingInstance.meeting_date)).limit(1)
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        return None
    projects = (
        await db.execute(
            select(Project)
            .where(Project.meeting_instance_id == meeting.id)
            .order_by(Project.created_at)
        )
    ).scalars().all()
    return meeting, list(projects)


@router.get("/meeting/current")
async def get_current_meeting(db: AsyncSession = Depends(get_db)):
    loaded = await _load_current_meeting(db)
    if loaded is None:
        return {"meeting": None}
    meeting, projects = loaded
    return {"meeting": _meeting_to_dict(meeting, projects)}


async def _load_entry(
    db: AsyncSession, meeting_id: int, user_email: str
) -> tuple[MeetingEntry | None, list[ProjectEntry]]:
    entry = (
        await db.execute(
            select(MeetingEntry).where(
                MeetingEntry.meeting_instance_id == meeting_id,
                MeetingEntry.user_email == user_email,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        return None, []
    project_entries = (
        await db.execute(
            select(ProjectEntry).where(ProjectEntry.meeting_entry_id == entry.id)
        )
    ).scalars().all()
    return entry, list(project_entries)


def _entry_to_dict(
    entry: MeetingEntry | None, project_entries: list[ProjectEntry]
) -> dict:
    if entry is None:
        return {"attended": False, "project_entries": []}
    return {
        "attended": entry.attended,
        "project_entries": [
            {"project_id": pe.project_id, "description": pe.description}
            for pe in project_entries
        ],
    }


@router.get("/meeting/{meeting_id}/my-entry")
async def get_my_entry(
    meeting_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    if not user_email:
        raise HTTPException(status_code=400, detail="OIDC session has no email claim")
    entry, project_entries = await _load_entry(db, meeting_id, user_email)
    return {"user_email": user_email, **_entry_to_dict(entry, project_entries)}


@router.post("/meeting/{meeting_id}/projects")
async def add_project(
    meeting_id: int,
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    if not user_email:
        raise HTTPException(status_code=400, detail="OIDC session has no email claim")
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    name = str(payload.get("name", "")).strip()
    leader = str(payload.get("leader", "")).strip()
    if not name or not leader:
        raise HTTPException(status_code=422, detail="name and leader are required")

    proj = Project(
        meeting_instance_id=meeting_id,
        name=name,
        leader=leader,
        created_by_email=user_email,
    )
    db.add(proj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A project named '{name}' already exists for this meeting",
        )
    await db.refresh(proj)
    return _project_to_dict(proj)


@router.get("/meeting/{meeting_id}/attendees")
async def get_attendees(meeting_id: int, db: AsyncSession = Depends(get_db)):
    """Roster-derived attendee list: every user whose first OIDC login
    was on or before this meeting's date, with their attendance flag
    from MeetingEntry (False if no entry yet)."""
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Users seen on or before this meeting's date (inclusive — someone
    # who first logged in today still shows up on today's meeting).
    end_of_day = datetime.combine(meeting.meeting_date, time(23, 59, 59))
    roster = (
        await db.execute(
            select(UserRoster.email)
            .where(UserRoster.first_seen_at <= end_of_day)
            .order_by(UserRoster.first_seen_at)
        )
    ).all()
    emails = [row[0] for row in roster]

    entries = (
        await db.execute(
            select(MeetingEntry.user_email, MeetingEntry.attended).where(
                MeetingEntry.meeting_instance_id == meeting_id
            )
        )
    ).all()
    attended_by = {email: bool(attended) for (email, attended) in entries}

    return {
        "attendees": [
            {"email": email, "attended": attended_by.get(email, False)}
            for email in emails
        ]
    }


async def _apply_entry_update(
    db: AsyncSession, meeting_id: int, user_email: str, payload: dict
) -> tuple[MeetingEntry, list[ProjectEntry]]:
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    attended = bool(payload.get("attended", False))
    raw_entries = payload.get("project_entries", []) or []

    entry = (
        await db.execute(
            select(MeetingEntry).where(
                MeetingEntry.meeting_instance_id == meeting_id,
                MeetingEntry.user_email == user_email,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        entry = MeetingEntry(
            meeting_instance_id=meeting_id,
            user_email=user_email,
            attended=attended,
        )
        db.add(entry)
        await db.flush()
    else:
        entry.attended = attended

    valid_project_ids = {
        pid for (pid,) in (
            await db.execute(
                select(Project.id).where(Project.meeting_instance_id == meeting_id)
            )
        ).all()
    }
    incoming = {}
    for pe in raw_entries:
        try:
            pid = int(pe.get("project_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="project_id must be int")
        if pid not in valid_project_ids:
            raise HTTPException(
                status_code=422, detail=f"project_id {pid} not in this meeting"
            )
        incoming[pid] = str(pe.get("description", ""))

    existing = (
        await db.execute(
            select(ProjectEntry).where(ProjectEntry.meeting_entry_id == entry.id)
        )
    ).scalars().all()
    existing_by_pid = {pe.project_id: pe for pe in existing}
    for pid_existing, pe in existing_by_pid.items():
        if pid_existing not in incoming:
            await db.delete(pe)
    for pid, desc_text in incoming.items():
        if pid in existing_by_pid:
            existing_by_pid[pid].description = desc_text
        else:
            db.add(
                ProjectEntry(
                    meeting_entry_id=entry.id,
                    project_id=pid,
                    description=desc_text,
                )
            )

    await db.commit()
    return await _load_entry(db, meeting_id, user_email)


@router.put("/meeting/{meeting_id}/my-entry")
async def put_my_entry(
    meeting_id: int,
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    if not user_email:
        raise HTTPException(status_code=400, detail="OIDC session has no email claim")
    entry, project_entries = await _apply_entry_update(db, meeting_id, user_email, payload)
    return {"user_email": user_email, **_entry_to_dict(entry, project_entries)}


@router.put(
    "/meeting/{meeting_id}/entries/{user_email}",
    dependencies=[Depends(require_admin)],
)
async def admin_put_user_entry(
    meeting_id: int,
    user_email: str,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: edit any user's attendance + project entries."""
    if not user_email:
        raise HTTPException(status_code=422, detail="user_email required")
    entry, project_entries = await _apply_entry_update(db, meeting_id, user_email, payload)
    return {"user_email": user_email, **_entry_to_dict(entry, project_entries)}


@router.get(
    "/meeting/{meeting_id}/entries/{user_email}",
    dependencies=[Depends(require_admin)],
)
async def admin_get_user_entry(
    meeting_id: int,
    user_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: inspect any user's entry without impersonating."""
    entry, project_entries = await _load_entry(db, meeting_id, user_email)
    return {"user_email": user_email, **_entry_to_dict(entry, project_entries)}
