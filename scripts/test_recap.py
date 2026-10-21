"""Tests for the SVP weekly recap: packet, prose validation, guard, filing, email.

Reads the committed panel boards read-only; anything written goes to tmp_path.
No network: the OpenAI call is injected.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_email_payload  # noqa: E402
import build_recap_packet  # noqa: E402
import column_guard  # noqa: E402
import generate_recap  # noqa: E402
from render import render_html  # noqa: E402


@pytest.fixture(scope="module")
def packet():
    return build_recap_packet.build("panel", week=7)


def good_reply(pk, **over):
    doc = {
        "deck": "The room is reduced to rooting interests.",
        "open": "Nobody moved a single place this week.",
        "managers": {m["manager_id"]: f"{m['name']} had a week." for m in pk["managers"]},
        "games": {g["game_key"]: "Somebody needs this one." for g in pk["games_that_matter"]},
        "close": "Pick your side.",
    }
    doc.update(over)
    return json.dumps(doc)


# --- packet -----------------------------------------------------------------

def test_packet_covers_every_manager_in_rank_order(packet):
    standings = json.loads((build_recap_packet.DOCS_DATA / "panel" / "standings.json")
                           .read_text(encoding="utf-8"))
    assert {m["manager_id"] for m in packet["managers"]} == \
        {m["manager_id"] for m in standings["managers"]}
    ranks = [m["rank"] for m in packet["managers"]]
    assert ranks == sorted(ranks)
    assert packet["week"] == 7


def test_root_for_follows_the_side_of_the_pick(packet):
    seen = set()
    for m in packet["managers"]:
        for p in m["picks"]:
            ng = p["next_game"]
            if not ng:
                continue
            want = p["team"] if p["direction"] == "O" else ng["opponent"]
            assert ng["root_for"] == want
            seen.add(p["direction"])
    assert seen == {"O", "U"}, "fixture should exercise both sides"


def test_bye_only_for_live_picks_without_a_next_week_game(packet):
    for m in packet["managers"]:
        for p in m["picks"]:
            if p["bye"]:
                assert p["next_game"] is None
            if p["next_game"]:
                assert not p["bye"]


def test_games_that_matter_list_only_managers_with_a_pace_stake(packet):
    games = packet["games_that_matter"]
    assert 0 < len(games) <= build_recap_packet.GAMES_THAT_MATTER
    for g in games:
        assert g["managers"]
        for s in g["managers"]:
            assert s["pace_if_team_wins"] != s["pace_if_team_loses"]
            want = g["team"] if s["pace_if_team_wins"] > s["pace_if_team_loses"] else g["opponent"]
            assert s["root_for"] == want


def test_games_limit_is_respected():
    assert len(build_recap_packet.build("panel", week=7, limit=2)["games_that_matter"]) <= 2


# --- prose validation -------------------------------------------------------

def test_parse_requires_every_manager(packet):
    doc = json.loads(good_reply(packet))
    doc["managers"].pop(packet["managers"][0]["manager_id"])
    with pytest.raises(generate_recap.RecapProseError) as e:
        generate_recap.parse_prose(json.dumps(doc), packet)
    assert packet["managers"][0]["manager_id"] in str(e.value)


def test_parse_drops_invented_game_keys(packet):
    reply = good_reply(packet, games={"Made Up vs Nobody": "x"})
    assert generate_recap.parse_prose(reply, packet)["games"] == {}


def test_parse_rejects_non_json(packet):
    with pytest.raises(generate_recap.RecapProseError) as e:
        generate_recap.parse_prose("Here is your recap!", packet)
    assert "not JSON" in str(e.value)


# --- guard ------------------------------------------------------------------

def test_schedule_phrases_are_licensed_only_for_the_recap(packet):
    text = "Texas travels to face Ole Miss."
    assert column_guard.check_column(text, packet)            # column rules
    assert not column_guard.check_column(text, packet, schedule_licensed=True)


@pytest.mark.parametrize("bad", [
    "Texas is 6-1 now.",                       # record
    "They play in November.",                  # month
    "He needs a 13.7 swing.",                  # untraceable number
    "Alabama Crimson Tide fans rejoice: Navy looms.",  # team not in packet
    "rivalry game energy",
])
def test_recap_guard_still_rejects_unsupported_claims(packet, bad):
    assert column_guard.check_column(bad, packet, schedule_licensed=True)


# --- writing ----------------------------------------------------------------

def test_guard_rejection_gets_one_rewrite(packet):
    leader = packet["managers"][0]["manager_id"]
    bad = json.loads(good_reply(packet))
    bad["managers"][leader] = "He is 6-1 in November."
    replies = iter([json.dumps(bad), good_reply(packet)])
    prompts = []

    def call(system, user):
        prompts.append(user)
        return next(replies)

    prose, why = generate_recap.write_prose(packet, "key", call=call)
    assert why == "filed" and prose
    assert "rejected by the factual guard" in prompts[1]


def test_two_rejections_file_nothing(packet):
    bad = json.loads(good_reply(packet))
    bad["open"] = "Texas is 6-1."
    prose, why = generate_recap.write_prose(packet, "key", call=lambda s, u: json.dumps(bad))
    assert prose is None and "unsupported" in why


@pytest.fixture
def tmp_out(tmp_path, monkeypatch, packet):
    monkeypatch.setattr(generate_recap, "out_dir", lambda g: tmp_path)
    monkeypatch.setattr(generate_recap.build_recap_packet, "build", lambda g: packet)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("utils.load_env_file", lambda *a, **k: None)
    return tmp_path


def test_no_key_files_data_only_and_exits_zero(tmp_out, packet):
    assert generate_recap.run("panel") == 0
    doc = json.loads((tmp_out / "recap_week_7.json").read_text(encoding="utf-8"))
    assert doc["meta"]["status"] == "data_only" and doc["prose"] is None
    assert doc["packet"]["week"] == 7


def test_filed_week_is_skipped_and_never_downgraded(tmp_out, packet):
    prose = generate_recap.parse_prose(good_reply(packet), packet)
    path = generate_recap.write_doc("panel", packet, prose, model="m")
    before = path.read_text(encoding="utf-8")
    assert generate_recap.run("panel") == 0                    # skip-if-exists
    assert generate_recap.run("panel", force=True) == 0        # no key: must not downgrade
    assert path.read_text(encoding="utf-8") == before


def test_dry_run_writes_prompt_with_recap_brief(tmp_out):
    assert generate_recap.run("panel", dry_run=True) == 0
    text = (tmp_out / "recap_prompt_preview.txt").read_text(encoding="utf-8")
    assert "This is the roundup" in text and '"games_that_matter"' in text


# --- email ------------------------------------------------------------------

def recap_doc(packet, prose):
    return {"meta": {"status": "filed" if prose else "data_only", "week": packet["week"],
                     "byline": "Scott Van Pelt"},
            "packet": packet, "prose": prose}


def test_email_takes_week_and_subject_from_recap_and_hides_stale_column(packet, monkeypatch):
    prose = generate_recap.parse_prose(good_reply(packet), packet)
    monkeypatch.setattr(build_email_payload, "latest_recap",
                        lambda g: recap_doc(packet, prose))
    p = build_email_payload.build("panel")
    assert p["meta"]["week"] == 7
    assert "rooting interests" in p["meta"]["subject"]
    index = json.loads((build_email_payload.DOCS_DATA / "panel" / "columns" / "index.json")
                       .read_text(encoding="utf-8"))
    if 7 not in [c["week"] for c in index["columns"]]:
        assert p["column"] is None
    html = render_html(p)
    assert "Around the room" in html and "Games that matter" in html
    for m in packet["managers"]:
        assert f"{m['name']} had a week." in html
    build_email_payload.assert_no_addresses(p)


def test_data_only_recap_still_renders_the_cards(packet):
    section = build_email_payload.build_recap(recap_doc(packet, None))
    assert section["open"] is None
    assert len(section["managers"]) == len(packet["managers"])
    assert all(m["blurb"] is None for m in section["managers"])
    assert section["games"] and section["games"][0]["rooting"]
    html = render_html({"meta": {"subject": "s", "display_name": "d", "week": 7},
                        "recap": section})
    assert "Around the room" in html and "The weekly recap" not in html
