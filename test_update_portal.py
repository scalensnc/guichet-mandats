from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import update_portal


class PortalExportTests(unittest.TestCase):
    @staticmethod
    def _write_mutations_workbook(path: Path) -> None:
        headings = (
            "NUMERO DE DOSSIER",
            "NUMERO CONVERCE",
            "COMMUNE",
            "PARCELLE",
            "DETAILS",
            "MENSURATION",
            "DIVISION OU CADASTRATION",
            "Date de réception",
            "Jours restants",
            " REMARQUE",
        )

        def inline_cell(reference: str, value: str, style: int = 0) -> str:
            style_attribute = f' s="{style}"' if style else ""
            return f'<c r="{reference}"{style_attribute} t="inlineStr"><is><t>{value}</t></is></c>'

        header_cells = "".join(
            inline_cell(f"{chr(ord('A') + index)}2", heading)
            for index, heading in enumerate(headings)
        )
        cadastration_cells = "".join(
            (
                '<c r="A3"><v>123</v></c>',
                inline_cell("B3", "001-26-123456"),
                inline_cell("C3", "Orbe"),
                '<c r="D3"><v>45</v></c>',
                inline_cell("E3", "Nouveau bâtiment"),
                inline_cell("F3", "Numérique"),
                inline_cell("G3", "Cadastration"),
                '<c r="H3"><v>46000</v></c>',
                '<c r="I3"><v>30</v></c>',
                inline_cell("J3", "À planifier", style=1),
            )
        )
        division_cells = "".join(
            (
                '<c r="A4"><v>124</v></c>',
                inline_cell("C4", "Orbe"),
                inline_cell("G4", "Division"),
            )
        )
        worksheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData>'
            f'<row r="1">{inline_cell("A1", "CADASTRATIONS A FAIRE")}</row>'
            f'<row r="2">{header_cells}</row>'
            f'<row r="3">{cadastration_cells}</row>'
            f'<row r="4">{division_cells}</row>'
            '</sheetData></worksheet>'
        )
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="A faire" sheetId="1" r:id="rId1"/></sheets></workbook>'
        )
        relationships = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        )
        styles = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fills count="3">'
            '<fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFFFF00"/>'
            '<bgColor indexed="64"/></patternFill></fill>'
            '</fills>'
            '<cellXfs count="2"><xf fillId="0"/><xf fillId="2" applyFill="1"/></cellXfs>'
            '</styleSheet>'
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", relationships)
            archive.writestr("xl/worksheets/sheet1.xml", worksheet)
            archive.writestr("xl/styles.xml", styles)

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

    def test_cadastration_export_filters_excel_and_uses_bexio_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "mutations.xlsx"
            source = root / "projects.csv"
            target = root / "cadastrations.json"
            self._write_mutations_workbook(workbook)
            with source.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter=";")
                writer.writerow(("num", "name", "Commune", "Commune_recherche", "parcelle", "lon", "lat", "date"))
                writer.writerow(("00123", "Orbe - BF 45 - Rue du Test 1", "Orbe", "Orbe", "45", "6.53", "46.72", "2026-01-02 00:00:00"))

            self.assertEqual(update_portal.export_cadastrations(workbook, source, target), 1)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["matched_count"], 1)
            self.assertEqual(payload["mapped_count"], 1)
            cadastration = payload["cadastrations"][0]
            self.assertEqual(cadastration["id"], "00123")
            self.assertEqual(cadastration["name"], "Orbe - BF 45 - Rue du Test 1")
            self.assertEqual(cadastration["lon"], 6.53)
            self.assertEqual(cadastration["received_date"], "2025-12-09")
            self.assertEqual(cadastration["remark"], "À planifier")
            self.assertEqual(cadastration["remark_color"], "FFFF00")
            self.assertEqual(cadastration["status"], "terrain_a_faire")
            self.assertNotIn("days_remaining", cadastration)

    def test_cadastration_status_maps_excel_colors(self) -> None:
        self.assertEqual(
            update_portal._cadastration_status("FFFF00"), "terrain_a_faire"
        )
        self.assertEqual(
            update_portal._cadastration_status("00B0F0"), "terrain_fait"
        )
        self.assertEqual(
            update_portal._cadastration_status("FFC000"),
            "cadastration_en_attente",
        )
        self.assertEqual(
            update_portal._cadastration_status(None),
            "cadastration_en_attente",
        )


if __name__ == "__main__":
    unittest.main()
