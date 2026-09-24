"""speechify() is deterministic, so every rule here is an assertion.

Each case is a real incident or a real thing that reaches the speaker. If a rule
can't be stated as an input/output pair, it doesn't belong in speechify.py — it
belongs in the summarizer prompt.
"""

import pytest

from alicia.speechify import MAX_SPOKEN_CHARS, chunk_for_speech, speechify


# --- homographs: the /lɪv/ incident ---------------------------------------


def test_live_is_never_read_as_the_adjective():
    assert "/lɪv/" not in speechify("Live.")
    assert speechify("The site is live.") == "The site is deployed and running."


# --- months: "Jul" reads as Jewel -----------------------------------------


@pytest.mark.parametrize(
    "abbrev,full",
    [("Jul", "July"), ("Aug", "August"), ("Sep", "September"), ("Dec", "December")],
)
def test_month_abbreviations_are_expanded(abbrev, full):
    assert full in speechify(f"shipped {abbrev} 14")


def test_iso_dates_become_spoken_dates():
    assert speechify("due 2026-08-05") == "due August 5"


def test_a_number_that_is_not_a_date_is_left_alone():
    assert "9999" in speechify("9999 items")


# --- ticket ids -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,spoken",
    [
        # Lowercase: "REV" in caps is read as a spelled-out abbreviation
        # ("revey"). Justin says "rev", so the synthesiser gets a word.
        ("REV-418", "rev four eighteen"),
        ("REV-9", "rev nine"),
        ("REV-100", "rev one hundred"),
        ("REV-412", "rev four twelve"),
    ],
)
def test_ticket_ids_are_spoken_the_way_people_say_them(raw, spoken):
    assert speechify(raw) == spoken


# --- things no human says out loud ----------------------------------------


def test_paths_are_not_read_aloud():
    out = speechify("failed in ~/Projects/alicia/alicia/tools.py")
    assert "Projects" not in out
    assert "a file" in out


def test_urls_are_not_read_aloud():
    assert speechify("see https://github.com/x/y/pull/3") == "see a link"


def test_commit_hashes_are_not_read_aloud():
    out = speechify("merged 3018589")
    assert "3018589" not in out
    assert "a commit" in out


def test_uuids_are_not_read_aloud():
    uid = "6d0b8f2a-7e2b-4e4f-b19c-6dc5f6fd80fe"
    out = speechify(f"{uid} needs you.")
    assert "6d0b8f2a" not in out.lower()
    assert "needs you" in out.lower()


def test_speakable_name_prefers_a_ticket_then_a_title():
    from alicia.speechify import speakable_name

    assert speakable_name("REV-484", "Ignore me") == "REV-484"
    assert "6d0b8f2a" not in speakable_name(
        "6d0b8f2a-7e2b-4e4f-b19c-6dc5f6fd80fe",
        "Approve GitHub Actions Salesforce CI run 31731276061 for SFDC Prod",
    ).lower()
    assert speakable_name("6d0b8f2a-7e2b-4e4f-b19c-6dc5f6fd80fe") == "this"


def test_versions_are_not_read_aloud():
    assert "a version" in speechify("bumped to 1.2.3")


def test_branch_names_are_not_read_aloud():
    out = speechify("pushed feat/phase1-conversation-core")
    assert "phase1" not in out


def test_markdown_syntax_never_reaches_the_mouth():
    out = speechify("## Status\n\n- **two** things `need` you\n\n---\n")
    for junk in ("#", "*", "`", "-", "|"):
        assert junk not in out
    assert "two things need you" in out


def test_emoji_are_stripped():
    assert "🚀" not in speechify("shipped 🚀 today")


def test_speech_has_no_line_breaks():
    assert "\n" not in speechify("one\ntwo\nthree")


# --- numbers --------------------------------------------------------------


def test_ratios_are_spoken():
    assert speechify("contrast 12.49:1") == "contrast 12.5 to one"


def test_long_decimals_are_rounded():
    assert "3.1" in speechify("took 3.14159 of them")


def test_units_are_spoken():
    assert speechify("waited 8s") == "waited 8 seconds"
    assert speechify("used 512mb") == "used 512 megabytes"


def test_money_suffixes_are_expanded():
    assert speechify("worth $1.2M") == "worth 1.2 million dollars"


# --- the cap --------------------------------------------------------------


def test_output_is_capped():
    assert len(speechify("word. " * 400)) <= MAX_SPOKEN_CHARS


def test_the_cap_prefers_a_sentence_boundary():
    text = ("This is a complete sentence that runs on for a while. " * 20)
    out = speechify(text)
    assert out.endswith(".")


def test_empty_in_empty_out():
    assert speechify("") == ""
    assert speechify(None) == ""


# --- it is a pure function ------------------------------------------------


def test_speechify_is_idempotent_on_clean_text():
    clean = "Two things need you."
    assert speechify(clean) == clean


def test_no_model_or_network_is_involved():
    """Guard the 'deterministic' claim structurally, not by comment."""
    import inspect

    import alicia.speechify as mod

    src = inspect.getsource(mod)
    for forbidden in ("httpx", "requests", "chat_completion", "openai", "urllib"):
        assert forbidden not in src, f"speechify must stay offline; found {forbidden}"


# --- chunking: first flush short so audio starts sooner -------------------


def test_first_chunk_is_shorter_than_the_rest():
    text = " ".join(f"Sentence number {i} here." for i in range(12))
    chunks = chunk_for_speech(text)
    assert len(chunks) > 1
    assert len(chunks[0]) <= 70


def test_chunks_reassemble_to_the_original():
    text = "One thing happened. Then a second. And finally a third one."
    assert " ".join(chunk_for_speech(text)) == text


def test_chunking_empty_is_empty():
    assert chunk_for_speech("") == []


def test_a_single_short_sentence_is_one_chunk():
    assert chunk_for_speech("Nothing needs you.") == ["Nothing needs you."]
