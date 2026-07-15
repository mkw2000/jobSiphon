from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def profile_payload(
    slug: str,
    *,
    search_terms: list[str] | None = None,
    role_terms: list[str] | None = None,
    locations: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    accepted_locations = locations or ["Austin, TX", "Round Rock, TX"]
    payload: dict[str, Any] = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "description": f"Synthetic {slug} test profile.",
        "resume_path": f"profiles/resumes/{slug}.txt",
        "search_terms": search_terms or ["Example Role"],
        "search_locations": [accepted_locations[0]],
        "locations": accepted_locations,
        "role_terms": role_terms or ["example role"],
        "preferred_terms": [],
        "excluded_title_terms": ["senior"],
        "incompatible_term_groups": [],
        "blocked_companies": [],
        "preferred_employers": [],
        "include_remote": True,
        "remote_policy": "us-only",
        "sources": [],
        "minimum_score": 50,
        "max_required_years": 3,
    }
    payload.update(overrides)
    return payload


def write_profile(root: Path, payload: dict[str, Any]) -> Path:
    profile_dir = root / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / f"{payload['slug']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
