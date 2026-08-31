"""Couche d'extraction : ScrapeGraphAI en production, fixtures en test.

`SmartScraperGraph` n'est importé qu'au moment où on en a besoin : le reste du
pipeline (scoring, export xlsx) tourne donc sans installer langchain ni
playwright, ce qui permet de tester la chaîne hors ligne.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from .schema import EXTRACTION_PROMPT, ProspectExtraction

logger = logging.getLogger(__name__)


class Extractor(Protocol):
    def extract(self, url: str, hint: Optional[str] = None) -> Dict[str, Any]: ...


# --- Configuration LLM -----------------------------------------------------

PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    # Local, gratuit, aucune donnée ne sort de la machine : à privilégier pour
    # un premier essai et pour tout ce qui touche à des données personnelles.
    "ollama": {
        "llm": {
            "model": "ollama/llama3.2",
            "model_tokens": 8192,
            "base_url": "http://localhost:11434",
        },
    },
    # Payant, plus fiable sur l'extraction structurée.
    "openai": {
        "llm": {
            "model": "openai/gpt-4o-mini",
            "model_tokens": 128000,
        },
    },
}


def build_graph_config(
    provider: str,
    api_key: Optional[str] = None,
    headless: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    if provider not in PROVIDER_PRESETS:
        raise ValueError(
            f"Fournisseur inconnu : {provider!r} (attendu : {', '.join(PROVIDER_PRESETS)})"
        )
    config = json.loads(json.dumps(PROVIDER_PRESETS[provider]))  # copie profonde
    if api_key:
        config["llm"]["api_key"] = api_key
    config["headless"] = headless
    config["verbose"] = verbose
    config["loader_kwargs"] = {"timeout": 30000}
    return config


# --- Extracteur réel -------------------------------------------------------


class ScrapeGraphExtractor:
    """Un `SmartScraperGraph` par URL, contraint par `ProspectExtraction`."""

    def __init__(self, graph_config: Dict[str, Any], delay_seconds: float = 2.0):
        self.graph_config = graph_config
        self.delay_seconds = delay_seconds
        self._last_call = 0.0

    def extract(self, url: str, hint: Optional[str] = None) -> Dict[str, Any]:
        from scrapegraphai.graphs import SmartScraperGraph  # import tardif volontaire

        self._throttle()
        prompt = EXTRACTION_PROMPT
        if hint:
            prompt += f"\n\nContexte connu sur cette entreprise : {hint}\n"

        graph = SmartScraperGraph(
            prompt=prompt,
            source=url,
            config=self.graph_config,
            schema=ProspectExtraction,
        )
        result = graph.run()
        return _coerce(result)

    def _throttle(self) -> None:
        """Un site à la fois, espacé : on reste un visiteur poli."""
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_call = time.monotonic()


# --- Extracteur fixtures ---------------------------------------------------


class FixtureExtractor:
    """Rejoue des extractions enregistrées : pipeline testable sans LLM ni réseau."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(f"Répertoire de fixtures introuvable : {self.directory}")

    def extract(self, url: str, hint: Optional[str] = None) -> Dict[str, Any]:
        path = self.directory / f"{slugify(url)}.json"
        if not path.exists():
            logger.warning("Aucune fixture pour %s (%s) — prospect ignoré", url, path.name)
            return {}
        return _coerce(json.loads(path.read_text(encoding="utf-8")))


def slugify(url: str) -> str:
    value = re.sub(r"^https?://", "", (url or "").strip().lower()).rstrip("/")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or "sans-url"


# --- Normalisation de la réponse ------------------------------------------


def _coerce(result: Any) -> Dict[str, Any]:
    """`run()` renvoie selon les cas un dict, un modèle Pydantic ou du JSON brut."""
    if result is None:
        return {}
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        text = result.strip()
        if text in {"", "No answer found.", "NA"}:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                logger.warning("Réponse non exploitable du LLM : %.120s", text)
                return {}
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("JSON invalide dans la réponse du LLM")
                return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
