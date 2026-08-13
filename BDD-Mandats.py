"""Génère la couche CSV des mandats utilisée par le projet QGIS.

Le script récupère les projets Bexio, extrait la commune et le numéro de bien-
fonds (BF), puis recherche les coordonnées de la parcelle sur geo.admin.ch.

Configuration requise :
    BEXIO_API_TOKEN    jeton d'accès personnel Bexio

Configuration optionnelle :
    OFS_SNAPSHOT_DATE date de référence OFS au format JJ-MM-AAAA
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import logging
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("mandats")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LOCALITIES_FILE = BASE_DIR / "AMTOVZ_CSV_LV95.csv"
DEFAULT_OUTPUT_FILE = BASE_DIR / "projects.csv"

OFS_COMMUNES_URL = "https://www.agvchapp.bfs.admin.ch/api/communes/snapshot"
BEXIO_PROJECTS_URL = "https://api.bexio.com/2.0/pr_project"
GEOADMIN_SEARCH_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"

HTTP_TIMEOUT = 45
CSV_COLUMNS = (
    "num",
    "name",
    "Commune",
    "Commune_recherche",
    "parcelle",
    "lon",
    "lat",
    "date",
)

# Corrections métier explicites, appliquées avant la correspondance approchée.
PRIORITY_COMMUNES = {
    "Arzier": "Arzier-Le Muids",
    "Bevaix": "La Grande Béroche",
    "Cugy": "Cugy (VD)",
    "La Chaux": "La Chaux (Cossonay)",
    "Neuveville (BE)": "La Neuveville",
    "Vugelles": "Vugelles-La Mothe",
    "Yverdon": "Yverdon-les-Bains",
}

NULL = "NULL"
BF_PATTERN = re.compile(
    r"(?<![A-Z0-9])BF\s*(?:N[°O]?\s*)?[:#-]?\s*(\d+)",
    re.IGNORECASE,
)
CANTON_CODE_PATTERN = re.compile(r"\s*\([A-Z]{2}\)\s*", re.IGNORECASE)
HTML_BOLD_PATTERN = re.compile(r"<b>(.*?)</b>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParcelResult:
    """Résultat normalisé d'une recherche de parcelle."""

    lon: Optional[float] = None
    lat: Optional[float] = None
    commune: Optional[str] = None


class HttpError(RuntimeError):
    """Erreur réseau ou réponse HTTP invalide."""


class HttpResponse:
    """Réponse HTTP minimale utilisée par le générateur."""

    def __init__(self, body: bytes, encoding: str = "utf-8") -> None:
        self.text = body.decode(encoding, errors="replace")

    def json(self) -> object:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as error:
            raise HttpError("La réponse HTTP ne contient pas de JSON valide") from error

    def raise_for_status(self) -> None:
        # ``urlopen`` lève déjà HTTPError pour les statuts 4xx et 5xx.
        return None


class HttpSession:
    """Petit client HTTP fondé uniquement sur la bibliothèque standard."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "HttpSession":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(
        self,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, object]] = None,
        timeout: Union[float, tuple[float, float]] = HTTP_TIMEOUT,
    ) -> HttpResponse:
        query = urlencode(params or {})
        request_url = f"{url}?{query}" if query else url
        request_headers = dict(self.headers)
        request_headers.update(headers or {})
        request = Request(request_url, headers=request_headers, method="GET")
        timeout_seconds = timeout[-1] if isinstance(timeout, tuple) else timeout

        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                encoding = response.headers.get_content_charset() or "utf-8"
                return HttpResponse(response.read(), encoding=encoding)
        except HTTPError as error:
            raise HttpError(
                f"HTTP {error.code} pour {url}: {error.reason}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise HttpError(f"Connexion impossible à {url}: {error}") from error


def _normalise(value: str, *, remove_canton: bool = False) -> str:
    """Normalise un nom pour les comparaisons sans modifier sa restitution."""

    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    if remove_canton:
        value = CANTON_CODE_PATTERN.sub(" ", value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalise(left), _normalise(right)).ratio()


def remove_canton_code(text: str) -> str:
    """Supprime un code cantonal comme ``(BE)`` d'un texte."""

    return " ".join(CANTON_CODE_PATTERN.sub(" ", text).split())


@lru_cache(maxsize=8)
def load_lieux_dits(path: str = str(DEFAULT_LOCALITIES_FILE)) -> dict[str, str]:
    """Charge une seule fois la correspondance lieu-dit → commune."""

    localities_path = Path(path)
    locality_candidates: dict[str, set[str]] = {}
    with localities_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        required = {"Ortschaftsname", "Gemeindename"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(
                f"Colonnes absentes de {localities_path}: "
                f"{', '.join(sorted(required))}"
            )

        for row in reader:
            locality = (row.get("Ortschaftsname") or "").strip()
            commune = (row.get("Gemeindename") or "").strip()
            if locality and commune and _normalise(locality) != _normalise(commune):
                locality_candidates.setdefault(_normalise(locality), set()).add(commune)

    # Un lieu postal associé à plusieurs communes n'est pas utilisable sans
    # information supplémentaire: mieux vaut ne pas le corriger arbitrairement.
    return {
        locality: next(iter(communes))
        for locality, communes in locality_candidates.items()
        if len(communes) == 1
    }


def get_list_communes(
    session: HttpSession,
    snapshot_date: Optional[str] = None,
) -> list[str]:
    """Récupère la liste des communes suisses auprès de l'OFS."""

    requested_date = snapshot_date or os.environ.get("OFS_SNAPSHOT_DATE")
    requested_date = requested_date or date.today().strftime("%d-%m-%Y")
    response = session.get(
        OFS_COMMUNES_URL,
        params={"date": requested_date},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    communes: list[str] = []
    for columns in csv.reader(io.StringIO(response.text)):
        if len(columns) <= 6:
            continue
        commune = columns[6].strip()
        if commune and _normalise(commune) not in {
            "commune",
            "commune name",
            "gemeindename",
            "name",
        }:
            communes.append(commune)

    # Conserve l'ordre de l'OFS tout en supprimant les éventuels doublons.
    communes = list(dict.fromkeys(communes))
    if not communes:
        raise RuntimeError("L'API de l'OFS n'a retourné aucune commune exploitable")

    LOGGER.info("%d communes chargées (référence %s)", len(communes), requested_date)
    return communes


def get_projects(
    session: HttpSession,
    token: str,
    page_size: int = 500,
) -> list[dict]:
    """Récupère tous les projets Bexio par pagination."""

    if not token.strip():
        raise ValueError("Le jeton Bexio est vide")
    if page_size < 1:
        raise ValueError("page_size doit être supérieur à zéro")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token.strip()}",
    }
    projects: list[dict] = []
    offset = 0

    while True:
        response = session.get(
            BEXIO_PROJECTS_URL,
            headers=headers,
            params={"orderby": "name_desc", "limit": page_size, "offset": offset},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise RuntimeError("Réponse Bexio inattendue: une liste était attendue")
        if any(not isinstance(project, dict) for project in page):
            raise RuntimeError("Réponse Bexio inattendue: projet invalide dans la liste")

        projects.extend(page)
        LOGGER.info("Bexio: %d projets reçus", len(projects))
        if len(page) < page_size:
            break
        offset += len(page)

    return projects


def get_bf(text: str) -> Optional[str]:
    """Retourne le premier numéro de bien-fonds présent dans un intitulé."""

    match = BF_PATTERN.search(text or "")
    return match.group(1) if match else None


def _clean_commune_candidate(value: str) -> str:
    value = value.replace("-s-", "-sur-")
    value = re.sub(r"\bSt-", "Saint-", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -_,")


@lru_cache(maxsize=8)
def _commune_indexes(
    communes: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Construit et met en cache les index de communes exacts."""

    by_key = {_normalise(commune): commune for commune in communes}
    without_canton_candidates: dict[str, list[str]] = {}
    for commune in communes:
        key = _normalise(commune, remove_canton=True)
        without_canton_candidates.setdefault(key, []).append(commune)
    without_canton = {
        key: values[0]
        for key, values in without_canton_candidates.items()
        if len(values) == 1
    }
    return by_key, without_canton


def _name_variants(value: str) -> list[str]:
    """Produit les variantes plausibles d'un préfixe de mandat."""

    cleaned = _clean_commune_candidate(value)
    primary_parts = [
        part.strip()
        for part in re.split(r"\s+-\s+|_+", cleaned)
        if part.strip()
    ]
    variants = [cleaned, *primary_parts]

    for part in list(variants):
        # Les guillemets servent souvent à indiquer un village secondaire.
        before_quote = re.split(r"\s+['\"“]{1,2}", part, maxsplit=1)[0].strip()
        without_long_parenthesis = re.sub(r"\s*\([^)]{3,}\)\s*", " ", part).strip()
        variants.extend((before_quote, without_long_parenthesis))

    return list(dict.fromkeys(variant for variant in variants if variant))


def correct_name(
    name: str,
    communes: Sequence[str],
    lieux_dits: Optional[Mapping[str, str]] = None,
    priority_communes: Mapping[str, str] = PRIORITY_COMMUNES,
) -> str:
    """Corrige un nom de commune avec des règles exactes puis approchées."""

    variants = _name_variants(name)
    if not variants:
        return ""

    commune_tuple = tuple(communes)
    commune_by_key, commune_without_canton = _commune_indexes(commune_tuple)
    priority_by_key = {
        _normalise(source): target for source, target in priority_communes.items()
    }
    localities = lieux_dits if lieux_dits is not None else load_lieux_dits()

    for variant in variants:
        key = _normalise(variant)
        if key in priority_by_key:
            return priority_by_key[key]
        if key in commune_by_key:
            return commune_by_key[key]
        if key in localities:
            return localities[key]
        key_without_canton = _normalise(variant, remove_canton=True)
        if key_without_canton in commune_without_canton:
            return commune_without_canton[key_without_canton]

    if not commune_tuple:
        return variants[1] if len(variants) > 1 else variants[0]

    best_match: Optional[tuple[float, str, str]] = None
    for variant in variants:
        candidate = max(
            commune_tuple,
            key=lambda commune: _similarity(variant, commune),
        )
        score = _similarity(variant, candidate)
        if best_match is None or score > best_match[0]:
            best_match = (score, candidate, variant)

    assert best_match is not None
    score, candidate, matched_variant = best_match
    threshold = 0.80 if len(_normalise(matched_variant)) > 5 else 0.90
    if score >= threshold:
        return candidate

    # Si aucun référentiel ne correspond, le premier segment reste une
    # information plus utile que l'intitulé complet du mandat.
    return variants[1] if len(variants) > 1 else variants[0]


def get_commune(
    text: str,
    communes: Sequence[str],
    lieux_dits: Optional[Mapping[str, str]] = None,
) -> str:
    """Extrait puis corrige la commune figurant dans le nom d'un projet."""

    text = text or ""
    bf_match = BF_PATTERN.search(text)
    prefix = text[: bf_match.start()] if bf_match else text
    prefix = prefix.strip(" -_,")
    return correct_name(prefix, communes, lieux_dits=lieux_dits)


def _commune_from_label(label: str) -> Optional[str]:
    match = HTML_BOLD_PATTERN.search(label or "")
    if not match:
        return None
    commune = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    return commune or None


def search_parcel(
    session: HttpSession,
    commune: str,
    parcel_number: str,
) -> ParcelResult:
    """Recherche une parcelle et retourne toujours une structure cohérente."""

    formatted_name = remove_canton_code(f"{commune} {parcel_number}")
    response = session.get(
        GEOADMIN_SEARCH_URL,
        params={
            "searchText": formatted_name,
            "origins": "parcel,district",
            "type": "locations",
        },
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", []) if isinstance(payload, dict) else []

    expected_key = _normalise(commune, remove_canton=True)
    ranked_results: list[tuple[float, dict, str]] = []
    for result in results:
        attrs = result.get("attrs", {}) if isinstance(result, dict) else {}
        result_commune = _commune_from_label(str(attrs.get("label", "")))
        if not result_commune:
            continue
        result_key = _normalise(result_commune, remove_canton=True)
        score = SequenceMatcher(None, expected_key, result_key).ratio()
        ranked_results.append((score, attrs, result_commune))

    if not ranked_results:
        LOGGER.warning("Parcelle introuvable: %s", formatted_name)
        return ParcelResult()

    score, attrs, result_commune = max(ranked_results, key=lambda item: item[0])
    # Un résultat éloigné est plus dangereux qu'une absence de coordonnées.
    if score < 0.88:
        LOGGER.warning(
            "Résultat géographique rejeté pour %s (commune proposée: %s)",
            formatted_name,
            result_commune,
        )
        return ParcelResult()

    try:
        return ParcelResult(
            lon=float(attrs["lon"]),
            lat=float(attrs["lat"]),
            commune=result_commune,
        )
    except (KeyError, TypeError, ValueError):
        LOGGER.warning("Coordonnées invalides pour %s", formatted_name)
        return ParcelResult()


def _csv_value(value: object) -> object:
    return NULL if value is None or value == "" else value


def _project_row(
    project: Mapping[str, object],
    communes: Sequence[str],
    lieux_dits: Mapping[str, str],
    session: HttpSession,
) -> list[object]:
    name = str(project.get("name") or "").strip()
    project_number = str(project.get("nr") or "").strip()
    start_date = project.get("start_date")

    parcel_number = get_bf(name)
    commune = get_commune(name, communes, lieux_dits=lieux_dits)
    parcel_result = ParcelResult()

    if commune and parcel_number:
        try:
            parcel_result = search_parcel(session, commune, parcel_number)
        except (HttpError, ValueError) as error:
            LOGGER.warning(
                "Recherche impossible pour le mandat %s (%s): %s",
                project_number,
                name,
                error,
            )

    return [
        _csv_value(project_number),
        _csv_value(name),
        _csv_value(commune),
        _csv_value(parcel_result.commune),
        _csv_value(parcel_number),
        _csv_value(parcel_result.lon),
        _csv_value(parcel_result.lat),
        _csv_value(start_date),
    ]


def write_bdd(
    projects: Iterable[Mapping[str, object]],
    communes: Sequence[str],
    session: HttpSession,
    output_path: Path = DEFAULT_OUTPUT_FILE,
    localities_path: Path = DEFAULT_LOCALITIES_FILE,
) -> int:
    """Écrit le CSV atomiquement et retourne le nombre de mandats traités."""

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lieux_dits = load_lieux_dits(str(Path(localities_path).resolve()))
    count = 0

    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
            writer = csv.writer(temporary_file, delimiter=";")
            writer.writerow(CSV_COLUMNS)

            for project in projects:
                try:
                    writer.writerow(
                        _project_row(project, communes, lieux_dits, session)
                    )
                except Exception:
                    # Une entrée Bexio mal formée ne doit pas annuler toute la base.
                    LOGGER.exception("Mandat ignoré car son format est invalide: %r", project)
                    writer.writerow(
                        [
                            _csv_value(project.get("nr")),
                            _csv_value(project.get("name")),
                            NULL,
                            NULL,
                            NULL,
                            NULL,
                            NULL,
                            _csv_value(project.get("start_date")),
                        ]
                    )
                count += 1

        os.replace(temporary_name, output_path)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)

    LOGGER.info("%d mandats écrits dans %s", count, output_path)
    return count


def build_database(
    output_path: Path = DEFAULT_OUTPUT_FILE,
    localities_path: Path = DEFAULT_LOCALITIES_FILE,
    snapshot_date: Optional[str] = None,
    page_size: int = 500,
) -> int:
    """Orchestre le téléchargement, l'enrichissement et l'écriture."""

    token = os.environ.get("BEXIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Variable BEXIO_API_TOKEN absente. Définissez-la avant de lancer le script."
        )

    with HttpSession() as session:
        session.headers.update({"User-Agent": "mandats-qgis/2.0"})
        communes = get_list_communes(session, snapshot_date=snapshot_date)
        projects = get_projects(session, token=token, page_size=page_size)
        return write_bdd(
            projects,
            communes,
            session,
            output_path=output_path,
            localities_path=localities_path,
        )


# Alias conservé pour les éventuels appels existants du script.
def writeBDD() -> int:  # noqa: N802 - compatibilité avec le nom historique
    return build_database()


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"CSV produit (défaut: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--localities",
        type=Path,
        default=DEFAULT_LOCALITIES_FILE,
        help=f"Fichier des lieux-dits (défaut: {DEFAULT_LOCALITIES_FILE})",
    )
    parser.add_argument(
        "--snapshot-date",
        help="Date de référence OFS au format JJ-MM-AAAA (défaut: aujourd'hui)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="Nombre de projets demandés par page Bexio (défaut: 500)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche les informations détaillées",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    try:
        build_database(
            output_path=args.output,
            localities_path=args.localities,
            snapshot_date=args.snapshot_date,
            page_size=args.page_size,
        )
    except (OSError, HttpError, RuntimeError, ValueError) as error:
        LOGGER.error("Génération interrompue: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
