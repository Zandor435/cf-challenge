# CEC — Persona Content

**Group:** `church` · CEC
**Tone:** straight (warm; jokes aimed at picks, not people)
**Managers:** 5

Source of truth for prose. Fields here map 1:1 to `groups/church/personas.json`.

**CONFIG CHANGES REQUIRED:**
- `display_name` in `groups/church/config.json` is currently `"Church League"` → change to `"CEC"` (Christ Episcopal Church).
- `john_tamu` display_name is currently `"Texas A&M John"` → change to `"John K"`. The `manager_id` stays `john_tamu` — ids are permanent.

**Shared context:** everyone here is in Charlottesville, VA, and talks college
football at church every week. This pool is an extension of that conversation.
All guys, so there's latitude — but the register stays closer to family than to
panel. Most humor lands on **teams selected**, via `pick_note`, not on people.

---

## brian — "The Cardinal"

- **tone:** straight
- **display_name:** Brian

**backstory:**

The one nobody sees coming. Software engineer by training, reader by habit,
quietly one of the smartest guys at the table — and then he opens his mouth about
college football and you realize he's been paying real attention this whole time.
A Stanford man with a UVA soft spot, which in Charlottesville makes him either a
diplomat or a man with divided loyalties, depending on the week. Recently moved
closer to town. His son Sam started kindergarten this year, which means Brian is
now on the same schedule as everyone else in this pool and has considerably less
time to research.

- **running_gag:** Nobody expects the depth of the football knowledge. It gets them every year.
- **draft_tendency:** Reads everything. Picks like someone who read everything.

---

## john_tamu — "Aggie by Blood"

- **tone:** straight
- **display_name:** John K

**backstory:**

Texas born, Texas A&M all the way, and the head of a genuinely serious college
football household — both his sons are die-hards and one of them actually runs a
college football site, which means John has in-house analytics the rest of us do
not. Works for a defense contractor. As decent a guy as there is in this group,
which is unfortunate, because his picks are going to get roasted regardless.

- **running_gag:** Whatever he does with Texas A&M. It's never neutral.
- **draft_tendency:** Has better information than he lets on.

---

## david — "The Spider"

- **tone:** straight (but he's the joker — most latitude in the group)
- **display_name:** David

**backstory:**

The best joker in the group and he knows it. Richmond man — a genuine Spiders
fan, which is a commitment — and a Georgia fan by way of his son, who went there
and takes him to games. Former CPA with some real estate in the mix, which means
he's spent a career around numbers and will not be taking the numbers seriously
in this pool. He's here for the material.

- **running_gag:** Richmond and Georgia in the same heart. He'll defend both.
- **draft_tendency:** Picks for the story, not the spread.
- **rival:** `zach`

---

## john_wells — "Wahoo"

- **tone:** straight
- **display_name:** John Wells

**backstory:**

Die-hard Virginia, degree from Virginia, financial planner in Charlottesville at
Morris & Wells. His kids are at Sunrise Elementary with Zach's, which is roughly
how everyone in this group ends up connected to everyone else. The most measured
guy in the pool and the one most likely to have actually done the arithmetic on
his own picks before submitting them.

- **running_gag:** A financial planner betting on Virginia is a risk-tolerance question.
- **draft_tendency:** Conservative. Won't reach.

---

## zach — "The Commissioner"

- **tone:** straight
- **display_name:** Zach

**backstory:**

Wake Forest alum, Baton Rouge raised, LSU heart — and the man who took a weekly
after-church conversation about college football and turned it into a scoring
engine, a projection model, and a website. He loves AI, loves building sites for
reasons that do not justify the effort, and has now done this three separate
times for three separate groups of people who would have been fine with a
spreadsheet. He and David have an ongoing thing that neither of them intends to
resolve. Everyone here understands the man who wrote the scoring is also in the
standings, and everyone has agreed to let it go.

- **running_gag:** Elaborate infrastructure, trivial stakes. Also: David.
- **draft_tendency:** Overthinks it, then takes the model's number anyway.
- **rival:** `david`

**NOTE:** Zach's CEC backstory differs deliberately from his panel and family
versions. Keep all three distinct.

---

## Open items

- **Rating bars** — recommend dropping for this group. Straight tone; "Delusion: 9" doesn't fit.
- **"Fat profiles"** — mentioned as existing for this group. Unclear what these are (art? a prior document?). Needs clarification before slotting into the art plan.
- **pick_note** per team — pending draft. This is where this group's humor should live.
- **Rival cross-links:** `zach` ↔ `david` is the only genuine pair here. Leave the rest empty — a manufactured rivalry reads as filler, and an empty rival hides the block.
