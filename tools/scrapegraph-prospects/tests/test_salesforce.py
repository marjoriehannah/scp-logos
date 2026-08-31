"""Normalisation Salesforce : téléphones, noms, codes postaux, picklist Industry."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from scpscrape.salesforce import (
    department_from_postal,
    is_mobile,
    map_industry,
    normalize_country,
    normalize_email,
    normalize_phone,
    normalize_postal_code,
    normalize_url,
    title_case_city,
    title_case_company,
)


@pytest.mark.parametrize(
    "raw,country,expected",
    [
        ("04 78 12 34 56", "FR", "+33478123456"),
        ("04.50.91.77.30", "FR", "+33450917730"),
        ("0033478123456", "FR", "+33478123456"),
        ("+33 4 78 12 34 56", "FR", "+33478123456"),
        ("022 345 67 89", "CH", "+41223456789"),
        ("+41 22 345 67 89", "CH", "+41223456789"),
        ("", "FR", ""),
        (None, "FR", ""),
    ],
)
def test_normalize_phone(raw, country, expected):
    assert normalize_phone(raw, country)[0] == expected


def test_normalize_phone_rejects_too_short():
    value, warning = normalize_phone("12 34", "FR")
    assert value == ""
    assert warning


def test_is_mobile():
    assert is_mobile("+33612457890")
    assert is_mobile("+41791234567")
    assert not is_mobile("+33450264118")


def test_title_case_company_keeps_legal_forms_and_particles():
    assert title_case_company("restaurant du port") == "Restaurant du Port"
    assert title_case_company("ets martin sarl") == "Établissements Martin SARL"
    assert title_case_company('  "HÔTEL   LAC PANORAMA" ') == "Hôtel Lac Panorama"
    # Un sigle au milieu d'un nom normalement casé reste intact
    assert title_case_company("SNCF Voyages") == "SNCF Voyages"
    assert title_case_company("garage mont-blanc") == "Garage Mont-Blanc"


def test_title_case_city():
    assert title_case_city("évian-les-bains") == "Évian-les-Bains"
    assert title_case_city("ANNECY") == "Annecy"
    assert title_case_city("saint-jean-de-maurienne") == "Saint-Jean-de-Maurienne"


def test_postal_code():
    assert normalize_postal_code("74 500", "FR")[0] == "74500"
    assert normalize_postal_code("1201", "CH")[0] == "1201"
    # Zéro initial mangé par un tableur
    assert normalize_postal_code("1200", "FR")[0] == "01200"
    assert normalize_postal_code("123", "FR")[1] is not None


def test_department_from_postal():
    assert department_from_postal("74500") == "74"
    assert department_from_postal("20090") == "2A"
    assert department_from_postal("20250") == "2B"
    assert department_from_postal("97400") == "974"


def test_map_industry_uses_picklist_values():
    assert map_industry("hôtel") == "Hôtellerie"
    assert map_industry("concession automobile") == "Automobile"
    assert map_industry("clinique") == "Healthcare"
    assert map_industry("crèche") == "Crèche / Garderie"
    assert map_industry("miroiterie") == "Vitrerie / Miroiterie"
    assert map_industry("activité totalement inconnue") == "Other"
    # Sans accents dans la source
    assert map_industry("hotellerie") == "Hôtellerie"


def test_normalize_url_and_email():
    assert normalize_url("www.exemple.fr/") == "https://www.exemple.fr"
    assert normalize_email("Contact@Exemple.FR")[0] == "contact@exemple.fr"
    assert normalize_email("pas-un-email")[0] == ""


def test_normalize_country():
    assert normalize_country("CH") == "Suisse"
    assert normalize_country("FRANCE") == "France"
    assert normalize_country(None) == "France"
