"""Named constants used across the application."""

# --- File size thresholds ---
MIN_VALID_FILE_BYTES: int = 1_048_576  # 1 MiB — below counts as failed download
HTTP_CHUNK_SIZE: int = 262_144  # 256 KiB

# --- Progress milestones (0–100) ---
PROGRESS_DL_CAP: float = 80.0  # HLS download phase cap
PROGRESS_DIRECT_DL_CAP: int = 88  # Progressive download cap
PROGRESS_MERGE: int = 85  # Merge/remux start
PROGRESS_NORMALIZE: int = 88  # MP4 normalize
PROGRESS_THUMB_QUEUE: int = 90  # Just queued for thumbnail
PROGRESS_THUMB_DL: int = 92  # Thumbnail downloading
PROGRESS_THUMB_EMBED: int = 95  # Thumbnail embedding
PROGRESS_DONE: int = 100

# --- YouTube (yt-dlp) progress ---
YT_PROGRESS_MULT: float = 0.9
YT_PROGRESS_CAP: float = 90.0
YT_PROGRESS_MERGE: int = 90
YT_PROGRESS_EXTRACT_AUDIO: int = 85

# --- Timing ---
THUMB_QUEUE_DELAY_SEC: float = 0.75

# --- Subprocess ---
TERMINATE_WAIT_SEC: float = 20.0

# --- yt-dlp JSON cache ---
YT_JSON_CACHE_MAX: int = 100
YT_META_TTL_SEC: float = 300.0
