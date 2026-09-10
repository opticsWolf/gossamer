"""XLSX tables_as option + office-oxide IR regression (real bytes).

office-oxide >= 0.1.10 changed two APIs under us:
- ``Document.from_bytes`` requires an explicit format argument;
- ``Document.to_ir_json`` returns a serialized JSON string whose tables
  live under ``sections[].elements`` (the old ``sheets`` shape is gone).

The from_bytes break killed every DOCX/XLSX/PPTX flat extraction; the IR
break killed structured XLSX payloads. Neither was caught by the suite
because no test pushed real office bytes through. These fixtures do.

``tables_as`` selects the rendering for spreadsheet tables: ``json``
(default; array of ``{sheet, headers, rows}``), ``markdown`` (pipe
tables), or ``csv`` (comma-separated, one ``## <sheet>`` block per
sheet).
"""

import csv
import json
import zipfile

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.cli import build_parser
from gossamer.config import TOOL_REGISTRY
from gossamer.structured_parser import office_ir_tables

CT = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
    'package/2006/content-types"><Default Extension="rels" ContentType='
    '"application/vnd.openxmlformats-package.relationships+xml"/><Default '
    'Extension="xml" ContentType="application/xml"/><Override PartName='
    '"/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName='
    '"/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
)
RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
    '.org/package/2006/relationships"><Relationship Id="rId1" Type='
    '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    'officeDocument" Target="xl/workbook.xml"/></Relationships>'
)
WB_RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
    '.org/package/2006/relationships"><Relationship Id="rId1" Type='
    '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    'worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
)
WORKBOOK = (
    '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
    'spreadsheetml/2006/main"><sheets><sheet name="Data" sheetId="1" r:id='
    '"rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships"/></sheets></workbook>'
)
SHEET = (
    '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
    'spreadsheetml/2006/main"><sheetData>'
    '<row><c t="inlineStr"><is><t>Region</t></is></c>'
    '<c t="inlineStr"><is><t>GDP</t></is></c></row>'
    '<row><c t="inlineStr"><is><t>Eurozone</t></is></c><c><v>15.3</v></c></row>'
    '<row><c t="inlineStr"><is><t>Euro, zone</t></is></c>'
    '<c t="inlineStr"><is><t>&quot;quoted&quot;</t></is></c></row>'
    '</sheetData></worksheet>'
)


def _write_xlsx(path) -> bytes:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("xl/_rels/workbook.xml.rels", WB_RELS)
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/worksheets/sheet1.xml", SHEET)
    with open(path, "rb") as f:
        return f.read()


def _toolbox(tmp_path) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0
    )


class TestXlsxRenderings:
    def test_default_is_json_tables(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        res = json.loads(_toolbox(tmp_path).extract_document(str(src)))
        assert "error" not in res
        tables = json.loads(res["content"])
        assert tables == [
            {
                "sheet": "Data",
                "headers": ["Region", "GDP"],
                "rows": [["Eurozone", "15.3"], ["Euro, zone", '"quoted"']],
            }
        ]

    def test_markdown_option_renders_pipe_tables(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        res = json.loads(
            _toolbox(tmp_path).extract_document(str(src), tables_as="markdown")
        )
        assert "error" not in res
        assert "| Region | GDP |" in res["content"]
        assert "| Eurozone | 15.3 |" in res["content"]

    def test_csv_option_renders_csv_blocks(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        res = json.loads(
            _toolbox(tmp_path).extract_document(str(src), tables_as="csv")
        )
        content = res["content"]
        assert "## Data" in content
        assert "Region,GDP" in content
        assert "Eurozone,15.3" in content
        assert "| Region" not in content

    def test_csv_escaping(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        res = json.loads(
            _toolbox(tmp_path).extract_document(str(src), tables_as="csv")
        )
        rows = list(csv.reader(res["content"].splitlines()))
        assert ["Euro, zone", '"quoted"'] in rows

    def test_invalid_tables_as_is_in_band_error(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        res = json.loads(
            _toolbox(tmp_path).extract_document(str(src), tables_as="tsv")
        )
        assert "tables_as must be 'markdown', 'csv' or 'json'" in res["error"]

    def test_renderings_do_not_collide_in_cache(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        tb = _toolbox(tmp_path)
        csv_res = json.loads(tb.extract_document(str(src), tables_as="csv"))
        md_res = json.loads(
            tb.extract_document(str(src), tables_as="markdown")
        )
        json_res = json.loads(tb.extract_document(str(src)))
        assert "Region,GDP" in csv_res["content"]
        assert "| Region | GDP |" in md_res["content"]
        assert json.loads(json_res["content"])[0]["headers"] == ["Region", "GDP"]


class TestStructuredXlsxRegression:
    def test_structured_payload_has_tables_and_metadata(self, tmp_path):
        src = tmp_path / "data.xlsx"
        src.write_bytes(_write_xlsx(src))
        res = json.loads(
            _toolbox(tmp_path).extract_document(str(src), structured=True)
        )
        assert "error" not in res, res.get("error")
        assert res["metadata"]["page_count"] == 1
        tables = res["tables"]
        assert tables, "IR tables must survive the 0.1.10 IR shape"
        assert tables[0]["headers"] == ["Region", "GDP"]
        assert ["Eurozone", "15.3"] in tables[0]["rows"]

    def test_office_ir_tables_helper_shape(self):
        ir = {
            "sections": [
                {
                    "title": "Data",
                    "elements": [
                        {
                            "type": "table",
                            "rows": [
                                {
                                    "cells": [
                                        {
                                            "content": [
                                                {
                                                    "type": "paragraph",
                                                    "content": [
                                                        {"type": "text", "text": "A"},
                                                        {"type": "text", "text": "B"},
                                                    ],
                                                }
                                            ]
                                        },
                                        {"content": []},
                                    ]
                                },
                                {"cells": "junk-not-a-list"},
                            ],
                        }
                    ],
                }
            ]
        }
        assert office_ir_tables(ir) == [("Data", [["A B", ""]])]


class TestRegistryAndCli:
    def test_registry_has_tables_as_param(self):
        spec = next(t for t in TOOL_REGISTRY if t.name == "extract_document")
        param = next(p for p in spec.params if p.name == "tables_as")
        assert param.default == "json"
        assert param.enum == ["markdown", "csv", "json"]

    def test_cli_flag_parses(self):
        args = build_parser().parse_args(
            ["extract", "data.xlsx", "--tables-as", "csv"]
        )
        assert args.tables_as == "csv"
        assert build_parser().parse_args(["extract", "x.xlsx"]).tables_as == "json"
