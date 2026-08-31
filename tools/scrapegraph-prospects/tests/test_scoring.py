"""Règles de classement : exclusions, hors-cible, à confirmer, scoring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from scpscrape.scoring import classify, suggest_angle
from scpscrape.spec import load_spec

SPEC = load_spec(Path(__file__).resolve().parents[1] / "config" / "exemple-installateur.yaml")


def record(**overrides):
    base = {
        "Name": "Hôtel Lac Panorama",
        "Phone": "+33450264118",
        "Website": "https://www.hotel-lac-panorama.fr",
        "BillingPostalCode": "74500",
        "BillingCity": "Évian-les-Bains",
        "BillingCountry": "France",
        "Industry": "Hôtellerie",
        "_activity": "Hôtel 4 étoiles au bord du lac",
        "_sector_guess": "hôtel",
        "_signals": [],
        "_glazing": [],
        "_employees": 28,
        "_is_chain": False,
        "_decision_makers": [],
    }
    base.update(overrides)
    return base


def signal(stype, date_hint=None):
    return {"type": stype, "evidence": "extrait du site", "date_hint": date_hint}


def test_competitor_is_excluded():
    verdict = classify(record(Name="Miroiterie du Léman", _activity="vitrerie et pose de films"), SPEC)
    assert verdict.panier == "exclu"
    assert "Concurrent" in verdict.raison


def test_spec_competitor_list_is_honoured():
    spec = load_spec(SPEC.path)
    spec.raw["competitors_to_avoid"] = ["Solar Films Léman"]
    verdict = classify(record(Name="Solar Films Léman"), spec)
    assert verdict.panier == "exclu"


def test_duplicate_is_excluded():
    verdict = classify(record(), SPEC, dedup_names={"hotel lac panorama"})
    assert verdict.panier == "exclu"
    assert "Doublon" in verdict.raison


def test_cessation_is_excluded():
    verdict = classify(record(_etat_administratif="C"), SPEC)
    assert verdict.panier == "exclu"


def test_holding_is_off_target():
    verdict = classify(record(Name="Savoie Participations Holding", _sector_guess="holding"), SPEC)
    assert verdict.panier == "hors_cible"


def test_structure_sans_local_is_off_target():
    verdict = classify(
        record(Phone="", Website="", BillingCity="", _employees=1),
        SPEC,
    )
    assert verdict.panier == "hors_cible"


def test_two_weak_signals_go_to_a_confirmer():
    verdict = classify(record(Phone="+33612457890", Website=""), SPEC)
    assert verdict.panier == "a_confirmer"
    assert verdict.question_verif


def test_out_of_zone_goes_to_a_confirmer():
    verdict = classify(record(BillingPostalCode="69002", BillingCity="Lyon"), SPEC)
    assert verdict.panier == "a_confirmer"
    assert "zone" in verdict.raison.lower()


def test_missing_postal_goes_to_a_confirmer():
    verdict = classify(record(BillingPostalCode=""), SPEC)
    assert verdict.panier == "a_confirmer"


def test_two_strong_signals_make_a_chaud():
    verdict = classify(
        record(_signals=[signal("renovation_recente", "2026"), signal("plainte_thermique")]),
        SPEC,
    )
    # 2 (rénovation) + 3 (thermique) + 1 (secteur) + 1 (taille) = 7
    assert verdict.score == 7
    assert verdict.panier == "chaud"
    assert verdict.confiance == "high"


def test_single_weak_signal_makes_a_tiede():
    verdict = classify(record(_signals=[signal("besoin_confidentialite")]), SPEC)
    assert verdict.panier == "tiede"


def test_no_signal_makes_a_volume():
    verdict = classify(record(_employees=None, _sector_guess="serrurerie", Industry="Other"), SPEC)
    assert verdict.panier == "volume"


def test_same_weight_counted_once():
    verdict = classify(
        record(_signals=[signal("renovation_recente"), signal("extension_agrandissement")]),
        SPEC,
    )
    # rénovation et extension partagent le poids recent_renovation : +2 une seule fois
    assert verdict.score == 4


def test_chain_is_downgraded_to_volume():
    verdict = classify(
        record(
            Name="Ibis Annecy Centre",
            _is_chain=True,
            _signals=[signal("renovation_recente", "2025"), signal("plainte_thermique")],
        ),
        SPEC,
    )
    assert verdict.panier == "volume"


def test_unknown_signal_type_is_ignored_by_scoring():
    verdict = classify(record(_signals=[signal("licorne_rose")]), SPEC)
    assert verdict.score == 2  # secteur + taille uniquement
    assert "licorne" in verdict.signal_label


def test_angle_follows_installer_products():
    angle = suggest_angle(record(_signals=[signal("plainte_thermique")]), SPEC)
    assert "solaire" in angle.lower()
