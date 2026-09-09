#!/usr/bin/env python3
"""
render.py — Email HTML rendering (ARCHITECTURE §8 CLONE, build order §10.7).

Job: turn the build_email_payload.py payload into the weekly email's HTML —
the two-board summary (delta standings + labeled projection) plus pundit
garnish, in the Geckoboard tiled-grid design language.

One place renders email/template.html so send_email.py and preview.py stay in
lockstep — same template, same environment, same autoescaping. The template is
pure presentation: every value it prints was resolved upstream by
build_email_payload.py and arrives as `p`. If you find yourself wanting an `{% if
x > y %}` in the template, the comparison belongs in the payload builder.

Autoescaping is ON and must stay on. The column paragraphs are model-generated
prose containing team names, nicknames and quotes; an unescaped `&` or `<` there
would corrupt the markup in some clients and silently truncate the email in
others.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "email"
TEMPLATE_NAME = "template.html"


def render_html(payload: dict, template_name: str = TEMPLATE_NAME) -> str:
    """Render the email template with `payload`, returning the HTML string."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(template_name).render(p=payload)
