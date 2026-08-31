"""Chargement de la spec YAML d'un installateur (format `scp-client-spec`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

DEFAULT_WEIGHTS: Dict[str, int] = {
    "recent_move": 3,
    "thermal_complaint": 3,
    "privacy_visible": 2,
    "recent_renovation": 2,
    "preferred_sector_match": 1,
    "size_in_range": 1,
    "threshold_chaud": 5,
    "threshold_tiede": 2,
}


@dataclass
class InstallerSpec:
    raw: Dict[str, Any]
    path: Path

    @property
    def id(self) -> str:
        return str(self.raw.get("installer", {}).get("id", "inconnu"))

    @property
    def name(self) -> str:
        return str(self.raw.get("installer", {}).get("name", self.id))

    @property
    def profile_type(self) -> str:
        return str(self.raw.get("installer", {}).get("profile_type", "solo_reseau"))

    @property
    def status(self) -> str:
        return str(self.raw.get("installer", {}).get("status", "active"))

    @property
    def zone(self) -> Dict[str, Any]:
        return self.raw.get("zone") or {}

    @property
    def departments_fr(self) -> List[str]:
        return [str(d) for d in (self.zone.get("departments_fr") or [])]

    @property
    def cantons_ch(self) -> List[str]:
        return [str(c).upper() for c in (self.zone.get("cantons_ch") or [])]

    @property
    def preferred_sectors(self) -> List[str]:
        segments = self.raw.get("target_segments") or {}
        return [str(s) for s in (segments.get("preferred_sectors") or [])]

    @property
    def size_range(self) -> tuple:
        segments = (self.raw.get("target_segments") or {}).get("company_size") or {}
        return segments.get("min_employees"), segments.get("max_employees")

    @property
    def products_primary(self) -> List[str]:
        return [str(p) for p in ((self.raw.get("products") or {}).get("primary") or [])]

    @property
    def exclusions(self) -> List[str]:
        return [str(e) for e in (self.raw.get("exclusions") or [])]

    @property
    def competitors_to_avoid(self) -> List[str]:
        return [str(c) for c in (self.raw.get("competitors_to_avoid") or [])]

    @property
    def weights(self) -> Dict[str, int]:
        merged = dict(DEFAULT_WEIGHTS)
        merged.update({k: int(v) for k, v in (self.raw.get("scoring_weights") or {}).items()})
        return merged


def load_spec(path: str | Path) -> InstallerSpec:
    spec_path = Path(path)
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    if "installer" not in data:
        raise ValueError(f"{spec_path}: clé 'installer' absente, ce n'est pas une spec SCP.")
    return InstallerSpec(raw=data, path=spec_path)
