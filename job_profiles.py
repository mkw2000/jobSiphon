"""Job-profile configuration and isolated storage paths."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class ProfilePaths:
    root: Path
    resume: Path
    database: Path
    cache: Path
    apply_csv: Path
    apply_markdown: Path
    master_csv: Path
    master_markdown: Path

    def ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.resume.parent.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class JobProfile:
    slug: str
    name: str
    description: str
    accent: str
    resume_path: str
    search_terms: tuple[str, ...]
    search_locations: tuple[str, ...]
    locations: tuple[str, ...]
    include_remote: bool
    remote_policy: str
    role_terms: tuple[str, ...]
    preferred_terms: tuple[str, ...]
    excluded_title_terms: tuple[str, ...]
    incompatible_term_groups: tuple[tuple[str, ...], ...]
    blocked_companies: tuple[str, ...]
    preferred_employers: tuple[str, ...]
    minimum_score: int
    max_required_years: int | None
    scoring_guidance: str
    outreach_style: str
    sources: tuple[str, ...]
    wellfound: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any], source: Path) -> "JobProfile":
        required = ("slug", "name", "description", "search_terms", "role_terms")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise ValueError(f"{source}: missing required fields: {', '.join(missing)}")

        slug = str(payload["slug"]).strip()
        if not PROFILE_SLUG_RE.fullmatch(slug):
            raise ValueError(f"{source}: invalid profile slug {slug!r}")

        remote_policy = str(payload.get("remote_policy", "us-only"))
        if remote_policy not in {"us-only", "any", "none"}:
            raise ValueError(f"{source}: remote_policy must be us-only, any, or none")

        minimum_score = int(payload.get("minimum_score", 50))
        if not 0 <= minimum_score <= 100:
            raise ValueError(f"{source}: minimum_score must be between 0 and 100")

        max_years = payload.get("max_required_years")
        if max_years is not None:
            max_years = int(max_years)
            if max_years < 0:
                raise ValueError(f"{source}: max_required_years cannot be negative")

        def strings(key: str) -> tuple[str, ...]:
            values = payload.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"{source}: {key} must be a list")
            return tuple(str(value).strip() for value in values if str(value).strip())

        groups = payload.get("incompatible_term_groups", [])
        if not isinstance(groups, list):
            raise ValueError(f"{source}: incompatible_term_groups must be a list")

        wellfound = payload.get("wellfound", {})
        if not isinstance(wellfound, dict):
            raise ValueError(f"{source}: wellfound must be an object")

        return cls(
            slug=slug,
            name=str(payload["name"]).strip(),
            description=str(payload["description"]).strip(),
            accent=str(payload.get("accent", "#d7ff43")).strip(),
            resume_path=str(
                payload.get("resume_path", f"profiles/resumes/{slug}.txt")
            ).strip(),
            search_terms=strings("search_terms"),
            search_locations=(strings("search_locations") or strings("locations")),
            locations=strings("locations"),
            include_remote=bool(payload.get("include_remote", True)),
            remote_policy=remote_policy,
            role_terms=strings("role_terms"),
            preferred_terms=strings("preferred_terms"),
            excluded_title_terms=strings("excluded_title_terms"),
            incompatible_term_groups=tuple(
                tuple(str(term).strip().lower() for term in group if str(term).strip())
                for group in groups
                if isinstance(group, list) and group
            ),
            blocked_companies=strings("blocked_companies"),
            preferred_employers=strings("preferred_employers"),
            minimum_score=minimum_score,
            max_required_years=max_years,
            scoring_guidance=str(payload.get("scoring_guidance", "")).strip(),
            outreach_style=str(payload.get("outreach_style", "")).strip(),
            sources=strings("sources"),
            wellfound=dict(wellfound),
        )

    def paths(self, project_root: Path) -> ProfilePaths:
        data_root = project_root / "data" / "profiles" / self.slug
        resume = (project_root / self.resume_path).resolve()
        project_resolved = project_root.resolve()
        if project_resolved not in resume.parents:
            raise ValueError(
                f"Profile {self.slug}: resume_path must stay inside the project"
            )
        return ProfilePaths(
            root=data_root,
            resume=resume,
            database=data_root / "seen_jobs.db",
            cache=data_root / "scraped_jobs_cache.json",
            apply_csv=data_root / "apply_list.csv",
            apply_markdown=data_root / "apply_list.md",
            master_csv=data_root / "master_list.csv",
            master_markdown=data_root / "master_list.md",
        )

    def public_dict(self, project_root: Path) -> dict[str, Any]:
        paths = self.paths(project_root)
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "accent": self.accent,
            "resume_path": self.resume_path,
            "resume_configured": paths.resume.exists(),
            "search_term_count": len(self.search_terms),
            "search_location_count": len(self.search_locations),
            "location_count": len(self.locations),
            "include_remote": self.include_remote,
            "remote_policy": self.remote_policy,
            "minimum_score": self.minimum_score,
            "outreach_style": self.outreach_style,
            "sources": list(self.sources),
            "wellfound_enabled": bool(self.wellfound.get("enabled", False)),
        }


def load_profiles(project_root: Path) -> dict[str, JobProfile]:
    profile_dir = project_root / "profiles"
    profiles: dict[str, JobProfile] = {}
    for path in sorted(profile_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile = JobProfile.from_dict(payload, path)
        if profile.slug in profiles:
            raise ValueError(f"Duplicate job profile slug: {profile.slug}")
        profiles[profile.slug] = profile
    if not profiles:
        raise ValueError(f"No job profiles found in {profile_dir}")
    return profiles


def get_profile(project_root: Path, slug: str | None = None) -> JobProfile:
    profiles = load_profiles(project_root)
    if slug:
        try:
            return profiles[slug]
        except KeyError as exc:
            available = ", ".join(profiles)
            raise ValueError(
                f"Unknown job profile {slug!r}. Available: {available}"
            ) from exc
    return next(iter(profiles.values()))


def default_profile_slug(profiles: dict[str, JobProfile]) -> str:
    return next(iter(profiles))
