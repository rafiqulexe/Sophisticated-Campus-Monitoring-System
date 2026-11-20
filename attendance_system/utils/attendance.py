"""Attendance marking and reporting utilities."""

import csv
import os
from datetime import datetime
import cv2
import numpy as np

from database.db_manager import get_connection, get_all_students, get_class_roster
from utils.detection import detect_faces, draw_face_boxes, extract_face
from utils.recognition import get_face_embedding, recognize_face
from config import UNKNOWN_DIR

# Per-owner cache of student embeddings to improve performance (W5-T6).
# Key None holds the legacy global view used by the tests.
_known_students_cache = {}

def get_cached_students(force_refresh=False, owner_id=None):
    cached = _known_students_cache.get(owner_id)
    if cached is None or force_refresh:
        cached = get_all_students(owner_id)
        _known_students_cache[owner_id] = cached
    return cached


def mark_attendance(student_id, status="Present", attendance_date=None, owner_id=0,
                    class_id=None, confidence_score=None, needs_review=False,
                    manual=False):
    """Insert or update one attendance row for a student, date and class.

    W1 (v1.1): attendance is now keyed by class as well, and carries the
    match distance that produced it.

    W2 (v2.0): `needs_review` marks a match that landed in the review band,
    and `manual` records that a teacher set this status by hand. A manual
    decision is never overwritten by a later automatic scan, otherwise
    rescanning a room would silently undo the teacher's corrections.

    INSERT OR REPLACE cannot be used here. The uniqueness rule includes
    class_id, and SQLite treats every NULL as distinct in a UNIQUE index, so
    an un-classed scan would insert a fresh duplicate row on every run instead
    of updating the existing one. The lookup below matches NULL explicitly.
    """
    if attendance_date is None:
        attendance_date = datetime.now().strftime("%Y-%m-%d")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        if class_id is None:
            existing = conn.execute(
                "SELECT id, manual_override FROM attendance WHERE owner_id = ? "
                "AND class_id IS NULL AND student_id = ? AND date = ?",
                (owner_id, student_id, attendance_date),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT id, manual_override FROM attendance WHERE owner_id = ? "
                "AND class_id = ? AND student_id = ? AND date = ?",
                (owner_id, class_id, student_id, attendance_date),
            ).fetchone()

        if existing:
            # An automatic scan leaves a teacher's own decision alone.
            if existing["manual_override"] and not manual:
                return True
            conn.execute(
                "UPDATE attendance SET status = ?, confidence_score = ?, "
                "needs_review = ?, manual_override = ?, timestamp = ? WHERE id = ?",
                (status, confidence_score, int(needs_review), int(manual), now,
                 existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO attendance "
                "(student_id, owner_id, class_id, date, status, confidence_score, "
                " needs_review, manual_override, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (student_id, owner_id, class_id, attendance_date, status,
                 confidence_score, int(needs_review), int(manual), now),
            )
        conn.commit()
        return True
    except Exception as exc:
        print(f"Error marking attendance: {exc}")
        return False
    finally:
        conn.close()


VALID_STATUSES = ("Present", "Absent", "Excused")


def set_attendance_status(student_id, status, class_id, attendance_date=None,
                          owner_id=0):
    """W2: a teacher sets a status by hand, clearing any review flag.

    Used by the one-click Present / Absent / Excused controls. The row is
    marked as a manual override so a later scan of the same room does not
    overwrite the decision. Returns (ok, error).
    """
    if status not in VALID_STATUSES:
        return False, f"Status must be one of {', '.join(VALID_STATUSES)}."

    ok = mark_attendance(
        student_id,
        status=status,
        attendance_date=attendance_date,
        owner_id=owner_id,
        class_id=class_id,
        # A hand-set status carries no machine confidence.
        confidence_score=None,
        needs_review=False,
        manual=True,
    )
    return (True, None) if ok else (False, "Could not update the register.")


def resolve_absentees(class_id, attendance_date=None, owner_id=0):
    """W1 (v1.1): Absent = class roster − students already marked Present.

    Writes an explicit Absent row for every enrolled student who has no
    Present record for the date, so the register is complete in the database
    and in any CSV exported from it rather than only being inferred in the UI.
    Students already marked by a teacher (Excused, or a manual override) are
    left exactly as they are.
    """
    if attendance_date is None:
        attendance_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    roster = [r["student_id"] for r in conn.execute(
        "SELECT student_id FROM class_enrollments WHERE class_id = ?", (class_id,)
    )]
    recorded = {r["student_id"] for r in conn.execute(
        "SELECT student_id FROM attendance WHERE class_id = ? AND date = ? AND owner_id = ?",
        (class_id, attendance_date, owner_id),
    )}
    conn.close()

    missing = [sid for sid in roster if sid not in recorded]
    for student_id in missing:
        mark_attendance(student_id, status="Absent", attendance_date=attendance_date,
                        owner_id=owner_id, class_id=class_id)
    return len(missing)


def get_attendance_records(attendance_date=None, owner_id=None):
    """Fetch attendance rows for a specific date (optionally per owner)."""
    if attendance_date is None:
        attendance_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()
    if owner_id is None:
        cursor.execute(
            "SELECT student_id, date, status, timestamp FROM attendance WHERE date = ? ORDER BY timestamp",
            (attendance_date,),
        )
    else:
        cursor.execute(
            "SELECT student_id, date, status, timestamp FROM attendance WHERE date = ? AND owner_id = ? ORDER BY timestamp",
            (attendance_date, owner_id),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_attendance_records_detailed(attendance_date=None, owner_id=None, class_id=None):
    """Fetch attendance rows for a date, joined with student and class names.

    class_id filters to a single course section; omit it for every class.
    """
    if attendance_date is None:
        attendance_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()
    # A student on the roster but not yet face-enrolled has no students row, so
    # the name falls back to the one their roster import carried. Without this
    # every absentee reads as "Unknown student".
    query = """
        SELECT a.student_id, COALESCE(s.name, e.name) AS name,
               a.date, a.status, a.timestamp,
               a.confidence_score, a.class_id,
               a.needs_review, a.manual_override,
               c.course_code, c.section
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.student_id AND a.owner_id = s.owner_id
        LEFT JOIN classes  c ON c.id = a.class_id
        LEFT JOIN class_enrollments e
               ON e.class_id = a.class_id AND e.student_id = a.student_id
        WHERE a.date = ?
    """
    params = [attendance_date]
    if owner_id is not None:
        query += " AND a.owner_id = ?"
        params.append(owner_id)
    if class_id is not None:
        query += " AND a.class_id = ?"
        params.append(class_id)
    # Present first, then Absent, each in the order they were recorded.
    query += " ORDER BY CASE a.status WHEN 'Present' THEN 0 ELSE 1 END, a.timestamp"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def export_attendance_report(output_path, attendance_date=None):
    """Export attendance records to CSV."""
    records = get_attendance_records(attendance_date)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["student_id", "date", "status", "timestamp"])
        writer.writeheader()
        writer.writerows(records)

    return output_path


def process_attendance_image(image, owner_id=None, class_id=None):
    """
    W3-T1: Full pipeline to detect, recognize and mark attendance from an image.
    owner_id scopes recognition and marking to one account's students.

    W1 (v1.1): when class_id is given, recognition is narrowed to that class's
    roster and every enrolled student who is not recognised is marked Absent.

    Returns: (processed_image, list_of_matches)
    """
    # 1. Detect faces
    face_boxes = detect_faces(image)
    if not face_boxes:
        # Still resolve absentees: a class photographed with nobody in it, or
        # with no detectable face, is a class where everyone is absent.
        if class_id is not None:
            resolve_absentees(class_id, owner_id=owner_id or 0)
        return image, []

    # 2. Get known students (Cached for performance W5-T6)
    known_students = get_cached_students(owner_id=owner_id)

    # 3. Narrow to the class roster so a student can never be marked present
    #    in a course they are not enrolled on.
    if class_id is not None:
        roster_ids = {r["student_id"] for r in get_class_roster(class_id)}
        known_students = [s for s in known_students if s["student_id"] in roster_ids]

    matches = []
    labels = []

    # 3. Process each face
    for i, box in enumerate(face_boxes):
        face_img = extract_face(image, box)
        embedding = get_face_embedding(face_img)

        if embedding is not None:
            res = recognize_face(embedding, known_students)
            matches.append(res)

            if res['match']:
                # 4. Mark attendance, keeping the distance and, when the match
                #    only just cleared the boundary, a flag for the teacher.
                review = res.get('band') == 'review'
                mark_attendance(res['student_id'], owner_id=owner_id or 0,
                                class_id=class_id, confidence_score=res['distance'],
                                needs_review=review)
                suffix = ' ?' if review else ''
                labels.append(f"{res['name']} ({res['distance']}){suffix}")
            else:
                # W5-T7: Handle "Unknown" faces – log them for manual review
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unknown_path = os.path.join(UNKNOWN_DIR, f"unknown_{timestamp}_{i}.jpg")
                cv2.imwrite(unknown_path, face_img)

                labels.append(f"Unknown ({res['distance']})")
        else:
            labels.append("Processing Error")
            matches.append({'student_id': 'ERROR', 'name': 'Error', 'match': False})

    # 5. Roster difference: everyone enrolled but not recognised is Absent.
    if class_id is not None:
        resolve_absentees(class_id, owner_id=owner_id or 0)

    # 6. Draw results
    processed_image = draw_face_boxes(image, face_boxes, labels)

    return processed_image, matches
