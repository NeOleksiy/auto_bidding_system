"""End-to-end audit of artificial impressions, without touching real reports."""

import csv
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.run_eda import FIELDS, connect, quality_sql

ROOT = Path(__file__).resolve().parents[1]


class AuditTest(unittest.TestCase):
    def test_quality_flags_invalid_records(self):
        db = connect()
        db.execute(
            "CREATE TABLE d (conversion INT, click INT, attribution INT, cost DOUBLE, cpo DOUBLE, conversion_id INT, conversion_timestamp INT, timestamp INT)"
        )
        db.execute("INSERT INTO d VALUES (1,2,0,-1,0.1,-1,-2,0), (0,0,1,0,0.1,100,10,0)")
        result = db.sql(quality_sql()).df().iloc[0]
        for field in [
            "invalid_click",
            "invalid_cost",
            "zero_cost",
            "invalid_positive",
            "invalid_negative",
            "attribution_without_conversion",
        ]:
            self.assertEqual(int(result[field]), 1, field)
        db.close()

    def test_duplicates_sentinels_and_delays(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            rows = []
            for ts, user, conv, event, event_time in [
                (0, 1, 0, -1, -1),
                (1, 1, 1, 100, 86401),
                (2, 1, 1, 100, 86401),
                (3, 4, 1, 100, 86401),
                (200000, 2, 0, -1, -1),
                (290000, 3, 1, 101, 300000),
            ]:
                row = dict.fromkeys(FIELDS, 1)
                row.update(
                    timestamp=ts,
                    uid=user,
                    campaign=1,
                    conversion=conv,
                    conversion_id=event,
                    conversion_timestamp=event_time,
                    attribution=conv,
                    cost=0.01,
                    cpo=0.1,
                    time_since_last_click=-1,
                )
                rows.append(row)
            rows.append(rows[1].copy())
            source = folder / "sample.tsv.gz"
            with gzip.open(source, "wt") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_eda.py"),
                    "--input",
                    str(source),
                    "--output",
                    str(folder / "report"),
                    "--database",
                    str(folder / "audit.duckdb"),
                ],
                check=True,
                cwd=ROOT,
                env={**os.environ, "MPLCONFIGDIR": str(folder / "mpl")},
                stdout=subprocess.DEVNULL,
            )

            def read(name):
                with (folder / f"report/tables/{name}.csv").open() as stream:
                    return next(csv.DictReader(stream))

            self.assertEqual(int(float(read("summary")["rows"])), 7)
            self.assertEqual(int(float(read("summary")["positive_rows"])), 5)
            self.assertEqual(int(float(read("summary")["conversions"])), 3)
            self.assertEqual(int(float(read("duplicates")["duplicate_rows"])), 1)
            self.assertEqual(int(float(read("quality")["invalid_negative"])), 0)
            self.assertEqual(int(float(read("quality")["invalid_positive"])), 0)
            self.assertEqual(int(float(read("feedback")["complete_windows_before_stream_end"])), 0)
            self.assertEqual(int(float(read("feedback")["positive_rows_after_end"])), 1)
            manifest = json.loads((folder / "report/manifest.json").read_text())
            self.assertNotIn("source_revision", manifest)


if __name__ == "__main__":
    unittest.main()
