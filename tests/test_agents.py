from dulus_bar.agents import style_for


def test_known_agents_are_recognized_inside_longer_names():
    assert style_for("Claude Code").key == "claude"
    assert style_for("claudecode").key == "claude"
    assert style_for("cursor-agent").key == "cursor"
    assert style_for("Qwen Code").key == "qwen"
    assert style_for("gpt-5 runner").key == "codex"
    assert style_for("Sourcegraph Amp").key == "amp"
    assert style_for("Roo Code").key == "cline"


def test_short_aliases_only_match_whole_words():
    # "cn", "amp", "zed", "roo", "gpt" used to match anywhere in a name.
    for name in ("Example Agent", "Organized", "McNulty", "Broom", "Champion CI"):
        style = style_for(name)
        assert style.display == name and style.emoji == "●", name


def test_unknown_agents_get_a_stable_color():
    assert style_for("My Agent").accent == style_for("my agent").accent
    assert style_for("").display == "Agent"
