"""CLI : génère les 4 fichiers xlsx SCP à partir d'une liste de sites web.

Exemples :
    # Hors ligne, sans LLM ni réseau — vérifie toute la chaîne
    python -m scpscrape.cli --spec config/exemple-installateur.yaml \
        --input fixtures/prospects.csv --provider fixtures \
        --fixtures-dir fixtures/extractions --outdir /tmp/batch

    # Réel, LLM local gratuit (ollama serve + ollama pull llama3.2)
    python -m scpscrape.cli --spec config/exemple-installateur.yaml \
        --input mes-prospects.csv --provider ollama --outdir batchs-livres/exemple-sa

    # Réel, OpenAI
    OPENAI_API_KEY=sk-... python -m scpscrape.cli --spec ... --provider openai --input ...
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from .export import write_batch, write_stratif_csv
from .extract import FixtureExtractor, ScrapeGraphExtractor, build_graph_config
from .pipeline import process
from .salesforce import title_case_company
from .spec import load_spec

logger = logging.getLogger("scpscrape")


def read_csv(path: Path) -> List[Dict[str, Any]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def build_dedup(paths: List[str]) -> set:
    """Noms déjà touchés (export SF, batchs précédents) → panier EXCLU."""
    names = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            logger.warning("Fichier de dédup introuvable, ignoré : %s", path)
            continue
        for row in read_csv(path):
            name = row.get("Name") or row.get("Nom du compte") or ""
            if name:
                names.add(_flat_name(name))
    return names


def _flat_name(name: str) -> str:
    import unicodedata

    value = title_case_company(name).lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn"
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scpscrape",
        description="Prototype ScrapeGraphAI → 4 xlsx de prospection Swiss Call Premium.",
    )
    parser.add_argument("--spec", required=True, help="Spec YAML de l'installateur actif.")
    parser.add_argument("--input", required=True, help="CSV des prospects (colonne Website requise).")
    parser.add_argument("--outdir", required=True, help="Répertoire de sortie des 4 xlsx.")
    parser.add_argument(
        "--provider",
        default="fixtures",
        choices=["fixtures", "ollama", "openai"],
        help="fixtures = rejoue des extractions locales (aucun appel LLM).",
    )
    parser.add_argument("--fixtures-dir", default="fixtures/extractions")
    parser.add_argument("--api-key", default=None, help="Sinon lu dans OPENAI_API_KEY.")
    parser.add_argument("--limit", type=int, default=None, help="N premiers prospects seulement.")
    parser.add_argument(
        "--dedup",
        action="append",
        default=[],
        help="CSV de comptes déjà touchés (répétable).",
    )
    parser.add_argument("--date", default=None, help="Date du batch (YYYY-MM-DD), défaut aujourd'hui.")
    parser.add_argument("--delay", type=float, default=2.0, help="Secondes entre deux sites.")
    parser.add_argument("--no-headless", action="store_true", help="Navigateur visible (débogage).")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def make_extractor(args: argparse.Namespace):
    if args.provider == "fixtures":
        return FixtureExtractor(args.fixtures_dir)
    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    if args.provider == "openai" and not api_key:
        raise SystemExit("Fournisseur openai : passez --api-key ou définissez OPENAI_API_KEY.")
    config = build_graph_config(
        args.provider,
        api_key=api_key,
        headless=not args.no_headless,
        verbose=args.verbose,
    )
    return ScrapeGraphExtractor(config, delay_seconds=args.delay)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    spec = load_spec(args.spec)
    if spec.status != "active":
        logger.warning("Installateur %s au statut « %s » — batch généré quand même.", spec.name, spec.status)

    rows = read_csv(Path(args.input))
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit(f"{args.input} : aucune ligne à traiter.")

    run_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    dedup = build_dedup(args.dedup)
    extractor = make_extractor(args)

    def on_progress(index: int, total: int, record, verdict) -> None:
        logger.info(
            "[%d/%d] %-40s → %-11s (score %s)",
            index, total, (record.get("Name") or "?")[:40], verdict.panier, verdict.score,
        )

    result = process(rows, extractor, spec, dedup_names=dedup, run_date=run_date, on_progress=on_progress)
    buckets = result["buckets"]

    outdir = Path(args.outdir)
    written = write_batch(outdir, run_date, buckets)
    stratif = write_stratif_csv(outdir, run_date, result["scored"])

    chauds = [r for r in buckets["valides"] if r.get("Lead_Panier__c") == "chaud"]
    tiedes = [r for r in buckets["valides"] if r.get("Lead_Panier__c") == "tiede"]

    print()
    print(f"Batch {spec.name} — {run_date.isoformat()} — {len(rows)} prospects analysés")
    print(f"  chauds        : {len(chauds)}")
    print(f"  tièdes        : {len(tiedes)}")
    print(f"  volume        : {len(buckets['volume'])}")
    print(f"  à confirmer   : {len(buckets['a_confirmer'])}")
    print(f"  exclus/hors-c.: {len(buckets['exclus'])}")
    print()
    for key, path in written.items():
        print(f"  {key:12s} → {path}")
    print(f"  {'stratif':12s} → {stratif}")

    if chauds:
        print("\nChauds prioritaires :")
        for row in chauds[:5]:
            print(f"  • {row['Name']} ({row['BillingCity']}) — {row['Phone'] or 'téléphone à trouver'}")
            print(f"    signal : {row['Lead_Signal__c'][:120]}")
            print(f"    angle  : {row['Lead_Angle__c']}")
            print(f"    contact: {row['Lead_Decideur__c']}")

    if result["warnings"]:
        print(f"\nAvertissements ({len(result['warnings'])}) :")
        for warning in result["warnings"][:20]:
            print(f"  - {warning}")
        if len(result["warnings"]) > 20:
            print(f"  ... et {len(result['warnings']) - 20} autres")

    return 0


if __name__ == "__main__":
    sys.exit(main())
