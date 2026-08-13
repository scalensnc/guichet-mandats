from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import update_portal


class PortalExportTests(unittest.TestCase):
    def test_export_normalizes_nulls_dates_and_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "projects.csv"
            target = root / "data" / "projects.json"
            with source.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter=";")
                writer.writerow(("num", "name", "Commune", "Commune_recherche", "parcelle", "lon", "lat", "date"))
                writer.writerow(("01734", "Bofflens - BF 73", "Bofflens", "Bofflens", "73", "6.49", "46.70", "2026-08-11 00:00:00"))
                writer.writerow(("01733", "Sans coordonnées", "Orbe", "NULL", "NULL", "NULL", "invalide", "NULL"))

            self.assertEqual(update_portal.export_portal(source, target), 2)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["source_updated_at"], "2026-08-11")
            self.assertEqual(payload["projects"][0]["id"], "01734")
            self.assertEqual(payload["projects"][0]["lat"], 46.70)
            self.assertIsNone(payload["projects"][1]["parcel"])
            self.assertIsNone(payload["projects"][1]["lat"])
            self.assertIsNone(payload["projects"][1]["commune"])

    def test_identical_export_does_not_rewrite_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "projects.csv"
            target = root / "projects.json"
            source.write_text(
                "num;name;Commune;Commune_recherche;parcelle;lon;lat;date\n"
                "1;Orbe - BF 2;Orbe;Orbe;2;6.5;46.7;2026-01-02 00:00:00\n",
                encoding="utf-8",
            )
            update_portal.export_portal(source, target)
            first_content = target.read_bytes()
            update_portal.export_portal(source, target)
            self.assertEqual(target.read_bytes(), first_content)


if __name__ == "__main__":
    unittest.main()
