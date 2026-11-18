"""W1 (v1.1) — course/section roster layer and absentee resolution.

Covers the roadmap's Week 1 acceptance criterion directly: select a section,
recognise 5 students out of a 30-student roster, and end up with 5 Present
and 25 Absent recorded.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def db(monkeypatch):
    """Point the whole data layer at a scratch database for each test."""
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)

    import config
    from database import db_manager

    monkeypatch.setattr(config, "DATABASE_PATH", path, raising=False)
    monkeypatch.setattr(db_manager, "DATABASE_PATH", path, raising=False)
    monkeypatch.setattr(db_manager, "_schema_ready_for", None, raising=False)

    db_manager.init_database()
    yield db_manager

    db_manager._schema_ready_for = None
    if os.path.exists(path):
        os.unlink(path)


OWNER = 1


def test_create_and_list_classes(db):
    class_id = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    assert class_id is not None

    classes = db.get_classes(owner_id=OWNER)
    assert len(classes) == 1
    assert classes[0]["course_code"] == "CSE499"
    assert classes[0]["section"] == "15"
    assert classes[0]["student_count"] == 0


def test_duplicate_section_rejected(db):
    assert db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    # Same code+section+semester for the same owner is a collision...
    assert db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER) is None
    # ...but a different section is fine.
    assert db.create_class("CSE499", "Senior Design", "16", "Summer 2026", owner_id=OWNER)


def test_classes_are_owner_scoped(db):
    db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=1)
    db.create_class("CSE331", "Compilers", "1", "Summer 2026", owner_id=2)

    assert len(db.get_classes(owner_id=1)) == 1
    assert len(db.get_classes(owner_id=2)) == 1
    # A class belonging to owner 2 is invisible to owner 1.
    foreign = db.get_classes(owner_id=2)[0]["id"]
    assert db.get_class(foreign, owner_id=1) is None
    assert db.get_class(foreign, owner_id=2) is not None


def test_roster_enrollment_and_removal(db):
    class_id = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)

    assert db.enroll_student(class_id, "CSE499-001", owner_id=OWNER) is True
    # Enrolling the same student twice is a no-op, not a crash.
    assert db.enroll_student(class_id, "CSE499-001", owner_id=OWNER) is False

    roster = db.get_class_roster(class_id)
    assert [r["student_id"] for r in roster] == ["CSE499-001"]
    # Imported from a roster CSV, so no face enrolled yet.
    assert roster[0]["has_face"] == 0

    assert db.unenroll_student(class_id, "CSE499-001") is True
    assert db.get_class_roster(class_id) == []


def test_week1_acceptance_5_present_25_absent(db):
    """The roadmap's stated Week 1 target."""
    from utils.attendance import (
        get_attendance_records_detailed,
        mark_attendance,
        resolve_absentees,
    )

    class_id = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)

    roster = [f"CSE499-{n:03d}" for n in range(1, 31)]
    for student_id in roster:
        db.enroll_student(class_id, student_id, owner_id=OWNER)
    assert len(db.get_class_roster(class_id)) == 30

    # Five students recognised by the pipeline.
    recognised = roster[:5]
    for student_id in recognised:
        mark_attendance(student_id, owner_id=OWNER, class_id=class_id, confidence_score=0.31)

    absent_count = resolve_absentees(class_id, owner_id=OWNER)
    assert absent_count == 25

    records = get_attendance_records_detailed(owner_id=OWNER, class_id=class_id)
    present = [r for r in records if r["status"] == "Present"]
    absent = [r for r in records if r["status"] == "Absent"]

    assert len(present) == 5, "expected exactly the recognised students present"
    assert len(absent) == 25, "expected the rest of the roster absent"
    assert len(records) == 30, "every enrolled student must appear in the register"
    assert {r["student_id"] for r in present} == set(recognised)
    # The match distance is persisted for present students only.
    assert all(r["confidence_score"] == 0.31 for r in present)
    assert all(r["confidence_score"] is None for r in absent)


def test_resolve_absentees_is_idempotent(db):
    """Re-scanning a class must not duplicate or overwrite the register."""
    from utils.attendance import get_attendance_records_detailed, mark_attendance, resolve_absentees

    class_id = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    for n in range(1, 11):
        db.enroll_student(class_id, f"S{n:03d}", owner_id=OWNER)

    mark_attendance("S001", owner_id=OWNER, class_id=class_id, confidence_score=0.2)
    assert resolve_absentees(class_id, owner_id=OWNER) == 9
    # Second pass finds nobody left to mark.
    assert resolve_absentees(class_id, owner_id=OWNER) == 0
    assert len(get_attendance_records_detailed(owner_id=OWNER, class_id=class_id)) == 10

    # A late arrival flips Absent to Present without adding a row.
    mark_attendance("S002", owner_id=OWNER, class_id=class_id, confidence_score=0.25)
    records = get_attendance_records_detailed(owner_id=OWNER, class_id=class_id)
    assert len(records) == 10
    statuses = {r["student_id"]: r["status"] for r in records}
    assert statuses["S002"] == "Present"


def test_same_student_two_classes_same_day(db):
    """The reason the uniqueness rule had to widen to include class_id."""
    from utils.attendance import get_attendance_records_detailed, mark_attendance

    a = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    b = db.create_class("CSE331", "Compilers", "1", "Summer 2026", owner_id=OWNER)
    db.enroll_student(a, "S001", owner_id=OWNER)
    db.enroll_student(b, "S001", owner_id=OWNER)

    assert mark_attendance("S001", owner_id=OWNER, class_id=a, confidence_score=0.3)
    assert mark_attendance("S001", owner_id=OWNER, class_id=b, confidence_score=0.4)

    assert len(get_attendance_records_detailed(owner_id=OWNER, class_id=a)) == 1
    assert len(get_attendance_records_detailed(owner_id=OWNER, class_id=b)) == 1
    # Both rows coexist on the same date for the same student.
    assert len(get_attendance_records_detailed(owner_id=OWNER)) == 2


def test_unclassed_attendance_does_not_duplicate(db):
    """NULL class_id must still upsert, despite SQLite's NULL uniqueness rule."""
    from utils.attendance import get_attendance_records_detailed, mark_attendance

    mark_attendance("S001", owner_id=OWNER)
    mark_attendance("S001", owner_id=OWNER)
    mark_attendance("S001", owner_id=OWNER)

    records = get_attendance_records_detailed(owner_id=OWNER)
    assert len(records) == 1, "repeat scans without a class must update, not duplicate"


def test_deleting_class_removes_roster_and_attendance(db):
    from utils.attendance import get_attendance_records_detailed, mark_attendance

    class_id = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    db.enroll_student(class_id, "S001", owner_id=OWNER)
    mark_attendance("S001", owner_id=OWNER, class_id=class_id)

    assert db.delete_class(class_id, owner_id=OWNER) is True
    assert db.get_class(class_id, owner_id=OWNER) is None
    assert db.get_class_roster(class_id) == []
    assert get_attendance_records_detailed(owner_id=OWNER, class_id=class_id) == []
