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
import posixpath
import re
import sys
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence
from xml.etree import ElementTree


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = BASE_DIR / "projects.csv"
DEFAULT_JSON = BASE_DIR / "portal" / "data" / "projects.json"
DEFAULT_CADASTRATIONS_JSON = BASE_DIR / "portal" / "data" / "cadastrations.json"
MANDATS_SCRIPT = BASE_DIR / "BDD-Mandats.py"
MUTATIONS_WORKBOOK_CANDIDATES = (
    Path(r"P:\07_Donnees\02-Données mutations\SCALEN-Listing des mutations.xlsx"),
)

XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


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


def _normalise_heading(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        raise ValueError(f"Référence de cellule Excel invalide: {reference}")
    index = 0
    for letter in letters.group(0):
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.findall(f".//{{{XLSX_NS}}}t"))
        for item in root.findall(f"{{{XLSX_NS}}}si")
    ]


def _xlsx_sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationship_id = None
    for sheet in workbook.findall(f".//{{{XLSX_NS}}}sheet"):
        if sheet.get("name") == sheet_name:
            relationship_id = sheet.get(f"{{{DOCUMENT_REL_NS}}}id")
            break
    if not relationship_id:
        raise ValueError(f"Feuille Excel absente: {sheet_name}")

    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        if relationship.get("Id") == relationship_id:
            target = relationship.get("Target") or ""
            return posixpath.normpath(posixpath.join("xl", target))
    raise ValueError(f"Relation Excel introuvable pour la feuille {sheet_name}")


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> object:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.findall(f".//{{{XLSX_NS}}}t")
        )
    value_node = cell.find(f"{{{XLSX_NS}}}v")
    if value_node is None or value_node.text is None:
        return None
    value = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return value
    if cell_type in {"str", "e"}:
        return value
    if cell_type == "b":
        return value == "1"
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _xlsx_rows(workbook_path: Path, sheet_name: str) -> list[list[object]]:
    try:
        with zipfile.ZipFile(workbook_path) as archive:
            shared_strings = _xlsx_shared_strings(archive)
            sheet = ElementTree.fromstring(
                archive.read(_xlsx_sheet_path(archive, sheet_name))
            )
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise ValueError(f"Classeur Excel illisible: {workbook_path}") from error

    rows: list[list[object]] = []
    for row_node in sheet.findall(f".//{{{XLSX_NS}}}sheetData/{{{XLSX_NS}}}row"):
        row_values: dict[int, object] = {}
        for cell in row_node.findall(f"{{{XLSX_NS}}}c"):
            reference = cell.get("r") or ""
            row_values[_column_index(reference)] = _xlsx_cell_value(
                cell, shared_strings
            )
        if row_values:
            width = max(row_values) + 1
            rows.append([row_values.get(index) for index in range(width)])
        else:
            rows.append([])
    return rows


def _find_column(headings: list[object], expected: str) -> int:
    expected_key = _normalise_heading(expected)
    for index, heading in enumerate(headings):
        if _normalise_heading(heading).startswith(expected_key):
            return index
    raise ValueError(f"Colonne absente du classeur: {expected}")


def _row_value(row: list[object], index: int) -> object:
    return row[index] if index < len(row) else None


def _text(value: object) -> Optional[str]:
    cleaned = str(value or "").strip()
    return cleaned or None


def _project_number(value: object) -> Optional[str]:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return cleaned.zfill(5) if cleaned.isdigit() else cleaned


def _excel_date(value: object) -> Optional[str]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    except (OverflowError, ValueError):
        return None


def read_cadastrations(workbook_path: Path) -> list[dict[str, object]]:
    """Lit les lignes « Cadastration » de la feuille métier ``A faire``."""

    workbook_path = Path(workbook_path)
    if not workbook_path.is_file():
        raise ValueError(f"Classeur des mutations introuvable: {workbook_path}")
    rows = _xlsx_rows(workbook_path, "A faire")
    header_position = next(
        (
            index
            for index, row in enumerate(rows)
            if "numero de dossier" in {_normalise_heading(value) for value in row}
        ),
        None,
    )
    if header_position is None:
        raise ValueError("Ligne d'en-tête introuvable dans la feuille « A faire »")

    headings = rows[header_position]
    columns = {
        "id": _find_column(headings, "numero de dossier"),
        "converce": _find_column(headings, "numero converce"),
        "commune": _find_column(headings, "commune"),
        "parcel": _find_column(headings, "parcelle"),
        "details": _find_column(headings, "details"),
        "measurement": _find_column(headings, "mensuration"),
        "type": _find_column(headings, "division ou cadastration"),
        "received": _find_column(headings, "date de reception"),
        "remaining": _find_column(headings, "jours restants"),
        "remark": _find_column(headings, "remarque"),
    }

    cadastration_rows = []
    for source_row, row in enumerate(rows[header_position + 1 :], header_position + 2):
        mutation_type = _text(_row_value(row, columns["type"]))
        if _normalise_heading(mutation_type) != "cadastration":
            continue
        project_id = _project_number(_row_value(row, columns["id"]))
        if not project_id:
            continue
        remaining = _row_value(row, columns["remaining"])
        cadastration_rows.append(
            {
                "id": project_id,
                "converce_number": _text(_row_value(row, columns["converce"])),
                "commune": _text(_row_value(row, columns["commune"])),
                "parcel": _text(_row_value(row, columns["parcel"])),
                "details": _text(_row_value(row, columns["details"])),
                "measurement": _text(_row_value(row, columns["measurement"])),
                "mutation_type": mutation_type,
                "received_date": _excel_date(_row_value(row, columns["received"])),
                "days_remaining": (
                    int(remaining)
                    if isinstance(remaining, (int, float))
                    and not isinstance(remaining, bool)
                    else None
                ),
                "remark": _text(_row_value(row, columns["remark"])),
                "source_row": source_row,
            }
        )
    return cadastration_rows


def export_cadastrations(
    workbook_path: Path,
    csv_path: Path = DEFAULT_CSV,
    json_path: Path = DEFAULT_CADASTRATIONS_JSON,
) -> int:
    """Croise les cadastrations Excel avec les coordonnées issues de Bexio."""

    projects_by_id = {
        str(project["id"]): project
        for project in read_projects(csv_path)
        if project.get("id")
    }
    cadastration_rows = read_cadastrations(workbook_path)
    cadastrations = []
    matched_count = 0
    mapped_count = 0
    for row in cadastration_rows:
        project = projects_by_id.get(str(row["id"]))
        if project:
            matched_count += 1
        longitude = project.get("lon") if project else None
        latitude = project.get("lat") if project else None
        if longitude is not None and latitude is not None:
            mapped_count += 1
        cadastrations.append(
            {
                "id": row["id"],
                "name": (project or {}).get("name") or row["details"],
                "commune": (project or {}).get("commune") or row["commune"],
                "parcel": row["parcel"] or (project or {}).get("parcel"),
                "lon": longitude,
                "lat": latitude,
                "date": (project or {}).get("date"),
                "details": row["details"],
                "measurement": row["measurement"],
                "mutation_type": row["mutation_type"],
                "received_date": row["received_date"],
                "remark": row["remark"],
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_updated_at": datetime.fromtimestamp(
            Path(workbook_path).stat().st_mtime, tz=timezone.utc
        ).date().isoformat(),
        "count": len(cadastrations),
        "matched_count": matched_count,
        "mapped_count": mapped_count,
        "cadastrations": cadastrations,
    }
    json_path = Path(json_path).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    if json_path.exists():
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
            comparable_keys = (
                "source_updated_at",
                "count",
                "matched_count",
                "mapped_count",
                "cadastrations",
            )
            if all(existing.get(key) == payload[key] for key in comparable_keys):
                return len(cadastrations)
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
    return len(cadastrations)


def find_mutations_workbook(explicit_path: Optional[Path] = None) -> Optional[Path]:
    if explicit_path is not None:
        path = Path(explicit_path)
        if not path.is_file():
            raise ValueError(f"Classeur des mutations introuvable: {path}")
        return path
    environment_path = os.environ.get("MUTATIONS_XLSX_PATH", "").strip()
    candidates = ([Path(environment_path)] if environment_path else []) + list(
        MUTATIONS_WORKBOOK_CANDIDATES
    )
    return next((path for path in candidates if path.is_file()), None)


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
    parser.add_argument("--mutations-xlsx", type=Path, help="Classeur SCALEN des mutations")
    parser.add_argument(
        "--cadastrations-output",
        type=Path,
        default=DEFAULT_CADASTRATIONS_JSON,
        help="Chemin du JSON de la couche des cadastrations",
    )
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
        mutations_workbook = find_mutations_workbook(args.mutations_xlsx)
        cadastration_count = None
        if mutations_workbook:
            cadastration_count = export_cadastrations(
                mutations_workbook,
                csv_path=args.csv,
                json_path=args.cadastrations_output,
            )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERREUR: {error}")
        return 1
    message = f"Portail mis à jour: {count} dossiers dans {Path(args.output).resolve()}"
    if cadastration_count is not None:
        message += f"; {cadastration_count} cadastrations dans {Path(args.cadastrations_output).resolve()}"
    else:
        message += "; couche des cadastrations conservée (classeur source introuvable)"
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
