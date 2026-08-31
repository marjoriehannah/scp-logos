"""Classement d'un prospect enrichi dans les paniers SCP.

Ordre de décision (le premier verdict qui tombe l'emporte) :
    1. EXCLU        — concurrent, doublon, cessation connue
    2. HORS-CIBLE   — pas la cible SCP (holding, snack, structure sans local)
    3. A_CONFIRMER  — doute levable en 1re minute d'appel (zone, ambiguïté)
    4. CHAUD / TIEDE / VOLUME — selon le score des signaux
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .salesforce import department_from_postal, is_mobile
from .spec import InstallerSpec

# Un signal extrait alimente un poids de la spec installateur.
SIGNAL_TO_WEIGHT = {
    "demenagement_recent": "recent_move",
    "nouveau_batiment": "recent_move",
    "plainte_thermique": "thermal_complaint",
    "besoin_confidentialite": "privacy_visible",
    "renovation_recente": "recent_renovation",
    "extension_agrandissement": "recent_renovation",
    "sinistre_recent": "recent_renovation",
    "changement_direction": "recent_move",
}

COMPETITOR_KEYWORDS = (
    "vitrerie", "miroiterie", "vitrophanie", "pose de film", "film solaire",
    "films pour vitres", "solar screen", "llumar", "glastint", "3m fenetre",
    "teinte de vitres", "covering vitre", "store solaire",
)

OFF_TARGET_KEYWORDS = (
    "sci ", "societe civile immobiliere", "holding", "snack", "kebab",
    "fast-food", "food truck", "pizzeria a emporter", "vente a emporter",
    "auto-entrepreneur", "particuliers uniquement",
)

CHAIN_KEYWORDS = (
    "ibis", "mercure", "novotel", "accor", "pierre & vacances", "maeva",
    "korian", "domusvi", "domus vi", "campanile", "b&b hotel", "premiere classe",
    "logis de france", "best western",
)


def _flat(value: Optional[str]) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFD", str(value).lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


@dataclass
class Verdict:
    panier: str  # chaud | tiede | volume | a_confirmer | hors_cible | exclu
    confiance: str  # high | medium | low
    score: int = 0
    raison: str = ""
    signal_label: str = ""
    question_verif: str = ""
    details: List[str] = field(default_factory=list)


def _signal_label(signals: List[dict]) -> str:
    parts = []
    for signal in signals:
        stype = signal.get("type")
        if not stype or stype == "aucun":
            continue
        evidence = (signal.get("evidence") or "").strip().replace("\n", " ")
        date_hint = signal.get("date_hint")
        label = stype.replace("_", " ")
        if date_hint:
            label += f" ({date_hint})"
        if evidence:
            label += f" : {evidence[:110]}"
        parts.append(label)
    return " | ".join(parts)[:200]


def _structure_sans_local(prospect: dict) -> List[str]:
    """Faisceau d'indices « pas de local commercial » (cf. patterns-business)."""
    flags = []
    phone = prospect.get("Phone") or ""
    if not phone:
        flags.append("aucun téléphone publié")
    elif is_mobile(phone):
        flags.append("téléphone mobile uniquement")
    if not prospect.get("Website"):
        flags.append("aucun site web")
    if not prospect.get("BillingCity"):
        flags.append("adresse d'établissement introuvable")
    employees = prospect.get("_employees")
    if isinstance(employees, int) and employees <= 1:
        flags.append("0-1 salarié")
    return flags


def _in_zone(prospect: dict, spec: InstallerSpec) -> Optional[bool]:
    """True/False si la zone est décidable, None si l'information manque."""
    postal = prospect.get("BillingPostalCode") or ""
    country = (prospect.get("BillingCountry") or "France").lower()
    if country.startswith("suisse"):
        cantons = spec.cantons_ch
        if not cantons:
            return False
        state = (prospect.get("BillingState") or "").upper()
        return state in cantons if state else None
    departments = spec.departments_fr
    if not departments:
        return None
    if not postal:
        return None
    return department_from_postal(postal) in departments


def classify(prospect: dict, spec: InstallerSpec, dedup_names: Optional[set] = None) -> Verdict:
    """Applique les règles SCP à un prospect déjà normalisé."""
    dedup_names = dedup_names or set()
    weights = spec.weights
    name_flat = _flat(prospect.get("Name"))
    activity_flat = _flat(prospect.get("_activity"))
    haystack = f"{name_flat} {activity_flat}"
    signals = prospect.get("_signals") or []

    # 1. EXCLU -------------------------------------------------------------
    if name_flat and name_flat in dedup_names:
        return Verdict("exclu", "high", raison="Doublon — compte déjà touché par un installateur SCP")

    for competitor in spec.competitors_to_avoid:
        if _flat(competitor) and _flat(competitor) in haystack:
            return Verdict("exclu", "high", raison=f"Concurrent listé dans la spec : {competitor}")

    for keyword in COMPETITOR_KEYWORDS:
        if keyword in haystack:
            return Verdict("exclu", "high", raison=f"Concurrent direct (métier du film/vitrage) — « {keyword} »")

    if prospect.get("_etat_administratif") == "C":
        return Verdict("exclu", "high", raison="Cessation/liquidation (état administratif « C »)")

    # 2. HORS-CIBLE --------------------------------------------------------
    for keyword in OFF_TARGET_KEYWORDS:
        if keyword in haystack:
            return Verdict("hors_cible", "medium", raison=f"Hors cible SCP — « {keyword.strip()} »")

    no_local = _structure_sans_local(prospect)
    if len(no_local) >= 3:
        return Verdict(
            "hors_cible",
            "medium",
            raison="Structure sans local commercial (" + ", ".join(no_local) + ")",
        )

    # 3. A CONFIRMER -------------------------------------------------------
    zone_ok = _in_zone(prospect, spec)
    if zone_ok is False:
        return Verdict(
            "a_confirmer",
            "low",
            raison="Hors zone déclarée de l'installateur",
            signal_label=_signal_label(signals),
            question_verif=f"Vos locaux sont-ils bien dans la zone couverte par {spec.name} ?",
        )
    if zone_ok is None:
        return Verdict(
            "a_confirmer",
            "low",
            raison="Zone indéterminée (code postal absent de la page)",
            signal_label=_signal_label(signals),
            question_verif="Quelle est l'adresse exacte de l'établissement à équiper ?",
        )
    if len(no_local) == 2:
        return Verdict(
            "a_confirmer",
            "low",
            raison="2 indices « structure sans local » (" + ", ".join(no_local) + ")",
            signal_label=_signal_label(signals),
            question_verif="Recevez-vous du public dans des locaux avec des surfaces vitrées ?",
        )

    # 4. Scoring -----------------------------------------------------------
    score = 0
    details: List[str] = []
    counted: set = set()
    for signal in signals:
        weight_key = SIGNAL_TO_WEIGHT.get(signal.get("type", ""))
        if not weight_key or weight_key in counted:
            continue  # un même poids ne compte qu'une fois
        counted.add(weight_key)
        points = weights.get(weight_key, 0)
        score += points
        details.append(f"{signal.get('type')} (+{points})")

    if _sector_match(prospect, spec):
        points = weights.get("preferred_sector_match", 0)
        score += points
        details.append(f"secteur prioritaire (+{points})")

    if _size_in_range(prospect, spec):
        points = weights.get("size_in_range", 0)
        score += points
        details.append(f"effectif dans la cible (+{points})")

    is_chain = bool(prospect.get("_is_chain")) or any(k in haystack for k in CHAIN_KEYWORDS)

    if score >= weights.get("threshold_chaud", 5):
        panier = "chaud"
    elif score >= weights.get("threshold_tiede", 2):
        panier = "tiede"
    else:
        panier = "volume"

    # Une chaîne intégrée décide au siège : jamais prioritaire, même bien scorée.
    if is_chain and panier != "volume":
        panier = "volume"
        details.append("chaîne intégrée → panier volume (décision au siège)")

    dated_signals = sum(1 for s in signals if s.get("date_hint") and s.get("type") != "aucun")
    if panier == "chaud" and dated_signals >= 1 and prospect.get("Phone"):
        confiance = "high"
    elif panier == "volume" or not prospect.get("Phone"):
        confiance = "low"
    else:
        confiance = "medium"

    return Verdict(
        panier=panier,
        confiance=confiance,
        score=score,
        raison=", ".join(details) or "aucun signal détecté, match zone + secteur uniquement",
        signal_label=_signal_label(signals),
        details=details,
    )


def _sector_match(prospect: dict, spec: InstallerSpec) -> bool:
    sector = _flat(prospect.get("_sector_guess")) + " " + _flat(prospect.get("Industry"))
    for preferred in spec.preferred_sectors:
        tokens = [t for t in _flat(preferred).replace("-", " ").split() if len(t) > 3]
        if tokens and all(token[:6] in sector for token in tokens):
            return True
    return False


def _size_in_range(prospect: dict, spec: InstallerSpec) -> bool:
    employees = prospect.get("_employees")
    if not isinstance(employees, int):
        return False
    low, high = spec.size_range
    if low is not None and employees < int(low):
        return False
    if high is not None and employees > int(high):
        return False
    return True


def suggest_angle(prospect: dict, spec: InstallerSpec) -> str:
    """Angle commercial : produit phare de l'installateur × signal détecté."""
    products = spec.products_primary or ["solaire"]
    types = {s.get("type") for s in (prospect.get("_signals") or [])}
    glazing = prospect.get("_glazing") or []

    if "plainte_thermique" in types and "solaire" in products:
        base = "Film solaire anti-chaleur — surchauffe déjà évoquée par l'établissement"
    elif "besoin_confidentialite" in types and "confidentialite" in products:
        base = "Film de confidentialité — vis-à-vis mentionné sur le site"
    elif types & {"renovation_recente", "extension_agrandissement", "nouveau_batiment"}:
        base = f"Chantier en cours — poser le film {products[0]} avant remise en service"
    elif "sinistre_recent" in types and "securite" in products:
        base = "Film de sécurité — suite au sinistre mentionné"
    elif "demenagement_recent" in types:
        base = f"Nouveaux locaux — équiper les vitrages en film {products[0]} dès l'installation"
    else:
        base = f"Film {products[0]} sur les surfaces vitrées"

    if glazing:
        base += f" ({glazing[0][:60]})"
    return base[:255]
