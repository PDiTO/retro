from __future__ import annotations

import pytest

from retro.redact import excerpt, find_secrets, redact
from tests.conftest import SOMEONE_HOME

# Fake secrets are assembled at runtime so this file never contains one that
# `retro audit` would flag.
FAKES = {
    "anthropic-key": "sk-" + "ant-" + "a1" * 20,
    "openai-key": "sk-" + "proj-" + "b2" * 20,
    "github-token": "gh" + "p_" + "c3" * 18,
    "aws-access-key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "slack-token": "xo" + "xb-" + "1234567890-abcdef",
    "jwt": "ey" + "J" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
    "hex-secret": "0x" + "ab" * 32,
    "email": "someone" + "@" + "example.org",
}


@pytest.mark.parametrize(("rule", "secret"), FAKES.items())
def test_finds_and_redacts(rule: str, secret: str) -> None:
    text = f"value: {secret} end"
    assert [m.rule for m in find_secrets(text)] == [rule]
    assert redact(text) == f"value: [{rule}] end"


def test_assignment_and_bearer() -> None:
    assert redact("API_KEY=" + "hunter2hunter2") == "[assignment]"
    assert redact('{"api' + '_key": "' + "hunter2hunter2" + '"}') == '{"[assignment]}'
    assert redact("def f(tokens: list[str]) -> None:") == "def f(tokens: list[str]) -> None:"
    assert redact("Authorization: Bearer " + "z" * 30) == "Authorization: [bearer-token]"


def test_private_key_block() -> None:
    block = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    assert redact(f"key:\n{block}\ndone") == "key:\n[private-key-block]\ndone"


def test_home_directories() -> None:
    home = "/ho" + "me/alice"
    assert redact(f"{home}/src and {SOMEONE_HOME}/x") == "~/src and ~/x"
    assert redact("/tmp/run/home/data.json") == "/tmp/run/home/data.json"


def test_leaves_ordinary_text_alone() -> None:
    text = "ran cargo test in 0x1f steps, commit 1a2b3c4d"
    assert find_secrets(text) == []
    assert redact(text) == text


def test_excerpt_flattens_and_truncates() -> None:
    assert excerpt("a\n  b\tc") == "a b c"
    long = excerpt("word " * 100, limit=20)
    assert len(long) == 20
    assert long.endswith("…")
