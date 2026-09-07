"""
Utilities to pull ground-truth text out of drawings.pdf for a given quad_px
region, correctly handling pages that are stored rotated (/Rotate 270).

quad_px is always given in "as viewed" 3024x2160 space (per the sheet's
page_width/page_height). For rotated pages, PyMuPDF's raw word coordinates
are in the *unrotated* mediabox space, so we transform each word's bbox
into "as viewed" space using page.rotation_matrix before comparing against
quad_px, rather than trying to inverse-transform the clip rectangle (which
doesn't work cleanly for axis-aligned rects under a 90-degree rotation).
"""
import fitz  # PyMuPDF


def quad_to_rect(quad_px):
    xs = quad_px[0::2]
    ys = quad_px[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def rect_overlap_area(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ox0, oy0 = max(ax0, bx0), max(ay0, by0)
    ox1, oy1 = min(ax1, bx1), min(ay1, by1)
    if ox1 <= ox0 or oy1 <= oy0:
        return 0.0
    return (ox1 - ox0) * (oy1 - oy0)


def rect_area(a):
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


class PageTextIndex:
    """Pre-computes word boxes in 'as viewed' (quad_px) coordinate space
    for one page, so region lookups are fast."""

    def __init__(self, page):
        self.page = page
        self.rotation = page.rotation
        words = page.get_text("words")  # (x0,y0,x1,y1,text,block,line,word)
        m = page.rotation_matrix if page.rotation else fitz.Matrix(1, 0, 0, 1, 0, 0)
        out = []
        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            if page.rotation:
                p0 = fitz.Point(x0, y0) * m
                p1 = fitz.Point(x1, y1) * m
                rx0, rx1 = sorted([p0.x, p1.x])
                ry0, ry1 = sorted([p0.y, p1.y])
            else:
                rx0, ry0, rx1, ry1 = x0, y0, x1, y1
            out.append((rx0, ry0, rx1, ry1, text, w[5], w[6], w[7]))
        self.words = out

    def text_in_quad(self, quad_px, pad=2.0, min_overlap_frac=0.4):
        """Return the words (in reading order: block,line,word) whose boxes
        substantially overlap the given quad's bounding rect."""
        rx0, ry0, rx1, ry1 = quad_to_rect(quad_px)
        # Some regions come through as degenerate (zero-height/width) rects -
        # e.g. a table-header label drawn along a single baseline. Give those
        # a generous pad so we still catch the actual glyphs sitting on/near
        # that line rather than reporting a false "empty" region.
        if (rx1 - rx0) < 5 or (ry1 - ry0) < 5:
            pad = max(pad, 14.0)
        rx0 -= pad; ry0 -= pad; rx1 += pad; ry1 += pad
        target = (rx0, ry0, rx1, ry1)
        hits = []
        for (x0, y0, x1, y1, text, blk, line, wno) in self.words:
            wa = rect_area((x0, y0, x1, y1))
            if wa <= 0:
                continue
            ov = rect_overlap_area((x0, y0, x1, y1), target)
            if ov / wa >= min_overlap_frac:
                hits.append((blk, line, wno, x0, y0, text))
        hits.sort(key=lambda h: (h[0], h[1], h[2]))
        return " ".join(h[5] for h in hits)


def build_page_indexes(pdf_path):
    doc = fitz.open(pdf_path)
    return doc, {i: PageTextIndex(doc[i]) for i in range(len(doc))}
