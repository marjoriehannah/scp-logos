"""Normalisation des données extraites vers les formats Salesforce SCP.

Règles issues de la skill `scp-data-cleaning` (téléphones E.164, Title Case,
codes postaux, picklist Industry de l'org swisscall2).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

# --- Téléphones ------------------------------------------------------------

_PHONE_JUNK = re.compile(r"[\s.\-()/ ]")


def normalize_phone(raw: Optional[str], country: str = "FR") -> Tuple[str, Optional[str]]:
    """Retourne (numéro E.164, avertissement).

    Le numéro vide n'est pas une erreur : il est simplement absent.
    """
    if not raw:
        return "", None

    value = _PHONE_JUNK.sub("", str(raw).strip())
    value = value.replace("+", "+", 1)

    if value.startswith("00"):
        value = "+" + value[2:]

    if not value.startswith("+"):
        digits = re.sub(r"\D", "", value)
        prefix = "+41" if country.upper() in {"CH", "SUISSE", "SWITZERLAND"} else "+33"
        if digits.startswith("0") and len(digits) == 10:
            value = prefix + digits[1:]
        elif digits.startswith("0") and len(digits) == 9 and prefix == "+41":
            # Numéro suisse écrit sans le 0 initial complet
            value = prefix + digits[1:]
        elif len(digits) in (9, 10, 11):
            value = prefix + digits.lstrip("0")
        else:
            return "", f"téléphone illisible: {raw!r}"

    digits = re.sub(r"\D", "", value)
    if not 9 <= len(digits) <= 15:
        return "", f"téléphone hors format E.164 ({len(digits)} chiffres): {raw!r}"

    return "+" + digits, None


def is_mobile(phone_e164: str) -> bool:
    """Mobile FR (+336/+337) ou CH (+4176..79) — signal de micro-structure."""
    if phone_e164.startswith("+336") or phone_e164.startswith("+337"):
        return True
    return bool(re.match(r"^\+417[5-9]", phone_e164))


# --- Noms ------------------------------------------------------------------

_ABBREV = {
    "st": "Saint",
    "ste": "Sainte",
    "ets": "Établissements",
    "cie": "Compagnie",
}
_KEEP_UPPER = {
    "SA", "SAS", "SASU", "SARL", "SÀRL", "EURL", "SCI", "SNC", "GMBH", "AG",
    "SCP", "SPA", "EHPAD", "CHU", "BTP", "TV", "AC",
}

# Particules françaises : minuscules sauf en tête de nom (« Restaurant du Port »,
# « Évian-les-Bains »), conformément à l'usage des noms propres.
_PARTICLES = {
    "de", "du", "des", "d", "le", "la", "les", "l", "au", "aux", "et",
    "sur", "sous", "en", "lès", "les-", "a", "à",
}


def _cap(token: str, first: bool) -> str:
    """Capitalise un mot, sauf les particules qui ne sont pas en tête."""
    if not token:
        return token
    if not first and token.lower() in _PARTICLES:
        return token.lower()
    return token[:1].upper() + token[1:].lower()


def _cap_hyphenated(word: str, first: bool) -> str:
    parts = word.split("-")
    return "-".join(_cap(part, first and index == 0) for index, part in enumerate(parts))


def title_case_company(name: Optional[str]) -> str:
    """Title Case en préservant accents, formes juridiques, sigles et particules."""
    if not name:
        return ""
    cleaned = re.sub(r"\s+", " ", str(name).strip()).strip("\"'")
    # Un nom écrit intégralement en capitales n'est pas un sigle : on le
    # retitre entièrement, sinon « HÔTEL LAC PANORAMA » garderait « LAC ».
    shouting = cleaned.isupper()
    words = []
    for index, word in enumerate(cleaned.split(" ")):
        bare = word.strip(".,")
        if not bare:
            continue
        if bare.upper() in _KEEP_UPPER:
            words.append(bare.upper())
            continue
        if bare.lower() in _ABBREV:
            words.append(_ABBREV[bare.lower()])
            continue
        if not shouting and bare.isupper() and len(bare) <= 4 and bare.isalpha():
            words.append(bare)  # sigle court laissé tel quel
            continue
        words.append(_cap_hyphenated(bare, first=index == 0))
    return " ".join(words)


def title_case_city(city: Optional[str]) -> str:
    if not city:
        return ""
    cleaned = re.sub(r"\s+", " ", str(city).strip())
    words = cleaned.replace("-", " - ").split(" ")
    out = []
    position = 0
    for word in words:
        if word == "-":
            out.append("-")
            continue
        out.append(_cap(word, first=position == 0))
        position += 1
    return "".join(w if w == "-" else (" " + w) for w in out).replace(" -", "-").replace("- ", "-").strip()


# --- Adresses --------------------------------------------------------------


def normalize_postal_code(raw: Optional[str], country: str = "FR") -> Tuple[str, Optional[str]]:
    if not raw:
        return "", None
    digits = re.sub(r"\D", "", str(raw))
    if country.upper().startswith("CH") or country.upper() == "SUISSE":
        if len(digits) == 4:
            return digits, None
        return digits, f"code postal CH inattendu: {raw!r}"
    if len(digits) == 5:
        return digits, None
    if len(digits) == 4:
        return "0" + digits, None  # zéro initial perdu par un tableur
    return digits, f"code postal FR inattendu: {raw!r}"


def department_from_postal(postal: str) -> str:
    """Code département FR à partir du code postal (gère la Corse et les DOM)."""
    if not postal or len(postal) < 2:
        return ""
    if postal.startswith("20"):
        return "2A" if postal < "20200" else "2B"
    if postal.startswith("97") or postal.startswith("98"):
        return postal[:3]
    return postal[:2]


def normalize_url(raw: Optional[str]) -> str:
    if not raw:
        return ""
    value = str(raw).strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def normalize_email(raw: Optional[str]) -> Tuple[str, Optional[str]]:
    if not raw:
        return "", None
    value = str(raw).strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", value):
        return "", f"email invalide: {raw!r}"
    return value, None


# --- Picklist Industry -----------------------------------------------------
# Valeurs API (colonne "Value") de la picklist Account.Industry de l'org SCP.
# Le mapping couvre les secteurs cibles SCP ; tout le reste tombe sur "Other".

INDUSTRY_MAP = [
    (("hotel", "hôtel", "hotellerie", "hôtellerie", "auberge", "resort", "apparthotel"), "Hôtellerie"),
    (("restaurant", "brasserie", "café", "cafe", "bistrot", "gastronomi"), "Restaurant / Café"),
    (("concession", "automobile", "garage", "carrosserie", "auto"), "Automobile"),
    (("clinique", "hôpital", "hopital", "centre medical", "centre médical", "laboratoire d'analyses"), "Healthcare"),
    (("ehpad", "maison de retraite", "medico-social", "médico-social", "ems"), "Santé & Médico-social"),
    (("creche", "crèche", "garderie", "micro-creche", "micro-crèche"), "Crèche / Garderie"),
    (("usine", "industriel", "industrie", "manufactur", "production"), "Usine"),
    (("banque", "banking", "agence bancaire"), "Banking"),
    (("assurance", "finance", "courtier"), "Finance"),
    (("avocat", "cabinet juridique", "notaire"), "Avocat"),
    (("fiduciaire", "expert-comptable", "comptab"), "Fiduciaire"),
    (("immobilier", "agence immobiliere", "agence immobilière", "real estate"), "Immobilier / Real Estate"),
    (("régie", "regie immobiliere"), "Régie immobilière"),
    (("centre commercial", "galerie marchande"), "Centre commercial"),
    (("fitness", "salle de sport", "gym"), "Club de fitness"),
    (("magasin", "boutique", "commerce de detail", "commerce de détail", "retail"), "Retail"),
    (("ecole", "école", "college", "collège", "lycee", "lycée", "formation"), "Education"),
    (("mairie", "commune", "administration", "service public", "prefecture"), "Government"),
    (("pharmacie",), "Pharmacie"),
    (("opticien", "optique"), "Opticien / Magasin d'optique"),
    (("dentiste", "cabinet dentaire"), "Dentiste"),
    (("veterinaire", "vétérinaire"), "Vétérinaire"),
    (("architecte d'interieur", "architecte d'intérieur"), "Architecte d'intérieur"),
    (("architecte",), "Architecte"),
    (("promoteur",), "Promoteur"),
    (("transport", "logistique"), "Transports / Logistique"),
    (("viticult", "domaine viticole", "cave"), "Viticulture"),
    (("hébergement touristique", "residence de tourisme", "résidence de tourisme", "camping"), "Voyage-Tourisme"),
    (("data center", "datacenter"), "Data Center"),
    (("informatique", "logiciel", "software"), "Informatique"),
    (("holding",), "Holding"),
    (("vitrerie", "miroiterie"), "Vitrerie / Miroiterie"),
    (("vitrophanie",), "Vitrophanie"),
]


def _strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn"
    )


def map_industry(sector_guess: Optional[str], activity: Optional[str] = None) -> str:
    """Secteur libre → valeur API de la picklist Account.Industry."""
    haystack = " ".join(filter(None, [sector_guess, activity])).lower()
    if not haystack.strip():
        return "Other"
    flat = _strip_accents(haystack)
    for keywords, value in INDUSTRY_MAP:
        for keyword in keywords:
            if keyword in haystack or _strip_accents(keyword) in flat:
                return value
    return "Other"


def normalize_country(raw: Optional[str], default: str = "France") -> str:
    if not raw:
        return default
    value = _strip_accents(str(raw).strip().lower())
    if value in {"ch", "suisse", "switzerland", "schweiz", "svizzera"}:
        return "Suisse"
    if value in {"fr", "france"}:
        return "France"
    return str(raw).strip()
