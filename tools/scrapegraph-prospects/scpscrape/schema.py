"""Schéma d'extraction ScrapeGraphAI pour la prospection Swiss Call Premium.

Ce schéma est passé tel quel à `SmartScraperGraph(schema=...)` : le LLM est
contraint de répondre dans cette structure, ce qui rend la sortie exploitable
directement par le moteur de scoring (pas de parsing de texte libre).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# Types de signaux reconnus par le moteur de scoring (scoring.py).
# Tout autre libellé retourné par le LLM est ignoré au scoring mais conservé
# dans la colonne Lead_Signal__c pour lecture humaine.
SIGNAL_TYPES = [
    "renovation_recente",
    "nouveau_batiment",
    "demenagement_recent",
    "extension_agrandissement",
    "plainte_thermique",
    "besoin_confidentialite",
    "sinistre_recent",
    "changement_direction",
    "aucun",
]


class Signal(BaseModel):
    """Un signal d'intérêt daté, justifié par une citation du site."""

    type: str = Field(
        description=(
            "Type de signal, une valeur parmi : " + ", ".join(SIGNAL_TYPES) + ". "
            "Utiliser 'aucun' si la page ne contient aucun signal."
        )
    )
    evidence: str = Field(
        description="Citation littérale (max 200 caractères) tirée de la page qui justifie le signal."
    )
    date_hint: Optional[str] = Field(
        default=None,
        description="Date ou période associée au signal si mentionnée (ex: '2025', 'printemps 2026'), sinon null.",
    )


class DecisionMaker(BaseModel):
    """Personne nommément citée sur le site avec sa fonction."""

    full_name: Optional[str] = Field(default=None, description="Prénom et nom.")
    role: Optional[str] = Field(
        default=None,
        description="Fonction exacte telle qu'écrite sur le site (gérant, directeur, responsable technique...).",
    )
    email: Optional[str] = Field(default=None, description="Email nominatif si publié, sinon null.")
    phone: Optional[str] = Field(default=None, description="Téléphone direct si publié, sinon null.")


class ProspectExtraction(BaseModel):
    """Fiche prospect extraite d'un site web d'entreprise."""

    company_name: Optional[str] = Field(default=None, description="Raison sociale ou nom commercial.")
    activity_summary: Optional[str] = Field(
        default=None, description="Activité de l'entreprise en une phrase, en français."
    )
    sector_guess: Optional[str] = Field(
        default=None,
        description=(
            "Secteur d'activité en français, en 1 à 3 mots "
            "(ex: 'hôtel', 'restaurant', 'concession automobile', 'clinique', 'crèche', "
            "'usine', 'banque', 'cabinet d'avocats', 'agence immobilière')."
        ),
    )

    phone: Optional[str] = Field(default=None, description="Téléphone principal publié sur le site.")
    email: Optional[str] = Field(default=None, description="Email de contact générique.")

    street: Optional[str] = Field(default=None, description="Numéro et rue de l'établissement.")
    postal_code: Optional[str] = Field(default=None, description="Code postal (5 chiffres FR, 4 chiffres CH).")
    city: Optional[str] = Field(default=None, description="Ville de l'établissement.")
    country: Optional[str] = Field(default=None, description="'France' ou 'Suisse'.")

    employees_hint: Optional[str] = Field(
        default=None,
        description="Effectif si mentionné (ex: '25 collaborateurs'), sinon null. Ne pas inventer.",
    )
    sites_count_hint: Optional[str] = Field(
        default=None,
        description="Nombre d'établissements/sites si le site évoque un réseau, sinon null.",
    )
    is_chain_member: Optional[bool] = Field(
        default=None,
        description=(
            "True si l'établissement appartient visiblement à une chaîne intégrée ou un groupe "
            "national (Ibis, Mercure, Novotel, Korian, Pierre & Vacances...), sinon False."
        ),
    )

    glazing_mentions: List[str] = Field(
        default_factory=list,
        description=(
            "Citations parlant de surfaces vitrées : baies vitrées, verrière, façade vitrée, "
            "véranda, showroom vitré, vitrine, terrasse panoramique. Liste vide si rien."
        ),
    )

    decision_makers: List[DecisionMaker] = Field(
        default_factory=list,
        description="Personnes citées avec leur fonction sur le site (équipe, mentions légales, 'à propos').",
    )

    signals: List[Signal] = Field(
        default_factory=list,
        description=(
            "Signaux d'intérêt détectés. N'inclure QUE ce qui est réellement écrit sur la page. "
            "Ne rien inventer : en l'absence de signal, retourner une liste vide."
        ),
    )


EXTRACTION_PROMPT = """
Tu analyses le site web d'une entreprise pour un prestataire qui pose des films
techniques sur parois vitrées (films solaires anti-chaleur, anti-UV, de sécurité,
de confidentialité, décoratifs).

Extrais uniquement des informations RÉELLEMENT présentes sur la page :

1. Identité : raison sociale, activité en une phrase, secteur en 1 à 3 mots.
2. Coordonnées : téléphone, email, adresse complète (rue, code postal, ville, pays).
3. Taille : effectif et nombre de sites SI mentionnés. Indique si l'établissement
   appartient à une chaîne intégrée ou un groupe national.
4. Surfaces vitrées : cite les passages parlant de baies vitrées, verrière, façade
   vitrée, véranda, vitrine, showroom, terrasse panoramique.
5. Décideurs : personnes nommées avec leur fonction (page équipe, à propos,
   mentions légales), avec email/téléphone direct s'ils sont publiés.
6. Signaux d'intérêt datés, avec pour chacun une citation littérale :
   - renovation_recente : travaux, rénovation, réaménagement
   - nouveau_batiment : construction neuve, nouveau site
   - demenagement_recent : déménagement, nouvelle adresse
   - extension_agrandissement : extension, agrandissement, nouvelle aile
   - plainte_thermique : chaleur, surchauffe, éblouissement, climatisation insuffisante
   - besoin_confidentialite : vis-à-vis, intimité, confidentialité des locaux
   - sinistre_recent : incendie, dégât, effraction, bris de glace
   - changement_direction : nouveau propriétaire, nouvelle direction, reprise

RÈGLE ABSOLUE : ne déduis rien, n'invente aucune donnée. Un champ non trouvé doit
rester null, une liste non alimentée doit rester vide.
"""
