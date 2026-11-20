"""The shared YuNet detector: reuse, per-frame sizing, and thread safety.

The detector is cached because loading the ONNX model costs 7-13 ms a frame
(see utils/detection.py). Caching makes it shared mutable state, so these
tests pin both halves of the fix: that it really is reused, and that
concurrent use of it is synchronised.
"""

import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import detection  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.exists(detection.YUNET_MODEL_PATH),
    reason="yunet.onnx not present; detection falls back to Haar",
)


@pytest.fixture()
def fresh_module():
    """Reset the cached detector around a test that perturbs it."""
    saved, failed = detection._yunet, detection._yunet_load_failed
    detection._yunet, detection._yunet_load_failed = None, False
    yield detection
    detection._yunet, detection._yunet_load_failed = saved, failed


def frame(h, w, seed=0):
    return np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)


def test_model_is_loaded_once_not_per_call(fresh_module):
    """The point of the change: one detector, however many frames arrive."""
    assert fresh_module._yunet is None

    fresh_module.detect_faces(frame(480, 640))
    first = fresh_module._yunet
    assert first is not None, "no detector was cached"

    for _ in range(5):
        fresh_module.detect_faces(frame(480, 640))
    assert fresh_module._yunet is first, "the detector was rebuilt"


def test_frame_size_changes_between_calls(fresh_module):
    """Input size is a constructor argument, so it must be set per frame.

    Without setInputSize() OpenCV raises on the second, differently sized
    frame instead of detecting.
    """
    for h, w in [(480, 640), (720, 1280), (480, 640), (240, 320)]:
        boxes = fresh_module.detect_faces(frame(h, w))
        assert isinstance(boxes, list)


def test_same_frame_gives_the_same_answer_after_a_resize(fresh_module):
    """A frame must not be scored against a previous frame's dimensions."""
    small = frame(480, 640, seed=11)

    before = fresh_module.detect_faces(small)
    fresh_module.detect_faces(frame(720, 1280, seed=12))
    after = fresh_module.detect_faces(small)

    assert before == after


def test_concurrent_detection_is_synchronised(fresh_module):
    """The web server is threaded, so two requests can land at once.

    Unsynchronised, interleaved setInputSize/detect on the shared detector
    raises or scores a frame at the wrong shape; serialised, every call
    returns a list.
    """
    sizes = [(480, 640), (720, 1280)]
    failures, results = [], []
    lock = threading.Lock()

    def run(size):
        img = frame(*size, seed=size[0])
        for _ in range(10):
            try:
                boxes = fresh_module.detect_faces(img)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    failures.append(f"{type(exc).__name__}: {exc}")
                return
            with lock:
                results.append(boxes)

    threads = [threading.Thread(target=run, args=(sizes[i % 2],)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not failures, f"concurrent detection failed: {failures[:2]}"
    assert len(results) == 60
    assert all(isinstance(r, list) for r in results)


def test_a_missing_model_falls_back_without_latching(fresh_module, monkeypatch):
    """A missing model means 'use Haar', and is re-checked, not remembered."""
    monkeypatch.setattr(fresh_module, "YUNET_MODEL_PATH", "no/such/model.onnx")
    assert fresh_module._detect_with_yunet(frame(480, 640), 30, 30) is None
    assert fresh_module._yunet_load_failed is False, "a missing file was latched"

    # detect_faces still answers, via the cascade.
    assert isinstance(fresh_module.detect_faces(frame(480, 640)), list)

    # With the real model back in place it loads normally.
    monkeypatch.undo()
    assert fresh_module._detect_with_yunet(frame(480, 640), 30, 30) is not None
