"""
Audit pipeline for the Wymount South Housing bid-item extraction QA task.

For every bid item in results.json:
  1. Pull the actual PDF text at each region's quad_px (source of truth),
     correctly handling rotated pages.
  2. Run a battery of heuristic checks (grid bubbles, dimension strings,
     title-block furniture, bare room labels, assembly-tag legend
     cross-check, keyword support) to reach a verdict.
  3. Anything the heuristics can't confidently resolve is marked
     NEEDS_REVIEW so a human spends their time only where it's needed.

Reference/legend sheets G-002.3, G-501, G-511 are excluded from the
findings (per the assignment) but are still copied through untouched in
the corrected JSON output.
"""
import json
import re
import sys
from collections import Counter

from pdf_extract import build_page_indexes
from legend_parser import (
    WALL_TAGS, ROOF_TAGS, CEILING_TAGS, FLOOR_TAGS,
    is_grid_bubble_text, is_dimension_string, is_title_block_text,
    is_bare_room_label, finish_code_keywords, site_code_keywords,
    has_botanical_name, find_disclaimer_zones, region_overlaps_zone,
)

REFERENCE_SHEETS = {"G-002.3", "G-501", "G-511"}

ALL_TAGS = {}
TAG_KIND = {}
for kind, d in (("wall", WALL_TAGS), ("roof", ROOF_TAGS), ("ceiling", CEILING_TAGS), ("floor", FLOOR_TAGS)):
    ALL_TAGS.update(d)
    for tag in d:
        TAG_KIND[tag] = kind

# If a bid item's own wording implies one of these product types, an assembly
# tag of the given "kind" cannot be the thing supporting it - those tags only
# ever appear as wall/roof/ceiling/floor TYPE callouts on this project's
# legend sheets (G-501/G-511), never as door/window/corner-guard designators.
INCOMPATIBLE_ITEM_KEYWORDS_BY_TAG_KIND = {
    "wall": {"door", "window", "corner guard", "corner guards"},
    "roof": {"door", "window", "corner guard", "corner guards", "vinyl clad"},
    "ceiling": {"corner guard", "corner guards", "door", "window"},
    "floor": {"door", "window", "vinyl clad windows", "corner guard", "corner guards"},
}

STOPWORDS = {
    "furnish", "install", "the", "and", "for", "a", "an", "of", "to", "per",
    "with", "as", "all", "or", "at", "in", "on", "-", "&",
}


SYNONYMS = {
    "grading": {"grade", "grading"},
    "grade": {"grade", "grading"},
}


def keywords(s):
    s = re.sub(r'[^a-zA-Z0-9\- ]', ' ', (s or '').lower())
    words = {w for w in s.split() if w not in STOPWORDS and len(w) > 2}
    out = set(words)
    for w in words:
        out |= SYNONYMS.get(w, set())
        # light singular/plural + gerund normalization so "drains"/"drain",
        # "hydrants"/"hydrant", "cabling"/"cable" etc. line up
        if w.endswith("ing") and len(w) > 5:
            out.add(w[:-3])
        if w.endswith("s") and len(w) > 4:
            out.add(w[:-1])
    return out


def normalize_ws(s):
    return re.sub(r'\s+', ' ', s or '').strip()


MANUAL_OVERRIDES = {
    # (sheet_number, scope_package, bid_item) -> (verdict, reason)
    # These are cases the automated heuristics correctly flagged as needing
    # a human look; each was resolved by reading the actual sheet content.
    ("A-005", "Thermal Protection", "Furnish & Install Wall Partition Assemblies As Per Wall Type Schedules And Tags"):
        ("WRONG", "A-005 is a roof plan (BLDG TYPES - ROOF PLAN). All referenced text is column/row grid bubbles "
                  "(A1, A4, AX, AV, AZ...) and roof-slope callouts (1.7%) around the edges of the roof plans, not "
                  "wall-type tags. No wall assembly tag appears in any referenced region."),
    ("A-005", "Flooring", "Furnish & Install Unspecified Floor Finish From Schedule"):
        ("WRONG", "A-005 is a roof plan with no floor finish content. Referenced text is column/row grid bubbles "
                  "(F1, F3, FZ, FX, FV, F2...), not a floor finish schedule reference."),
    ("A-005", "Flooring", "Furnish & Install Carpet"):
        ("WRONG", "A-005 is a roof plan with no flooring content. Referenced text is column/row grid bubbles "
                  "(C1, C4, CX, CV, CZ...), not a carpet callout."),
    ("A-005", "Electrical", "Furnish & Install Unidentified Electrical Components for Estimator Review"):
        ("WRONG", "Referenced text is column/row grid bubbles (E1, E4, EX, EV, EZ, E2, E3, E5, EW, EY), not "
                  "electrical components. There is no electrical content on this roof plan sheet."),
    ("A-005", "Wall Finishes", "Furnish & Install Wall Base"):
        ("WRONG", "A-005 is a roof plan with no wall-base content. Referenced text is column/row grid bubbles "
                  "(B1, B4, BX, BV, BZ...), not a wall base callout."),
    ("A-101CD", "Drywall", "Furnish & Install Wall Framing Layout And Coordination"):
        ("WRONG", "Referenced text is only the abbreviation \"F.O.S.\" (Face Of Stud) repeated - a dimension-"
                  "reference annotation used throughout the floor plan, not evidence of a distinct 'layout and "
                  "coordination' scope item."),
    ("A-425", "Windows", "Furnish & Install Vinyl Clad Windows"):
        ("WRONG", "Referenced text (\"96 UNITS WT7\", \"84 UNITS WT6\", \"60 UNITS WT5\") is the unit-count callout "
                  "from the site equipment plan legend (number of apartment units per building), unrelated to "
                  "windows."),
    ("CU300", "Site Concrete", "Furnish & Install Concrete Formwork and Accessories"):
        ("WRONG", "Referenced text is general note 3 about minimum pipe cover depth for fire sprinkler/water lines "
                  "(\"ALL FIRE SPRINKLER LINES SHALL HAVE 60\\\" OF COVER...\"). It is a utility-depth note, not "
                  "concrete formwork scope."),
    ("CU320", "Utilities", "Furnish & Install Trench Drains and Drain Structures"):
        ("WRONG", "Referenced text is note 12, which describes the 15\" storm line routing through Overflow Box-1's "
                  "orifice plate wall - it does not describe or specify a trench drain. The sheet's actual trench-"
                  "drain requirements are in a different note (note 13), which is what the 'Pipe Connection "
                  "Accessories' item on this same sheet points to instead."),
    ("CU320", "Utilities", "Furnish & Install Relocation and Adjustment of Existing Utilities"):
        ("WRONG", "Referenced text (\"4. REFER TO NOTES ON CU300 FOR GENERAL UTILITY NOTES, TYP.\") is a generic "
                  "cross-reference pointer, not a description of relocation/adjustment work. It doesn't itself "
                  "support the item as written."),
    ("A-421", "Flashing and Sheet Metal", "Furnish & Install Gutters, Downspouts, and Associated Accessories"):
        ("RIGHT", "Referenced text \"DS\" is the downspout symbol/abbreviation shown on the roof plan, consistent "
                  "with the item."),
    ("A-421", "Interior Specialties", "Furnish & Install Toilet & Bathroom Accessories"):
        ("RIGHT", "Referenced equipment tags (TH1 Towel Hook, TR1 Towel Ring, MR1 Mirror, TP1 TP Holder) are all "
                  "bathroom accessories per the specialty equipment schedule."),
    ("A-421", "Storage Specialties", "Furnish & Install Shelving and Closet Rods"):
        ("RIGHT", "Referenced text (\"ROD AND SHELF\", \"SR\", \"SHELVES\") matches shelving/closet-rod callouts in "
                  "the closet and linen closet details."),
    ("A-421", "Residential Equipment", "Furnish & Install Residential Appliances"):
        ("RIGHT", "Referenced equipment tags (WA1 Washer, DR1 Dryer, FR1 Fridge, MW1 Microwave, RH1 Range Hood, "
                  "GD1 Disposal, DW1 Dishwasher, OV1 Oven/Range) are all residential appliances per the specialty "
                  "equipment schedule."),
    ("A-421", "Plumbing", "Furnish & Install — Plumbing Fixture Accessories and Trim"):
        ("RIGHT", "Referenced tags (TS1 Tub Surround, SC1 Shower Caddy) are plumbing fixture accessories/trim per "
                  "the specialty equipment schedule."),
    ("A-425", "Storage Specialties", "Furnish & Install Mailboxes"):
        ("RIGHT", "Referenced text (\"MB1 MAILBOX... 16 MAIL & 2 PACKAGES PER UNIT\") directly matches the item."),
    ("A-425", "Furnishings and Accessories", "Furnish & Install Shower Caddies"):
        ("RIGHT", "Referenced text (\"SC1 SHWR CADDY, BASIS OF DESIGN: BCI SURROUND, FOUR SHELF CADDY\") directly "
                  "matches the item."),
    ("A-602", "Rough Carpentry", "Furnish & Install Fasteners"):
        ("RIGHT", "Referenced text (\"HOT-DIP GALV. RING SHANK NAIL @ 12\\\" O.C.\") is a fastener specification, "
                  "matching the item."),
    ("A-602", "Thermal Protection", "Furnish & Install Acoustic Batt Insulation"):
        ("RIGHT", "Referenced text (\"SOUND ATTENUATION BLANKET; WHERE OCCURS, SEE WALL TYPES\") is the project's "
                  "term for acoustic batt insulation, matching the item."),
    ("A-602", "Flashing and Sheet Metal", "Furnish & Install Flashing Installation Accessories"):
        ("RIGHT", "Referenced text (\"POP-RIVET AT TOP AND BOTTOM / 18\\\" O.C.\", \"OPEN HEM SLIP JOINT\") are "
                  "flashing/trim installation accessory details, matching the item."),
    ("A-602", "Joint Sealants", "Furnish & Install Interior Sealant at Finishes"):
        ("RIGHT", "Referenced text (\"CAULK FRAME TO GYP. BD. JOINT, TYP.\") describes a sealant/caulk joint, "
                  "matching the item."),
    ("CU300", "Earthwork", "Furnish & Install General Excavation, Trenching, and Backfill"):
        ("RIGHT", "Referenced text is general note 4 instructing to pothole (excavate to verify) existing utility "
                  "crossings before routing new utilities - genuine earthwork/excavation scope."),
    ("CU300", "Utilities", "Refer to the Utilities schedules"):
        ("RIGHT", "Referenced text is the actual Utility Structure Label / Detail # schedule table on this sheet."),
    ("CU320", "Utilities", "Furnish & Install Pipe Connection Accessories"):
        ("RIGHT", "Referenced note 13 (\"REFER TO NOTES ON CU300 FOR TRENCH DRAIN REQUIREMENTS. CONNECT TRENCH "
                  "DRAINS TO STORM DRAIN SYSTEM...\") describes a pipe connection instruction, consistent with the "
                  "item."),
    ("LP101", "Landscaping", "Provide Coordination And Clarifications For Landscaping Scope"):
        ("RIGHT", "Referenced text is the plant schedule category headers (Deciduous Trees, Evergreen Trees, "
                  "Shrubs, Perennials, Ground Covers, etc.), genuine landscaping content on this sheet."),
    ("EP501", "Electrical", "Refer to the Electrical schedules"):
        ("RIGHT", "Referenced text is drawn from the actual branch panel schedules on this sheet."),
    ("CG452", "Utilities", "Refer to the Utilities schedules"):
        ("RIGHT", "Region overlaps a rasterized (image, no text layer) drainage-calculation exhibit for Wymount "
                  "Basin 3/4 that is genuinely present on this sheet, confirmed by visual inspection of the "
                  "rendered page; PyMuPDF cannot OCR it automatically."),
    ("CG452", "Site Improvements", "Refer to the Site Improvements schedules"):
        ("RIGHT", "Region overlaps a rasterized (image, no text layer) Water Quality Volume (WQV) calculation "
                  "table, genuinely present on this sheet (confirmed by visual inspection). Note: this content is "
                  "stormwater/hydrology data and arguably belongs under a Utilities package rather than \"Site "
                  "Improvements\" - flagged as a scope-package mislabel, not a fabricated item."),
}


def find_tags(text):
    found = set()
    for tok in re.findall(r'\b[A-Z]{1,3}[\d.#]{1,6}[A-Z]?\b', text.upper()):
        if tok in ALL_TAGS:
            found.add(tok)
    return found


def classify_item(sheet_number, pkg, item, region_texts):
    """region_texts: list of extracted ground-truth strings, one per region."""
    combined = normalize_ws(" | ".join(t for t in region_texts if t))
    bid_name = item.get("bid_item", "")
    subheading = item.get("subheading", "")
    bid_l = bid_name.lower()
    kw_item = keywords(bid_name) | keywords(subheading)

    if not combined:
        return "WRONG", "No text could be extracted at the referenced region(s) on the actual PDF page (empty/blank area) - nothing on the sheet supports this item."

    # --- Known abbreviation-collision special case ---
    # "EM" is the site-plan symbol for an Electrical Manhole (see CU300/CU320,
    # where the same symbol correctly backs the "Electrical Manholes" item).
    # On CG452 that identical symbol is instead attached to an "Emergency
    # System Components" item - a different reading of the same two letters.
    if "emergency" in bid_l:
        stripped = re.sub(r'[>|]', ' ', combined).strip()
        tokens = stripped.split()
        if tokens and all(t == "EM" for t in tokens):
            return "WRONG", (
                "The only referenced text is the repeated symbol \"EM\", which on this project's civil "
                "sheets (see CU300/CU320) denotes an Electrical Manhole, not an emergency system "
                "component - the same abbreviation appears to have been read the wrong way here."
            )

    # --- Tag-context confusion check (highest-confidence signal) ---
    # e.g. bid item claims "Wood Doors" but the only text hit is "WD42A",
    # which the G-501 legend defines as a rated WOOD-STUD WALL tag, not a door.
    tags_found = find_tags(combined)
    for tag in tags_found:
        kind = TAG_KIND[tag]
        category, note = ALL_TAGS[tag]
        bad_kw = INCOMPATIBLE_ITEM_KEYWORDS_BY_TAG_KIND.get(kind, set())
        if any(k in bid_l for k in bad_kw):
            return "WRONG", (
                f"The referenced text is the tag \"{tag}\", which the {kind} assembly-type legend on "
                f"G-501/G-511 defines as {note} - a {kind} assembly designation, not \"{bid_name}\". "
                f"This looks like the abbreviation/tag was read as the wrong kind of callout."
            )

    # --- Per-region classification ---
    region_flags = []
    for t in region_texts:
        t = normalize_ws(t)
        if not t:
            region_flags.append(("empty", t))
            continue
        if is_grid_bubble_text(t):
            region_flags.append(("grid_bubble", t))
        elif is_title_block_text(t):
            region_flags.append(("title_block", t))
        elif is_dimension_string(t):
            region_flags.append(("dimension", t))
        elif is_bare_room_label(t) and "signage" not in bid_l and "identification" not in bid_l:
            region_flags.append(("room_label", t))
        else:
            region_flags.append(("content", t))

    flag_counts = Counter(f for f, _ in region_flags)
    n = len(region_flags)
    n_content = flag_counts.get("content", 0)

    if n_content == 0 and n > 0:
        dominant, _ = flag_counts.most_common(1)[0]
        examples = "; ".join(t for f, t in region_flags if f == dominant)[:200]
        reason_map = {
            "grid_bubble": f"All referenced regions are column/row grid bubbles (e.g. \"{examples}\"), not a scope of work.",
            "title_block": f"All referenced regions are title-block/page furniture - e.g. the architect's own office address/phone (\"{examples}\") - not a scope of work.",
            "dimension": f"All referenced regions are dimension strings (e.g. \"{examples}\"), not a scope of work.",
            "room_label": f"All referenced regions are bare room labels (e.g. \"{examples}\") - a room name alone does not establish this specific scope of work.",
            "empty": "Referenced region(s) contain no readable text on the actual PDF page.",
        }
        return "WRONG", reason_map.get(dominant, f"Regions do not contain scope-supporting text: {examples}")

    # --- Keyword-overlap support check, boosted by finish-schedule shorthand
    # codes, civil/site-utility plan symbols, and botanical plant names ---
    combined_kw = keywords(combined) | finish_code_keywords(combined) | site_code_keywords(combined)
    if has_botanical_name(combined):
        combined_kw |= {"tree", "shrub", "plant", "planting", "landscap", "ground", "cover", "perennial"}
    overlap = kw_item & combined_kw
    if overlap:
        return "RIGHT", f"Referenced text (\"{combined[:150]}\") supports the item; matched terms: {', '.join(sorted(overlap))[:120]}."

    # Tag found and no incompatibility flagged above -> treat as legitimate
    # assembly-tag support (e.g. "Wall Partition Assemblies" matched to "W60A").
    if tags_found:
        t = ", ".join(sorted(tags_found))
        return "RIGHT", f"Referenced tag(s) \"{t}\" appear on the G-501/G-511 assembly legend and are consistent with \"{bid_name}\"."

    return "NEEDS_REVIEW", f"Region text (\"{combined[:150]}\") does not obviously contain matching keywords for \"{bid_name}\" - verify by eye."


def main():
    with open("results.json") as f:
        data = json.load(f)
    sheets = data["drawings_processing_results"]
    doc, page_index = build_page_indexes("drawings.pdf")

    findings = []
    removed_item_keys = []  # (sheet_idx, pkg, item_index) to drop from corrected json

    for s_idx, s in enumerate(sheets):
        sheet_number = s["sheet_number"]
        page_num = s["page_number"]
        page = doc[page_num - 1]
        pindex = page_index[page_num - 1]
        is_reference = sheet_number in REFERENCE_SHEETS
        img_rects = [page.get_image_bbox(img) for img in page.get_images(full=True)] if page.get_images() else []
        disclaimer_zones = find_disclaimer_zones(page)

        def overlaps_image(quad_px):
            rx0, ry0, rx1, ry1 = quad_px[0], quad_px[1], quad_px[4], quad_px[5]
            rx0, rx1 = sorted([rx0, rx1]); ry0, ry1 = sorted([ry0, ry1])
            for ir in img_rects:
                if ir.x0 < rx1 and ir.x1 > rx0 and ir.y0 < ry1 and ir.y1 > ry0:
                    return True
            return False

        for pkg, items in s.get("bid_items", {}).items():
            for it_idx, item in enumerate(items):
                region_texts = []
                region_is_image = []
                for reg in item.get("regions", []):
                    try:
                        t = pindex.text_in_quad(reg["quad_px"])
                    except Exception:
                        t = ""
                    region_texts.append(t)
                    region_is_image.append((not t) and overlaps_image(reg["quad_px"]))

                if is_reference:
                    # excluded from findings/audit per assignment instructions
                    continue

                all_empty = all(t == "" for t in region_texts)
                if all_empty and not any(region_is_image):
                    # Live extraction failed (often a degenerate/near-zero-area
                    # quad) - fall back to the pipeline's own recorded text so
                    # we don't silently mis-classify these as "blank".
                    fallback_texts = [normalize_ws(r.get("text", "")) for r in item.get("regions", [])]
                    if any(fallback_texts):
                        region_texts = fallback_texts

                if all(region_texts[i] == "" for i in range(len(region_texts))) and any(region_is_image):
                    verdict, reason = "NEEDS_REVIEW", (
                        "Referenced region(s) contain rasterized/embedded-image content (e.g. a "
                        "pasted calculation table or scanned exhibit) with no selectable text layer - "
                        "PyMuPDF text extraction cannot verify this automatically; check the rendered "
                        "page by eye."
                    )
                else:
                    verdict, reason = classify_item(sheet_number, pkg, item, region_texts)

                # Flag (don't auto-fail) any item whose evidence sits inside a
                # "reference only / previously submitted with [an earlier
                # package]" disclaimer box - the text may be perfectly real,
                # but the scope it represents may already be covered under a
                # prior bid package rather than being new work for this one.
                if disclaimer_zones and verdict == "RIGHT":
                    for reg in item.get("regions", []):
                        if any(region_overlaps_zone(reg["quad_px"], z) for z in disclaimer_zones):
                            verdict = "NEEDS_REVIEW"
                            reason = (
                                reason + " [FLAGGED: this item's evidence region overlaps a "
                                "'reference only / previously submitted' disclaimer box on this "
                                "sheet - the text may be real, but this scope might already be "
                                "covered under an earlier bid package rather than being new work "
                                "for this one. Verify with the project team before pricing.]"
                            )
                            break

                override = MANUAL_OVERRIDES.get((sheet_number, pkg, item.get("bid_item", "")))
                if override:
                    verdict, reason = override
                    reason = "[Manual review] " + reason

                # For items a reviewer needs to go check by eye (WRONG or
                # NEEDS_REVIEW), record the exact quad_px boxes so they can be
                # pointed at directly in the PDF without re-deriving them -
                # kept empty for RIGHT items to keep the file lean.
                quad_boxes = []
                if verdict in ("WRONG", "NEEDS_REVIEW"):
                    quad_boxes = [r["quad_px"] for r in item.get("regions", [])]

                findings.append({
                    "sheet_number": sheet_number,
                    "sheet_name": s.get("sheet_name", ""),
                    "discipline": s.get("discipline", ""),
                    "page_number": page_num,
                    "scope_package": pkg,
                    "bid_item": item.get("bid_item", ""),
                    "subheading": item.get("subheading", ""),
                    "n_regions": len(item.get("regions", [])),
                    "extracted_text_sample": normalize_ws(" | ".join(region_texts))[:400],
                    "json_text_sample": normalize_ws(
                        " | ".join(r.get("text", "") for r in item.get("regions", []))
                    )[:400],
                    "verdict": verdict,
                    "reason": reason,
                    "quad_px_boxes": quad_boxes,
                })

                if verdict == "WRONG":
                    removed_item_keys.append((s_idx, pkg, it_idx))

    with open("findings_raw.json", "w") as f:
        json.dump(findings, f, indent=2)

    print(f"Total findings rows: {len(findings)}")
    print(Counter(f["verdict"] for f in findings))
    print(f"Items marked WRONG (to remove from corrected json): {len(removed_item_keys)}")


if __name__ == "__main__":
    main()
