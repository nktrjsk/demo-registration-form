"""Internal endpoints for the live meeting form and project catalog."""
from datetime import date, datetime, time

from fastapi import Body, Depends, HTTPException
from sqlalchemy import desc, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_email_from_request, is_admin_claims, require_admin
from app.auto_create import now_local
from app.database import get_db
from app.models import (
    MeetingInstance,
    MeetingEntry,
    ProjectEntry,
    Person,
    Project,
    ProjectSubscription,
)
from app.routers.internal import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person_to_dict(p: Person) -> dict:
    return {
        "id": p.id,
        "display_name": p.display_name,
        "email": p.email,
        "resolved": p.email is not None,
    }


def _project_to_dict(p: Project, leader: Person) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "leader": _person_to_dict(leader),
    }


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


async def _load_leader(db: AsyncSession, project: Project) -> Person:
    """Project.leader_person_id is NOT NULL so the lookup is total."""
    leader = await db.get(Person, project.leader_person_id)
    if leader is None:
        # This would mean the FK was violated, which the schema prevents.
        raise HTTPException(status_code=500, detail="Project leader missing")
    return leader


# ---------------------------------------------------------------------------
# People (Persons: real + placeholders)
# ---------------------------------------------------------------------------


@router.get("/people")
async def list_people(
    q: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """All known persons — resolved (email present) and placeholders (no
    email yet). Optional case-insensitive substring search across both
    display_name and email."""
    limit = max(1, min(int(limit), 500))
    query = select(Person).order_by(Person.display_name).limit(limit)
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(Person.display_name.ilike(like), Person.email.ilike(like))
        )
    rows = (await db.execute(query)).scalars().all()
    return {"people": [_person_to_dict(p) for p in rows]}


@router.post("/people")
async def create_person(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Create a placeholder Person. Used by the leader picker when the
    target user isn't in the roster yet — the row will be auto-paired
    when that user signs in via OIDC."""
    display_name = str(payload.get("display_name", "")).strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="display_name required")
    p = Person(display_name=display_name, email=None)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _person_to_dict(p)


@router.patch("/people/{person_id}")
async def rename_person(
    person_id: int,
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: rename a Person. Used to repair display_name when the
    OIDC claim chain produces only an email (e.g. realms that don't
    populate name/given_name)."""
    if not is_admin_claims(request.state.claims):
        raise HTTPException(status_code=403, detail="Admin role required")
    display_name = str(payload.get("display_name", "")).strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="display_name required")
    p = await db.get(Person, person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Person not found")
    p.display_name = display_name
    await db.commit()
    await db.refresh(p)
    return _person_to_dict(p)


# ---------------------------------------------------------------------------
# Projects (global catalog)
# ---------------------------------------------------------------------------


async def _projects_with_leaders(
    db: AsyncSession, query
) -> list[tuple[Project, Person]]:
    rows = (await db.execute(query)).scalars().all()
    if not rows:
        return []
    leader_ids = list({p.leader_person_id for p in rows})
    leaders = (
        await db.execute(select(Person).where(Person.id.in_(leader_ids)))
    ).scalars().all()
    by_id = {l.id: l for l in leaders}
    return [(p, by_id[p.leader_person_id]) for p in rows]


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
        # Search across project name + leader's display_name + email.
        query = (
            select(Project)
            .join(Person, Person.id == Project.leader_person_id)
            .where(
                or_(
                    Project.name.ilike(like),
                    Person.display_name.ilike(like),
                    Person.email.ilike(like),
                )
            )
            .order_by(Project.name)
            .limit(limit)
        )
    paired = await _projects_with_leaders(db, query)
    return {"projects": [_project_to_dict(p, l) for p, l in paired]}


@router.post("/projects")
async def create_project(
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    name = str(payload.get("name", "")).strip()
    try:
        leader_person_id = int(payload.get("leader_person_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="leader_person_id required")
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    if (await db.get(Person, leader_person_id)) is None:
        raise HTTPException(status_code=422, detail="leader_person_id not found")

    proj = Project(
        name=name, leader_person_id=leader_person_id, created_by_email=user_email
    )
    db.add(proj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"A project named '{name}' already exists"
        )
    await db.refresh(proj)
    leader = await _load_leader(db, proj)
    return _project_to_dict(proj, leader)


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
    if "leader_person_id" in payload:
        try:
            new_leader_id = int(payload["leader_person_id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="leader_person_id must be int")
        if (await db.get(Person, new_leader_id)) is None:
            raise HTTPException(status_code=422, detail="leader_person_id not found")
        proj.leader_person_id = new_leader_id
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Another project with that name already exists"
        )
    await db.refresh(proj)
    leader = await _load_leader(db, proj)
    return _project_to_dict(proj, leader)


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
# Subscriptions
# ---------------------------------------------------------------------------


@router.get("/me/subscriptions")
async def list_my_subscriptions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_email = get_email_from_request(request)
    query = (
        select(Project)
        .join(ProjectSubscription, ProjectSubscription.project_id == Project.id)
        .where(ProjectSubscription.user_email == user_email)
        .order_by(Project.name)
    )
    paired = await _projects_with_leaders(db, query)
    return {"subscriptions": [_project_to_dict(p, l) for p, l in paired]}


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
        return {"attending": False, "project_entries": []}
    return {
        "attending": entry.attending,
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

    # attending is admin-only — non-admin callers must not include the key.
    # When absent, keep the existing value (default False on new rows).
    attending_provided = "attending" in payload
    attending = bool(payload.get("attending", False))
    raw_entries = payload.get("project_entries", []) or []

    incoming: dict[int, str] = {}
    for pe in raw_entries:
        try:
            pid = int(pe.get("project_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="project_id must be int")
        if (await db.get(Project, pid)) is None:
            raise HTTPException(status_code=422, detail=f"project_id {pid} does not exist")
        incoming[pid] = str(pe.get("description", ""))

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
            attending=attending if attending_provided else False,
        )
        db.add(entry)
        await db.flush()
    elif attending_provided:
        entry.attending = attending

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
    if "attending" in payload and not is_admin_claims(request.state.claims):
        raise HTTPException(
            status_code=403, detail="Only admins can change attendance"
        )
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
#
# The attendee roster is "Persons who have logged in by the end of this
# meeting day" — i.e., resolved Persons (email IS NOT NULL) whose
# first_seen_at <= the meeting date. Placeholders (no email) are NOT
# attendees; they exist only as project leaders until they sign in.


async def _resolved_persons_by_first_seen(
    db: AsyncSession, end_of_day: datetime
) -> list[tuple[str, str]]:
    """Return (email, display_name) pairs for resolved Persons who had
    logged in by `end_of_day`, ordered by first_seen_at."""
    rows = (
        await db.execute(
            select(Person.email, Person.display_name)
            .where(Person.email.is_not(None), Person.first_seen_at <= end_of_day)
            .order_by(Person.first_seen_at)
        )
    ).all()
    return [(email, name) for (email, name) in rows]


@router.get("/meeting/{meeting_id}/attendees")
async def get_attendees(meeting_id: int, db: AsyncSession = Depends(get_db)):
    """Roster for the current/historic meeting.

    Returns every resolved Person known by the end of the meeting day,
    each with a 3-state `status`:
    - `yes`         — user submitted an entry with attending=True
    - `no`          — user submitted an entry with attending=False
    - `no_response` — user never opened the form for this meeting

    The third state matters because the form defaults aren't an answer:
    a colleague who hasn't logged in this week is different from one
    who's explicitly skipping.
    """
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    end_of_day = datetime.combine(meeting.meeting_date, time(23, 59, 59))
    roster = await _resolved_persons_by_first_seen(db, end_of_day)
    entries = (
        await db.execute(
            select(MeetingEntry.user_email, MeetingEntry.attending).where(
                MeetingEntry.meeting_instance_id == meeting_id
            )
        )
    ).all()
    attending_by = {email: bool(attending) for (email, attending) in entries}

    def _status_for(email: str) -> str:
        if email not in attending_by:
            return "no_response"
        return "yes" if attending_by[email] else "no"

    return {
        "attendees": [
            {
                "email": email,
                "display_name": display_name,
                "status": _status_for(email),
            }
            for (email, display_name) in roster
        ]
    }


@router.get("/meeting/{meeting_id}/details")
async def get_meeting_details(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await db.get(MeetingInstance, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    discussed_query = (
        select(Project)
        .join(ProjectEntry, ProjectEntry.project_id == Project.id)
        .join(MeetingEntry, MeetingEntry.id == ProjectEntry.meeting_entry_id)
        .where(MeetingEntry.meeting_instance_id == meeting_id)
        .distinct()
        .order_by(Project.name)
    )
    discussed_paired = await _projects_with_leaders(db, discussed_query)

    end_of_day = datetime.combine(meeting.meeting_date, time(23, 59, 59))
    roster = await _resolved_persons_by_first_seen(db, end_of_day)

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
                select(ProjectEntry).where(ProjectEntry.meeting_entry_id.in_(entry_ids))
            )
        ).scalars().all()
        if entry_ids
        else []
    )
    pe_by_entry_id: dict[int, list[ProjectEntry]] = {}
    for pe in project_entries:
        pe_by_entry_id.setdefault(pe.meeting_entry_id, []).append(pe)

    attendees = []
    for email, _display_name in roster:
        entry = entries_by_email.get(email)
        if entry is None:
            attendees.append({"email": email, "attending": False, "project_entries": []})
        else:
            pes = pe_by_entry_id.get(entry.id, [])
            attendees.append(
                {
                    "email": email,
                    "attending": entry.attending,
                    "project_entries": [
                        {"project_id": pe.project_id, "description": pe.description}
                        for pe in pes
                    ],
                }
            )

    return {
        "meeting": {
            **_meeting_summary(meeting),
            "projects": [_project_to_dict(p, l) for p, l in discussed_paired],
        },
        "attendees": attendees,
    }
