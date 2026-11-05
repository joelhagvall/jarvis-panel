#!/usr/bin/env python3
"""The NAME=value files in ~/.config/hallpanel (server.env and the key files), read in one place: the last
assignment of a name wins, an empty one too, and one pair of matching quotes around a value goes. Keep the
files to plain NAME=value lines: systemd reads server.env too, and deploy_jarvis.sh sources it with sh.
Standard library only."""
import os
from pathlib import Path


def env_file(file):
    """The NAME=value lines of ~/.config/hallpanel/<file>, or {} when there is none."""
    path = Path.home() / ".config" / "hallpanel" / file
    if not path.is_file():
        return {}
    pairs = (line.strip().partition("=") for line in path.read_text().splitlines())
    return {key.strip(): unquote(value.strip()) for key, sep, value in pairs if sep}


def unquote(value):
    """value without one pair of matching quotes around it."""
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def load_key_file(name="OPENAI_API_KEY", file="openai.env"):
    """Put the key `name` from ~/.config/hallpanel/<file> into the environment if it is not set."""
    if not os.environ.get(name) and (value := env_file(file).get(name)):
        os.environ[name] = value
