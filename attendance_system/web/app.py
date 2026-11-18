"""Flask web application for the student attendance system.

Serves the web UI (Jinja templates + static assets) and a small JSON API
that wraps the existing detection/recognition pipeline in ``utils/``.
"""

import base64
import csv
import io
import os
import secrets
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from config import DATA_DIR, NUM_REGISTRATION_SAMPLES
from database.db_manager import (
    create_class,
    delete_class,
    delete_student,
    enroll_student,
    get_all_students,
    get_class,
    get_class_roster,
    get_classes,
    get_classes_for_student,
    get_student_count,
    get_user_by_id,
    init_database,
    unenroll_student,
    update_class,
)
from utils.attendance import (
    get_attendance_records_detailed,
    get_cached_students,
    process_attendance_image,
    resolve_absentees,
)
from utils.registration import finalize_registration, process_registration_sample
from web.auth import auth_bp


def decode_image_payload(payload):
    """Decode a base64 data-URL (or raw base64) into a BGR image."""
    if not payload or not isinstance(payload, str):
        return None
    try:
        if "," in payload:
            payload = payload.split(",", 1)[1]
        buffer = np.frombuffer(base64.b64decode(payload), dtype=np.uint8)
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    except Exception:
        return None


def encode_image_payload(image):
    """Encode a BGR image as a JPEG data-URL."""
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("ascii")


def _load_secret_key():
    """Persist a random secret key so sessions survive app restarts."""
    os.makedirs(DATA_DIR, exist_ok=True)
    key_path = os.path.join(DATA_DIR, "secret_key")
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    key = secrets.token_hex(32)
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write(key)
    return key


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB uploads
    app.secret_key = _load_secret_key()
    app.register_blueprint(auth_bp)
    init_database()

    def current_owner_id():
        """user id of the logged-in account; every query is scoped to it."""
        return session.get("user_id")

    @app.before_request
    def require_login():
        # Auth pages and static assets are public; everything else needs a
        # logged-in account so each person only reaches their own data.
        if request.path.startswith("/static") or request.endpoint in (
            "auth.login_page",
            "auth.login_submit",
            "auth.signup_page",
            "auth.signup_submit",
        ):
            return None
        if session.get("user_id") is None:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Not authenticated."}), 401
            return redirect(url_for("auth.login_page", next=request.path))
        return None

    @app.context_processor
    def inject_current_user():
        return {"current_user": get_user_by_id(session["user_id"]) if session.get("user_id") else None}

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index_page():
        return render_template("index.html")

    @app.get("/register")
    def register_page():
        return render_template("register.html", num_samples=NUM_REGISTRATION_SAMPLES)

    @app.get("/attendance")
    def attendance_page():
        return render_template("attendance.html")

    @app.get("/reports")
    def reports_page():
        return render_template("reports.html")

    # W1 (v1.1): course / section management
    @app.get("/courses")
    def courses_page():
        return render_template("courses.html")

    @app.get("/courses/<int:class_id>")
    def course_detail_page(class_id):
        course = get_class(class_id, current_owner_id())
        if course is None:
            return render_template("not_found.html"), 404
        return render_template("course_detail.html", course=course)

    @app.get("/students")
    def students_page():
        return render_template("students.html")

    # -------------------------------------------------------------------- api
    @app.get("/api/stats")
    def api_stats():
        owner_id = current_owner_id()
        today = datetime.now().strftime("%Y-%m-%d")
        records = get_attendance_records_detailed(today, owner_id, _requested_class_id())
        students = get_student_count(owner_id)

        # W1: the register now also holds Absent rows, so presence has to be
        # counted by status rather than by distinct student id.
        present_rows = [r for r in records if r["status"] == "Present"]
        present = len({r["student_id"] for r in present_rows})
        absent = len({r["student_id"] for r in records if r["status"] == "Absent"})

        return jsonify(
            {
                "ok": True,
                "today": today,
                "students": students,
                "classes": len(get_classes(owner_id)),
                "present_today": present,
                "absent_today": absent,
                "rate": round(100.0 * present / students, 1) if students else 0.0,
                "recent": list(reversed(present_rows))[:8],
            }
        )

    # ------------------------------------------------------- classes (W1)
    @app.get("/api/classes")
    def api_classes():
        return jsonify({"ok": True, "classes": get_classes(current_owner_id())})

    @app.post("/api/classes")
    def api_class_create():
        payload = request.get_json(silent=True) or {}
        fields = {k: (payload.get(k) or "").strip()
                  for k in ("course_code", "course_name", "section", "semester")}
        missing = [k for k, v in fields.items() if not v]
        if missing:
            return jsonify({
                "ok": False,
                "error": "All course fields are required.",
                "fields": missing,
            }), 400

        class_id = create_class(**fields, owner_id=current_owner_id())
        if class_id is None:
            return jsonify({
                "ok": False,
                "error": f"{fields['course_code']} section {fields['section']} "
                         f"already exists for {fields['semester']}.",
            }), 409
        return jsonify({"ok": True, "class_id": class_id})

    @app.put("/api/classes/<int:class_id>")
    def api_class_update(class_id):
        if get_class(class_id, current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404
        payload = request.get_json(silent=True) or {}
        fields = {k: (payload.get(k) or "").strip()
                  for k in ("course_code", "course_name", "section", "semester")}
        if not all(fields.values()):
            return jsonify({"ok": False, "error": "All course fields are required."}), 400
        if not update_class(class_id, **fields, owner_id=current_owner_id()):
            return jsonify({"ok": False, "error": "Another section already uses those details."}), 409
        return jsonify({"ok": True})

    @app.delete("/api/classes/<int:class_id>")
    def api_class_delete(class_id):
        if get_class(class_id, current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404
        delete_class(class_id, current_owner_id())
        return jsonify({"ok": True})

    @app.get("/api/classes/<int:class_id>/roster")
    def api_class_roster(class_id):
        course = get_class(class_id, current_owner_id())
        if course is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404
        return jsonify({"ok": True, "course": course, "roster": get_class_roster(class_id)})

    @app.post("/api/classes/<int:class_id>/roster")
    def api_class_enroll(class_id):
        if get_class(class_id, current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404

        payload = request.get_json(silent=True) or {}
        ids = payload.get("student_ids")
        if not isinstance(ids, list):
            ids = [payload.get("student_id")]

        added, skipped = [], []
        for raw in ids:
            student_id = (raw or "").strip()
            if not student_id:
                continue
            if enroll_student(class_id, student_id, owner_id=current_owner_id()):
                added.append(student_id)
            else:
                skipped.append(student_id)
        return jsonify({"ok": True, "added": added, "skipped": skipped})

    @app.delete("/api/classes/<int:class_id>/roster/<student_id>")
    def api_class_unenroll(class_id, student_id):
        if get_class(class_id, current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404
        unenroll_student(class_id, student_id)
        return jsonify({"ok": True})

    @app.post("/api/classes/<int:class_id>/roster/import")
    def api_class_roster_import(class_id):
        """Import a roster from CSV text.

        Accepts a header row or none, and takes the first column as the
        student ID and an optional second column as the student's name. The
        name is kept because a roster is normally loaded from the registrar's
        export before anyone has been photographed — without it the register
        can only show an ID for every student who is not yet face-enrolled.
        """
        if get_class(class_id, current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404

        payload = request.get_json(silent=True) or {}
        text = payload.get("csv") or ""
        if not text.strip():
            return jsonify({"ok": False, "error": "The file was empty."}), 400

        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return jsonify({"ok": False, "error": "No rows found in the file."}), 400

        # Drop a header row if the first cell is clearly a label, not an ID.
        header_like = {"student_id", "student id", "id", "studentid", "roll", "roll no"}
        if rows and rows[0] and rows[0][0].strip().lower() in header_like:
            rows = rows[1:]

        added, skipped = [], []
        for row in rows:
            if not row:
                continue
            student_id = row[0].strip()
            if not student_id:
                continue
            name = row[1].strip() if len(row) > 1 else None
            if enroll_student(class_id, student_id, owner_id=current_owner_id(), name=name):
                added.append(student_id)
            else:
                skipped.append(student_id)

        if not added and not skipped:
            return jsonify({"ok": False, "error": "No student IDs found in the file."}), 400
        return jsonify({"ok": True, "added": added, "skipped": skipped})

    # -------------------------------------------------------- students (W1)
    @app.get("/api/students")
    def api_students():
        owner_id = current_owner_id()
        students = [
            {"student_id": s["student_id"], "name": s["name"]}
            for s in get_all_students(owner_id)
        ]
        for student in students:
            student["classes"] = [
                f"{c['course_code']}-{c['section']}"
                for c in get_classes_for_student(student["student_id"], owner_id)
            ]
        students.sort(key=lambda s: s["student_id"])
        return jsonify({"ok": True, "students": students})

    @app.delete("/api/students/<student_id>")
    def api_student_delete(student_id):
        owner_id = current_owner_id()
        known = {s["student_id"] for s in get_all_students(owner_id)}
        if student_id not in known:
            return jsonify({"ok": False, "error": "Student not found."}), 404
        delete_student(student_id, owner_id)
        get_cached_students(force_refresh=True, owner_id=owner_id)
        return jsonify({"ok": True})

    @app.post("/api/register/batch")
    def api_register_batch():
        payload = request.get_json(silent=True) or {}
        images = payload.get("images") or []
        names = payload.get("names") or []
        if not isinstance(images, list) or not images:
            return jsonify({"ok": False, "error": "No images received."}), 400

        embeddings, errors = [], []
        for index, data_url in enumerate(images):
            label = names[index] if index < len(names) else f"Image {index + 1}"
            image = decode_image_payload(data_url)
            if image is None:
                errors.append(f"{label}: could not be read.")
                continue
            embedding, error = process_registration_sample(image)
            if embedding is None:
                errors.append(f"{label}: {error}")
            else:
                embeddings.append(embedding.tolist())

        return jsonify(
            {"ok": bool(embeddings), "embeddings": embeddings, "errors": errors}
        )

    @app.post("/api/register/finalize")
    def api_register_finalize():
        payload = request.get_json(silent=True) or {}
        student_id = (payload.get("student_id") or "").strip()
        name = (payload.get("name") or "").strip()
        embeddings = payload.get("embeddings") or []
        if not student_id or not name:
            return jsonify(
                {"ok": False, "error": "Student ID and name are required."}
            ), 400
        if not isinstance(embeddings, list) or len(embeddings) < NUM_REGISTRATION_SAMPLES:
            return jsonify(
                {
                    "ok": False,
                    "error": f"At least {NUM_REGISTRATION_SAMPLES} samples are required "
                    f"(received {len(embeddings) if isinstance(embeddings, list) else 0}).",
                }
            ), 400

        vectors = [np.asarray(e, dtype=np.float64) for e in embeddings]
        success = finalize_registration(student_id, name, vectors, owner_id=current_owner_id())
        if not success:
            return jsonify(
                {"ok": False, "error": f"Student ID '{student_id}' already exists."}
            ), 409

        # Refresh this account's embedding cache.
        get_cached_students(force_refresh=True, owner_id=current_owner_id())
        return jsonify({"ok": True, "student_id": student_id, "name": name})

    @app.post("/api/attendance")
    def api_attendance():
        payload = request.get_json(silent=True) or {}
        image = decode_image_payload(payload.get("image"))
        if image is None:
            return jsonify({"ok": False, "error": "Could not read the image."}), 400

        # W1: an optional class scopes recognition to that roster and triggers
        # absentee resolution once the scan completes.
        class_id = payload.get("class_id")
        class_id = int(class_id) if class_id not in (None, "", "null") else None
        if class_id is not None and get_class(class_id, current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404

        processed, matches = process_attendance_image(
            image, owner_id=current_owner_id(), class_id=class_id
        )
        result_image = encode_image_payload(processed)
        if result_image is None:
            return jsonify(
                {"ok": False, "error": "Failed to encode the processed image."}
            ), 500

        recognized = [
            {
                "student_id": m["student_id"],
                "name": m["name"],
                "distance": m["distance"],
            }
            for m in matches
            if m.get("match")
        ]
        unknown = sum(1 for m in matches if not m.get("match"))

        # With a class selected the register is now complete, so report the
        # roster arithmetic back to the UI.
        absent_count = 0
        roster_size = 0
        if class_id is not None:
            roster_size = len(get_class_roster(class_id))
            records = get_attendance_records_detailed(
                owner_id=current_owner_id(), class_id=class_id
            )
            absent_count = sum(1 for r in records if r["status"] == "Absent")

        return jsonify(
            {
                "ok": True,
                "image": result_image,
                "faces_detected": len(matches),
                "recognized": recognized,
                "unknown_count": unknown,
                "class_id": class_id,
                "roster_size": roster_size,
                "absent_count": absent_count,
            }
        )

    def _requested_class_id():
        """Read ?class_id= as an int, treating blank/all as 'every class'."""
        raw = request.args.get("class_id")
        if raw in (None, "", "all"):
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @app.get("/api/attendance/records")
    def api_attendance_records():
        date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        return jsonify(
            {
                "ok": True,
                "date": date,
                "records": get_attendance_records_detailed(
                    date, current_owner_id(), _requested_class_id()
                ),
            }
        )

    @app.post("/api/attendance/resolve")
    def api_attendance_resolve():
        """Close a register: mark every unrecognised roster member Absent."""
        payload = request.get_json(silent=True) or {}
        class_id = payload.get("class_id")
        if class_id is None or get_class(int(class_id), current_owner_id()) is None:
            return jsonify({"ok": False, "error": "Course not found."}), 404
        date = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
        marked = resolve_absentees(int(class_id), date, current_owner_id())
        return jsonify({"ok": True, "marked_absent": marked})

    @app.get("/api/reports/download")
    def api_reports_download():
        date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        class_id = _requested_class_id()
        records = get_attendance_records_detailed(date, current_owner_id(), class_id)

        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "student_id", "name", "course_code", "section",
                "date", "status", "confidence_score", "timestamp",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(records)

        suffix = ""
        if class_id is not None:
            course = get_class(class_id, current_owner_id())
            if course:
                suffix = f"_{course['course_code']}-{course['section']}"
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=attendance{suffix}_{date}.csv"
            },
        )

    return app
