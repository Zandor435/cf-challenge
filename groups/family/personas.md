# Family — Persona Content

**Group:** `family` · Family League
**Tone:** SPLIT — see per-manager `tone` field
**Managers:** 8

Source of truth for prose. Fields here map 1:1 to `groups/family/personas.json`.

**Tone rule for this group (important):** family splits into two registers. The
older crowd — John, Rachel, Vic — is `straight`: warm, factual, no roast. The
younger four — Gayden, Devin, Gunner, Zach — are `roast`, same register as panel.

A `straight` profile renders backstory + picks and **hides** `fatal_flaw`,
`running_gag`, and the rating bars. "Delusion: 9" on someone's father is the
wrong note.

Most of the joking in this group should land on **teams selected**, not people.
Those go in `pick_note`, next to the pick on the board — so a joke about Vic's
team is not a joke about Vic.

**Shared context:** three generations. John and Rachel are Zach's parents; Vic is
John's brother. Gayden is Zach's older brother. Holly is a cousin, raised close
enough to be a sister. Gunner is Holly's son. Devin is a cousin.

**The World Cup asterisk:** the previous pool ended with Gunner holding the best
board and Devin taking the title, because a final-match blowout overwhelmed a
season of better picks under a scoring system Zach wrote. Ten dollars. Gunner was
a good sport. It has not been forgotten. This is live material for the four
`roast` profiles and appears nowhere in the `straight` ones.

---

## john — "The Counselor"

- **tone:** straight
- **display_name:** John

**backstory:**

McComb, Mississippi, and still the best football conversation in the family. He
was a high school star in McComb before most of this roster's parents were born,
went to Ole Miss undergrad, then LSU Law, then spent years in the Attorney
General's office building arguments for a living. He is an Ole Miss man first and
an LSU man immediately after, and he will explain the ordering with the patience
of someone who has explained harder things to worse audiences. Every college
football take Zach has ever had was pressure-tested against this man first.

- **note:** Ole Miss first, LSU second, and he'd like the record to reflect that.
- **draft_tendency:** Builds a case. Won't take a pick he can't defend out loud.

---

## rachel — "The Real Fan"

- **tone:** straight
- **display_name:** Rachel

**backstory:**

Baton Rouge raised, one of a big family, Ole Miss undergrad and LSU Law — the
same track as John, which tells you something about how this household ended up.
A genuine college football fan in her own right, not a spouse-adjacent one, a
distinction she has never once had to make because everybody in this family
already knows it.

- **draft_tendency:** Sneaky-good. Doesn't announce anything.

---

## vic — "Abita Springs"

- **tone:** straight
- **display_name:** Vic

**backstory:**

John's brother and Zach's uncle. McComb-born, spent years in Diamondhead, and
recently moved to Abita Springs, Louisiana — a decision he is delighted with. He
is here because he likes being in it with everybody, which is a better reason
than most people in this pool have.

- **draft_tendency:** Unbothered. Picks, commits, moves on.

---

## gayden — "Blue Belt"

- **tone:** roast
- **display_name:** Gayden

**backstory:**

Seven years older than Zach and running at a completely different RPM. Medical
device rep in Denver, obsessive about jiu-jitsu — picked it up only a couple of
years ago and is already a blue belt, training with his son, which is exactly the
level of intensity you'd expect from a man who does not have a casual setting. He
is not a died-in-the-wool college football guy. He is a died-in-the-wool
*competition* guy, and this happens to be the competition available. He will take
a joke. He will also take your arm.

- **running_gag:** Intensity. Applied to a $10 bet.
- **draft_tendency:** Aggressive. Will take a side hard and stay there.
- **fatal_flaw:** No casual setting. Anywhere. Ever.

---

## holly — "Rocky Top in Enemy Territory"

- **tone:** roast (light)
- **display_name:** Holly

**backstory:**

Cousin to Zach and Gayden, niece to John and Rachel, and functionally a sister —
she lived with them for a year in high school. Baton Rouge raised, Tennessee
educated, currently residing in Chelsea, Alabama, surrounded on all sides by
Alabama and Auburn fans, a situation she describes with the weariness of a
hostage. She is a Vol first and an LSU fan underneath, and her actual position is
less pro-Tennessee than anti-obnoxious. Bikes, walks, dogs. The dogs are
non-negotiable.

- **running_gag:** Living in Alabama and refusing to convert.
- **draft_tendency:** Picks against the teams whose fans have annoyed her most. Historically not a bad system.

---

## gunner — "Robbed"

- **tone:** roast
- **display_name:** Gunner

**backstory:**

Holly's son. Played college soccer at North Alabama, now a physical therapist in
South Florida, likes fishing, doesn't especially follow college football, and is
going to be a problem anyway — because last time out he had the World Cup pool
won on merit and lost it to a scoring system his cousin wrote. Devin's
final-match blowout swallowed a season of better picks. Ten dollars. He was a
good sport about it, which is somehow worse. He is the quietest manager in the
family and the one with the most outstanding balance.

- **running_gag:** The asterisk. He didn't lose, he was *scored* out of it.
- **draft_tendency:** Says nothing, walks out with the best board.
- **rival:** `zach` (the scoring), `devin` (the trophy)

---

## devin — "The Defending Champ"

- **tone:** roast
- **display_name:** Devin

**backstory:**

Auburn undergrad, dad played receiver at LSU, so he's an Auburn man with an LSU
fallback and a lifelong inability to pick a side cleanly. Originally Birmingham,
now outside Tampa, sales rep for a company that builds power line trucks. He is
also the reigning World Cup champion, a title he holds legitimately, permanently,
and — depending on which cousin you ask — through the exploitation of a scoring
bug. He has never once acknowledged the asterisk. Excellent sport, terrible about
this.

- **running_gag:** The title, and the manner of its acquisition.
- **draft_tendency:** Confident. Trusts the pick, waits it out.
- **rival:** `gunner`

---

## zach — "The Commissioner"

- **tone:** roast
- **display_name:** Zach

**backstory:**

The one who started this, and — because he wrote the scoring engine — the one
whose scoring is now a family legal matter. Wake Forest, Baton Rouge raised, LSU
heart, and enough of a college football obsessive that this pool exists mainly so
he has more games to care about. He built the site, the pipeline, and the
projection model for a bet you could settle with a handshake. He also built the
last one, which is how his cousin ended up with a trophy and his other cousin
ended up with a grievance.

- **running_gag:** Every scoring dispute goes to the man who wrote the scoring.
- **draft_tendency:** Overthinks it, then takes the model's number anyway.
- **rival:** `gunner`

**NOTE:** Zach's family backstory differs deliberately from his panel and CEC
versions. Same person, different room, different joke. Keep all three distinct.

---

## Open items

- **Rating bars** — recommend dropping entirely for this group, or authoring only for the four `roast` managers.
- **Rafe** — present in the WC pool, absent from the CF family roster. In or out?
- **pick_note** per team — pending draft. This is where most of this group's humor should live.
