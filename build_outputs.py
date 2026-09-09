"""
Produces the two data deliverables from findings_raw.json + results.json:
  1. results_corrected.json - same structure as the input, with WRONG items
     removed from bid_items (packages that become empty are dropped too).
  2. findings.csv / findings.xlsx - one row per audited item.

Reference sheets (G-002.3, G-501, G-511) are untouched and passed through
as-is, since they were excluded from the audit per the assignment.
"""
import json
import copy
import csv

with open("results.json") as f:
    data = json.load(f)

with open("findings_raw.json") as f:
    findings = json.load(f)

# Build a lookup of exactly which (sheet_number, scope_package, bid_item) are WRONG.
# bid_item text is unique enough within a sheet+package in this dataset; we
# match on all three to be safe.
wrong_keys = {
    (x["sheet_number"], x["scope_package"], x["bid_item"])
    for x in findings if x["verdict"] == "WRONG"
}

corrected = copy.deepcopy(data)
removed_log = []

for s in corrected["drawings_processing_results"]:
    sheet_number = s["sheet_number"]
    bid_items = s.get("bid_items", {})
    new_bid_items = {}
    for pkg, items in bid_items.items():
        kept = []
        for item in items:
            key = (sheet_number, pkg, item.get("bid_item", ""))
            if key in wrong_keys:
                removed_log.append(key)
                continue
            kept.append(item)
        if kept:  # drop the package entirely if every item under it was removed
            new_bid_items[pkg] = kept
    s["bid_items"] = new_bid_items

with open("results_corrected.json", "w") as f:
    json.dump(corrected, f, indent=2)

print(f"Removed {len(removed_log)} WRONG items from corrected JSON "
      f"(requested {len(wrong_keys)} unique removals).")

# --- CSV findings export ---
fieldnames = [
    "sheet_number", "sheet_name", "discipline", "page_number",
    "scope_package", "bid_item", "subheading", "n_regions", "verdict",
    "reason", "extracted_text_sample", "json_text_sample", "quad_px_boxes",
]
with open("findings.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for row in findings:
        r = dict(row)
        # flatten the list-of-lists into a compact string that still opens
        # cleanly in Excel/CSV (one region's box per semicolon-separated group)
        boxes = r.get("quad_px_boxes") or []
        r["quad_px_boxes"] = "; ".join(
            ",".join(f"{n:.0f}" for n in box) for box in boxes
        )
        w.writerow({k: r.get(k, "") for k in fieldnames})

print(f"Wrote findings.csv with {len(findings)} rows.")
