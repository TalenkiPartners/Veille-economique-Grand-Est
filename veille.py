#!/usr/bin/env python3
"""
Veille économique — Grand Est & Luxembourg
Secteurs : industrie, ingénierie, énergie
Thématiques : investissements, levées de fonds, fermetures, licenciements,
              recrutement, rachats / cessions

Deux modes d'exécution :
  --mode daily    -> items des dernières 24-48h, format court
  --mode weekly   -> récap des 7 derniers jours, format plus complet

Sources :
  - Google News RSS, interrogé avec des requêtes ciblées (thème x zone)
  - Sources spécialisées via Google News restreint au domaine (La Semaine,
    Le Journal des Entreprises, Société.tech, Traces Écrites News,
    Les Affiches d'Alsace et de Lorraine, Point Éco Alsace, Paperjam, Delano)
  - L'essentiel (Luxembourg), flux RSS direct
  - BODACC (API officielle DILA/data.gouv.fr) pour les procédures collectives,
    cessions et créations dans les départements couverts

Sortie :
  - un fichier HTML (docs/index.html) pensé pour être publié via GitHub Pages
  - un email envoyé par SMTP (si les variables d'environnement sont fournies)
"""

import argparse
import html
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus

import feedparser
import requests

# ---------------------------------------------------------------------------
# Configuration — à ajuster librement, c'est le seul endroit à toucher
# ---------------------------------------------------------------------------

# Zones couvertes. Chaque entrée sert de mot-clé de recherche pour Google News.
ZONES = [
    "Grand Est",
    "Moselle",
    "Meurthe-et-Moselle",
    "Bas-Rhin",
    "Haut-Rhin",
    "Meuse",
    "Vosges",
    "Marne",
    "Ardennes",
    "Luxembourg",
    "Metz",
    "Nancy",
    "Strasbourg",
    "Mulhouse",
    "Colmar",
    "Reims",
    "Troyes",
    "Charleville-Mézières",
    "Épinal",
    "Thionville",
    "Forbach",
    "Sarreguemines",
    "Verdun",
    "Châlons-en-Champagne",
    "Franche-Comté",
    "Bourgogne",
    "Belfort",
    "Dijon",
    "Montbéliard",
]

# Départements couverts pour le filtrage BODACC (codes INSEE).
DEPARTEMENTS_GRAND_EST = ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88", "25", "90", "70", "21"]

# Thématiques suivies. Chaque valeur est une liste de synonymes/variantes
# combinés en OR dans la requête.
THEMES = {
    "Levées de fonds / investissements": ["levée de fonds", "investissement", "financement", "capital-risque"],
    "Recrutement": ["recrutement", "embauche", "création d'emplois", "plan de recrutement"],
    "Licenciements / fermetures": ["licenciement", "plan social", "PSE", "fermeture d'usine", "liquidation judiciaire"],
    "Rachats / cessions": ["rachat", "acquisition", "cession d'entreprise", "reprise d'activité"],
}

# Filtre sectoriel : un article passe s'il contient au moins un mot de cette
# liste OU le nom d'une entreprise-repère (liste juste en dessous). Beaucoup
# d'articles industriels ne disent jamais "industrie" ou "usine" — ils citent
# juste le nom de l'entreprise — d'où l'intérêt de la seconde liste.
SECTEURS = [
    "industrie", "industriel", "usine", "ingénierie", "énergie", "énergétique",
    "métallurgie", "automobile", "aéronautique", "production", "manufactur",
    "mécanique", "fonderie", "sidérurgie", "hydrogène", "nucléaire",
    "chimie", "chimique", "plasturgie", "verre", "papier", "carton",
    "agroalimentaire", "textile", "semi-conducteur", "batterie", "éolien",
    "solaire", "photovoltaïque", "décarbonation", "sous-traitance",
    "site de production", "unité de production", "chaîne de production",
    "robotique", "automatisation", "maintenance industrielle",
    "bureau d'études", "forge", "emboutissage", "injection plastique",
    "ferroviaire", "construction navale", "spatial", "logistique industrielle",
]

# Entreprises-repères de l'industrie Grand Est : si l'une d'elles est citée,
# l'article est retenu même sans mot-clé sectoriel générique. Liste à
# compléter librement au fil de l'eau selon ce que tu observes manquer.
ENTREPRISES_SURVEILLANCE = [
    "Stellantis", "ArcelorMittal", "Renault", "SOVAB", "Schaeffler",
    "Bugatti", "Ineos", "Smart", "Punch Powerglide", "Clemessy",
    "Alstom", "Siemens", "Vallourec", "De Dietrich", "SEW-Usocome",
    "Liebherr", "Lohr", "Continental", "Faurecia", "Vitesco",
    "Safran", "Thales", "Air Liquide", "Solvay", "Arkema",
    "TotalEnergies", "GRTgaz", "EDF", "Bosch",
]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"

BODACC_API = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"

# Sources spécialisées interrogées via Google News restreint au domaine
# (site:xxx), sans exigence de zone puisque ces sources sont déjà
# régionales/locales par nature.
# Format : "Nom affiché": "domaine.tld"  (domaine seul, sans https://www. ni chemin)
SOURCES_SPECIALISEES = {
    "Les Affiches d'Alsace et de Lorraine": "affiches-moniteur.com",
    "La Semaine": "lasemaine.fr",
    "Le Journal des Entreprises (Grand Est)": "lejournaldesentreprises.com",
    "Société.tech": "societe.tech",
    "Traces Écrites News": "tracesecritesnews.fr",
    "Point Éco Alsace": "pointecoalsace.fr",
    "Paperjam (Luxembourg)": "paperjam.lu",
    "Delano (Luxembourg)": "delano.lu",
}

# Flux RSS direct de L'essentiel (Luxembourg), rubrique économie.
LESSENTIEL_RSS = "https://partner-feeds.lessentiel.lu/rss/lessentiel-fr/economie"

# API officielle, gratuite, sans clé (agrège le registre SIRENE de l'INSEE).
# Permet de filtrer les entreprises directement par département et code/
# section NAF — précision qu'aucune recherche par mots-clés ne peut égaler.
RECHERCHE_ENTREPRISES_API = "https://recherche-entreprises.api.gouv.fr/search"

# Divisions NAF ciblées pour le rapprochement : 10-33 = industrie
# manufacturière, 35 = énergie, 71.12 = ingénierie / études techniques.
# (voir naf_matches_cible() pour la logique de correspondance)

# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------

def fetch_google_news(theme_keywords, zone, since):
    """Interroge Google News RSS pour un thème donné dans une zone donnée."""
    keyword_clause = " OR ".join(f'"{kw}"' for kw in theme_keywords)
    query = f"({keyword_clause}) \"{zone}\""
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))

    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            published = _parse_date(entry.get("published"))
            if published and published < since:
                continue
            items.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "source": entry.get("source", {}).get("title", "Google News"),
                "published": published,
                "summary": _strip_html(entry.get("summary", "")),
                "zone": zone,
            })
    except Exception as exc:  # noqa: BLE001 — on ne veut jamais planter tout le run pour une source
        print(f"[warn] échec Google News pour '{query}': {exc}", file=sys.stderr)

    return items


def fetch_bodacc(since):
    """Récupère les annonces BODACC (procédures collectives, cessions,
    créations) pour les départements couverts depuis `since`.

    Une requête simple par département plutôt qu'une clause combinée
    (departement + date) : les tentatives précédentes avec une clause where
    combinant plusieurs départements ET une condition de date échouaient
    systématiquement (400 Bad Request), probablement à cause de la syntaxe
    de comparaison de date. Le filtrage par date se fait ici côté Python,
    après récupération — plus robuste que de deviner la bonne syntaxe ODSQL."""
    items = []

    for dept in DEPARTEMENTS_GRAND_EST:
        params = {
            "where": f'departement="{dept}"',
            "limit": 20,
            "order_by": "dateparution desc",
        }
        try:
            resp = requests.get(BODACC_API, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for rec in data.get("results", []):
                published = _parse_date(rec.get("dateparution", ""))
                if published and published < since:
                    continue
                nom = rec.get("registre") or rec.get("commercant") or "Entreprise non identifiée"
                famille = rec.get("familleavis_lib") or rec.get("familleavis") or ""
                ville = rec.get("ville") or ""
                items.append({
                    "title": f"[BODACC] {famille} — {nom} ({ville}, {dept})",
                    "link": "https://www.bodacc.fr/",
                    "source": "BODACC",
                    "published": published,
                    "summary": rec.get("texte") or "",
                    "zone": ville or dept,
                    "_entreprise_nom": nom,
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] échec BODACC département {dept}: {exc}", file=sys.stderr)

    return items


def fetch_specialized_source(theme_keywords, domain, source_label, since):
    """Interroge Google News, restreint à un domaine précis (site:xxx),
    pour une source spécialisée. Pas d'exigence de zone : ces sources sont
    déjà régionales par nature (Alsace/Lorraine ou Luxembourg)."""
    keyword_clause = " OR ".join(f'"{kw}"' for kw in theme_keywords)
    query = f"site:{domain} ({keyword_clause})"
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))

    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            published = _parse_date(entry.get("published"))
            if published and published < since:
                continue
            items.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "source": source_label,
                "published": published,
                "summary": _strip_html(entry.get("summary", "")),
                "zone": "",
            })
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] échec source spécialisée '{source_label}': {exc}", file=sys.stderr)

    return items


def fetch_lessentiel(since):
    """Récupère le flux RSS direct de L'essentiel, rubrique économie."""
    items = []
    try:
        feed = feedparser.parse(LESSENTIEL_RSS)
        for entry in feed.entries:
            published = _parse_date(entry.get("published"))
            if published and published < since:
                continue
            items.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "source": "L'essentiel (Luxembourg)",
                "published": published,
                "summary": _strip_html(entry.get("summary", "")),
                "zone": "Luxembourg",
            })
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] échec L'essentiel: {exc}", file=sys.stderr)

    return items


def naf_matches_cible(code_naf):
    """Vérifie si un code NAF appartient aux secteurs ciblés : industrie
    manufacturière (divisions 10 à 33), énergie (division 35), ingénierie /
    études techniques (code 71.12)."""
    if not code_naf:
        return False
    code = code_naf.replace(".", "").upper()
    if code.startswith("7112"):
        return True
    division = code[:2]
    if division.isdigit():
        div_num = int(division)
        if div_num == 35 or 10 <= div_num <= 33:
            return True
    return False


def lookup_naf(company_name, cache):
    """Cherche le code NAF réel d'une entreprise par son nom via l'API
    Recherche d'Entreprises (registre SIRENE, officielle, gratuite, sans
    clé). Mis en cache pour éviter les appels répétés dans une même
    exécution si la même entreprise revient plusieurs fois."""
    key = company_name.strip().lower()
    if key in cache:
        return cache[key]

    naf = None
    try:
        resp = requests.get(
            RECHERCHE_ENTREPRISES_API,
            params={"q": company_name, "per_page": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            naf = (results[0].get("siege", {}) or {}).get("activite_principale")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] échec lookup NAF pour '{company_name}': {exc}", file=sys.stderr)

    cache[key] = naf
    return naf


def collect(mode):
    """Lance la collecte complète selon le mode (daily/weekly)."""
    now = datetime.now(timezone.utc)
    since = now - (timedelta(days=2) if mode == "daily" else timedelta(days=8))

    all_items = []
    for theme, keywords in THEMES.items():
        for zone in ZONES:
            found = fetch_google_news(keywords, zone, since)
            for item in found:
                item["theme"] = theme
            all_items.extend(found)

        for source_label, domain in SOURCES_SPECIALISEES.items():
            found = fetch_specialized_source(keywords, domain, source_label, since)
            for item in found:
                item["theme"] = theme
            all_items.extend(found)

    all_items.extend(_tag_theme(fetch_lessentiel(since), "Actualité générale (L'essentiel)"))
    all_items.extend(_tag_theme(fetch_bodacc(since), "Procédures / annonces légales (BODACC)"))

    print(f"[info] {len(all_items)} items collectés avant filtrage/dédoublonnage", file=sys.stderr)
    apres_filtre = _filter_sector(all_items)
    print(f"[info] {len(apres_filtre)} items après filtrage sectoriel/NAF", file=sys.stderr)
    apres_dedupe = _dedupe(apres_filtre)
    print(f"[info] {len(apres_dedupe)} items après dédoublonnage", file=sys.stderr)

    return apres_dedupe


# ---------------------------------------------------------------------------
# Filtrage / nettoyage
# ---------------------------------------------------------------------------

def _tag_theme(items, theme_label):
    for item in items:
        item["theme"] = theme_label
    return items


def _filter_sector(items):
    """Ne garde un article que si l'information est réellement exploitable
    pour le périmètre visé :
      - BODACC : le nom d'entreprise cité est vérifié via son vrai code NAF
        (registre SIRENE) — on ne garde plus tout par défaut.
      - Article citant une entreprise-repère : son code NAF réel est vérifié
        pour confirmer qu'elle relève bien d'un secteur ciblé (une entreprise
        peut avoir plusieurs activités ; le nom seul ne suffit pas).
      - Sinon : on retombe sur le filtre par mots-clés génériques.
    Le rapprochement NAF n'est possible que lorsqu'un nom d'entreprise est
    identifiable (BODACC, ou présence d'une entreprise de la liste de
    surveillance) — au-delà, faute d'extraction fiable du nom d'entreprise
    dans un titre d'article quelconque, le filtre par mots-clés reste la
    seule option.
    """
    import re as _re

    naf_cache = {}
    kept = []

    for item in items:
        # Retire un éventuel préfixe de rubrique ("Énergie: ", "Automobile: ")
        # qui peut faire matcher un mot-clé sectoriel sans rapport avec le
        # contenu réel de l'article — cas fréquent sur L'essentiel, dont les
        # titres sont préfixés par leur catégorie éditoriale.
        titre_nettoye = _re.sub(r"^[^:]{1,40}:\s*", "", item["title"])
        text_lower = f"{titre_nettoye} {item['summary']}".lower()

        if item["source"] == "BODACC":
            entreprise = item.get("_entreprise_nom")
            if entreprise and entreprise != "Entreprise non identifiée":
                naf = lookup_naf(entreprise, naf_cache)
                if naf_matches_cible(naf):
                    kept.append(item)
            continue

        entreprise_citee = next(
            (e for e in ENTREPRISES_SURVEILLANCE if e.lower() in text_lower), None
        )
        if entreprise_citee:
            naf = lookup_naf(entreprise_citee, naf_cache)
            if naf_matches_cible(naf):
                kept.append(item)
                continue
            # Nom non trouvé dans le registre, ou secteur hors cible : on
            # retombe sur le filtre par mots-clés plutôt que d'écarter
            # directement (le lookup peut échouer sans que ce soit une
            # vraie non-pertinence).

        # L'essentiel n'est pas scopé géographiquement au moment de la
        # requête (contrairement à Google News, interrogé avec le nom de
        # zone en dur, ou aux sources spécialisées, régionales par nature) :
        # on exige donc explicitement une mention du Luxembourg ou d'une des
        # zones suivies avant d'accepter un simple mot-clé sectoriel, sinon
        # le préfixe de rubrique suffit à faire passer n'importe quelle
        # actualité internationale.
        if item["source"] == "L'essentiel (Luxembourg)":
            zone_mentionnee = any(z.lower() in text_lower for z in ZONES)
            if not zone_mentionnee:
                continue

        if any(sect in text_lower for sect in SECTEURS):
            kept.append(item)

    return kept


def _dedupe(items):
    """Déduplique par similarité de titre (pas seulement égalité stricte),
    pour repérer le même événement raconté différemment par deux sources."""
    import difflib
    import re

    def normalize(title):
        t = title.lower().strip()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t)
        return t

    items = sorted(
        items,
        key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    kept = []
    kept_normalized = []
    for item in items:
        norm = normalize(item["title"])
        is_duplicate = any(
            difflib.SequenceMatcher(None, norm, other).ratio() > 0.72
            for other in kept_normalized
        )
        if is_duplicate:
            continue
        kept.append(item)
        kept_normalized.append(norm)

    return kept


def _parse_date(value):
    if not value:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _strip_html(text):
    import re
    return re.sub("<[^<]+?>", "", text or "").strip()


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------

def render_html(items, mode):
    title = "Veille quotidienne" if mode == "daily" else "Récap hebdomadaire"
    today = datetime.now().strftime("%d/%m/%Y")

    by_theme = {}
    for item in items:
        by_theme.setdefault(item["theme"], []).append(item)

    sections = []
    for theme, theme_items in by_theme.items():
        rows = []
        for item in theme_items:
            date_str = item["published"].strftime("%d/%m") if item["published"] else ""
            rows.append(f"""
            <li>
              <span class="date">{date_str}</span>
              <a href="{html.escape(item['link'])}" target="_blank">{html.escape(item['title'])}</a>
              <span class="source">— {html.escape(item['source'])}</span>
            </li>""")
        sections.append(f"""
        <section>
          <h2>{html.escape(theme)} <span class="count">({len(theme_items)})</span></h2>
          <ul>{''.join(rows)}</ul>
        </section>""")

    body = "".join(sections) if sections else "<p>Rien de significatif détecté sur cette période.</p>"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Veille économique Grand Est & Luxembourg — {title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.4em; border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; }}
  h1 .sub {{ display: block; font-size: 0.6em; font-weight: normal; color: #666; margin-top: 4px; }}
  h2 {{ font-size: 1.05em; margin-top: 28px; color: #b5651d; }}
  .count {{ color: #999; font-weight: normal; font-size: 0.85em; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 8px 0; border-bottom: 1px solid #e5e5e5; }}
  .date {{ color: #999; font-size: 0.85em; margin-right: 8px; }}
  .source {{ color: #999; font-size: 0.85em; }}
  a {{ color: #1a1a1a; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  footer {{ margin-top: 40px; font-size: 0.8em; color: #999; }}
</style>
</head>
<body>
  <h1>Veille économique — Grand Est & Luxembourg
    <span class="sub">{title} · industrie / ingénierie / énergie · {today}</span>
  </h1>
  {body}
  <footer>Généré automatiquement · sources : Google News, BODACC</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Envoi email
# ---------------------------------------------------------------------------

def send_email(html_content, mode):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    recipient = os.environ.get("RECIPIENT_EMAIL")
    sender = os.environ.get("SENDER_EMAIL", smtp_user)

    if not all([smtp_host, smtp_user, smtp_password, recipient]):
        print("[info] variables SMTP absentes, envoi email ignoré (mode test local ?)", file=sys.stderr)
        return

    subject = "Veille éco Grand Est/Luxembourg — " + ("quotidienne" if mode == "daily" else "récap hebdo")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(sender, [recipient], msg.as_string())
        print(f"[ok] email envoyé à {recipient}")
    except Exception as exc:  # noqa: BLE001 — un échec d'envoi ne doit pas bloquer la publication de la page
        print(f"[warn] échec de l'envoi email: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--output", default="docs/index.html")
    args = parser.parse_args()

    items = collect(args.mode)
    print(f"[info] {len(items)} items retenus en mode {args.mode}")

    rendered = render_html(items, args.mode)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(rendered)
    print(f"[ok] page écrite dans {args.output}")

    send_email(rendered, args.mode)


if __name__ == "__main__":
    main()
