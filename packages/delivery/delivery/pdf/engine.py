"""Dispatch: fill AcroForm fields via pypdf where they exist, else a reportlab
overlay against the form's coordinate map, else a from-scratch generated
letter. See `forms.py` for why each of the four shipped forms takes the path
it takes.

`pypdf` and `reportlab` are imported lazily (inside the functions that need
them) rather than at module scope. This is not a style nit: `packages/rules`
and `pyproject.toml`'s `pythonpath` put this whole package on the collection
path for every persona's `pytest` run, and CI's base install is
`ruff+pytest+pytest-cov` only (see the PR's HANDOFF re: `requirements.txt`).
A top-level `import pypdf` here would turn "form-filling library not
installed in this environment" into "the entire test suite fails to
collect" for everyone, not just RELAY.
"""

from __future__ import annotations

import contextlib
import io

from .forms import FORM_REGISTRY, PAGE_H, PAGE_W, advocate_hospital_checkbox_field
from .letters import render_debt_validation_letter, render_records_request_letter

_GENERATORS = {
    "debt_validation_letter": render_debt_validation_letter,
    "records_request_letter": render_records_request_letter,
}


def fill_form(form_id: str, case: dict, extra: dict | None = None) -> bytes:
    """Render `form_id` for `case`. Returns the filled PDF's bytes.

    `case` follows the §3.1 `cases/{case_id}` shape. `extra` carries anything
    the case document doesn't (hospital address, PPDR yes/no answers already
    decided by STATUTE's rules engine, the filing date, an account number
    minted for this filing) -- see each `FormSpec`'s field-builder functions
    in `forms.py` for exactly what it reads from `extra`.
    """
    spec = FORM_REGISTRY.get(form_id)
    if spec is None:
        raise ValueError(f"unknown form_id {form_id!r}; have: {sorted(FORM_REGISTRY)}")
    extra = extra or {}

    if spec.method == "acroform":
        return _fill_acroform(spec, case, extra)
    if spec.method == "overlay":
        return _fill_overlay(spec, case, extra)
    if spec.method == "generated":
        generator = _GENERATORS.get(form_id)
        if generator is None:
            raise ValueError(f"no generator registered for generated form {form_id!r}")
        return generator(case, extra)
    raise ValueError(f"unknown fill method {spec.method!r} for {form_id!r}")


def _template_path(spec) -> str:
    from .forms import TEMPLATES_DIR

    if spec.template is None:
        raise ValueError(f"{spec.form_id!r} has method={spec.method!r} but no template")
    path = TEMPLATES_DIR / spec.template
    if not path.exists():
        raise FileNotFoundError(
            f"template for {spec.form_id!r} not found at {path} -- was it committed to "
            "packages/delivery/delivery/pdf/templates/?"
        )
    return str(path)


def _fill_acroform(spec, case: dict, extra: dict) -> bytes:
    import pypdf

    reader = pypdf.PdfReader(_template_path(spec))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    # Without NeedAppearances, some PDF viewers show the OLD (blank) glyph
    # even though the field value is set correctly in the file -- the value
    # is right but invisible until the viewer regenerates the appearance
    # stream. Verified against this exact template in the RELAY PR.
    with contextlib.suppress(Exception):  # cosmetic fallback only, never fatal
        writer.set_need_appearances_writer(True)

    values: dict[str, str] = {}
    for f in spec.acro_fields:
        v = f.value_fn(case, extra)
        if v:
            values[f.pdf_field] = v
    for page in writer.pages:
        writer.update_page_form_field_values(page, values)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _fill_overlay(spec, case: dict, extra: dict) -> bytes:
    import pypdf
    from reportlab.pdfgen import canvas

    reader = pypdf.PdfReader(_template_path(spec))
    n_pages = len(reader.pages)

    fields = list(spec.overlay_fields)
    hospital_name = extra.get("hospital_facility")
    if hospital_name and spec.form_id == "advocate_fap":
        extra_field = advocate_hospital_checkbox_field(hospital_name)
        if extra_field is not None:
            fields.append(extra_field)

    by_page: dict[int, list] = {i: [] for i in range(n_pages)}
    for f in fields:
        if 0 <= f.page < n_pages:
            by_page[f.page].append(f)

    overlay_buf = io.BytesIO()
    c = canvas.Canvas(overlay_buf, pagesize=(PAGE_W, PAGE_H))
    for i in range(n_pages):
        for f in by_page[i]:
            value = f.value_fn(case, extra)
            if value:
                c.setFont(f.font, f.size)
                c.drawString(f.x, f.y, str(value))
        c.showPage()
    c.save()
    overlay_buf.seek(0)
    overlay_reader = pypdf.PdfReader(overlay_buf)

    writer = pypdf.PdfWriter()
    writer.append(reader)
    for i in range(n_pages):
        writer.pages[i].merge_page(overlay_reader.pages[i])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
