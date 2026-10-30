from grammarhotkey.guard import check


def test_keeps_a_good_result():
    r = check("Aš nežinau, ką daryti.", "as nezinau ka darit", True)
    assert r.ok and r.text == "Aš nežinau, ką daryti."


def test_strips_fence_and_preamble():
    r = check("Here is the corrected text:\n\n```\nGerai.\n```", "gerai", True)
    assert r.ok and r.text == "Gerai."


def test_strips_lithuanian_preamble_and_trailing_note():
    r = check("Pataisytas tekstas:\nLabas rytas.\n\nPastaba: pataisiau skyrybą.", "labas rytas", True)
    assert r.ok and r.text == "Labas rytas."


def test_keeps_fence_when_the_original_has_one():
    original = "```\nprint( 1 )\n```"
    assert check(original, original, True).text == original


def test_empty_is_refused():
    assert not check("  \n", "tekstas", True).ok


def test_wildly_longer_is_refused_in_strict_mode():
    original = "Labas, kaip sekasi šiandien tau, drauge? " * 3
    assert not check(original * 10, original, True).ok


def test_short_text_can_grow_a_little():
    assert check("Aš einu namo, nes jau vėlu.", "einu namo", True).ok


def test_rewrite_mode_allows_more_change():
    original = "Labas, kaip sekasi šiandien tau, drauge? " * 3
    assert check(original * 3, original, False).ok


def test_refusal_is_refused():
    assert not check("I'm sorry, but I can't help with that.", "kažkoks tekstas čia", True).ok


def test_lost_url_is_refused():
    r = check("Žiūrėk čia.", "ziurek cia https://example.com/a?b=1", True)
    assert not r.ok and "link" in r.reason


def test_lost_emoji_is_refused():
    assert not check("Labas!", "labas 😀", True).ok


def test_emoji_kept_is_fine():
    assert check("Labas! 😀", "labas 😀", True).ok


def test_line_endings_and_outer_whitespace_follow_the_original():
    r = check("Pirma.\nAntra.", "  pirma\r\nantra\r\n", True)
    assert r.ok and r.text == "  Pirma.\r\nAntra.\r\n"


def test_answer_that_starts_like_the_original_is_not_stripped():
    original = "Here is my plan:\nwe go home"
    r = check("Here is my plan:\nWe go home.", original, True)
    assert r.text == "Here is my plan:\nWe go home."
