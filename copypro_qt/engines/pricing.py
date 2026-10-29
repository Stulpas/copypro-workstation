"""Print-counter pricing. Lithuanian names and tier rules stay in the Excel data."""

from __future__ import annotations

from copypro_qt.legacy import ct


def parse_entry(text):
    return ct.PrintCounterTab._parse_entry(None, text)


def apply_discount(amount, discount):
    return ct.PrintCounterTab._apply_discount(amount, discount)


def search_catalog(query, limit=12):
    host = ct.PrintCounterTab.__new__(ct.PrintCounterTab)
    return ct.PrintCounterTab._search_catalog(host, query, limit)


def price_lines(entries, line_discounts, global_discounts):
    totals = {}
    for text in entries:
        parsed = parse_entry(text)
        if not parsed:
            continue
        amount, code = parsed
        totals[code] = totals.get(code, 0.0) + amount
    rows = []
    subtotal = 0.0
    missing = 0
    for code, qty in sorted(totals.items()):
        output = ct.resolve_output_code(code, qty)
        price = ct.get_plu_price(output)
        original = None if price is None else qty * price
        final = original
        discount_text = "—"
        if original is not None and code in line_discounts:
            final = apply_discount(original, line_discounts[code])
            kind, value = line_discounts[code]
            discount_text = f"{value:g}%" if kind == "percent" else f"€{value:.2f}"
        if final is None:
            missing += 1
        else:
            subtotal += final
        rows.append({
            "code": code,
            "qty": qty,
            "output": output,
            "description": ct.get_plu_description(output) or "Unknown code",
            "discount": discount_text,
            "total": final,
        })
    grand = subtotal
    for discount in global_discounts:
        grand = apply_discount(grand, discount)
    return {"rows": rows, "subtotal": subtotal, "grand": grand, "missing": missing}


def wide_format_lines(amount, coverage):
    ct.reload_editable_data()
    lines = []
    for code in ct.WIDE_FORMAT_ORDER:
        item = ct.WIDE_FORMAT_ITEMS.get(code)
        if not item:
            continue
        description, price, item_coverage, label = item
        searchable = f"{description} {label}".lower()
        if "vatman" in searchable or code in {"3008", "3009"}:
            continue
        include = item_coverage in (coverage, "fixed") or (item_coverage == "drawing" and coverage == "partial")
        if include:
            lines.append(f"{label or description} - {amount * price:.2f} Eur")
    return lines
