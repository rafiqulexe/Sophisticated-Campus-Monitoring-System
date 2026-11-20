# config.py - Project Configuration Settings

import os

# DeepFace settings
DEEPFACE_MODEL = "Facenet"          # or "VGG-Face", "ArcFace", etc.
# Face detection is handled by utils/detection.py (YuNet model in models/yunet.onnx);
# embeddings are generated directly from the detected face crop.
DEEPFACE_DETECTOR = "skip"

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database", "attendance.db")
DATA_DIR = os.path.join(BASE_DIR, "data")
ENROLLED_DIR = os.path.join(DATA_DIR, "enrolled_students")
UNKNOWN_DIR = os.path.join(DATA_DIR, "unknown_faces")
LOGS_DIR = os.path.join(DATA_DIR, "attendance_logs")

# Face recognition settings
FACE_ENCODING_DIM = 128          # Facenet produces a 128-dimensional embedding

# Matching uses COSINE distance (see utils/recognition.compare_faces), not
# Euclidean. Cosine distance on these embeddings runs roughly 0..2, so these
# values do not transfer to a Euclidean metric or to a different embedding
# model: ArcFace's published optimum of 0.5 is for its own 512-d space.
#
# W2: two bands rather than one cut-off.
#   d <= COSINE_MATCH_THRESHOLD   -> confident match, marked Present
#   d <= COSINE_REVIEW_THRESHOLD  -> plausible match, marked Present but
#                                    flagged for the teacher to confirm
#   d >  COSINE_REVIEW_THRESHOLD  -> not recognised, saved as an unknown face
# Tune with utils.recognition.find_optimal_threshold against labelled pairs;
# scripts/tune_threshold.py drives it.
COSINE_MATCH_THRESHOLD = 0.40
COSINE_REVIEW_THRESHOLD = 0.60

# The single accept/reject boundary. Anything beyond this is not a match at
# all, so it remains the threshold the recogniser gates on.
RECOGNITION_THRESHOLD = COSINE_REVIEW_THRESHOLD

NUM_REGISTRATION_SAMPLES = 10    # W1-T7: Capture 10 frames per student

# Ensure directories exist
for directory in [ENROLLED_DIR, UNKNOWN_DIR, LOGS_DIR]:
    os.makedirs(directory, exist_ok=True)