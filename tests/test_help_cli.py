"""The command catalog matches installed scripts and actual option parsing."""
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


def invoke(*args, cwd):
    return subprocess.run([str(Path(sys.executable).with_name("help")), *args],
                          cwd=cwd, capture_output=True, text=True)


def test_installed_help_table_and_option_ownership_without_config(tmp_path):
    result = invoke(cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.isascii()
    lines = result.stdout.splitlines()
    assert lines[0] == lines[2] == lines[-1]
    assert lines[0].startswith("+-") and lines[0].endswith("-+")
    assert len({len(line) for line in lines}) == 1
    rows = [line.split("|")[1:-1] for line in lines if line.startswith("|")]
    assert [cell.strip() for cell in rows[0]] == ["command", "description"]
    assert all(len(row) == 2 for row in rows)
    manifest = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert lines.count(lines[0]) == len(manifest["project"]["scripts"]) + 2
    for index, line in enumerate(lines):
        if line.startswith("| uv run "):
            assert lines[index - 1] == lines[0]  # Each command starts a separated group.
    assert [row[0].strip() for row in rows[1:] if row[0].strip().startswith("uv run ")] == [
        f"uv run {name}" for name in manifest["project"]["scripts"]
    ]
    sections = {}
    for command, description in rows[1:]:
        assert description.strip()
        if command.strip().startswith("uv run "):
            current = command.strip()
            sections[current] = []
        else:
            assert command.startswith("   ")  # Cell padding plus option indentation.
            sections[current].append((command.strip(), description.strip()))
    for name in (n for n in manifest["project"]["scripts"] if n != "help"):
        assert sections[f"uv run {name}"] == [
            ("-h, --help", "show this help message and exit"),
            ("--config PATH", "YAML configuration path (default: config.yaml)"),
        ]
    assert sections["uv run help"] == [("-h, --help", "show this help message and exit")]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_command_accepts_listed_help_flags(tmp_path, flag):
    result = invoke(flag, cwd=tmp_path)
    assert result.returncode == 0
    assert "usage: help" in result.stdout
    assert "List project commands and their options." in result.stdout


def test_help_command_rejects_unlisted_options(tmp_path):
    result = invoke("--config", "anything.yaml", cwd=tmp_path)
    assert result.returncode == 2
    assert "unrecognized arguments: --config anything.yaml" in result.stderr
