# database/db_manager.py
import sqlite3
import pickle
import numpy as np
import os
import sys

# Fix: Add parent directory to path so 'config' can be found
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from config import DATABASE_PATH

# Legacy rows created before the login system existed are owned by user 0.
# The first account created claims them (see utils/auth.create_user).
LEGACY_OWNER_ID = 0

_schema_ready_for = None

def get_connection():
    """Create and return database connection.

    The first connection to a database file also ensures the schema exists
    (and migrates a pre-login database), so callers that never run
    init_database() explicitly still work against the current schema.
    """
    global _schema_ready_for
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    if _schema_ready_for != DATABASE_PATH:
        _ensure_schema(conn)
        _schema_ready_for = DATABASE_PATH
    return conn

def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

def _migrate_legacy_tables(conn):
    """Rebuild pre-login tables so students/attendance carry an owner_id.

    SQLite cannot ALTER TABLE to add UNIQUE constraints, so the old tables
    are renamed, recreated with the new schema, and their rows copied with
    owner_id = LEGACY_OWNER_ID.
    """
    for table, create_sql, columns in [
        (
            "students",
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                name TEXT NOT NULL,
                embedding BLOB NOT NULL,
                owner_id INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_id, student_id)
            )
            """,
            "student_id, name, embedding, owner_id, created_at",
        ),
        (
            "attendance",
            """
            CREATE TABLE attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                owner_id INTEGER NOT NULL DEFAULT 0,
                date DATE NOT NULL,
                status TEXT DEFAULT 'Present',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(student_id),
                UNIQUE(owner_id, student_id, date)
            )
            """,
            "student_id, owner_id, date, status, timestamp",
        ),
    ]:
        cols = _table_columns(conn, table)
        if not cols or "owner_id" in cols:
            continue  # fresh install or already migrated
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        conn.execute(create_sql)
        conn.execute(
            f"INSERT INTO {table} ({columns}) "
            f"SELECT {columns.replace('owner_id', str(LEGACY_OWNER_ID))} FROM {table}_old"
        )
        conn.execute(f"DROP TABLE {table}_old")

def _migrate_attendance_for_classes(conn):
    """W1 (v1.1): give attendance a class_id and confidence_score.

    The pre-class table is UNIQUE(owner_id, student_id, date), which allows a
    student exactly one attendance row per day. Once a student belongs to more
    than one course that constraint silently rejects the second course's
    register, so uniqueness has to widen to include the class.

    SQLite cannot ALTER TABLE to change a UNIQUE constraint, so the table is
    rebuilt the same way _migrate_legacy_tables does it. Existing rows predate
    courses and keep class_id = NULL rather than being invented into some
    arbitrary class.
    """
    cols = _table_columns(conn, "attendance")
    if not cols or "class_id" in cols:
        return  # fresh install, or already migrated

    conn.execute("ALTER TABLE attendance RENAME TO attendance_pre_class")
    conn.execute(ATTENDANCE_SCHEMA)
    conn.execute(
        "INSERT INTO attendance "
        "(student_id, owner_id, class_id, date, status, confidence_score, timestamp) "
        "SELECT student_id, owner_id, NULL, date, status, NULL, timestamp "
        "FROM attendance_pre_class"
    )
    conn.execute("DROP TABLE attendance_pre_class")


# Single source of truth for the attendance table, used both by _ensure_schema
# and by the rebuild in _migrate_attendance_for_classes.
ATTENDANCE_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        owner_id INTEGER NOT NULL DEFAULT 0,
        class_id INTEGER,
        date DATE NOT NULL,
        status TEXT DEFAULT 'Present',
        -- Euclidean distance from the matched embedding: LOWER IS A BETTER
        -- match. Named confidence_score to match the roadmap spec; NULL for
        -- absentees and for rows written before this column existed.
        confidence_score REAL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(student_id),
        FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
        UNIQUE(owner_id, class_id, student_id, date)
    )
'''


def _ensure_schema(conn):
    """Create missing tables and migrate a pre-login schema in place."""
    _migrate_legacy_tables(conn)

    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            name TEXT NOT NULL,
            embedding BLOB NOT NULL,
            owner_id INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, student_id)
        )
    ''')

    # W1 (v1.1): courses / sections, and the students enrolled on each.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL DEFAULT 0,
            course_code TEXT NOT NULL,
            course_name TEXT NOT NULL,
            section TEXT NOT NULL,
            semester TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, course_code, section, semester)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS class_enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            owner_id INTEGER NOT NULL DEFAULT 0,
            -- Name as it appeared on the imported roster. A roster row exists
            -- before the student has been photographed, so this is the only
            -- name available until a face profile is created; without it the
            -- register can only show "Unknown student" for every absentee.
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
            UNIQUE(class_id, student_id)
        )
    ''')

    # Add the column to rosters created before it existed.
    if "name" not in _table_columns(conn, "class_enrollments"):
        conn.execute("ALTER TABLE class_enrollments ADD COLUMN name TEXT")

    _migrate_attendance_for_classes(conn)
    conn.execute(ATTENDANCE_SCHEMA)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_enroll_class ON class_enrollments(class_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attend_lookup ON attendance(owner_id, class_id, date)")

    conn.commit()


def init_database():
    """W1-T3, W1-T4: Create database and tables"""
    conn = get_connection()
    conn.close()
    print("Database initialized successfully!")

def add_student(student_id, name, embedding, owner_id=LEGACY_OWNER_ID):
    """Store student with averaged face embedding, scoped to an owner"""
    conn = get_connection()
    cursor = conn.cursor()
    embedding_blob = pickle.dumps(embedding.astype(np.float64))

    try:
        cursor.execute(
            "INSERT INTO students (student_id, name, embedding, owner_id) VALUES (?, ?, ?, ?)",
            (student_id, name, embedding_blob, owner_id)
        )
        conn.commit()
        print(f"Student '{name}' (ID: {student_id}) registered!")
        return True
    except sqlite3.IntegrityError:
        print(f"Student ID '{student_id}' already exists!")
        return False
    finally:
        conn.close()

def get_all_students(owner_id=None):
    """Retrieve students with their embeddings.

    owner_id=None returns every student (legacy global view used by the
    tests); the web app always passes the logged-in user's id so each
    account only sees its own students.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if owner_id is None:
        cursor.execute("SELECT student_id, name, embedding FROM students")
    else:
        cursor.execute(
            "SELECT student_id, name, embedding FROM students WHERE owner_id = ?",
            (owner_id,)
        )

    students = []
    for row in cursor.fetchall():
        students.append({
            'student_id': row['student_id'],
            'name': row['name'],
            'embedding': pickle.loads(row['embedding'])
        })

    conn.close()
    return students

def get_student_count(owner_id=None):
    """Get total number of registered students (optionally per owner)"""
    conn = get_connection()
    cursor = conn.cursor()
    if owner_id is None:
        cursor.execute("SELECT COUNT(*) FROM students")
    else:
        cursor.execute("SELECT COUNT(*) FROM students WHERE owner_id = ?", (owner_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def delete_student(student_id, owner_id=None):
    """Remove a student from database (optionally scoped to an owner)"""
    conn = get_connection()
    cursor = conn.cursor()
    if owner_id is None:
        cursor.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
    else:
        cursor.execute(
            "DELETE FROM students WHERE student_id = ? AND owner_id = ?",
            (student_id, owner_id)
        )
    conn.commit()
    conn.close()
    print(f"Student {student_id} deleted")

# ----------------------------------------------------------------- classes
# W1 (v1.1): course / section management and roster enrollment.

def create_class(course_code, course_name, section, semester, owner_id=LEGACY_OWNER_ID):
    """Create a course section. Returns its id, or None if it already exists."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO classes (owner_id, course_code, course_name, section, semester) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner_id, course_code, course_name, section, semester),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_classes(owner_id=None):
    """List an owner's course sections with their enrolled-student counts."""
    conn = get_connection()
    sql = (
        "SELECT c.*, COUNT(e.id) AS student_count "
        "FROM classes c LEFT JOIN class_enrollments e ON e.class_id = c.id "
    )
    params = ()
    if owner_id is not None:
        sql += "WHERE c.owner_id = ? "
        params = (owner_id,)
    sql += "GROUP BY c.id ORDER BY c.course_code, c.section"
    rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return rows


def get_class(class_id, owner_id=None):
    """Fetch one course section, scoped to its owner when one is given."""
    conn = get_connection()
    sql = "SELECT * FROM classes WHERE id = ?"
    params = [class_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def update_class(class_id, course_code, course_name, section, semester, owner_id=None):
    """Rename or re-section a course. False if it collides with another row."""
    conn = get_connection()
    sql = ("UPDATE classes SET course_code = ?, course_name = ?, section = ?, semester = ? "
           "WHERE id = ?")
    params = [course_code, course_name, section, semester, class_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    try:
        changed = conn.execute(sql, params).rowcount
        conn.commit()
        return changed > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_class(class_id, owner_id=None):
    """Delete a course section together with its roster and attendance."""
    conn = get_connection()
    sql = "DELETE FROM classes WHERE id = ?"
    params = [class_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    # ON DELETE CASCADE only fires with foreign keys enabled, which is off by
    # default in SQLite, so dependent rows are removed explicitly.
    conn.execute("DELETE FROM class_enrollments WHERE class_id = ?", (class_id,))
    conn.execute("DELETE FROM attendance WHERE class_id = ?", (class_id,))
    changed = conn.execute(sql, params).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def enroll_student(class_id, student_id, owner_id=LEGACY_OWNER_ID, name=None):
    """Add one student to a class roster. False if already enrolled.

    `name` is the roster name from a CSV import, kept so the register can
    identify students who have not been face-enrolled yet.
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO class_enrollments (class_id, student_id, owner_id, name) "
            "VALUES (?, ?, ?, ?)",
            (class_id, student_id, owner_id, (name or "").strip() or None),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def unenroll_student(class_id, student_id):
    """Remove a student from a class roster (their attendance is kept)."""
    conn = get_connection()
    changed = conn.execute(
        "DELETE FROM class_enrollments WHERE class_id = ? AND student_id = ?",
        (class_id, student_id),
    ).rowcount
    conn.commit()
    conn.close()
    return changed > 0


def get_class_roster(class_id):
    """The full roster for a class.

    LEFT JOIN so a roster row imported from CSV still appears when no face has
    been enrolled for that student yet — the absentee calculation must count
    those students, and the UI needs to show which ones still need enrolment.
    """
    conn = get_connection()
    rows = conn.execute(
        # The face profile's name wins when one exists; otherwise fall back to
        # the name the roster was imported with.
        "SELECT e.student_id, COALESCE(s.name, e.name) AS name, "
        "       CASE WHEN s.student_id IS NULL THEN 0 ELSE 1 END AS has_face "
        "FROM class_enrollments e "
        "LEFT JOIN students s ON s.student_id = e.student_id AND s.owner_id = e.owner_id "
        "WHERE e.class_id = ? ORDER BY e.student_id",
        (class_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_classes_for_student(student_id, owner_id=None):
    """Every course section a student is on."""
    conn = get_connection()
    sql = ("SELECT c.* FROM classes c JOIN class_enrollments e ON e.class_id = c.id "
           "WHERE e.student_id = ?")
    params = [student_id]
    if owner_id is not None:
        sql += " AND c.owner_id = ?"
        params.append(owner_id)
    rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return rows


# ------------------------------------------------------------------- users

def count_users():
    """Number of registered accounts (used to detect first sign-up)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def create_user_row(username, password_hash, display_name=None):
    """Insert an account row; returns the new user id or None on conflict."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
            (username, password_hash, display_name)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_user_by_username(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, password_hash, display_name FROM users WHERE username = ?",
        (username,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name FROM users WHERE id = ?", (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def claim_legacy_data(owner_id):
    """Assign pre-login (owner 0) students and attendance to the first account."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET owner_id = ? WHERE owner_id = ?", (owner_id, LEGACY_OWNER_ID))
    cursor.execute("UPDATE attendance SET owner_id = ? WHERE owner_id = ?", (owner_id, LEGACY_OWNER_ID))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_database()
    print(f"Database path: {DATABASE_PATH}")
