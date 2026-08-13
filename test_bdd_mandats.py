"""Tests hors ligne du générateur de mandats."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("BDD-Mandats.py")
SPEC = importlib.util.spec_from_file_location("bdd_mandats", SCRIPT_PATH)
assert SPEC and SPEC.loader
mandats = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mandats
SPEC.loader.exec_module(mandats)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self) -> object:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"Aucune réponse prévue pour {url}")
        return FakeResponse(self.responses.pop(0))


class ApiTests(unittest.TestCase):
    def test_bexio_projects_are_paginated(self) -> None:
        session = FakeSession(
            [
                [{"id": 1}, {"id": 2}],
                [{"id": 3}],
            ]
        )
        projects = mandats.get_projects(session, "secret", page_size=2)
        self.assertEqual([project["id"] for project in projects], [1, 2, 3])
        self.assertEqual(session.calls[0][1]["params"]["offset"], 0)
        self.assertEqual(session.calls[1][1]["params"]["offset"], 2)

    def test_ofs_csv_is_parsed_with_csv_reader(self) -> None:
        response = (
            "a,b,c,d,e,f,Commune\n"
            '1,2,3,4,5,6,"Commune, avec virgule"\n'
        )
        communes = mandats.get_list_communes(
            FakeSession([response]), snapshot_date="01-01-2026"
        )
        self.assertEqual(communes, ["Commune, avec virgule"])


class ParsingTests(unittest.TestCase):
    COMMUNES = [
        "Cugy (FR)",
        "La Grande Béroche",
        "Le Mont-sur-Lausanne",
        "Yverdon-les-Bains",
    ]

    def test_get_bf_accepts_common_variants(self) -> None:
        self.assertEqual(mandats.get_bf("Payerne - BF 1098 - Rue du Nord"), "1098")
        self.assertEqual(mandats.get_bf("Lausanne - BF1092"), "1092")
        self.assertEqual(mandats.get_bf("Orbe - BF 1309-1310"), "1309")
        self.assertEqual(mandats.get_bf("Suscévaz_Plan_BF 67"), "67")
        self.assertIsNone(mandats.get_bf("Vacances"))

    def test_get_commune_keeps_multiword_name(self) -> None:
        result = mandats.get_commune(
            "La Grande Béroche - BF 3122 - Rue du Débarcadère 21",
            self.COMMUNES,
            lieux_dits={},
        )
        self.assertEqual(result, "La Grande Béroche")

    def test_priority_correction(self) -> None:
        self.assertEqual(
            mandats.correct_name("Yverdon", self.COMMUNES, lieux_dits={}),
            "Yverdon-les-Bains",
        )

    def test_historical_name_correction(self) -> None:
        self.assertEqual(
            mandats.correct_name("Arzier", self.COMMUNES, lieux_dits={}),
            "Arzier-Le Muids",
        )
        self.assertEqual(
            mandats.correct_name("Bevaix", self.COMMUNES, lieux_dits={}),
            "La Grande Béroche",
        )

    def test_description_before_bf_does_not_hide_commune(self) -> None:
        result = mandats.get_commune(
            "Yverdon - Kiener - Construction - BF 411",
            self.COMMUNES,
            lieux_dits={},
        )
        self.assertEqual(result, "Yverdon-les-Bains")

    def test_locality_correction(self) -> None:
        localities = {mandats._normalise("Vesin"): "Cugy (FR)"}
        self.assertEqual(
            mandats.correct_name("Vesin", self.COMMUNES, lieux_dits=localities),
            "Cugy (FR)",
        )


class ParcelSearchTests(unittest.TestCase):
    def test_exact_multiword_commune_is_returned(self) -> None:
        session = FakeSession(
            [
                {
                    "results": [
                        {
                            "attrs": {
                                "label": "<b>La Grande Béroche</b> 3122",
                                "lon": 6.82,
                                "lat": 46.90,
                            }
                        }
                    ]
                }
            ]
        )
        result = mandats.search_parcel(session, "La Grande Béroche", "3122")
        self.assertEqual(result.commune, "La Grande Béroche")
        self.assertEqual((result.lon, result.lat), (6.82, 46.90))

    def test_no_result_has_consistent_empty_structure(self) -> None:
        result = mandats.search_parcel(FakeSession([{"results": []}]), "Orbe", "1")
        self.assertEqual(result, mandats.ParcelResult())

    def test_unrelated_commune_is_rejected(self) -> None:
        session = FakeSession(
            [
                {
                    "results": [
                        {
                            "attrs": {
                                "label": "<b>Genève</b> 42",
                                "lon": 6.14,
                                "lat": 46.20,
                            }
                        }
                    ]
                }
            ]
        )
        self.assertEqual(
            mandats.search_parcel(session, "Lausanne", "42"),
            mandats.ParcelResult(),
        )


class CsvTests(unittest.TestCase):
    def test_failed_geocoding_preserves_commune_and_parcel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            localities = root / "localities.csv"
            localities.write_text(
                "Ortschaftsname;Gemeindename\nOrbe;Orbe\n",
                encoding="utf-8",
            )
            output = root / "projects.csv"
            projects = [
                {
                    "nr": "001",
                    "name": "Orbe - BF 342 - Rue Centrale 1",
                    "start_date": "2026-01-01 00:00:00",
                }
            ]

            count = mandats.write_bdd(
                projects,
                ["Orbe"],
                FakeSession([{"results": []}]),
                output_path=output,
                localities_path=localities,
            )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file, delimiter=";"))

            self.assertEqual(count, 1)
            self.assertEqual(rows[0]["Commune"], "Orbe")
            self.assertEqual(rows[0]["parcelle"], "342")
            self.assertEqual(rows[0]["lon"], "NULL")
            self.assertEqual(tuple(rows[0]), mandats.CSV_COLUMNS)


if __name__ == "__main__":
    unittest.main()
