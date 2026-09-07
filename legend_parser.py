"""
Parse the legend sheets (G-501 vertical assemblies, G-511 horizontal assemblies)
to build a lookup of assembly-tag -> category, so we can check whether a bid
item's claimed scope matches what the tag actually designates on the legend.

This is deliberately lightweight: it looks for the "WALL TYPE:", "ROOF TYPE:",
"CEILING TYPE:", "FLOOR TYPE:" callouts that precede each assembly block on
G-501 / G-511 (visible directly in the sheet text) and records the tag plus
a rough material category based on nearby keywords.
"""
import re

WALL_TAGS = {
    # tag -> (category, note)
    "C#": ("concrete", "cast-in-place concrete wall (C8/C10/C12/C32)"),
    "BR1": ("exterior-finish", "scheduled wall, brick veneer finish note only"),
    "FB1": ("exterior-finish", "scheduled wall, fiber board finish note only"),
    "FB2": ("exterior-finish", "scheduled wall, fiber board (vertical) finish note only"),
    "W4": ("wood-stud-interior", "2x wood stud, 5/8in type X gyp both sides, N/A rating"),
    "W4G": ("wood-stud-interior", "2x wood stud, 5/8in type X gyp, similar to W4"),
    "W6": ("wood-stud-interior", "2x wood stud wider, 5/8in type X gyp"),
    "W6G": ("wood-stud-interior", "2x wood stud, 5/8in type X gyp"),
    "W6SG": ("wood-stud-interior", "2x wood stud w/ plywood sheathing one side"),
    "W40": ("wood-stud-interior-rated", "1/2 hr rated wood stud wall, UL U305"),
    "W60A": ("wood-stud-unit-demising", "1/2 hr rated, UL U305, unit-to-unit"),
    "WD40A": ("wood-stud-demising-rated", "1/2 hr rated, UL U341, unit demising"),
    "WD42A": ("wood-stud-demising-rated", "2 hr rated, UL V324, unit demising, double layer gyp"),
    "XW6": ("wood-stud-exterior", "2x6 wood stud exterior wall, sheathing + WRB"),
    "XW6.0": ("wood-stud-exterior", "2x wood stud exterior, plywood shear panel"),
    "XW6.1": ("wood-stud-exterior", "2x6 wood stud exterior"),
    "XW6.2": ("wood-stud-exterior", "2x6 wood stud exterior"),
    "XW60": ("wood-stud-exterior-rated", "1/2 hr rated exterior wall, UL U305"),
    "XW62": ("wood-stud-exterior-rated", "2 hr rated exterior wall, double layer gyp"),
}

ROOF_TAGS = {"RW4": ("roof-asphalt-shingle", "asphalt shingle over plywood, wood truss")}

CEILING_TAGS = {
    "CW4.1": ("ceiling-gyp", "1/2 hr floor-ceiling, gyp board"),
    "CG1.11": ("ceiling-gyp", "1/2 hr floor-ceiling, moisture resistant gyp"),
    "CG1.12": ("ceiling-gyp", "1/2 hr floor-ceiling, gyp board"),
    "CG1.13": ("ceiling-gyp-roof", "1/2 hr roof-ceiling, UL P522, attic, batt+loose fill insul"),
    "CG1.14": ("ceiling-gyp", "1/2 hr floor-ceiling, glass mat sheathing"),
}

FLOOR_TAGS = {
    "FG5.1": ("floor-slab-on-grade", "cast-in-place slab on grade"),
    "FW18": ("floor-wood-truss", "1/2 hr UL L501, wood floor truss, gypcrete"),
    "FW18.1": ("floor-wood-truss-roof-recess", "wood floor truss w/ shower recess, waterproofing"),
    "FW22": ("floor-wood-truss", "1/2 hr UL L501, wood floor truss, resilient channel"),
}

# grid-bubble-like tokens seen throughout plan/roof/elevation sheets:
# single or double letter optionally followed by 1 digit, used for column/row
# gridlines (A1, AX, AV, AZ, E1...E5, B1...C4, F1...F3 etc.)
GRID_TOKEN_RE = re.compile(r'^([A-Z]{1,2}[0-9]?|\d{1,3}(\.\d+)?%)$')

# Common room-finish shorthand codes seen on this project's finish plans
# (e.g. "C-1" carpet, "T-2" tile, "RB-1" rubber base) -> keywords they support.
FINISH_CODE_KEYWORDS = {
    r'^C-\d+$': {"carpet"},
    r'^CB-\d+$': {"carpet", "base"},
    r'^RB-\d+(/RB-\d+)?$': {"rubber", "base"},
    r'^T-\d+$': {"tile", "ceramic", "porcelain"},
    r'^TB-\d+$': {"tile", "base"},
    r'^LVT-\d+$': {"luxury", "vinyl", "tile", "resilient"},
    r'^P-\d+$': {"paint", "painting"},
    r'^EP-\d+$': {"epoxy", "paint", "painting", "coating"},
    r'^EF-\d+$': {"resinous", "flooring", "epoxy"},
    r'^GB-\d+$': {"gypsum", "board"},
    r'^PL-\d+$': {"plastic", "laminate", "casework"},
    r'^SS-\d+$': {"stainless", "steel"},
    r'^CG-\d+$': {"corner", "guard", "guards"},
    r'^WS\d+$': {"shade", "window", "treatment"},
}

# Civil/site-utility and landscape plan symbol abbreviations (per the legends
# on CU300/CU320/CG452/LP101) -> keywords they support when they appear
# verbatim on those plans.
SITE_CODE_KEYWORDS = {
    r"\bFH\b": {"fire", "hydrant", "hydrants"},
    r"\bPB\b": {"pull", "box", "boxes", "electrical"},
    r"\bFO\b": {"optical", "fiber", "cabling"},
    r"\bIRR\b": {"irrigation"},
    r"\bSDMH[-\d]*\b": {"storm", "drain", "manholes", "manhole"},
    r"\bCIB[-\d]*\b": {"catch", "basins", "basin", "inlet", "boxes"},
    r"\bADS[-\d]*\b": {"area", "drains", "drain"},
    r"\d+''SS\b": {"sanitary", "sewer", "pipe"},
    r"\bSS\b": {"sanitary", "sewer"},
    r"\d+''SD\b": {"storm", "drain", "pipe"},
    r"\bSD[-\d]*\b": {"storm", "drain"},
    r"\d+''W\b": {"water", "pipe"},
    r"\bEM\b": {"electrical", "manholes", "manhole"},
    r"\bFF\s*=": {"survey", "layout", "grading", "elevation", "elevations"},
    r"\bEXISTING GRADE\b": {"grading", "grade"},
    r"\bFINISH GRADE\b": {"grading", "grade"},
}

# Botanical name pattern used throughout the plant schedule (Latin name in
# quotes, slash, common name) -> if present, supports Trees/Shrubs/Ground
# Cover & Perennials items even though the literal words "tree"/"shrub"
# don't appear in the region text itself.
BOTANICAL_RE = re.compile(r"[A-Z][a-z]+ [a-z]+.*?/.*", re.MULTILINE)

# tokens allowed inside an otherwise "grid bubble" region without breaking
# the classification (roof slope callouts routinely sit next to grid lines)
GRID_CONTEXT_EXTRA_RE = re.compile(r'^(\d{1,3}(\.\d+)?%|SLOPE|TYP\.?|EQ)$', re.IGNORECASE)

DIMENSION_TOKEN_RE = re.compile(
    r'^(\d+([\'"]|\s*-\s*\d+(\s\d+/\d+)?["\']?)?|\d+/\d+["\']?|TYP\.?|VARIES|EQ|CLR|MIN\.?|MAX\.?)$',
    re.IGNORECASE,
)

TITLE_BLOCK_PHRASES = [
    "NEXUS PROJ", "CHECKED BY", "DRAWN BY", "DATE:", "AGENCY APPROVAL",
    "ARCHITECTURAL NEXUS", "SCALE 1", "BP3 - 100% CD", "REVIEW SET",
    "NOT FOR", "LICENSED", "PROFESSIONAL ENGINEER",
    "801.924.5000", "EAST PARLEYS WAY", "SALT LAKE CITY, UTAH",
    "ARCHNEXUS.COM",
]

ROOM_NAME_WORDS = {
    "BEDROOM", "PRIMARY BEDROOM", "BATH", "KITCHEN", "LIVING", "LAUNDRY",
    "HALL", "CLOSET", "MECH", "LINEN CLOSET", "MECHANICAL ROOM", "UNIT",
    "BREEZEWAY", "IDF",
}


_ALL_TAG_KEYS = set(WALL_TAGS) | set(ROOF_TAGS) | set(CEILING_TAGS) | set(FLOOR_TAGS)

# Short 1-2 letter civil/site-utility symbols that would otherwise look like
# grid bubbles but are legitimate plan symbols (see SITE_CODE_KEYWORDS above).
_SITE_SYMBOL_TOKENS = {"FH", "PB", "FO", "IRR", "SS", "SD", "EM", "ADS", "CIB", "SDMH", "DS", "G"}
_EXCLUDED_FROM_GRID = _ALL_TAG_KEYS | _SITE_SYMBOL_TOKENS


def is_grid_bubble_text(text):
    tokens = [t.strip('.,') for t in text.split() if t.strip('.,')]
    if not tokens:
        return False
    # single bare grid-style token (A4, AX, E1...) - still a grid bubble,
    # unless it's actually a real assembly tag / site symbol from a legend
    if len(tokens) == 1:
        t = tokens[0]
        return bool(re.match(r'^[A-Z]{1,2}[0-9]?$', t)) and t not in _EXCLUDED_FROM_GRID
    real_grid = [t for t in tokens if re.match(r'^[A-Z]{1,2}[0-9]?$', t) and t not in _EXCLUDED_FROM_GRID]
    if len(real_grid) < 2:
        return False
    return all(
        (re.match(r'^[A-Z]{1,2}[0-9]?$', t) and t not in _EXCLUDED_FROM_GRID) or GRID_CONTEXT_EXTRA_RE.match(t)
        for t in tokens
    )


def finish_code_keywords(text):
    """Return the set of keywords supported by any recognizable finish-schedule
    shorthand codes (C-1, RB-1, T-2, etc.) found in the text."""
    out = set()
    for tok in re.findall(r'[A-Za-z]{1,4}-\d{1,2}(?:/[A-Za-z]{1,4}-\d{1,2})?', text):
        for pattern, kws in FINISH_CODE_KEYWORDS.items():
            if re.match(pattern, tok.upper()):
                out |= kws
    return out


def site_code_keywords(text):
    """Return keywords supported by civil/site-utility plan symbol codes
    (FH, SD, SS, W pipe callouts, SDMH, CIB, ADS, EM, PB, FO, IRR, FF=...)."""
    out = set()
    up = text.upper()
    for pattern, kws in SITE_CODE_KEYWORDS.items():
        if re.search(pattern, up):
            out |= kws
    return out


def has_botanical_name(text):
    if "/" not in text:
        return False
    words = re.findall(r"[A-Za-z']+", text)
    caps_words = [w for w in words if w.isupper() and len(w) > 2]
    return len(caps_words) >= 4


def is_dimension_string(text):
    tokens = [t.strip(',') for t in text.split() if t.strip(',')]
    if not tokens:
        return False
    matches = sum(1 for t in tokens if DIMENSION_TOKEN_RE.match(t))
    return matches / len(tokens) >= 0.7


def is_title_block_text(text):
    up = text.upper()
    return any(p in up for p in TITLE_BLOCK_PHRASES)


def is_bare_room_label(text):
    up = text.upper().strip()
    # strip trailing/leading single-letter/number room ids e.g. "LIVING A", "BEDROOM 118"
    words = up.split()
    core = [w for w in words if not re.match(r'^[A-Z]$', w) and not re.match(r'^\d+$', w)]
    core_str = " ".join(core)
    return core_str in ROOM_NAME_WORDS or any(core_str == r for r in ROOM_NAME_WORDS)
