#!/usr/bin/env python3
"""
Merge the CSVs made by pdf_to_csv.py into two files:

    merged_markets.csv      (daily per-market prices, tidy format)
    merged_ncr_summary.csv  (NCR-wide Low / High / Prevailing / Average)

It accepts any mix of what pdf_to_csv.py produces and tells them apart from
their headers:
    - all_markets.csv / all_ncr_summary.csv   (from --combine)
    - <name>.csv / <name>_ncr_summary.csv     (one pair per PDF)
Per-market files in the wide format (one row per market) are converted to the
tidy format automatically.

    python merge_csvs.py                  # merges the CSVs in the current folder and in
                                          # its "Per Market" and "NCR Summary" folders
    python merge_csvs.py a/*.csv b/*.csv  # or give specific files / wildcards

Rows are sorted by date and exact duplicate rows are removed.
"""
import argparse
import csv
import glob
import re
import sys
from datetime import datetime
from pathlib import Path

MARKETS_HEADER = ["date", "market", "commodity", "unit", "price_low", "price_high",
                  "source_file"]
SUMMARY_HEADER = ["date", "group", "commodity", "unit", "low", "high", "prevailing",
                  "average", "source_file"]
# Same category rules as pdf_to_csv.py, used to fill in a blank `group` in summary
# files made by an older version of the converter.
GROUP_RULES = [
    (r"\bCorn\b", "Corn"),
    (r"^(Beef|Pork|Frozen|Whole Chicken|Chicken)", "Livestock And Poultry Products"),
    (r"Milkfish|Bangus|Tilapia|Galunggong|Round Scad|Alumahan|Tamban|Sardines|Squid|Pusit",
     "Fish"),
    (r"Ampalaya|Bittergourd|Sitao|String Beans|Pechay Tagalog|Kalabasa|Squash|Talong|"
     r"Eggplant|Kamatis|Tomato", "Lowland Vegetables"),
    (r"Bell Pepper|Broccoli|Cabbage|Repolyo|Carrot|Baguio Beans|Habitchuelas|Potato|"
     r"Pechay Baguio|Sayote|Chayote|Cauliflower|Celery|Lettuce", "Highland Vegetables"),
    (r"Calamansi|Banana|Papaya|Mango|Avocado|Melon|Pomelo", "Fruits"),
    (r"Sugar|Palm Oil|Coconut Oil", "Other Commodities"),
    (r"Onion|Garlic|Ginger|Luya|Chil", "Spices"),
]


def category_for(name):
    for pattern, group in GROUP_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return group
    return ""


OUTPUTS = {"markets": "merged_markets.csv", "summary": "merged_ncr_summary.csv"}
IGNORE = set(OUTPUTS.values()) | {"conversion_errors.csv"}
WIDE_COL = re.compile(r"^(?P<name>.+) (?P<end>Low|High) \((?P<unit>[^)]+)\)$")
OLD_DATE = re.compile(r"(?:\w+,\s*)?(\d{1,2}\s+\w+\s+\d{4})")


def iso(value):
    """'Wednesday, 24 December 2025' -> '2025-12-24'; anything else unchanged."""
    m = OLD_DATE.fullmatch(value.strip())
    if m:
        try:
            return datetime.strptime(m.group(1), "%d %B %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def read_file(path):
    """Returns (kind, rows) with rows in the standard tidy layout, or raises ValueError."""
    src = Path(path).name
    with open(path, newline="", encoding="utf-8-sig") as f:       # strips the BOM
        r = csv.reader(f)
        header = next(r, None) or []
        body = [row for row in r if row]

    if header and header[:2] == ["date", "market"] and "price_low" not in header:
        # wide per-PDF file: date, market, <name> Low (<unit>), <name> High (<unit>), ...
        cols = []
        for i, h in enumerate(header[2:], 2):
            m = WIDE_COL.match(h)
            if not m:
                raise ValueError("unrecognised column in a markets file: " + h)
            cols.append((i, m))
        pairs = {}
        for i, m in cols:
            key = (m["name"], m["unit"])
            pairs.setdefault(key, {})[m["end"]] = i
        rows = []
        for row in body:
            for (name, unit), idx in pairs.items():
                rows.append([iso(row[0]), row[1], name, unit,
                             row[idx["Low"]], row[idx["High"]], src])
        return "markets", rows

    if "market" in header and "price_low" in header:
        std = MARKETS_HEADER[:-1]
        kind, want = "markets", std
    elif "prevailing" in header and "average" in header:
        std = SUMMARY_HEADER[:-1]
        kind, want = "summary", std
    else:
        raise ValueError("not a markets or summary file (columns: "
                         + ", ".join(header[:4]) + ("..." if len(header) > 4 else "") + ")")

    if header[:len(want)] != want:
        raise ValueError("unexpected columns; made by a different version of pdf_to_csv.py?")
    rows = []
    for row in body:
        row = row + [""] * (len(want) - len(row))
        source = row[len(want)] if len(row) > len(want) and row[len(want)] else src
        out = [iso(row[0])] + row[1:len(want)] + [source]
        if kind == "summary" and not out[1]:          # backfill a blank group
            out[1] = category_for(out[2])
        rows.append(out)
    return kind, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*",
                    help="CSV files (default: .csv files in the current folder and in "
                         "its 'Per Market' and 'NCR Summary' folders)")
    ap.add_argument("-d", "--outdir", default=".", help="where to write the merged files")
    args = ap.parse_args()

    paths = []
    default = ["*.csv", "Per Market/*.csv", "NCR Summary/*.csv"]
    for pat in args.files or default:
        paths.extend(sorted(glob.glob(pat)) or [pat])
    paths = [p for p in dict.fromkeys(paths) if Path(p).name not in IGNORE]
    if not paths:
        sys.exit("No CSV files found in this folder or its 'Per Market' / "
                 "'NCR Summary' folders.")

    data = {k: {"rows": [], "seen": set(), "dupes": 0, "files": 0} for k in OUTPUTS}
    skipped = []
    for p in paths:
        try:
            kind, rows = read_file(p)
        except (OSError, ValueError, IndexError) as e:
            skipped.append((p, str(e)))
            print(f"SKIPPED {p}: {e}", file=sys.stderr)
            continue
        d = data[kind]
        n = 0
        for row in rows:
            t = tuple(row)
            if t in d["seen"]:
                d["dupes"] += 1
                continue
            d["seen"].add(t)
            d["rows"].append(row)
            n += 1
        d["files"] += 1
        print(f"{p}: {n} {kind} rows", file=sys.stderr)

    headers = {"markets": MARKETS_HEADER, "summary": SUMMARY_HEADER}
    wrote = 0
    for kind, d in data.items():
        if not d["rows"]:
            print(f"No {kind} data found, so {OUTPUTS[kind]} was not created.",
                  file=sys.stderr)
            continue
        d["rows"].sort(key=lambda x: (x[0], x[-1]))
        out = Path(args.outdir) / OUTPUTS[kind]
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers[kind])
            w.writerows(d["rows"])
        print(f"Wrote {out}: {len(d['rows'])} rows from {d['files']} file(s) "
              f"({d['dupes']} exact duplicates removed)", file=sys.stderr)
        wrote += 1
    if not wrote:
        sys.exit("Nothing was merged.")


if __name__ == "__main__":
    main()
