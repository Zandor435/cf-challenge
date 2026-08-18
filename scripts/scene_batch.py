#!/usr/bin/env python3
"""The named scene-batch compositions for the panel group.

DATA, not logic — generate_scenes.py imports BATCH and builds the calls. Kept
separate so the composition list can grow without turning the generator into a
wall of prose.

Each entry:
  slug    output name -> output/scenes/panel/panel_<slug>_<nn>.png
  who     manager_ids supplying character references, IN ORDER. Prompts address
          them as "reference image #N", so order is load-bearing. Empty list =
          no people in the frame.
  trophy  True appends the canonical trophy plate as the final reference.
  text    the composition itself. No style clause and no lock clauses — the
          generator appends those.
  leads   present only on rotating entries; expands to one composition per
          manager, slug becoming <slug>_<manager_id>.

Wording notes: avoid "photo"/"photograph" anywhere (BANNED_TERMS rejects the
prompt outright) — say "framed portraits", not "framed photos".
"""

ALL = ["blaine", "chris", "jonathan", "zach"]

BATCH = [
    # ---- the league taking itself too seriously ---------------------------
    dict(slug="warroom", who=ALL, text=(
        "A fantasy-league draft war room. All four men crowd a cheap folding "
        "table buried under stacks of printouts and spreadsheets. Three "
        "phones are going at once. Behind them a whiteboard is covered in "
        "conference names and crossed-out team names. One man is on his feet, "
        "leaning in, pointing hard at the board. The whole room has the "
        "coiled tension of a professional war room on draft night, applied "
        "with total seriousness to a wager worth nothing.")),

    dict(slug="commissioner", who=["blaine"], text=(
        "A dim wood-panelled executive office. ONE man sits alone behind an "
        "enormous polished desk, hands steepled in front of him, expression "
        "deadly serious and self-important. On the wall behind him hang three "
        "framed portraits of the other three men, arranged like official "
        "state portraits. He has appointed himself to this office; nobody "
        "gave it to him.")),

    dict(slug="presser", who=["chris"], text=(
        "A postgame press-conference room. ONE man stands at a podium behind "
        "a cluster of microphones, caught mid-sentence with one hand raised "
        "in explanation, entirely earnest. Facing him are rows and rows of "
        "empty folding chairs. Not a single reporter is present. The room is "
        "silent and he is talking anyway.")),

    dict(slug="rulebook", who=["blaine", "zach"], text=(
        "Late at night under a single desk lamp. TWO men hunch over a thick "
        "bound rulebook lying open on a table. The first jabs a finger at a "
        "specific line on the page, insistent. The second stands with his "
        "arms crossed, jaw set, entirely unconvinced. Everything beyond the "
        "lamp's pool of light falls away into darkness.")),

    dict(slug="filmroom", who=ALL, text=(
        "A darkened room lit only by the beam of a projector throwing game "
        "footage on the far wall. All four men sit watching, cold blue light "
        "washing across their faces. One of them has fallen fast asleep, head "
        "tipped back, while the others stare intently at the screen.")),

    # ---- the bet as religion ----------------------------------------------
    dict(slug="theline", who=["jonathan"], text=(
        "A single crisp chalk line running across an empty turf field at "
        "dusk. ONE man stands squarely on the line, arms held out from his "
        "sides for balance, head bowed, looking down at his own feet. "
        "Absolutely no other figures anywhere in the frame. The line is "
        "treated as a sacred boundary and the moment as reverent.")),

    dict(slug="theover", who=["zach"], text=(
        "ONE man rendered in near-silhouette against blazing stadium "
        "floodlights, both arms thrust straight up overhead in the "
        "touchdown-signal pose, triumphant. The upward gesture alone carries "
        "the meaning. Backlit, rim-lit, heroic.")),

    dict(slug="theunder", who=["blaine"], text=(
        "Same blazing stadium floodlights and near-silhouette treatment. ONE "
        "man stands with his arms crossed hard over his chest, chin tucked "
        "down, refusing. The posture of someone rejecting all enthusiasm on "
        "principle.")),

    dict(slug="crystalball", who=[], trophy=True, text=(
        "The faceted crystal championship trophy from the reference image "
        "standing alone in the exact centre of a cheap folding card table in "
        "a cluttered suburban garage. Reproduce that trophy's design "
        "faithfully. A single bare bulb hangs directly above it, throwing a "
        "hard pool of light. The staging is reverent and absurdly ceremonial "
        "for the setting. No people in the frame.")),

    dict(slug="trophycar", who=[], trophy=True, text=(
        "Interior of a pickup truck. The faceted crystal championship trophy "
        "from the reference image is strapped into the passenger seat with "
        "the seatbelt buckled across it, treated as a passenger. Reproduce "
        "that trophy's design faithfully. The driver's hands rest on the "
        "steering wheel at the edge of frame; the driver's face is not "
        "visible and is not needed.")),

    # ---- manager vs manager ------------------------------------------------
    dict(slug="scouting", who=["chris", "jonathan"], text=(
        "TWO men, both clearly identifiable. The man from reference image #1 "
        "crouches high in empty stadium seating with binoculars raised to his "
        "eyes and an open notepad on his knee, plainly spying. Far below on "
        "the field, the man from reference image #2 is coaching, gesturing at "
        "the play, completely unaware he is being watched. Steep downward "
        "vantage from the stands.")),

    dict(slug="handshake_a", who=["blaine", "chris"], text=(
        "A postgame handshake at midfield. TWO men clasp hands, both wearing "
        "tight forced smiles that do not reach the eyes, each gripping far "
        "harder than necessary. Knuckles white, forearms tense. Cordial on "
        "the surface and openly hostile underneath.")),

    dict(slug="handshake_b", who=["jonathan", "zach"], text=(
        "A postgame handshake at midfield. TWO men clasp hands, both wearing "
        "tight forced smiles that do not reach the eyes, each gripping far "
        "harder than necessary. Knuckles white, forearms tense. Cordial on "
        "the surface and openly hostile underneath.")),

    dict(slug="argument", who=["chris", "zach"], text=(
        "TWO men squared off nose to nose on a sideline, mid-shout, faces "
        "inches apart. An officiating referee is wedged bodily between them "
        "with one flat hand planted on each man's chest, holding them apart. "
        "The horizon is steeply tilted for a canted, off-balance "
        "composition.")),

    dict(slug="collision", who=["blaine", "jonathan"], text=(
        "A split-frame composition divided by a hard vertical line down the "
        "centre. The man from reference image #1 occupies the left half, the "
        "man from reference image #2 the right half, each facing the divide. "
        "Centred on the dividing line, between them, floats a single football "
        "helmet belonging to one team. They have taken the same team on "
        "opposite sides of the same bet.")),

    # ---- fortune, good and bad --------------------------------------------
    dict(slug="hotseat", who=["chris"], text=(
        "A metal folding chair on an empty sideline, fully engulfed in "
        "flames. ONE man sits in the burning chair regardless, arms folded "
        "across his chest, expression composed, pretending with total "
        "commitment that everything is fine.")),

    dict(slug="benchrow", who=ALL, text=(
        "All four men sit along a sideline bench in identical postures of "
        "total dejection, elbows on knees, helmets held loosely in their "
        "hands, heads hanging. They are staggered at clearly different "
        "distances from the viewer rather than lined up flat, the nearest "
        "large in frame and the furthest small.")),

    dict(slug="gatorade", who=None, leads=ALL, text=(
        "ONE man at the instant of a celebratory sports-drink drenching, a "
        "sheet of bright liquid crashing over his head and shoulders, both "
        "arms flung up in triumph, confetti falling all around him. His face "
        "shows completely genuine, unguarded joy. The other three men are not "
        "present.")),

    dict(slug="boxday", who=["jonathan"], text=(
        "ONE man walks out through the doors of a football facility carrying "
        "a cardboard banker's box of his personal belongings, a framed "
        "picture poking out of the top. Heavy rain is falling and he is "
        "soaked. Nobody has come to help him and nobody is watching him go.")),

    dict(slug="milkcarton", who=["zach"], allow_text=True, text=(
        "A cardboard milk carton standing on a worn kitchen table in flat "
        "morning light. On the side panel of the carton, printed in the style "
        "of a missing-persons notice, is a portrait of the man from reference "
        "image #1, with the single word MISSING set in plain block capitals "
        "directly above the portrait. He has been mathematically eliminated. "
        "Render only that one word and nothing else.")),

    dict(slug="statue", who=ALL, text=(
        "The unveiling of a heroic bronze statue. The statue depicts the man "
        "from reference image #1 mid-stride, larger than life on a stone "
        "plinth, the drape just fallen away. Watching from below is a very "
        "small crowd of exactly three men -- the other three from the "
        "reference images -- standing together, unimpressed. The turnout is "
        "the joke.")),

    # ---- atmosphere (no likeness) -----------------------------------------
    dict(slug="marquee", who=[], strict_no_text=True, text=(
        "A stadium letterboard marquee sign standing against a dusk sky, "
        "seen straight on. The sign face is completely BLANK and EMPTY -- a "
        "clean, smooth, unbroken surface with no letters, no numbers, no "
        "characters, no symbols and no marks of any kind on it. Empty slots "
        "only. The surrounding structure, frame, lamps and sky are richly "
        "detailed. No people in the frame.")),
]


# ---------------------------------------------------------------------------
# Fight posters. Separate list from BATCH: different output root, different
# aspect, and lettering is REQUIRED rather than suppressed.
# ---------------------------------------------------------------------------
# Names are display names, not surnames. The repo records no surnames anywhere
# (personas.json and groups/panel/config.json carry display_name only); the one
# known surname is Chris's. Mixing one surname with three first names would read
# worse than four first names, so these are uniform. Swap in real surnames here
# if they are ever recorded.
POSTER_NAMES = {"blaine": "BLAINE", "chris": "CHRIS",
                "jonathan": "JONATHAN", "zach": "ZACH"}

# One per unique pairing. Filename order is alphabetical and frame position
# follows it: first name left, second right, every time.
POSTERS = [
    dict(slug="fight_blaine_chris", who=["blaine", "chris"],
         promo="ONE NIGHT ONLY"),
    dict(slug="fight_blaine_jonathan", who=["blaine", "jonathan"],
         promo="TITLE ON THE LINE"),
    dict(slug="fight_blaine_zach", who=["blaine", "zach"],
         promo="NO MORE TALK"),
    dict(slug="fight_chris_jonathan", who=["chris", "jonathan"],
         promo="SETTLE IT INSIDE"),
    # Both men wear gold; without split grounds the poster reads as one man
    # twice. This is the only pairing that needs it.
    dict(slug="fight_chris_zach", who=["chris", "zach"],
         promo="WINNER TAKE ALL", split_ground=True),
    dict(slug="fight_jonathan_zach", who=["jonathan", "zach"],
         promo="THE RECKONING"),
]

# ASSUMED SPEC: the brief's BONUS section was truncated mid-sentence ("THE FULL
# CARD (1"). Built from the intro line "plus one full-card bill" and the rules
# already established for the six: same era and style, same 3:4, same lettering
# furniture, same empty lower band, same no-numbers rule. All four men, stacked
# as a card rather than squared off as a pairing.
FULLCARD = dict(slug="fight_fullcard", who=ALL, promo="THE FULL CARD",
                fullcard=True)
