"""Internal endpoints for the live meeting form and project catalog."""
from datetime import date, datetime, time

from fastapi import Body, Depends, HTTPException
from sqlalchemy import desc, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_email_from_request, require_admin
from app.auto_create import now_local
from app.database import get_db
from app.models import (
    MeetingInstance,
    MeetingEntry,
    ProjectEntry,
    Project,
    ProjectSubscription,
    UserRoster,
)
from app.routers.internal import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_to_dict(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "leader": p.leader}


def _meeting_summary(m: MeetingInstance) -> dict:
    return {"id": m.id, "meeting_date": m.meeting_date.isoformat()}


async def _load_current_meeting(db: AsyncSession) -> MeetingInstance | None:
    return (
        await db.execute(
            select(MeetingInstance).order_by(desc(MeetingInstance.meeting_date)).limit(1)
        )
    ).scalar_one_or_none()


async def _ensure_subscription(db: AsyncSession, user_email: str, project_id: int) -> None:
    stmt = (
        insert(ProjectSubscription)
        .values(user_email=user_email, project_id=project_id)
        .on_conflict_do_nothing(index_elements=["user_email", "project_id"])
    )
    await db.execute(stmt)


# ---------------------------------------------------------------------------
# Projects (global catalog)
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects(
    q: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(int(limit), 200))
    query = select(Project).order_by(Project.name).limit(limit)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Project.name.ilike(like), Project.leader.ilike(like)))
    rows = (await db.execute(query)).scalars().all()
    return {"projects": [_project_to_dict(p) for p in rows]}


@router.post("/projects")
async def create_project(
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    name = str(payload.get("name", "")).strip()
    leader = str(payload.get("leader", "")).strip()
    if not name or not leader:
        raise HTTPException(status_code=422, detail="name and leader are required")
    proj = Project(name=name, leader=leader, created_by_email=user_email)
    db.add(proj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A project named '{name}' already exists",
        )
    await db.refresh(proj)
    return _project_to_dict(proj)


@router.put("/projects/{project_id}")
async def update_project(
    project_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if "name" in payload:
        new_name = str(payload["name"]).strip()
        if not new_name:
            raise HTTPException(status_code=422, detail="name must be non-empty")
        proj.name = new_name
    if "leader" in payload:
        new_leader = str(payload["leader"]).strip()
        if not new_leader:
            raise HTTPException(status_code=422, detail="leader must be non-empty")
        proj.leader = new_leader
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Another project with that name already exists"
        )
    await db.refresh(proj)
    return _project_to_dict(proj)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(proj)
    await db.commit()
    return {"deleted": project_id}


# ---------------------------------------------------------------------------
# Subscriptions (the user's "my projects" list)
# ---------------------------------------------------------------------------


@router.get("/me/subscriptions")
async def list_my_subscriptions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    rows = (
        await db.execute(
            select(Project)
            .join(ProjectSubscription, ProjectSubscription.project_id == Project.id)
            .where(ProjectSubscription.user_email == user_email)
            .order_by(Project.name)
        )
    ).scalars().all()
    return {"subscriptions": [_project_to_dict(p) for p in rows]}


@router.post("/me/subscriptions/{project_id}")
async def subscribe(
    project_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    if (await db.get(Project, project_id)) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _ensure_subscription(db, user_email, project_id)
    await db.commit()
    return {"subscribed": project_id}


@router.delete("/me/subscriptions/{project_id}")
async def unsubscribe(
    project_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    sub = (
        await db.execute(
            select(ProjectSubscription).where(
                ProjectSubscription.user_email == user_email,
                ProjectSubscription.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if sub is not None:
        await db.delete(sub)
        await db.commit()
    return {"unsubscribed": project_id}


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


@router.get("/meeting/current")
async def get_current_meeting(db: AsyncSession = Depends(get_db)):
    meeting = await _load_current_meeting(db)
    if meeting is None:
        return {"meeting": None}
    return {"meeting": _meeting_summary(meeting)}


@router.get("/meetings")
async def list_meetings(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(MeetingInstance).order_by(desc(MeetingInstance.meeting_date))
        )
    ).scalars().all()
    return {"meetings": [_meeting_summary(m) for m in rows]}


@router.post(
    "/admin/meeting/recreate",
    dependencies=[Depends(require_admin)],
)
async def admin_recreate_meeting(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: create or recreate a Demo meeting for the given date
    (default today, cascading delete of any existing meeting on the same date)."""
    date_str = payload.get("date")
    if date_str:
        try:
            target = date.fromisoformat(str(date_str))
        except ValueError:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    else:
        target = now_local().date()

    existing = (
        await db.execute(
            select(MeetingInstance).where(MeetingInstance.meeting_date == target)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.flush()

    new_m = MeetingInstance(meeting_date=target)
    db.add(new_m)
    await db.commit()
    await db.refresh(new_m)
    return _meeting_summary(new_m)


# --- Per-user meeting entries ---


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


async def _apply_entry_update(
    db: AsyncSession, meeting_id: int, user_email: str, payload: dict
) -> tuple[MeetingEntry, list[ProjectEntry]]:
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    attended = bool(payload.get("attended", False))
    raw_entries = payload.get("project_entries", []) or []

    # Validate project_ids exist in the global catalog (no per-meeting scope now).
    incoming: dict[int, str] = {}
    for pe in raw_entries:
        try:
            pid = int(pe.get("project_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="project_id must be int")
        if (await db.get(Project, pid)) is None:
            raise HTTPException(status_code=422, detail=f"project_id {pid} does not exist")
        incoming[pid] = str(pe.get("description", ""))

    # Upsert MeetingEntry.
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

    # Replace ProjectEntry rows.
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

    # Auto-subscribe the user to every project they're recording a note for.
    for pid in incoming:
        await _ensure_subscription(db, user_email, pid)

    await db.commit()
    return await _load_entry(db, meeting_id, user_email)


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
    entry, project_entries = await _load_entry(db, meeting_id, user_email)
    return {"user_email": user_email, **_entry_to_dict(entry, project_entries)}


# --- Meeting attendees / details ---


@router.get("/meeting/{meeting_id}/attendees")
async def get_attendees(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
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


@router.get("/meeting/{meeting_id}/details")
async def get_meeting_details(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Projects discussed = those with at least one ProjectEntry on this meeting.
    discussed = (
        await db.execute(
            select(Project)
            .join(ProjectEntry, ProjectEntry.project_id == Project.id)
            .join(MeetingEntry, MeetingEntry.id == ProjectEntry.meeting_entry_id)
            .where(MeetingEntry.meeting_instance_id == meeting_id)
            .distinct()
            .order_by(Project.name)
        )
    ).scalars().all()

    end_of_day = datetime.combine(meeting.meeting_date, time(23, 59, 59))
    roster_emails = [
        row[0]
        for row in (
            await db.execute(
                select(UserRoster.email)
                .where(UserRoster.first_seen_at <= end_of_day)
                .order_by(UserRoster.first_seen_at)
            )
        ).all()
    ]

    entries = (
        await db.execute(
            select(MeetingEntry).where(MeetingEntry.meeting_instance_id == meeting_id)
        )
    ).scalars().all()
    entries_by_email = {e.user_email: e for e in entries}
    entry_ids = [e.id for e in entries]
    project_entries = (
        (
            await db.execute(
                select(ProjectEntry).where(
                    ProjectEntry.meeting_entry_id.in_(entry_ids)
                )
            )
        ).scalars().all()
        if entry_ids
        else []
    )
    pe_by_entry_id: dict[int, list[ProjectEntry]] = {}
    for pe in project_entries:
        pe_by_entry_id.setdefault(pe.meeting_entry_id, []).append(pe)

    attendees = []
    for email in roster_emails:
        entry = entries_by_email.get(email)
        if entry is None:
            attendees.append({"email": email, "attended": False, "project_entries": []})
        else:
            pes = pe_by_entry_id.get(entry.id, [])
            attendees.append(
                {
                    "email": email,
                    "attended": entry.attended,
                    "project_entries": [
                        {"project_id": pe.project_id, "description": pe.description}
                        for pe in pes
                    ],
                }
            )

    return {
        "meeting": {
            **_meeting_summary(meeting),
            "projects": [_project_to_dict(p) for p in discussed],
        },
        "attendees": attendees,
    }
