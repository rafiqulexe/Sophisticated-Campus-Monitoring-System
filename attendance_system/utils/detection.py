# utils/detection.py - Face Detection Module
import cv2
import numpy as np
import os
import sys
import threading

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# YuNet CNN detector (bundled at models/yunet.onnx). Far more reliable than the
# old Haar cascade on uploaded photos: handles tilted heads, glasses, and
# uneven lighting without producing false positives. Available in OpenCV >= 4.7
# (including 5.x, where CascadeClassifier was removed).
YUNET_MODEL_PATH = os.path.join(parent_dir, 'models', 'yunet.onnx')
YUNET_SCORE_THRESHOLD = 0.6

# FaceDetectorYN_create parses the ONNX graph, so constructing a detector per
# call re-reads the model from disk on every frame. That is invisible for one
# uploaded photo but it is the dominant cost once frames arrive continuously,
# so the detector is built once and reused. Input size is a constructor
# argument, hence setInputSize() per frame below.
#
# Reuse costs thread safety. That input size is mutable state on a now-shared
# object and the web server runs threaded (main.py), so two concurrent
# requests could interleave setInputSize and detect and score one frame
# against the other's dimensions. _YUNET_LOCK keeps the pair atomic.
# Per-call construction was accidentally thread-safe; caching without the lock
# would not be. A thread-local detector would avoid the lock but not help:
# Werkzeug's threaded mode uses a fresh thread per connection, so each request
# would still load the model.
_YUNET_LOCK = threading.Lock()
_yunet = None               # shared detector, built on first use
_yunet_load_failed = False  # OpenCV too old, or the model would not load


def _get_yunet():
    """Return the shared YuNet detector, or None if unusable.

    Must be called with _YUNET_LOCK held.
    """
    global _yunet, _yunet_load_failed

    if _yunet is not None:
        return _yunet
    if _yunet_load_failed:
        return None

    # Checked per call rather than latched, so dropping the model in does not
    # need a restart. A missing file is the normal "use Haar instead" path.
    if not os.path.exists(YUNET_MODEL_PATH):
        return None
    if not hasattr(cv2, 'FaceDetectorYN_create'):
        _yunet_load_failed = True  # OpenCV too old for YuNet; will not change
        return None

    try:
        # Placeholder size; every detection sets the real one.
        _yunet = cv2.FaceDetectorYN_create(
            YUNET_MODEL_PATH, "", (320, 320),
            score_threshold=YUNET_SCORE_THRESHOLD,
        )
    except cv2.error as exc:
        # A truncated or corrupt model should fall back to Haar once, not
        # raise on every detection from here on.
        print(f"WARNING: could not load YuNet from {YUNET_MODEL_PATH}: {exc}")
        _yunet_load_failed = True
        return None
    return _yunet


def _detect_with_yunet(image, min_w, min_h):
    """Run the YuNet detector. Returns box list, or None if unavailable."""
    height, width = image.shape[:2]

    with _YUNET_LOCK:
        detector = _get_yunet()
        if detector is None:
            return None
        detector.setInputSize((width, height))
        _, detections = detector.detect(image)

    if detections is None:
        return []

    faces = []
    for det in detections:
        x, y, w, h, score = int(det[0]), int(det[1]), int(det[2]), int(det[3]), float(det[-1])
        if w >= min_w and h >= min_h:
            faces.append(((x, y, w, h), score))
    # Most confident detections first so callers can use faces[0] safely
    faces.sort(key=lambda f: (f[1], f[0][2] * f[0][3]), reverse=True)
    return [box for box, _ in faces]

def _detect_with_haar(image, min_w, min_h):
    """Haar cascade fallback for OpenCV versions without FaceDetectorYN."""
    # OpenCV 5 dropped CascadeClassifier, so there is no cascade to run even
    # when the XML is bundled. Checked before the file, or a present XML sends
    # us into an AttributeError instead of an empty result.
    if not hasattr(cv2, 'CascadeClassifier'):
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    local_cascade = os.path.join(parent_dir, 'models', 'cascades', 'haarcascade_frontalface_default.xml')
    if os.path.exists(local_cascade):
        cascade_path = local_cascade
    else:
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'

    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        print(f"ERROR: Could not load cascade classifier from {cascade_path}")
        return []

    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_w, min_h)
    )
    return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]

def detect_faces(image, min_face_size=(30, 30)):
    """Detect all faces, return [(x, y, w, h), ...]"""
    if isinstance(image, str):
        image = cv2.imread(image)
        if image is None:
            raise ValueError(f"Could not load image: {image}")

    if image.size == 0:
        return []

    min_w, min_h = min_face_size
    faces = _detect_with_yunet(image, min_w, min_h)
    if faces is None:
        faces = _detect_with_haar(image, min_w, min_h)
    return faces

def draw_face_boxes(image, face_boxes, labels=None, color=(0, 255, 0)):
    """Draw bounding boxes and labels on detected faces"""
    result = image.copy()
    for i, (x, y, w, h) in enumerate(face_boxes):
        cv2.rectangle(result, (x, y), (x + w, y + h), color, 2)
        if labels and i < len(labels):
            label = str(labels[i])
            (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(result, (x, y - text_h - 10), (x + text_w, y), color, -1)
            cv2.putText(result, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return result

def extract_face(image, face_box, padding=0.2):
    """Extract a single face from image given bounding box"""
    x, y, w, h = face_box
    pad_x = int(w * padding)
    pad_y = int(h * padding)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(image.shape[1], x + w + pad_x)
    y2 = min(image.shape[0], y + h + pad_y)
    return image[y1:y2, x1:x2]
