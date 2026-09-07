# Wymount South Housing — bid-item extraction audit

Audits an AI-extracted `bid_items` list against the actual drawing PDF
(source of truth) and classifies every item as RIGHT / WRONG /
NEEDS_REVIEW, then writes a corrected `results.json` and a findings
spreadsheet.

## Files

- `pdf_extract.py` — pulls the real PDF text under a `quad_px` region,
  correctly handling pages stored rotated (`/Rotate 270`, portrait
  MediaBox) by transforming word boxes with `page.rotation_matrix` into
  the same "as viewed" 3024×2160 space the region coordinates use.
- `legend_parser.py` — encodes:
  - the wall/roof/ceiling/floor assembly-type tags from this project's
    G-501/G-511 legend sheets (`WALL_TAGS`, `ROOF_TAGS`, `CEILING_TAGS`,
    `FLOOR_TAGS`), used to catch a tag being read as the wrong kind of
    callout (e.g. `WD42A` is a rated wall tag, not a door code);
  - civil/site-utility plan symbols (`SITE_CODE_KEYWORDS`: FH, EM, SS,
    SD, PB, FO, IRR, SDMH, CIB, ADS, pipe-size callouts, `FF=` spot
    elevations);
  - finish-schedule shorthand codes (`FINISH_CODE_KEYWORDS`: C-1, RB-1,
    T-2, LVT-1, P-1, EP-1, GB-1, etc.);
  - detectors for grid bubbles, dimension strings, title-block
    furniture, and bare room labels.
- `audit.py` — the classifier. For each bid item it:
  1. pulls ground-truth text for every region,
  2. checks for tag-context confusion (highest-confidence signal),
  3. checks whether every region is a non-content artifact (grid bubble,
     title block, dimension string, bare room label) → WRONG,
  4. checks keyword / finish-code / site-code overlap with the item's
     own wording → RIGHT,
  5. anything left over is `NEEDS_REVIEW`.
  A small `MANUAL_OVERRIDES` dict at the top records the handful of
  cases resolved by a human reading the sheet directly (documented
  inline with the reasoning for each).
- `build_outputs.py` — removes WRONG items from a copy of `results.json`
  (packages that become empty are dropped) and exports `findings.csv`.

## Running on a new set of sheets (e.g. the second, unseen set)

```bash
pip install pymupdf --break-system-packages   # if not already installed

# 1. Put the new PDF and its extraction JSON next to these scripts as
#    drawings.pdf and results.json (or edit the paths at the bottom of
#    audit.py / build_outputs.py).
python3 audit.py            # -> findings_raw.json + console summary
python3 build_outputs.py    # -> results_corrected.json + findings.csv
```

Then open `findings_raw.json` (or `findings.csv`) and skim the
`NEEDS_REVIEW` rows first — that's where the heuristics are telling you
they're not confident, and it's usually a short list.

## Known limitations (see write-up for full discussion)

- Some sheets embed content (calculation tables, exhibits) as
  **rasterized images** with no text layer — PyMuPDF can't read those,
  so those regions come back empty and get flagged `NEEDS_REVIEW`
  rather than auto-resolved. They'd need OCR or a manual look.
- The tag/code dictionaries (`WALL_TAGS`, `SITE_CODE_KEYWORDS`, etc.)
  were hand-built from *this* project's legend sheets. A different
  project's legend will use different tag/code vocabularies, so this
  part of the classifier needs to be re-derived (or at least reviewed)
  for a new drawing set before trusting its verdicts.
- Region-overlap text extraction uses each region's bounding rectangle
  with padding; very tightly packed callouts can pull in a neighboring
  word or two. This mostly self-corrects because the classifier looks
  for *supporting* keywords rather than requiring an exact match, but
  it's worth spot-checking dense areas (e.g. equipment plans).
