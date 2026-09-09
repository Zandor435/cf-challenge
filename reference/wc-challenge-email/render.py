#!/usr/bin/env python3
"""Shared Jinja2 render helper for the WC Challenge email.

One place renders email/template.html so send_email.py and preview.py stay in
lockstep (DRY — same template, same environment, same autoescaping). The template
is pure presentation; everything it needs is in the payload dict passed as `p`.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_NAME = "template.html"


def render_html(payload: dict) -> str:
    """Render template.html with the payload, returning the email HTML string."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(HERE),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(TEMPLATE_NAME)
    return template.render(p=payload)
