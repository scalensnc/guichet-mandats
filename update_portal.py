"""Met à jour la base des mandats et les données du guichet web.

Par défaut, le script récupère les projets Bexio, reconstruit ``projects.csv``
puis exporte ``portal/data/projects.json``. L'option ``--from-csv`` permet de
reconstruire uniquement le JSON, sans accès réseau ni jeton Bexio.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = BASE_DIR / "projects.csv"
DEFAULT_JSON = BASE_DIR / "portal" / "data" / "projects.json"
MANDATS_SCRIPT = BASE_DIR / "BDD-Mandats.py"


def _nullable(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip()
    return None if not cleaned or cleaned.upper() == "NULL" else cleaned


def _coordinate(value: Optional[str]) -> Optional[float]:
    cleaned = _nullable(value)
    if cleaned is None:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(value: Optional[str]) -> Optional[str]:
    cleaned = _nullable(value)
    if cleaned is None:
        return None
    # Bexio fournit actuellement « YYYY-MM-DD 00:00:00 ». Le portail ne
    # conserve que la date civile, ce qui évite tout décalage de fuseau.
    return cleaned[:10]


def read_projects(csv_path: Path) -> list[dict[str, object]]:
    """Lit le CSV métier et retourne des objets prêts à publier."""

    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=";")
        required = {"num", "name", "Commune", "parcelle", "lon", "lat", "date"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = sorted(required.difference(reader.fieldnames or []))
            raise ValueError(f"Colonnes absentes de {csv_path}: {', '.join(missing)}")

        projects = []
        for row in reader:
            number = _nullable(row.get("num"))
            parcel = _nullable(row.get("parcelle"))
            geocoded_commune = _nullable(row.get("Commune_recherche"))
            # Sans parcelle et sans résultat géographique, la colonne Commune
            # peut contenir un intitulé libre (p. ex. « Vacances »). Ne pas le
            # présenter comme une commune dans les filtres du guichet.
            commune = geocoded_commune or (_nullable(row.get("Commune")) if parcel else None)
            projects.append(
                {
                    "id": number,
                    "name": _nullable(row.get("name")),
                    "commune": commune,
                    "parcel": parcel,
                    "lon": _coordinate(row.get("lon")),
                    "lat": _coordinate(row.get("lat")),
                    "date": _date(row.get("date")),
                }
            )

    projects.sort(key=lambda item: (item.get("date") or "", item.get("id") or ""), reverse=True)
    return projects


def export_portal(csv_path: Path = DEFAULT_CSV, json_path: Path = DEFAULT_JSON) -> int:
    """Exporte les données web atomiquement et retourne le nombre de dossiers."""

    projects = read_projects(csv_path)
    dated = [str(project["date"]) for project in projects if project.get("date")]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_updated_at": max(dated) if dated else None,
        "count": len(projects),
        "projects": projects,
    }

    json_path = Path(json_path).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)

    # Ne modifie pas le fichier pour une simple nouvelle heure d'execution.
    # Ainsi, le script de publication ne cree un commit que si les dossiers ou
    # leur date de reference ont vraiment change.
    if json_path.exists():
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
            if (
                existing.get("source_updated_at") == payload["source_updated_at"]
                and existing.get("count") == payload["count"]
                and existing.get("projects") == payload["projects"]
            ):
                return len(projects)
        except (json.JSONDecodeError, OSError, AttributeError):
            pass

    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{json_path.name}.",
            suffix=".tmp",
            dir=json_path.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary.write("\n")
        os.replace(temporary_name, json_path)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return len(projects)


def _load_generator():
    spec = importlib.util.spec_from_file_location("bdd_mandats", MANDATS_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Impossible de charger {MANDATS_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-csv", action="store_true", help="N'appelle pas Bexio; exporte le CSV existant")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Chemin du CSV intermédiaire")
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON, help="Chemin du JSON du portail")
    parser.add_argument("--snapshot-date", help="Date OFS au format JJ-MM-AAAA")
    parser.add_argument("--page-size", type=int, default=500, help="Taille des pages Bexio")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        if not args.from_csv:
            generator = _load_generator()
            generator.build_database(
                output_path=args.csv,
                snapshot_date=args.snapshot_date,
                page_size=args.page_size,
            )
        count = export_portal(args.csv, args.output)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERREUR: {error}")
        return 1
    print(f"Portail mis à jour: {count} dossiers dans {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
