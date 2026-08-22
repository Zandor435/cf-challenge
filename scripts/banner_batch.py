#!/usr/bin/env python3
"""Banner COMPOSITIONS -- part 2 of five. Pure data, no side effects.

Mirrors scene_batch.BATCH, which is the pattern this repo already got right:
composition lives in its own file, style is a separate fragment, and the
generator concatenates them.

Each entry:
  slug    the composition's name. Output is
          output/banners/<group>/<group>_<style_id>_<nn>.png -- the slug does
          NOT appear in the filename, because a composition is chosen per run
          and the style is what you are comparing across runs.
  aspect  default aspect for this composition; --aspect overrides.
  text    the composition itself: how many, how arranged, where. Takes {n}.
          NO style clause, NO wardrobe clause, NO locks -- the generator
          appends all three.

WHY THIS FILE EXISTS. generate_banners.STYLES fuses four concerns into one
string. `comic` carries "dramatic low-angle heroic framing" and "stadium and
floodlights in the background"; `vintage70s` carries "posed stiffly in a row on
a practice field ... hands on hips". So switching style also switched pose and
setting, and "the same picture in a different medium" could not be expressed.

It also caused a real failure. `comic` says "in the style of a classic
SUPERHERO comic" -- a subject word inside a style string. On
output/banners/browns/browns_comic_01.png it leaked into the subject: two of
the five men came back as muscled superheroes in armor, slimmed, beating
BUILD_LOCK. Splitting the axes is what prevents that class of bug.

The four STYLES entries stay frozen in generate_banners.py -- they produced the
approved panel art and browns_comic_02. This is the decomposed path beside
them, opted into with --composition.
"""

# The two compositions the four legacy STYLES collapse to:
#   comic / tradingcard / boxart  -> "lineup"   (heroic, stadium, together)
#   vintage70s                    -> "teamphoto" (stiff row, practice field)
BANNERS = [
    dict(slug="lineup", aspect="21:9", text=(
        "{n} heavy-set college football head coaches standing together as one "
        "team in a single wide frame, side by side, roughly equal prominence, "
        "three-quarter length, facing the viewer, a floodlit stadium behind "
        "them.")),

    dict(slug="teamphoto", aspect="21:9", text=(
        "{n} heavy-set college football head coaches posed in one straight row "
        "on a practice field, deadpan and formal, hands on hips, flat midday "
        "light, the whole row visible end to end.")),

    dict(slug="sideline", aspect="21:9", text=(
        "{n} heavy-set college football head coaches spread along a sideline "
        "during a game, headsets and play sheets, caught mid-moment rather "
        "than posed, the field and stands behind them.")),

    # NOT coaches, NOT all men, NOT heavy-set. family's roster spans a
    # grandmother, a boy and several adults, and its art is real family
    # photographs rather than the fat-coach treatment the other three groups
    # share. Rendering them as "heavy-set college football head coaches" would
    # be wrong about who these people are, and GROUP_LOCK's "every man is
    # large and heavy-set" is wrong twice over.
    #
    # `subject` is the noun the wardrobe clause uses; `lock` picks which
    # invariant block the renderer appends. Art direction does not port
    # between groups even when the rendering style does.
    dict(slug="row", aspect="5:4", subject="person", lock="plain", text=(
        "{n} people of different ages standing together in one row, side by "
        "side, roughly equal prominence, three-quarter length, facing the "
        "viewer, a bright open outdoor background behind them.")),
]

# Default for every entry that does not say otherwise -- the three coach
# compositions above.
DEFAULT_SUBJECT = "man"
DEFAULT_LOCK = "heavyset"

BY_SLUG = {e["slug"]: e for e in BANNERS}
