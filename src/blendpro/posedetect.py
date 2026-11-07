"""MediaPipe Pose Landmarker (Tasks API) on rendered images, plus a skeleton overlay drawer."""
import os
import urllib.request
from pathlib import Path

MODEL_DIR = Path.home() / ".blendpro" / "models"
MODELS = {
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
}
_landmarkers = {}


def _model_path(variant):
    p = MODEL_DIR / f"pose_landmarker_{variant}.task"
    if not p.exists():
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODELS[variant], p)
    return str(p)


def _landmarker(variant):
    if variant not in _landmarkers:
        try:
            from mediapipe.tasks.python import vision, BaseOptions
        except ImportError as e:
            raise RuntimeError("mediapipe is not installed in the BlendPro environment: "
                               "uv tool install -e <blendpro-mcp> --with mediapipe") from e
        opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=_model_path(variant)),
                                            num_poses=1, min_pose_detection_confidence=0.3,
                                            min_pose_presence_confidence=0.3)
        _landmarkers[variant] = vision.PoseLandmarker.create_from_options(opts)
    return _landmarkers[variant]


def detect(image_path, variant="heavy"):
    """Return {'landmarks': [[x,y,z,vis]*33] normalized (origin top-left), 'world': [[x,y,z]*33] metres} or None."""
    import mediapipe as mp
    img = mp.Image.create_from_file(image_path)
    res = _landmarker(variant).detect(img)
    if not res.pose_landmarks:
        return None
    lms = res.pose_landmarks[0]
    out = {"landmarks": [[l.x, l.y, l.z, l.visibility if l.visibility is not None else 1.0] for l in lms]}
    if res.pose_world_landmarks:
        out["world"] = [[l.x, l.y, l.z] for l in res.pose_world_landmarks[0]]
    return out


_PAIRS = [(1, 4), (2, 5), (3, 6), (7, 8), (9, 10)] + [(i, i + 1) for i in range(11, 33, 2)]


def normalize_sides(landmarks, view):
    """MediaPipe sometimes mirrors left/right (front vs back ambiguity). We know the camera: in a 'front' view the
    subject faces the camera, so their LEFT must be on the image RIGHT (opposite for 'back'). Swap labels if the
    body pairs (shoulders, elbows, hips, knees) mostly disagree. Returns (landmarks, swapped)."""
    if view not in ("front", "back"):
        return landmarks, False
    votes = 0
    for a, b in ((11, 12), (13, 14), (23, 24), (25, 26), (27, 28)):
        la, lb = landmarks[a], landmarks[b]
        if min(la[3], lb[3]) < 0.3:
            continue
        left_on_right = la[0] > lb[0]
        votes += 1 if left_on_right == (view == "front") else -1
    if votes >= 0:
        return landmarks, False
    lm = [list(x) for x in landmarks]
    for a, b in _PAIRS:
        lm[a], lm[b] = lm[b], lm[a]
    return lm, True


def draw(image_path, landmarks, out_path, min_visibility=0.5, joints3d_labels=None):
    """Draw the MediaPipe skeleton on the image: left side blue, right side orange, low-confidence grey."""
    import cv2
    from mediapipe.tasks.python import vision
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    pts = [(int(l[0] * w), int(l[1] * h)) for l in landmarks]
    vis = [l[3] for l in landmarks]
    t = max(2, w // 300)
    for c in vision.PoseLandmarksConnections.POSE_LANDMARKS:
        a, b = c.start, c.end
        ok = vis[a] >= min_visibility and vis[b] >= min_visibility
        side = "l" if (a % 2 == 1 and b % 2 == 1 and a > 10) else ("r" if (a % 2 == 0 and b % 2 == 0 and a > 10) else "c")
        color = (120, 120, 120) if not ok else {"l": (255, 140, 30), "r": (30, 140, 255), "c": (60, 220, 60)}[side]
        cv2.line(img, pts[a], pts[b], color, t, cv2.LINE_AA)
    for i, p in enumerate(pts):
        cv2.circle(img, p, t + 2, (255, 255, 255) if vis[i] >= min_visibility else (90, 90, 90), -1, cv2.LINE_AA)
    cv2.putText(img, "L=blue R=orange (subject's sides)", (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, max(0.4, w / 1600),
                (230, 230, 230), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, img)
    return out_path
