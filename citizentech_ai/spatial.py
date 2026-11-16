"""Distância (haversine), centroide e pontos de interesse próximos."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

EARTH_R = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


@dataclass(frozen=True)
class POI:
    id: str
    type: str  # escola | hospital | area_de_risco
    name: str
    lat: float
    lon: float


def load_pois(path: str | Path) -> list[POI]:
    return [POI(**p) for p in json.loads(Path(path).read_text(encoding="utf-8"))]


def nearby(lat: float, lon: float, pois: list[POI], poi_type: str, radius_m: float) -> list[tuple[POI, float]]:
    found = [(p, haversine_m(lat, lon, p.lat, p.lon)) for p in pois if p.type == poi_type]
    return sorted([x for x in found if x[1] <= radius_m], key=lambda x: x[1])
