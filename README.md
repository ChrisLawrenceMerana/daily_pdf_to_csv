# Bantay Presyo PDF to CSV

Converts DA Bantay Presyo price monitoring PDFs (e.g. `Price-Monitoring-December-24-2025.pdf`) into CSV files you can open in Excel, and merges the results from many PDFs into two files.

## What it produces

Each PDF contains two kinds of data, so each PDF creates two CSV files, saved in two folders that are created in the folder you run the script from:

| File | Contents |
|------|----------|
| `Per Market/<name>.csv` | Per-market prices: one row per market, with **Low** and **High** columns for every commodity |
| `NCR Summary/<name>_ncr_summary.csv` | NCR-wide figures: **Low, High, Prevailing, Average** for every commodity (columns: `date`, `group`, `commodity`, `unit`, `low`, `high`, `prevailing`, `average`) |

Notes on the data:
- Dates are written as ISO dates (`2025-12-24`).
- A single price (e.g. `45.00`) appears in both the Low and High columns.
- `NOT AVAILABLE` is left blank.
- Eggs are `PHP/piece`, palm and coconut oils are `PHP/bottle`, everything else is `PHP/kg`.
- The `group` column in the summary is one of: Kadiwa Rice-For-All, Imported Commercial Rice, Local Commercial Rice, Corn, Livestock And Poultry Products, Fish, Lowland Vegetables, Highland Vegetables, Fruits, Other Commodities, Spices.
  - The rice groups come from the headings in the PDF (names like "Glutinous" and "Premium" appear under both imported and local rice).
  - The other groups are assigned from the commodity name using the `GROUP_RULES` list near the top of `pdf_to_csv.py`, because the category labels in the PDF are printed sideways and can't be read reliably.
  - Anything not matched is left blank. Today that is only **Mung Bean**, which has no category label in the PDF. Add new commodities to `GROUP_RULES` when they appear.

## Setup (once)

1. Install [Python](https://www.python.org/downloads/). On Windows, tick **"Add Python to PATH"** in the installer.
2. Install the PDF library:
   ```
   pip install pdfplumber
   ```
3. Keep these files together in one folder:
   - `pdf_to_csv.py`: the converter
   - `merge_csvs.py`: merges results (only needed for bulk or shared work)
   - `Convert-PDF-ALL.bat`: Windows, double-click to convert a whole folder
   - `Convert-PDF-to-CSV.bat`: Windows, drag and drop

## Usage

### Windows: convert everything in a folder
1. Put the PDFs in the same folder as the script and `.bat` files.
2. Double-click `Convert-PDF-ALL.bat`.
3. Press Enter when it says "Done".

### Windows: convert specific files
Drag one or more PDFs onto `Convert-PDF-to-CSV.bat`.

Double-clicking `Convert-PDF-to-CSV.bat` with nothing dropped on it asks whether to convert every PDF in the folder.

### Terminal or VS Code (Windows, Mac, Linux)
Open a terminal in the folder (in VS Code: **File > Open Folder**, then Ctrl+`) and run:

```
python pdf_to_csv.py --all                    # every PDF in the current folder
python pdf_to_csv.py file.pdf                 # one file
python pdf_to_csv.py a.pdf b.pdf c.pdf        # several files
python pdf_to_csv.py file.pdf -o prices.csv   # custom output name (single file only)
```

On Mac or Linux, use `python3` instead of `python`.

### Bulk mode (months or years of PDFs)

Put all the PDFs in one folder (subfolders are fine), open a terminal there, and run:

```
python pdf_to_csv.py --all -r --combine
```

This uses every CPU core and writes two tidy files instead of thousands of small ones:

| File | Contents |
|------|----------|
| `Per Market/all_markets.csv` | `date, market, commodity, unit, price_low, price_high, source_file` |
| `NCR Summary/all_ncr_summary.csv` | `date, group, commodity, unit, low, high, prevailing, average, source_file` |

Rows are sorted by date. Files that fail are listed in `conversion_errors.csv` (in the same place the folders are created). Because the tidy format has one row per commodity, it works even if the commodities or markets change between years (the wide per-PDF format would have different columns each time).

Tips:
- Try a handful of the oldest PDFs first to check the layout still parses, and read any `!` warnings.
- Add `--also-individual` to keep the per-PDF CSVs as well, or `-j 4` to limit the number of worker processes.

**How long it takes:** roughly 1.3 seconds per PDF on one CPU core. For about 1,800 PDFs (five years of daily reports) that is about 40 minutes on one core, and roughly 5 to 15 minutes on a laptop with 4 to 8 cores. These are estimates; your computer may differ.

### Merging results

Use `merge_csvs.py` when the PDFs were split between several people, or when you converted in separate batches.

1. Collect everyone's CSVs into one folder. Rename them first so they don't overwrite each other, e.g. `alice_markets.csv` and `alice_summary.csv` (take them out of the `Per Market` and `NCR Summary` folders first, or they will all be called `all_markets.csv` / `all_ncr_summary.csv`).
2. Put `merge_csvs.py` in that folder and run:
   ```
   python merge_csvs.py
   ```

It tells the files apart automatically and writes:
- `merged_markets.csv`: all the daily per-market prices, one row per market and commodity
- `merged_ncr_summary.csv`: all the NCR-wide summaries

What it accepts and does:
- Any mix of the files from bulk mode (`all_markets.csv`, `all_ncr_summary.csv`) and the per-PDF files (`<name>.csv`, `<name>_ncr_summary.csv`). Per-PDF market files (one row per market) are converted to the tidy format automatically.
- Older-style dates like `Wednesday, 24 December 2025` are converted to `2025-12-24`.
- A blank `group` in older summary files is filled in, so you don't need to re-convert them.
- Rows are sorted by date, and exact duplicate rows are removed.
- A file it can't use is reported as `SKIPPED` with the reason, and the rest still merge. `conversion_errors.csv` and anything that isn't a markets or summary file are ignored.

By default it looks for CSVs in the current folder and in its `Per Market` and `NCR Summary` folders, so you can also run it in the folder where you converted the PDFs. Don't run it where both per-PDF CSVs and `all_*.csv` files exist for the same PDFs, or those rows will be counted twice.

You can also pass specific files: `python merge_csvs.py a/*.csv b/*.csv`. Use `-d folder` to write the merged files somewhere else.

### Options for pdf_to_csv.py

| Option | Effect |
|--------|--------|
| `--all` | Convert every PDF in the current folder |
| `-r`, `--recursive` | With `--all`, also search subfolders |
| `--combine` | Write `all_markets.csv` and `all_ncr_summary.csv` instead of per-PDF CSVs |
| `--also-individual` | With `--combine`, also write the per-PDF CSVs |
| `--skip-existing` | Skip PDFs whose CSV already exists (per-PDF mode) |
| `-j`, `--jobs N` | Number of worker processes (default: all CPU cores) |
| `--long` | Per-PDF market CSV with one row per market and commodity instead of one row per market |
| `--no-summary` | Skip the NCR-wide summary data |
| `-o`, `--output` | Output file name for the per-market CSV (only with a single PDF) |
| `--outdir DIR` | Create the `Per Market` and `NCR Summary` folders in `DIR` instead of the current folder |

Existing CSVs with the same name are overwritten. Two PDFs with the same file name in different subfolders would overwrite each other's output.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `'python' is not recognized` | Reinstall Python with "Add Python to PATH" ticked, or try `py` instead of `python` |
| `Missing dependency` | Run `pip install pdfplumber` |
| Window closes immediately | Open a terminal and run the command there to see the error |
| A file is reported as `FAILED` | It isn't a valid PDF, or it has no price tables. Other files still convert |
| A row is skipped with a warning (`!`) | Its number of values didn't match the table header. The script skips it instead of writing misaligned data |
| `merged_markets.csv` was not created | Check the `SKIPPED` lines the merge printed. Make sure the market files came from `pdf_to_csv.py`, and that you're using the latest `merge_csvs.py` |
| `group` is blank for a commodity | Add it to `GROUP_RULES` in `pdf_to_csv.py` (and `merge_csvs.py`) |
| Header mismatch / unexpected columns in the merge | Those files came from a different version of `pdf_to_csv.py`. Re-convert them with the current one |

## Limitations

- Built and tested on the Bantay Presyo layout for 24 December 2025. Other reports with the same layout should work, but a changed layout may need script changes.
- The `.bat` files are Windows only. On other systems use the terminal commands above.
- Group names are assigned by commodity name, so new commodities need a rule added to appear under a group.
