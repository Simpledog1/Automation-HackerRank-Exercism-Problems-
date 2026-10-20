"""Load and validate config.json, and handle Philippine (Asia/Manila) time."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    MANILA = ZoneInfo("Asia/Manila")
except Exception:
    # Windows Python often ships without tz data. Manila is UTC+8 with no daylight
    # saving since 1978, so a fixed offset gives the same calendar day.
    MANILA = timezone(timedelta(hours=8), "Asia/Manila")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def manila_now() -> datetime:
    return datetime.now(MANILA)


def manila_day(moment: datetime | None = None) -> str:
    """Philippine calendar date as 'YYYY-MM-DD'. Used for the daily quota."""
    moment = moment or manila_now()
    return moment.astimezone(MANILA).strftime("%Y-%m-%d")


@dataclass
class RepoConfig:
    platform: str
    path: Path
    remote_url: str
    branch: str
    # Maps a language (HackerRank language or Exercism track slug) to the repo's top folder.
    language_dirs: dict[str, str]


@dataclass
class Config:
    root: Path
    daily_limit: int
    uploads_enabled: bool
    repos: dict[str, RepoConfig]
    exercism_api_base: str
    exercism_credential_target: str
    hackerrank_inbox: Path
    max_downloads_per_run: int = 10
    state_dir: Path = field(init=False)
    log_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = self.root / "state"
        self.log_dir = self.root / "logs"


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None, root: Path | None = None) -> Config:
    root = root or PROJECT_ROOT
    path = path or root / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc

    limit = raw.get("daily_limit", 2)
    if not isinstance(limit, int) or not 0 <= limit <= 2:
        # The 2-file cap is a hard requirement; config may only lower it.
        raise ConfigError("daily_limit must be an integer from 0 to 2")

    repos = {}
    for platform in ("hackerrank", "exercism"):
        r = raw.get("repos", {}).get(platform)
        if not r:
            raise ConfigError(f"repos.{platform} is missing")
        repo_path = Path(r["path"])
        if not repo_path.is_absolute():
            repo_path = root / repo_path
        if repo_path.resolve() == root.resolve() or root.resolve() in repo_path.resolve().parents:
            raise ConfigError("Solution repos must live outside the automation project")
        repos[platform] = RepoConfig(
            platform=platform,
            path=repo_path,
            remote_url=r["remote_url"],
            branch=r.get("branch", "main"),
            language_dirs=dict(r.get("language_dirs", {})),
        )

    ex = raw.get("exercism", {})
    hr = raw.get("hackerrank", {})
    inbox = Path(hr.get("inbox", "inbox/hackerrank"))
    return Config(
        root=root,
        daily_limit=limit,
        uploads_enabled=bool(raw.get("uploads_enabled", False)),
        repos=repos,
        exercism_api_base=ex.get("api_base", "https://api.exercism.org").rstrip("/"),
        exercism_credential_target=ex.get("credential_target", "SolutionSync-Exercism"),
        hackerrank_inbox=inbox if inbox.is_absolute() else root / inbox,
        max_downloads_per_run=int(raw.get("max_downloads_per_run", 10)),
    )
