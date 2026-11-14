"""
pose.py - Southpaw OS, computer vision (Member D)

Turns MediaPipe's pose result into a simple dictionary of named body points:

    pose = pose_from_mediapipe(result, width, height)
    pose["L_WRIST"].x, pose["L_WRIST"].y      position in PIXELS (y grows DOWNWARDS, like all images)
    pose["L_WRIST"].vis                       0-1: how sure MediaPipe is that the point is visible

"L" and "R" are the PLAYER's left and right (not the left/right of the image), which
matches the wrist boards: wristL is on the player's left arm.

Kept free of OpenCV/MediaPipe imports so the guard logic can be tested without them.
"""

from collections import namedtuple

# One body point: pixel position + visibility
Point = namedtuple("Point", "x y vis")

# The MediaPipe Pose landmarks we use, by their index in MediaPipe's list of 33
# (https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker#pose_landmarker_model)
POINT_INDEX = {
    "NOSE": 0,
    "L_EYE": 2, "R_EYE": 5,
    "MOUTH_L": 9, "MOUTH_R": 10,
    "L_SHOULDER": 11, "R_SHOULDER": 12,
    "L_ELBOW": 13, "R_ELBOW": 14,
    "L_WRIST": 15, "R_WRIST": 16,
    "L_PINKY": 17, "R_PINKY": 18,       # knuckle points: together with the wrist they give the fist
    "L_INDEX": 19, "R_INDEX": 20,
    "L_HIP": 23, "R_HIP": 24,
}


def pose_from_mediapipe(result, width, height):
    """Convert a MediaPipe PoseLandmarker result into {name: Point}. None if nobody was found."""
    if not result.pose_landmarks:
        return None
    landmarks = result.pose_landmarks[0]          # num_poses=1, so only one person
    return {name: Point(landmarks[i].x * width, landmarks[i].y * height, landmarks[i].visibility)
            for name, i in POINT_INDEX.items()}
