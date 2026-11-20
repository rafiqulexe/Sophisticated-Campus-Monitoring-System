# utils/recognition.py - Face Recognition Module
import numpy as np
import os
import sys
from scipy.spatial.distance import cosine

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from deepface import DeepFace
from config import (
    COSINE_MATCH_THRESHOLD,
    COSINE_REVIEW_THRESHOLD,
    DEEPFACE_MODEL,
    RECOGNITION_THRESHOLD,
)

def get_face_embedding(face_image, model_name=DEEPFACE_MODEL):
    """W2-T3: Generate 128-d embedding from face image"""
    temp_path = "temp_embedding.jpg"
    cv2 = __import__('cv2')
    cv2.imwrite(temp_path, face_image)

    try:
        result = DeepFace.represent(
            img_path=temp_path,
            model_name=model_name,
            # Callers pass an already-cropped face, so skip the internal
            # detector: re-detecting the crop can fail and lose the sample.
            detector_backend="skip",
            enforce_detection=False
        )
        embedding = np.array(result[0]['embedding'])
        return embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def compare_faces(embedding1, embedding2, metric="cosine"):
    """W2-T4: Compare two embeddings, return distance (lower = more similar)"""
    if metric == "cosine":
        return cosine(embedding1, embedding2)
    elif metric == "euclidean":
        return np.linalg.norm(embedding1 - embedding2)
    else:
        raise ValueError(f"Unknown metric: {metric}")

def confidence_band(distance, match_threshold=COSINE_MATCH_THRESHOLD,
                    review_threshold=COSINE_REVIEW_THRESHOLD):
    """W2: classify a cosine distance into one of three bands.

    'high'    the match is confident enough to record without asking
    'review'  plausible but near the boundary; a teacher should confirm it
    'unknown' beyond the accept boundary, so not a match at all
    """
    if distance is None:
        return 'unknown'
    if distance <= match_threshold:
        return 'high'
    if distance <= review_threshold:
        return 'review'
    return 'unknown'


def recognize_face(face_embedding, known_students, threshold=RECOGNITION_THRESHOLD, metric="cosine"):
    """W2-T5: Find the best matching student and band the result.

    Returns 'band' alongside 'match' so callers can distinguish a confident
    identification from one that only just cleared the boundary. 'match'
    keeps its original meaning: the distance is within `threshold`.
    """
    if len(known_students) == 0:
        return {'student_id': 'UNKNOWN', 'name': 'Unknown', 'distance': None,
                'match': False, 'band': 'unknown'}

    best_match = None
    best_distance = float('inf')

    for student in known_students:
        distance = compare_faces(face_embedding, student['embedding'], metric)
        if distance < best_distance:
            best_distance = distance
            best_match = student

    distance = round(best_distance, 4)
    # Band against the configured review boundary, but never report a band
    # better than the caller's own threshold allows.
    band = confidence_band(best_distance, review_threshold=min(threshold, COSINE_REVIEW_THRESHOLD))

    if best_distance <= threshold:
        return {
            'student_id': best_match['student_id'],
            'name': best_match['name'],
            'distance': distance,
            'match': True,
            'band': band,
        }
    return {
        'student_id': 'UNKNOWN',
        'name': 'Unknown',
        'distance': distance,
        'match': False,
        'band': 'unknown',
    }

def batch_recognize(face_embeddings, known_students, threshold=RECOGNITION_THRESHOLD, metric="cosine"):
    """Recognize multiple faces at once (for group photos)"""
    return [recognize_face(emb, known_students, threshold, metric) for emb in face_embeddings]

def find_optimal_threshold(test_pairs, metric="cosine"):
    """W2-T6: Find optimal threshold by testing different values"""
    thresholds = np.arange(0.1, 0.8, 0.05)
    best_threshold, best_accuracy = 0.5, 0
    
    for threshold in thresholds:
        correct = sum(
            (compare_faces(emb1, emb2, metric) <= threshold) == is_same
            for emb1, emb2, is_same in test_pairs
        )
        accuracy = correct / len(test_pairs) if test_pairs else 0
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_threshold = threshold
    
    return {'optimal_threshold': round(best_threshold, 2), 'accuracy': round(best_accuracy * 100, 2)}