"""Assemblage : extraction brute → enregistrement normalisé → verdict → lignes xlsx."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .salesforce import (
    map_industry,
    normalize_country,
    normalize_email,
    normalize_phone,
    normalize_postal_code,
    normalize_url,
    title_case_city,
    title_case_company,
)
from .scoring import Verdict, classify, suggest_angle
from .spec import InstallerSpec

DECIDEUR_SOURCE = "ScrapeGraphAI (site web)"
DECIDEUR_FALLBACK_SOURCE = "Fallback (intitulé générique)"

# Intitulé de repli quand aucun nom n'est publié, par secteur.
FALLBACK_ROLES = {
    "Hôtellerie": "Le directeur de l'établissement",
    "Restaurant / Café": "Le gérant",
    "Automobile": "Le chef des ventes / responsable du site",
    "Healthcare": "Le directeur d'établissement",
    "Santé & Médico-social": "Le directeur d'établissement",
    "Crèche / Garderie": "La direction de la crèche",
    "Usine": "Le responsable maintenance / services généraux",
    "Banking": "Le directeur d'agence",
    "Centre commercial": "Le directeur du centre",
    "Retail": "Le responsable du magasin",
}
DEFAULT_FALLBACK_ROLE = "Le dirigeant"


def parse_employees(hint: Optional[str]) -> Optional[int]:
    """« 25 collaborateurs » → 25 ; « une équipe soudée » → None."""
    if not hint:
        return None
    match = re.search(r"\d[\d\s]*", str(hint))
    if not match:
        return None
    try:
        return int(re.sub(r"\s", "", match.group(0)))
    except ValueError:
        return None


def build_record(
    source_row: Dict[str, Any],
    extraction: Dict[str, Any],
    spec: InstallerSpec,
) -> Tuple[Dict[str, Any], List[str]]:
    """Fusionne la ligne d'entrée et l'extraction en un enregistrement Salesforce.

    La ligne d'entrée (export SF, liste Marjorie) fait autorité : on ne remplace
    jamais une valeur fournie par une valeur devinée sur le web.
    """
    warnings: List[str] = []
    extraction = extraction or {}

    country = normalize_country(
        source_row.get("BillingCountry") or extraction.get("country"),
        default="France",
    )

    name = title_case_company(source_row.get("Name") or extraction.get("company_name"))

    phone, warn = normalize_phone(
        source_row.get("Phone") or extraction.get("phone"), country=country
    )
    if warn:
        warnings.append(f"{name or source_row.get('Website')}: {warn}")

    postal, warn = normalize_postal_code(
        source_row.get("BillingPostalCode") or extraction.get("postal_code"), country=country
    )
    if warn:
        warnings.append(f"{name}: {warn}")

    email, warn = normalize_email(source_row.get("Email") or extraction.get("email"))
    if warn:
        warnings.append(f"{name}: {warn}")

    industry = source_row.get("Industry") or map_industry(
        extraction.get("sector_guess"), extraction.get("activity_summary")
    )

    signals = [s for s in (extraction.get("signals") or []) if isinstance(s, dict)]
    signals = [s for s in signals if s.get("type") and s.get("type") != "aucun"]

    record: Dict[str, Any] = {
        "Id": source_row.get("Id", ""),
        "Name": name,
        "Phone": phone,
        "BillingPostalCode": postal,
        "BillingCity": title_case_city(source_row.get("BillingCity") or extraction.get("city")),
        "BillingStreet": source_row.get("BillingStreet") or extraction.get("street") or "",
        "BillingCountry": country,
        "BillingState": source_row.get("BillingState", ""),
        "Industry": industry,
        "Website": normalize_url(source_row.get("Website")),
        "Email": email,
        "Clients__c": spec.name,
        "LinkedIn_Entreprise_URL": normalize_url(source_row.get("LinkedIn_Entreprise_URL")),
        "Lead_Decideur_LinkedIn_URL": normalize_url(source_row.get("Lead_Decideur_LinkedIn_URL")),
        # Champs de travail (préfixe _) : utilisés par le scoring, non exportés.
        "_activity": extraction.get("activity_summary") or "",
        "_sector_guess": extraction.get("sector_guess") or "",
        "_signals": signals,
        "_glazing": [g for g in (extraction.get("glazing_mentions") or []) if isinstance(g, str)],
        "_employees": parse_employees(extraction.get("employees_hint")),
        "_is_chain": extraction.get("is_chain_member"),
        "_etat_administratif": source_row.get("etat_administratif", ""),
        "_decision_makers": [
            d for d in (extraction.get("decision_makers") or []) if isinstance(d, dict)
        ],
    }

    if not record["Name"]:
        warnings.append(f"{source_row.get('Website')}: nom d'entreprise introuvable")

    return record, warnings


def format_decideur(record: Dict[str, Any]) -> Tuple[str, str]:
    """(libellé décideur, provenance). Nom réel si publié, sinon intitulé de repli."""
    for person in record.get("_decision_makers") or []:
        full_name = (person.get("full_name") or "").strip()
        if not full_name:
            continue
        role = (person.get("role") or "").strip()
        label = f"{full_name} — {role}" if role else full_name
        contact = person.get("email") or person.get("phone")
        if contact:
            label += f" ({contact})"
        return label[:255], DECIDEUR_SOURCE
    role = FALLBACK_ROLES.get(record.get("Industry", ""), DEFAULT_FALLBACK_ROLE)
    return role, DECIDEUR_FALLBACK_SOURCE


def build_description(record: Dict[str, Any], verdict: Verdict, spec: InstallerSpec, run_date: date) -> str:
    prefix = f"Batch {spec.name} {run_date.isoformat()} — {verdict.panier}"
    raison = verdict.raison or "sans motif"
    website = record.get("Website")
    tail = f" | source: {website}" if website else ""
    return f"{prefix} | {raison}{tail}"[:32000]


def to_rows(
    record: Dict[str, Any],
    verdict: Verdict,
    spec: InstallerSpec,
    run_date: date,
) -> Dict[str, Any]:
    """Enregistrement + verdict → ligne prête pour le fichier xlsx correspondant."""
    decideur, decideur_source = format_decideur(record)
    common = {
        "Id": record.get("Id", ""),
        "Name": record.get("Name", ""),
        "Phone": record.get("Phone", ""),
        "BillingPostalCode": record.get("BillingPostalCode", ""),
        "BillingCity": record.get("BillingCity", ""),
        "Industry": record.get("Industry", ""),
        "Description": build_description(record, verdict, spec, run_date),
    }

    if verdict.panier in {"chaud", "tiede", "volume"}:
        return {
            **common,
            "Clients__c": spec.name,
            "Lead_Panier__c": verdict.panier,
            "Lead_Confiance__c": verdict.confiance,
            "Lead_Signal__c": verdict.signal_label or verdict.raison[:200],
            "Lead_Angle__c": suggest_angle(record, spec),
            "Lead_Decideur__c": decideur,
            "Lead_Decideur_Source__c": decideur_source,
            "LinkedIn_Entreprise_URL": record.get("LinkedIn_Entreprise_URL", ""),
            "Lead_Decideur_LinkedIn_URL": record.get("Lead_Decideur_LinkedIn_URL", ""),
        }

    if verdict.panier == "a_confirmer":
        return {
            **common,
            "Lead_Panier__c": "a_confirmer",
            "Lead_Confiance__c": verdict.confiance,
            "Lead_Question_Verif__c": verdict.question_verif,
            "Lead_Decideur__c": decideur,
            "Lead_Decideur_Source__c": decideur_source,
            "LinkedIn_Entreprise_URL": record.get("LinkedIn_Entreprise_URL", ""),
            "Lead_Decideur_LinkedIn_URL": record.get("Lead_Decideur_LinkedIn_URL", ""),
        }

    return {
        **common,
        "Statut": "Exclu" if verdict.panier == "exclu" else "Hors-cible",
        "Raison": verdict.raison[:150],
    }


def process(
    source_rows: List[Dict[str, Any]],
    extractor,
    spec: InstallerSpec,
    dedup_names: Optional[set] = None,
    run_date: Optional[date] = None,
    on_progress=None,
) -> Dict[str, Any]:
    """Boucle principale : extrait, normalise, classe, ventile dans les 4 paniers."""
    run_date = run_date or date.today()
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "valides": [],
        "volume": [],
        "a_confirmer": [],
        "exclus": [],
    }
    warnings: List[str] = []
    scored: List[Tuple[Dict[str, Any], Verdict]] = []

    for index, source_row in enumerate(source_rows, start=1):
        url = normalize_url(source_row.get("Website"))
        extraction: Dict[str, Any] = {}
        if url:
            try:
                extraction = extractor.extract(url, hint=source_row.get("Name"))
            except Exception as exc:  # une URL cassée ne doit pas tuer le batch
                warnings.append(f"{source_row.get('Name') or url}: extraction échouée ({exc})")
        else:
            warnings.append(f"{source_row.get('Name', '?')}: aucune URL, scoring sur la seule ligne source")

        record, record_warnings = build_record(source_row, extraction, spec)
        warnings.extend(record_warnings)

        verdict = classify(record, spec, dedup_names=dedup_names)
        scored.append((record, verdict))
        row = to_rows(record, verdict, spec, run_date)

        if verdict.panier in {"chaud", "tiede"}:
            buckets["valides"].append(row)
        elif verdict.panier == "volume":
            buckets["volume"].append(row)
        elif verdict.panier == "a_confirmer":
            buckets["a_confirmer"].append(row)
        else:
            buckets["exclus"].append(row)

        if on_progress:
            on_progress(index, len(source_rows), record, verdict)

    # Chauds d'abord dans le fichier VALIDES : le téléprospecteur attaque par là.
    buckets["valides"].sort(key=lambda r: (r.get("Lead_Panier__c") != "chaud", r.get("Name", "")))
    return {"buckets": buckets, "warnings": warnings, "scored": scored}
