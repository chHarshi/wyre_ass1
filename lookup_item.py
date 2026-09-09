"""
Quick lookup tool: given a sheet number + bid item name, crop out a
zoomed-in image of exactly where its regions point in the real PDF, so
you can visually check a claim without hunting through the whole page.

Usage:
    python3 lookup_item.py "A-101CD" "Furnish & Install Wood Doors Per Schedule And Drawings"

Coordinates always come from results.json (it has quad_px for every item,
regardless of verdict). If findings_raw.json is present in the same folder,
this also looks up that item's audit verdict + reason and prints it, so you
see the crop and your own prior conclusion together in one step. Missing
findings_raw.json (e.g. on a brand new, not-yet-audited sheet set) is fine -
it just skips that part.

Saves images into an "output" folder (created automatically if it doesn't
exist), named after the sheet, page, and region number - e.g.
output/A-101CD_page5_region1.png - so they're easy to find later instead of
overwriting a generic lookup_1.png each time.
"""
import sys
import os
import re
import json
import fitz


def safe_name(s):
    """Turn arbitrary text into a filesystem-safe filename fragment."""
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')


def load_findings_lookup():
    """Returns {(sheet_number, bid_item): {"verdict":..., "reason":...}} if
    findings_raw.json exists in the current folder, else an empty dict."""
    if not os.path.exists("findings_raw.json"):
        return {}
    with open("findings_raw.json") as f:
        findings = json.load(f)
    return {
        (row["sheet_number"], row["bid_item"]): row
        for row in findings
    }


def main():
    if len(sys.argv) < 3:
        print('Usage: python3 lookup_item.py "<sheet_number>" "<bid_item text>"')
        return

    sheet_number, bid_item_query = sys.argv[1], sys.argv[2]

    out_dir = "output"
    os.makedirs(out_dir, exist_ok=True)

    with open("results.json") as f:
        data = json.load(f)

    findings_lookup = load_findings_lookup()
    doc = fitz.open("drawings.pdf")

    found = False
    for s in data["drawings_processing_results"]:
        if s["sheet_number"] != sheet_number:
            continue
        page_num = s["page_number"]
        page = doc[page_num - 1]
        for pkg, items in s.get("bid_items", {}).items():
            for item in items:
                if bid_item_query.lower() not in item.get("bid_item", "").lower():
                    continue
                found = True
                bid_name = item["bid_item"]

                print(f"\nSheet {sheet_number} (page {page_num}) | Package: {pkg}")
                print(f"Item: {bid_name}")
                print(f"Subheading: {item.get('subheading','')}")
                print(f"Number of regions: {len(item['regions'])}")

                # cross-check against a prior audit run, if one exists
                audit_row = findings_lookup.get((sheet_number, bid_name))
                if audit_row:
                    print(f"Prior verdict: {audit_row['verdict']}")
                    print(f"Prior reason: {audit_row['reason']}")
                else:
                    print("Prior verdict: (none found - not yet audited, or "
                          "findings_raw.json not present in this folder)")

                item_tag = safe_name(bid_name)[:40]

                for i, reg in enumerate(item["regions"]):
                    q = reg["quad_px"]
                    xs, ys = q[0::2], q[1::2]
                    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                    # widen the crop a bit so you see surrounding context
                    pad = 150
                    clip = fitz.Rect(
                        max(0, x0 - pad), max(0, y0 - pad),
                        min(page.rect.width, x1 + pad), min(page.rect.height, y1 + pad),
                    )
                    pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))

                    out_name = f"{safe_name(sheet_number)}_page{page_num}_{item_tag}_region{i+1}.png"
                    out_path = os.path.join(out_dir, out_name)
                    pix.save(out_path)
                    print(f"  region {i+1}: x={x0:.0f}-{x1:.0f}, y={y0:.0f}-{y1:.0f} "
                          f"-> saved {out_path} | recorded text: {reg['text'][:80]}")

    if not found:
        print(f"No item matching '{bid_item_query}' found on sheet '{sheet_number}'.")


if __name__ == "__main__":
    main()