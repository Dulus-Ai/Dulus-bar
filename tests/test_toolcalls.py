from dulus_bar.toolcalls import describe_suggestions, summarize


def test_bash_shows_the_full_command_and_its_purpose():
    title, args, detail = summarize("Bash", {"command": "rm -rf build/\nnpm run build",
                                             "description": "Clean rebuild"})
    assert (title, args) == ("Bash", "rm -rf build/ npm run build")
    assert detail == "rm -rf build/\nnpm run build\n# Clean rebuild"


def test_file_tools_use_paths_relative_to_the_project():
    title, args, detail = summarize(
        "Edit", {"file_path": "/home/kev/app/src/auth.py", "old_string": "a = 1", "new_string": "a = 2"},
        cwd="/home/kev/app",
    )
    assert (title, args) == ("Edit", "src/auth.py")
    assert detail == "- a = 1\n+ a = 2"
    assert summarize("Write", {"file_path": "/x/y.txt", "content": "hi"}, cwd="/home")[1] == "/x/y.txt"


def test_long_content_is_clipped_with_a_count():
    _, _, detail = summarize("Write", {"file_path": "f", "content": "\n".join(map(str, range(40)))})
    assert detail.splitlines()[-1] == "… (28 more lines)"


def test_mcp_and_unknown_tools():
    title, args, _ = summarize("mcp__github__create_issue", {"title": "Bug"})
    assert title == "github · create_issue" and '"title": "Bug"' in args
    assert summarize("WebFetch", {"url": "https://x.dev", "prompt": "read"})[1] == "https://x.dev"
    assert summarize("Mystery", None)[0] == "Mystery"


def test_always_allow_label_comes_from_the_suggestions():
    suggestions = [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "npm test:*"}],
         "behavior": "allow", "destination": "localSettings"},
        {"type": "addDirectories", "directories": ["/home/kev/other"], "destination": "session"},
    ]
    assert describe_suggestions(suggestions) == "Bash(npm test:*), files in other"
    assert describe_suggestions(None) == ""
