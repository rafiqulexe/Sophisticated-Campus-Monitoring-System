"""W2 (v2.0) — confidence bands, manual correction and the review flag."""

import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def db(monkeypatch):
    """Point the data layer at a scratch database for each test."""
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


# ── bands ────────────────────────────────────────────────────────────────
def test_confidence_band_boundaries():
    from config import COSINE_MATCH_THRESHOLD, COSINE_REVIEW_THRESHOLD
    from utils.recognition import confidence_band

    assert COSINE_MATCH_THRESHOLD < COSINE_REVIEW_THRESHOLD

    assert confidence_band(0.0) == 'high'
    assert confidence_band(COSINE_MATCH_THRESHOLD) == 'high'      # inclusive
    assert confidence_band(COSINE_MATCH_THRESHOLD + 0.01) == 'review'
    assert confidence_band(COSINE_REVIEW_THRESHOLD) == 'review'   # inclusive
    assert confidence_band(COSINE_REVIEW_THRESHOLD + 0.01) == 'unknown'
    assert confidence_band(None) == 'unknown'


def test_recognize_face_reports_a_band():
    """A near template bands high, a middling one bands review."""
    from utils.recognition import recognize_face

    target = np.zeros(128)
    target[0] = 1.0

    # Identical direction -> cosine distance 0.
    near = {'student_id': 'S1', 'name': 'Near', 'embedding': target.copy()}
    assert recognize_face(target, [near])['band'] == 'high'

    # Orthogonal -> cosine distance 1.0, beyond any accept boundary.
    far = np.zeros(128)
    far[1] = 1.0
    res = recognize_face(target, [{'student_id': 'S2', 'name': 'Far', 'embedding': far}])
    assert res['match'] is False
    assert res['band'] == 'unknown'


def test_recognize_face_with_no_students_is_unknown():
    from utils.recognition import recognize_face

    res = recognize_face(np.ones(128), [])
    assert res['match'] is False
    assert res['band'] == 'unknown'
    assert res['distance'] is None


# ── review flag ──────────────────────────────────────────────────────────
def test_review_flag_is_persisted(db):
    from utils.attendance import get_attendance_records_detailed, mark_attendance

    cid = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    db.enroll_student(cid, "2122414642", owner_id=OWNER, name="Rafiqul Islam")

    mark_attendance("2122414642", owner_id=OWNER, class_id=cid,
                    confidence_score=0.52, needs_review=True)

    row = get_attendance_records_detailed(owner_id=OWNER, class_id=cid)[0]
    assert row["status"] == "Present"
    assert row["needs_review"] == 1
    assert row["manual_override"] == 0


# ── manual correction ────────────────────────────────────────────────────
def test_set_status_marks_override_and_clears_review(db):
    from utils.attendance import (
        get_attendance_records_detailed,
        mark_attendance,
        set_attendance_status,
    )

    cid = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    db.enroll_student(cid, "2122414642", owner_id=OWNER, name="Rafiqul Islam")

    mark_attendance("2122414642", owner_id=OWNER, class_id=cid,
                    confidence_score=0.52, needs_review=True)
    ok, err = set_attendance_status("2122414642", "Excused", cid, owner_id=OWNER)
    assert ok and err is None

    row = get_attendance_records_detailed(owner_id=OWNER, class_id=cid)[0]
    assert row["status"] == "Excused"
    assert row["needs_review"] == 0
    assert row["manual_override"] == 1
    # A hand-set status carries no machine confidence.
    assert row["confidence_score"] is None


def test_set_status_rejects_an_unknown_status(db):
    from utils.attendance import set_attendance_status

    cid = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    ok, err = set_attendance_status("2122414642", "Maybe", cid, owner_id=OWNER)
    assert ok is False
    assert "Present" in err


def test_rescan_does_not_overwrite_a_teachers_decision(db):
    """The point of manual_override: a later scan must not undo a correction."""
    from utils.attendance import (
        get_attendance_records_detailed,
        mark_attendance,
        set_attendance_status,
    )

    cid = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    db.enroll_student(cid, "2122414642", owner_id=OWNER, name="Rafiqul Islam")

    mark_attendance("2122414642", owner_id=OWNER, class_id=cid, confidence_score=0.55,
                    needs_review=True)
    set_attendance_status("2122414642", "Absent", cid, owner_id=OWNER)

    # A second scan recognises the same face again.
    mark_attendance("2122414642", owner_id=OWNER, class_id=cid, confidence_score=0.21)

    row = get_attendance_records_detailed(owner_id=OWNER, class_id=cid)[0]
    assert row["status"] == "Absent", "an automatic scan overwrote a manual decision"
    assert row["manual_override"] == 1


def test_absentee_resolution_leaves_excused_alone(db):
    """Closing the register must not turn an Excused student into Absent."""
    from utils.attendance import (
        get_attendance_records_detailed,
        resolve_absentees,
        set_attendance_status,
    )

    cid = db.create_class("CSE499", "Senior Design", "15", "Summer 2026", owner_id=OWNER)
    for sid in ("S001", "S002", "S003"):
        db.enroll_student(cid, sid, owner_id=OWNER)

    set_attendance_status("S001", "Excused", cid, owner_id=OWNER)
    assert resolve_absentees(cid, owner_id=OWNER) == 2

    by_id = {r["student_id"]: r["status"]
             for r in get_attendance_records_detailed(owner_id=OWNER, class_id=cid)}
    assert by_id == {"S001": "Excused", "S002": "Absent", "S003": "Absent"}
