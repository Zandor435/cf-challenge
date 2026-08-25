#!/usr/bin/env python3
"""
persona_schema.py -- the editorial-profile field contract, and its validator.

WHY THIS IS A SEPARATE MODULE FROM sync_personas.py
---------------------------------------------------
sync_personas.py answers one question: "what leaves the repo, and does the
roster reconcile". The editorial profile system added nine new optional fields
with real internal structure (nested dicts, enum'd layout keys, hex colors,
per-module blocks), and validating those inline would have buried the roster
reconciliation it already does well. Splitting also gives the rules a home a
test can import without running a publish.

THE DIVIDING LINE THIS FILE MAINTAINS -- and it is the load-bearing one:

  PUBLISHED    fields the page paints. Listed in PROFILE_SITE_FIELDS, appended
               to sync_personas.SITE_FIELDS.
  PRIVATE      art direction -- the creative brief that feeds the image
               prompt-writer. Listed in PRIVATE_FIELDS and deliberately NEVER
               written to docs/. Same posture the repo already had for
               `traits` / `silhouette_cue`: internal art direction with no
               surface on the page has no business being served to a browser.

`north_star`, `motifs` and `easter_eggs` are the creative brief. They exist so
a human -- or, later, generate_persona_batch.py -- has a stated intent to
generate against. They are not page copy and they do not ship.

EVERY NEW FIELD IS OPTIONAL. That is not politeness: 2 of the 24 managers
across the four leagues have no persona content at all, and 12 more have only
the four original prose fields. The page has to compose gracefully around every
absent field, so the schema has to permit every absence. What is NOT permitted
is a field that is PRESENT and MALFORMED -- an unknown layout key, a bad hex, a
module block with a typo'd sub-key. Those fail the build and name the offender,
per the playbook's rule 4: a silent creative-data miss renders a broken page,
which is exactly as bad as silently dropping points.

`modules` ONLY EVER ENRICHES A FLAT FIELD -- it carries the label, headline and
art slot, never the body prose, which stays in the flat field. A module block
authored over an empty flat field is therefore a headline with nothing under
it, and that is a hard failure here rather than a page that renders "Special
Teams PTSD" over blank space. This used to matter twice over, because the tone
registers could null a flat field out from under its module block; the
registers were retired on 2026-08-25 and the paired stripper with them, so the
build-time check below is now the only thing standing between an authored
module and an empty body.
"""

import re

# ---------------------------------------------------------------------------
# The layout variants. A `layout` value outside this set is a hard failure
# rather than a fallback: a typo'd variant silently rendering as SIDELINE is
# how a profile ships in the wrong composition and nobody notices for a month.
# Absent (or null) IS allowed and means DEFAULT_LAYOUT -- "unspecified" and
# "misspelled" are genuinely different states and are treated differently.
#
#   sideline   portrait left, editorial right. The Blaine reference. Wants a
#              tall portrait; the strongest variant when real art exists.
#   headliner  full-bleed portrait, name overlapping the image edge, one wide
#              measure beneath. Wants art with headroom at the top.
#   dossier    scouting-sheet orientation -- ruled header, facts as a wide
#              table, picks promoted. THE VARIANT THAT NEEDS NO ART AT ALL,
#              and therefore the right choice for a manager who has none.
#   program    vintage game-program -- centred nameplate, symmetric rules,
#              portrait in a framed box. Wants a squarer crop.
LAYOUTS = ("sideline", "headliner", "dossier", "program")
DEFAULT_LAYOUT = "sideline"

# The four blocks `modules` may enrich. Keyed by the flat field they decorate:
# there is no module key that does not correspond to a real prose field, which
# is what makes "authored module, empty body" a decidable check.
MODULE_KEYS = ("draft_tendency", "fatal_flaw", "running_gag", "rival")
MODULE_SUBKEYS = ("label", "headline", "art")

DOSSIER_KEYS = ("role", "nicknames", "known_for", "hometown", "college",
                "drafted", "status")
THEME_KEYS = ("accent", "accent_secondary", "paper", "ink")
ASSET_KEYS = ("hero", "nameplate", "signature", "badge", "spots")
QUOTE_KEYS = ("text", "attribution")
FOOTER_KEYS = ("left", "right")

# Published: painted by the page.
PROFILE_SITE_FIELDS = (
    "archetype",
    "thesis",
    "dossier",
    "modules",
    "pull_quote",
    "footer",
    "theme",
    "layout",
    "assets",
)

# Private: the creative brief. Never published. See the module docstring.
PRIVATE_FIELDS = (
    "north_star",
    "motifs",
    "easter_eggs",
)

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class PersonaSchemaError(ValueError):
    """Raised with a message that names the group, the manager and the field."""


def _err(group_id, mid, msg):
    raise PersonaSchemaError("FAIL [{g}/{m}]: {msg}".format(g=group_id, m=mid, msg=msg))


def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _check_dict(group_id, mid, field, value, allowed):
    """A present dict field must be a dict and may only carry known keys.

    An unknown key is a failure, not an ignore. Every one seen in practice has
    been a typo (`nickname` for `nicknames`, `accent2` for `accent_secondary`),
    and the ignore-path renders that as an absent field -- a block quietly
    missing from an otherwise finished-looking page.
    """
    if not isinstance(value, dict):
        _err(group_id, mid, "{f} must be an object, got {t}".format(
            f=field, t=type(value).__name__))
    unknown = sorted(k for k in value if k not in allowed)
    if unknown:
        _err(group_id, mid, "{f} has unknown key(s) {u}. Allowed: {a}".format(
            f=field, u=", ".join(repr(k) for k in unknown), a=", ".join(allowed)))


def validate_manager(group_id, mid, src):
    """Validate the editorial-profile fields on ONE manager's source record.

    Absent and null are always fine. Present-and-malformed always fails.
    Returns None; raises PersonaSchemaError naming the offender.
    """
    # --- layout ------------------------------------------------------------
    layout = src.get("layout")
    if layout is not None:
        if not _is_str(layout) or layout not in LAYOUTS:
            _err(group_id, mid,
                 "layout is {l!r}; must be one of {v} (or absent for {d!r}). "
                 "A misspelled variant would silently render as the default, "
                 "which is how a profile ships in the wrong composition."
                 .format(l=layout, v=LAYOUTS, d=DEFAULT_LAYOUT))

    # --- simple strings ----------------------------------------------------
    for f in ("archetype", "thesis"):
        v = src.get(f)
        if v is not None and not _is_str(v):
            _err(group_id, mid, "{f} must be a non-empty string or null".format(f=f))

    # --- theme -------------------------------------------------------------
    theme = src.get("theme")
    if theme is not None:
        _check_dict(group_id, mid, "theme", theme, THEME_KEYS)
        for k in sorted(theme):
            v = theme[k]
            if v is None:
                continue
            if not _is_str(v) or not HEX_RE.match(v):
                _err(group_id, mid,
                     "theme.{k} is {v!r}; must be a #rgb or #rrggbb hex string. "
                     "It is injected straight into a CSS custom property, so an "
                     "unparseable value silently drops the whole declaration and "
                     "the profile renders in the fallback accent."
                     .format(k=k, v=v))

    # --- dossier -----------------------------------------------------------
    dossier = src.get("dossier")
    if dossier is not None:
        _check_dict(group_id, mid, "dossier", dossier, DOSSIER_KEYS)
        nick = dossier.get("nicknames")
        if nick is not None:
            if not isinstance(nick, list) or not all(_is_str(n) for n in nick):
                _err(group_id, mid,
                     "dossier.nicknames must be a list of non-empty strings")
        for k in DOSSIER_KEYS:
            if k == "nicknames":
                continue
            v = dossier.get(k)
            if v is not None and not _is_str(v):
                _err(group_id, mid,
                     "dossier.{k} must be a non-empty string or null".format(k=k))

    # --- modules -----------------------------------------------------------
    modules = src.get("modules")
    if modules is not None:
        _check_dict(group_id, mid, "modules", modules, MODULE_KEYS)
        for k in sorted(modules):
            block = modules[k]
            if block is None:
                continue
            _check_dict(group_id, mid, "modules." + k, block, MODULE_SUBKEYS)
            for sk in MODULE_SUBKEYS:
                v = block.get(sk)
                if v is not None and not _is_str(v):
                    _err(group_id, mid,
                         "modules.{k}.{sk} must be a non-empty string or null"
                         .format(k=k, sk=sk))
            # A module decorating a flat field that does not exist is a
            # headline with no body. The page would render the label and the
            # headline over empty space; better to say so at build time.
            if not _is_str(src.get(k)):
                _err(group_id, mid,
                     "modules.{k} is authored but the flat `{k}` field it decorates is "
                     "empty. `modules` carries the label/headline/art only -- the body "
                     "prose stays in the flat field, so this would render a headline "
                     "with nothing under it.".format(k=k))

    # --- pull_quote --------------------------------------------------------
    quote = src.get("pull_quote")
    if quote is not None:
        _check_dict(group_id, mid, "pull_quote", quote, QUOTE_KEYS)
        if not _is_str(quote.get("text")):
            _err(group_id, mid, "pull_quote.text is required when pull_quote is present")
        attr = quote.get("attribution")
        if attr is not None and not _is_str(attr):
            _err(group_id, mid,
                 "pull_quote.attribution must be a non-empty string or null")

    # --- footer ------------------------------------------------------------
    # Both halves or neither. The band is a two-part construction -- "AGITATES
    # BY DAY. / OVERTHINKS BY NIGHT." -- and one half alone is not a shorter
    # version of it, it is a broken one.
    footer = src.get("footer")
    if footer is not None:
        _check_dict(group_id, mid, "footer", footer, FOOTER_KEYS)
        have = [k for k in FOOTER_KEYS if _is_str(footer.get(k))]
        if len(have) != len(FOOTER_KEYS):
            _err(group_id, mid,
                 "footer needs BOTH `left` and `right` (got {h}). The closing band is a "
                 "two-part slogan; half of one reads as a rendering fault, not as brevity."
                 .format(h=", ".join(have) if have else "neither"))

    # --- assets ------------------------------------------------------------
    assets = src.get("assets")
    if assets is not None:
        _check_dict(group_id, mid, "assets", assets, ASSET_KEYS)
        spots = assets.get("spots")
        if spots is not None:
            if not isinstance(spots, list) or not all(_is_str(s) for s in spots):
                _err(group_id, mid,
                     "assets.spots must be a list of non-empty path strings")
        for k in ASSET_KEYS:
            if k == "spots":
                continue
            v = assets.get(k)
            if v is not None and not _is_str(v):
                _err(group_id, mid,
                     "assets.{k} must be a docs-relative path string or null".format(k=k))

    # --- private creative brief -------------------------------------------
    for f in ("motifs", "easter_eggs"):
        v = src.get(f)
        if v is not None and (not isinstance(v, list) or not all(_is_str(x) for x in v)):
            _err(group_id, mid, "{f} must be a list of non-empty strings or null".format(f=f))
    ns = src.get("north_star")
    if ns is not None and not _is_str(ns):
        _err(group_id, mid, "north_star must be a non-empty string or null")

