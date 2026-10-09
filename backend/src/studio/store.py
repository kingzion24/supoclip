"""File-backed storage for Studio productions.

Each production is a folder under ``{TEMP_DIR}/studio/<id>/`` holding
``production.json`` plus uploaded scene clips and render outputs. Finished
videos are copied to ``{TEMP_DIR}/clips/studio/`` so they land in the user's
clips folder next to their other clips.
"""

import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..config import get_config

PRODUCTION_FILE = "production.json"
_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")

# Lifecycle: researching -> directing -> proposal -> prompting -> shooting
#            -> rendering -> done. Any step can end in "error"; the user can
#            retry from the last good state.
BUSY_STATUSES = {"researching", "directing", "prompting", "generating", "rendering"}
# Jobs save progress at least every few minutes; a busy production untouched
# for longer than this lost its worker (e.g. a restart) and may be retried.
STUCK_AFTER_SECONDS = 45 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_busy(production: Dict[str, Any]) -> bool:
    """True while a job is working on the production (and has not gone stale)."""
    if production.get("status") not in BUSY_STATUSES:
        return False
    try:
        updated = datetime.fromisoformat(production.get("updated_at") or "")
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - updated).total_seconds() < STUCK_AFTER_SECONDS


def studio_root() -> Path:
    return Path(get_config().temp_dir) / "studio"


def output_dir() -> Path:
    return Path(get_config().temp_dir) / "clips" / "studio"


def valid_id(production_id: str) -> bool:
    return bool(_ID_PATTERN.match(production_id or ""))


def production_dir(production_id: str) -> Path:
    if not valid_id(production_id):
        raise ValueError("Invalid production id")
    return studio_root() / production_id


def new_production_id() -> str:
    return uuid.uuid4().hex


def _atomic_write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def production_lock(production_id: str) -> Iterator[Path]:
    directory = production_dir(production_id)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield directory
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load(production_id: str) -> Optional[Dict[str, Any]]:
    try:
        path = production_dir(production_id) / PRODUCTION_FILE
    except ValueError:
        return None
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save(production: Dict[str, Any]) -> Dict[str, Any]:
    production["updated_at"] = now_iso()
    _atomic_write(production_dir(production["id"]) / PRODUCTION_FILE, production)
    return production


def update(production_id: str, **changes: Any) -> Dict[str, Any]:
    """Apply ``changes`` to a production under its lock and save it."""
    with production_lock(production_id):
        production = load(production_id)
        if production is None:
            raise FileNotFoundError(production_id)
        production.update(changes)
        return save(production)


def list_for_user(user_id: str) -> List[Dict[str, Any]]:
    root = studio_root()
    if not root.exists():
        return []
    productions = []
    for directory in root.iterdir():
        if not directory.is_dir() or not valid_id(directory.name):
            continue
        production = load(directory.name)
        if production and production.get("user_id") == user_id:
            productions.append(production)
    productions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return productions


def scene_clip_name(scene_number: int) -> str:
    return f"scene-{scene_number:02d}.mp4"


# --- Reusable assets: inspiration styles and cloned voices -----------------

ASSET_KINDS = {"styles", "voices"}
ASSET_FILE = "record.json"


def asset_dir(kind: str, asset_id: str) -> Path:
    if kind not in ASSET_KINDS or not valid_id(asset_id):
        raise ValueError("Invalid asset")
    return Path(get_config().temp_dir) / "studio_assets" / kind / asset_id


def load_asset(kind: str, asset_id: str) -> Optional[Dict[str, Any]]:
    try:
        path = asset_dir(kind, asset_id) / ASSET_FILE
    except ValueError:
        return None
    return json.loads(path.read_text()) if path.exists() else None


def save_asset(kind: str, record: Dict[str, Any]) -> Dict[str, Any]:
    record["updated_at"] = now_iso()
    _atomic_write(asset_dir(kind, record["id"]) / ASSET_FILE, record)
    return record


def update_asset(kind: str, asset_id: str, **changes: Any) -> Dict[str, Any]:
    record = load_asset(kind, asset_id)
    if record is None:
        raise FileNotFoundError(asset_id)
    record.update(changes)
    return save_asset(kind, record)


def list_assets(kind: str, user_id: str) -> List[Dict[str, Any]]:
    root = Path(get_config().temp_dir) / "studio_assets" / kind
    if not root.exists():
        return []
    records = [load_asset(kind, item.name) for item in root.iterdir() if item.is_dir() and valid_id(item.name)]
    owned = [record for record in records if record and record.get("user_id") == user_id]
    return sorted(owned, key=lambda record: record.get("created_at", ""), reverse=True)
