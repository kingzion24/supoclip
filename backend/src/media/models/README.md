# Face detection models

BlazeFace face detector models from Google MediaPipe, used by
`media/face_detection.py` with the MediaPipe Tasks `FaceDetector`:

- `blaze_face_full_range.tflite` (used; finds small and distant faces)
- `blaze_face_short_range.tflite` (kept for close-up use)

Source: https://storage.googleapis.com/mediapipe-models/face_detector/
License: Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0)

The Tasks runtime needs the system libraries `libgles2` and `libegl1` (installed
in `backend/Dockerfile`). Without them face detection falls back to OpenCV.
