#!/usr/bin/env python3
"""
Convert DA "Bantay Presyo" price monitoring PDFs into CSV files.

Single / few files (creates two CSVs next to each PDF):
    <name>.csv               per-market prices (Low/High per commodity)
    <name>_ncr_summary.csv   NCR-wide Low / High / Prevailing / Average

Drag PDFs onto this file, or from a terminal:
    python pdf_to_csv.py a.pdf b.pdf
    python pdf_to_csv.py --all                  # every PDF in the current folder
    python pdf_to_csv.py input.pdf -o prices.csv   # single file only
    python pdf_to_csv.py input.pdf --long       # one row per market+commodity

Bulk mode (years of data): parallel, recursive, one combined file
    python pdf_to_csv.py --all -r --combine
creates all_markets.csv and all_ncr_summary.csv (tidy format, ISO dates, sorted
by date) instead of thousands of small CSVs. Add --also-individual to keep the
per-file CSVs too. --jobs N sets the number of worker processes.

Requires:  pip install pdfplumber
"""

import argparse
import csv
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Run:  pip install pdfplumber")

NA = "NOT AVAILABLE"
# A price cell: "45.00", "45.00 - 47.00", "45.00 -47.00", or NOT AVAILABLE
TOKEN = rf"(?:{NA}|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)"
ROW_RE = re.compile(rf"^(?P<market>.+?)\s+(?P<values>{TOKEN}(?:\s+{TOKEN})*)\s*$")
TOKEN_RE = re.compile(TOKEN)
DATE_RE = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
                     r"(\d{1,2}\s+\w+\s+\d{4})")
SUM_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<nums>\d+(?:\.\d+)?\**(?:\s*-\s*\d+(?:\.\d+)?\**)?"
    r"(?:\s+\d+(?:\.\d+)?){1,2})(?:\s+[A-Za-z()]{1,8})?$")
MARKET_PAGE_MARK = "COMMODITY (PHP/KG)"


# ----------------------------------------------------------------- parsing
def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def iso_date(raw):
    try:
        return datetime.strptime(raw, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return raw


def parse_price(cell):
    """'45.00 - 47.00' -> (45.0, 47.0); '45.00' -> (45.0, 45.0); N/A -> (None, None)."""
    cell = clean(cell)
    if not cell or cell.upper() == NA:
        return None, None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cell)]
    return (nums[0], nums[0]) if len(nums) == 1 else (min(nums), max(nums))


def get_commodities(page):
    """Commodity names from the table header (the row starting with MARKET)."""
    for table in page.extract_tables():
        for row in table:
            if row and clean(row[0]).upper() == "MARKET":
                # Merged cells can leave stray empty columns; drop them.
                return [clean(c) for c in row[1:] if clean(c)]
    return None


def parse_market_page(page, text, warn):
    commodities = get_commodities(page)
    if not commodities:
        return None, []
    rows = []
    for line in text.splitlines():
        line = clean(line)
        if DATE_RE.search(line):
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
        if len(cells) != len(commodities):
            warn(f"skipped '{m.group('market')}': expected {len(commodities)} "
                 f"values, found {len(cells)}")
            continue
        rows.append((clean(m.group("market")), cells))
    return commodities, rows


# The category labels in the PDF are printed sideways beside the tables and do not
# extract reliably, so each commodity is assigned its category by name instead.
# (Rice is handled separately from the real headings in the PDF.) Add new
# commodities here; anything not matched is left blank.
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


def clean_name(name):
    name = re.sub(r"\s*[\u1d43-\u1d48]$", "", name)        # superscript footnote letters
    name = re.sub(r"(?<=\))\s*[a-d]$", "", name)           # "(Galunggong)b" / ")c"
    # drop side-label fragments that bleed in ("CORN ", "VESTOCK AND ", "(per kg) ")
    name = re.sub(r"^(?:\(per kg\)|[A-Z]{2,})(?:\s+[A-Z]{2,})*\s+(?=[A-Z][a-z])",
                  "", clean(name))
    return clean(name)


def parse_summary_page(page):
    """NCR-wide Low/High/Prevailing/Average rows (two columns per page)."""
    rows = []
    w, h = page.width, page.height
    for box in ((0, 0, w / 2, h), (w / 2, 0, w, h)):
        group = ""
        for line in (page.crop(box).extract_text() or "").splitlines():
            line = clean(line)
            if line.startswith("COMMODITIES"):
                group = ""                       # a new table begins
                continue
            if line.isupper() and ("COMMERCIAL RICE" in line or "RICE-FOR-ALL" in line):
                group = line.title()
                continue
            m = SUM_RE.match(line)
            if not m or DATE_RE.search(line):
                continue
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", m.group("nums"))]
            if len(nums) == 4:
                low, high, prev, avg = nums
            elif len(nums) == 3:                 # single-value row: low == high
                low, prev, avg = nums
                high = low
            else:
                continue
            name = clean_name(m.group("name"))
            if name.upper().startswith("COMMODITIES"):
                continue
            unit = "PHP/piece" if re.search(r"\bEgg\b", name) else "PHP/kg"
            if re.search(r"\b(350ml|1L)\b", name):
                unit = "PHP/bottle"
            rows.append([group or category_for(name), name, unit, low, high, prev, avg])
    return rows


def convert(pdf_path, warn=lambda msg: None):
    """Open the PDF once. Returns (iso_date, commodity_order, markets, summary_rows)."""
    markets, order, srows = {}, [], []
    report_date = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not report_date:
                d = DATE_RE.search(text)
                if d:
                    report_date = iso_date(d.group(2))
            if MARKET_PAGE_MARK not in text:     # cheap check before table detection
                srows.extend(parse_summary_page(page))
                continue
            commodities, rows = parse_market_page(page, text, warn)
            if not commodities:
                srows.extend(parse_summary_page(page))
                continue
            names = []
            for c in commodities:                # a leading '*' means "per piece"
                unit = "PHP/piece" if c.startswith("*") else "PHP/kg"
                names.append((c.lstrip("*").strip(), unit))
            order.extend(n for n in names if n not in order)
            for market, cells in rows:
                prices = markets.setdefault(market, {})
                for name, cell in zip(names, cells):
                    prices[name] = parse_price(cell)
    return report_date, order, markets, srows


# ----------------------------------------------------------------- writing
def fmt(x):
    return "" if x is None else f"{x:.2f}"


def write_wide(path, report_date, order, markets):
    header = ["date", "market"]
    for name, unit in order:
        header += [f"{name} Low ({unit})", f"{name} High ({unit})"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for market, prices in markets.items():
            row = [report_date, market]
            for key in order:
                low, high = prices.get(key, (None, None))
                row += [fmt(low), fmt(high)]
            w.writerow(row)


MARKET_LONG_HEADER = ["date", "market", "commodity", "unit", "price_low", "price_high"]


def market_long_rows(report_date, order, markets):
    for market, prices in markets.items():
        for name, unit in order:
            low, high = prices.get((name, unit), (None, None))
            yield [report_date, market, name, unit, fmt(low), fmt(high)]


SUMMARY_HEADER = ["date", "group", "commodity", "unit", "low", "high",
                  "prevailing", "average"]


def summary_csv_rows(report_date, srows):
    for g, name, unit, low, high, prev, avg in srows:
        yield [report_date, g, name, unit, fmt(low), fmt(high), fmt(prev), fmt(avg)]


def write_rows(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ----------------------------------------------------------------- workers
def work(job):
    """Runs in a worker process. Never raises; returns a plain tuple."""
    pdf, = job
    warnings = []
    try:
        report_date, order, markets, srows = convert(pdf, warnings.append)
        if not markets and not srows:
            raise ValueError("no price tables found")
        return pdf, report_date, order, markets, srows, warnings, None
    except Exception as e:
        return pdf, "", [], {}, [], warnings, str(e)


def pause():
    """Keep the window open when launched by drag-and-drop / double-click."""
    if sys.platform == "win32" and sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")


def find_pdfs(folder, recursive):
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() == ".pdf")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", help="one or more input PDF files")
    ap.add_argument("-o", "--output", help="output CSV (only with a single PDF)")
    ap.add_argument("--all", action="store_true",
                    help="convert every PDF in the current folder")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="with --all, also search subfolders")
    ap.add_argument("--combine", action="store_true",
                    help="write all_markets.csv and all_ncr_summary.csv (tidy format) "
                         "instead of one pair of CSVs per PDF")
    ap.add_argument("--also-individual", action="store_true",
                    help="with --combine, also write the per-PDF CSVs")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip PDFs whose CSV already exists (per-file mode)")
    ap.add_argument("-j", "--jobs", type=int, default=0,
                    help="worker processes (default: all CPU cores)")
    ap.add_argument("--no-summary", action="store_true",
                    help="skip the NCR-wide Low/High/Prevailing/Average data")
    ap.add_argument("--long", action="store_true",
                    help="per-file CSV in tidy format: one row per market + commodity")
    args = ap.parse_args()

    if not args.pdfs and not args.all:
        # Double-clicked with nothing dropped: offer to convert the whole folder.
        if sys.stdin and sys.stdin.isatty():
            print("No files given. Drag PDFs onto this script, or convert them all.")
            if input(f"Convert every PDF in {Path.cwd()}? [Y/n] ").strip().lower() in ("", "y", "yes"):
                args.all = True
        if not args.all:
            pause()
            return

    if args.all:
        found = find_pdfs(Path.cwd(), args.recursive)
        args.pdfs = [str(p) for p in found] + args.pdfs
        print(f"Found {len(found)} PDF(s) in {Path.cwd()}"
              f"{' (including subfolders)' if args.recursive else ''}", file=sys.stderr)
    if not args.pdfs:
        print("No PDF files found.")
        pause()
        return
    if args.output and len(args.pdfs) > 1:
        sys.exit("-o/--output can only be used with a single PDF.")

    write_individual = (not args.combine) or args.also_individual
    pdfs = list(dict.fromkeys(args.pdfs))
    if args.skip_existing and write_individual and not args.combine:
        keep = [p for p in pdfs if not Path(p).with_suffix(".csv").exists()]
        print(f"Skipping {len(pdfs) - len(keep)} already converted", file=sys.stderr)
        pdfs = keep

    jobs = args.jobs or os.cpu_count() or 1
    jobs = max(1, min(jobs, len(pdfs)))
    start = time.time()
    done, failed, errors = 0, 0, []
    master_m, master_s = [], []                  # (sort_key, rows)

    def handle(res, n):
        nonlocal done, failed
        pdf, report_date, order, markets, srows, warnings, err = res
        tag = f"[{n}/{len(pdfs)}] {Path(pdf).name}"
        if err:
            failed += 1
            errors.append((pdf, err))
            print(f"{tag}  FAILED: {err}", file=sys.stderr)
            return
        for w in warnings:
            print(f"{tag}  ! {w}", file=sys.stderr)
        if write_individual:
            out = args.output or str(Path(pdf).with_suffix(".csv"))
            if args.long:
                write_rows(out, MARKET_LONG_HEADER,
                           market_long_rows(report_date, order, markets))
            else:
                write_wide(out, report_date, order, markets)
            if srows and not args.no_summary:
                sout = str(Path(out).with_name(Path(out).stem + "_ncr_summary.csv"))
                write_rows(sout, SUMMARY_HEADER, summary_csv_rows(report_date, srows))
        if args.combine:
            key = (report_date, Path(pdf).name)
            src = Path(pdf).name
            master_m.append((key, [r + [src] for r in
                                   market_long_rows(report_date, order, markets)]))
            if srows and not args.no_summary:
                master_s.append((key, [r + [src] for r in
                                       summary_csv_rows(report_date, srows)]))
        done += 1
        print(f"{tag}  ok ({len(markets)} markets, {len(srows)} summary rows)",
              file=sys.stderr)

    if jobs == 1:
        for n, pdf in enumerate(pdfs, 1):
            handle(work((pdf,)), n)
    else:
        print(f"Using {jobs} worker processes", file=sys.stderr)
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(work, (p,)) for p in pdfs]
            for n, fut in enumerate(as_completed(futs), 1):
                handle(fut.result(), n)

    if args.combine and (master_m or master_s):
        out_dir = Path.cwd()
        if master_m:
            master_m.sort(key=lambda x: x[0])
            write_rows(out_dir / "all_markets.csv", MARKET_LONG_HEADER + ["source_file"],
                       (r for _, rows in master_m for r in rows))
            print(f"Wrote {out_dir / 'all_markets.csv'}", file=sys.stderr)
        if master_s:
            master_s.sort(key=lambda x: x[0])
            write_rows(out_dir / "all_ncr_summary.csv", SUMMARY_HEADER + ["source_file"],
                       (r for _, rows in master_s for r in rows))
            print(f"Wrote {out_dir / 'all_ncr_summary.csv'}", file=sys.stderr)

    if errors:
        write_rows(Path.cwd() / "conversion_errors.csv", ["file", "error"], errors)
        print(f"{failed} file(s) failed; listed in conversion_errors.csv", file=sys.stderr)

    print(f"\nDone: {done} converted, {failed} failed in {time.time() - start:.1f}s.",
          file=sys.stderr)
    pause()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
