# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Fuzz harness: clone URL safety and credential redaction.

Contracts checked, in the code's own words:

- :func:`reject_credentialed_url` refuses "a clone URL that carries a
  credential of its own", raising ``UnsafeCloneUrlError``: "The
  configured token, wherever it appears and in any form it can be
  recovered from -- percent-encoded to any depth, or split by a
  character the parse discards"; "A password under any scheme"; "A
  username over HTTP(S) only"; "Path parameters, a query string or a
  fragment over HTTP(S)"; and "A remote helper's address ... is checked
  as the URL it is, by these same rules".  It raises nothing else.
- :func:`redact_url`: "A hierarchical URL loses its userinfo, query and
  fragment; a scp-like one its user; a remote helper's address is
  redacted in turn."
- :func:`redact_text`: "Each secret given ... becomes REDACTED wherever
  it appears; so does the userinfo of every ``scheme://user:secret@``
  in it."
- :func:`remote_location` gives "a URL's scheme, host, port and path",
  "without any credential"; "a query or a fragment is never part of
  the path".
- :func:`helper_address`: "Nested helpers are unwrapped too: what the
  innermost receives is what a credential would reach."

Each oracle builds its URL from parts and derives the expected answer
from those parts, never from the code under test.

Run ``python tests/fuzz/fuzz_url_safety.py`` to fuzz from the seeds, or
name input files to replay them once.
"""

from __future__ import annotations

import string
import sys
from pathlib import Path
from urllib.parse import quote

_ROOT = Path(__file__).resolve().parents[2]
for _entry in (_ROOT, _ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from source_clone.github_url_safety import (  # noqa: E402
    UnsafeCloneUrlError,
    reject_credentialed_url,
)
from source_clone.url_credentials import (  # noqa: E402
    REDACTED,
    RemoteLocation,
    helper_address,
    redact_text,
    redact_url,
    remote_location,
)
from tests.fuzz.harness_support import (  # noqa: E402
    ContractViolation,
    encode_seed,
    keep,
    libfuzzer_arguments,
    require,
    text_fields,
)

SCHEME_CHARACTERS = string.ascii_letters + string.digits + "+.-"
HELPER_CHARACTERS = SCHEME_CHARACTERS
#: RFC 3986 userinfo characters, ``:`` aside, with ``%`` for encodings.
USERINFO_CHARACTERS = string.ascii_letters + string.digits + "-._~!$&'()*+,;=%"
HOST_CHARACTERS = string.ascii_letters + string.digits + ".-"
PATH_CHARACTERS = string.ascii_letters + string.digits + "-._~/"
#: What ``urlparse`` discards, so what can split a token unseen.
SPLITTERS = "\t\r\n"
#: Printable ASCII without space or ``%``: the shape of a real token.  A
#: token holding ``%`` could be destroyed by the very decode that joins
#: it, leaving a URL that genuinely does not carry it.
TOKEN_CHARACTERS = "".join(chr(point) for point in range(0x21, 0x7F) if point != 0x25)


def _scheme(text: str, default: str) -> str:
    """A URL scheme from *text*: a letter, then scheme characters."""
    scheme = keep(text, SCHEME_CHARACTERS)
    return scheme if scheme[:1].isalpha() else default


def _helpers(text: str) -> str:
    """Zero or more ``<transport>::`` prefixes, one per ``,`` in *text*."""
    names = [keep(name, HELPER_CHARACTERS) for name in text.split(",")]
    return "".join(f"{name}::" for name in names if name[:1].isalpha())


def _must_refuse(url: str, token: str | None, why: str) -> None:
    """Require :func:`reject_credentialed_url` to refuse *url*."""
    try:
        reject_credentialed_url(url, token)
    except UnsafeCloneUrlError:
        return
    raise ContractViolation(f"accepted {url!r} ({why})")


def check_arbitrary_url(data: bytes) -> None:
    """Any value is refused or accepted, and no function here crashes on it.

    ``remote_location`` returns a location or None, ``redact_url`` and
    ``helper_address`` a string; none documents an exception.

    An accepted value must not hold the token literally, nor once
    percent-decoded, nor once a discarded character is removed.
    """
    url, token = text_fields(data, 2)
    token = keep(token, TOKEN_CHARACTERS)
    redact_url(url)
    remote_location(url)
    helper_address(url)
    try:
        reject_credentialed_url(url, token or None)
    except UnsafeCloneUrlError:
        return
    if token:
        joined = url.replace("\t", "").replace("\r", "").replace("\n", "")
        require(token not in url, f"accepted {url!r} holding the token")
        require(token not in joined, f"accepted {url!r} with a split token")


def check_password_refused(data: bytes) -> None:
    """A URL with a password is refused under any scheme."""
    helpers, scheme, user, password, host, path = text_fields(data, 6)
    url = (
        f"{_helpers(helpers)}{_scheme(scheme, 'ssh')}://"
        f"{keep(user, USERINFO_CHARACTERS)}:{keep(password, USERINFO_CHARACTERS)}"
        f"@{keep(host, HOST_CHARACTERS, 'example.org')}"
        f"/{keep(path, PATH_CHARACTERS)}"
    )
    _must_refuse(url, None, "it carries a password")


def check_http_userinfo_refused(data: bytes) -> None:
    """Over HTTP(S), any userinfo, a bare username included, is refused."""
    helpers, scheme, user, host, path = text_fields(data, 5)
    chosen = "https" if len(scheme) % 2 else "http"
    chosen = "".join(
        char.upper() if index < len(scheme) and scheme[index].isupper() else char
        for index, char in enumerate(chosen)
    )
    url = (
        f"{_helpers(helpers)}{chosen}://"
        f"{keep(user, USERINFO_CHARACTERS, 'token')}"
        f"@{keep(host, HOST_CHARACTERS, 'github.com')}"
        f"/{keep(path, PATH_CHARACTERS)}"
    )
    _must_refuse(url, None, "it carries userinfo over HTTP(S)")


def check_http_opaque_parts_refused(data: bytes) -> None:
    """Over HTTP(S), path parameters, a query or a fragment are refused."""
    host, path, part, kind = text_fields(data, 4)
    marker = ";?#"[len(kind) % 3]
    content = keep(part, USERINFO_CHARACTERS, "x")
    url = (
        f"https://{keep(host, HOST_CHARACTERS, 'github.com')}"
        f"/{keep(path, PATH_CHARACTERS.replace('/', ''), 'repo')}{marker}{content}"
    )
    _must_refuse(url, None, f"it carries {marker!r} content over HTTPS")


def _hidden_token(token: str, mode: int, position: int) -> str:
    """*token* in one of the forms the token rule must still recover."""
    cut = position % (len(token) + 1)
    splitter = SPLITTERS[position % len(SPLITTERS)]
    # quote() leaves letters, digits and "_.-~" alone, so encode every
    # byte explicitly: an ordinary token must still change form.
    encoded = "".join(f"%{byte:02X}" for byte in token.encode())
    forms = (
        token,
        encoded,
        quote(encoded, safe=""),
        token[:cut] + splitter + token[cut:],
        token[:cut] + quote(splitter, safe="") + token[cut:],
    )
    return forms[mode % len(forms)]


def check_token_refused(data: bytes) -> None:
    """The configured token is refused however it is hidden in the URL."""
    prefix, token, suffix, mode = text_fields(data, 4)
    # A '%' just before the token would decode together with its start,
    # making a URL that genuinely does not carry the token.
    prefix = prefix.replace("%", "")
    token = keep(token, TOKEN_CHARACTERS, "ghp_token")
    hidden = _hidden_token(token, len(mode), sum(mode.encode()))
    _must_refuse(prefix + hidden + suffix, token, "it carries the token")


def check_redact_url(data: bytes) -> None:
    """A hierarchical URL loses its userinfo, query and fragment."""
    helpers, scheme, userinfo, host, path, query, fragment = text_fields(data, 7)
    prefix = _helpers(helpers)
    scheme = _scheme(scheme, "")
    scheme_part = f"{scheme}:" if scheme else ""
    userinfo = "".join(char for char in userinfo if char not in "/?#")
    host = "".join(char for char in host if char not in "/?#@")
    path = "/" + "".join(char for char in path if char not in "?#")
    url = f"{prefix}{scheme_part}//{userinfo}@{host}{path}"
    if query:
        url += "?" + query
    if fragment:
        url += "#" + fragment
    expected = f"{prefix}{scheme_part}//{host}{path}"
    redacted = redact_url(url)
    require(redacted == expected, f"{url!r} redacted to {redacted!r}")


def check_redact_scp_url(data: bytes) -> None:
    """A scp-like URL loses its user."""
    user, host, path = text_fields(data, 3)
    user = "".join(char for char in user if char not in "/@:") or "git"
    host = "".join(char for char in host if char not in "/:@[]") or "example.org"
    url = f"{user}@{host}:{path}"
    redacted = redact_url(url)
    require(redacted == f"{host}:{path}", f"{url!r} redacted to {redacted!r}")


def check_redact_text_secrets(data: bytes) -> None:
    """No secret given survives in the redacted text.

    A secret containing ``*`` is skipped: :data:`REDACTED` is made of
    that character, so no replacement could remove it.
    """
    text, *secrets = text_fields(data, 4)
    secrets = [secret for secret in secrets if secret and "*" not in secret]
    redacted = redact_text(text, secrets)
    for secret in secrets:
        require(
            secret not in redacted,
            f"secret {secret!r} survives in {redacted!r}",
        )


def check_redact_text_userinfo(data: bytes) -> None:
    """The userinfo of a URL quoted in text is replaced by REDACTED."""
    before, scheme, userinfo, host, after = text_fields(data, 5)
    before = before.replace("@", "")
    after = after.replace("@", "")
    scheme = keep(scheme, string.ascii_letters, "https")
    userinfo = (
        "".join(
            char for char in userinfo if not char.isspace() and char not in "/@'\"<>*"
        )
        or "user:secret"
    )
    host = "".join(char for char in host if char not in "@") or "example.org"
    text = f"{before} {scheme}://{userinfo}@{host}{after}"
    redacted = redact_text(text)
    require(
        f"{userinfo}@" not in redacted,
        f"userinfo {userinfo!r} survives in {redacted!r}",
    )
    require(
        f"://{REDACTED}@" in redacted,
        f"no redaction marker in {redacted!r}",
    )


def check_remote_location(data: bytes) -> None:
    """The location of a hierarchical URL is its scheme, host, port and path."""
    helpers, scheme, userinfo, host, port, path, rest = text_fields(data, 7)
    prefix = _helpers(helpers)
    scheme = _scheme(scheme, "")
    scheme_part = f"{scheme}:" if scheme else ""
    authority = keep(host, HOST_CHARACTERS)
    if port[:1] == "[":
        authority = "[" + keep(port, string.hexdigits + ":") + "]"
    digits = keep(port, string.digits)[:5]
    if userinfo:
        authority = keep(userinfo, USERINFO_CHARACTERS + ":@", "u") + "@" + authority
    url = f"{prefix}{scheme_part}//{authority}"
    if digits:
        url += f":{digits}"
    path = "/" + keep(path, PATH_CHARACTERS) if path else ""
    url += path
    if rest:
        url += "?#"[len(rest) % 2] + rest
    bare_host = authority.rpartition("@")[2].strip("[]")
    expected = RemoteLocation(
        scheme=scheme.lower(),
        host=bare_host.lower().rstrip("."),
        port=int(digits) if digits else None,
        path=path,
    )
    location = remote_location(url)
    require(location == expected, f"{url!r} located at {location!r}")


def check_helper_address(data: bytes) -> None:
    """Every helper prefix is removed, however deeply nested."""
    helpers, address = text_fields(data, 2)
    address = "/" + address
    url = _helpers(helpers) + address
    unwrapped = helper_address(url)
    require(unwrapped == address, f"{url!r} unwrapped to {unwrapped!r}")


CHECKS = (
    check_arbitrary_url,
    check_password_refused,
    check_http_userinfo_refused,
    check_http_opaque_parts_refused,
    check_token_refused,
    check_redact_url,
    check_redact_scp_url,
    check_redact_text_secrets,
    check_redact_text_userinfo,
    check_remote_location,
    check_helper_address,
)


def check(data: bytes) -> None:
    """Run the check the first byte of *data* selects on the rest.

    Args:
        data: Raw fuzz input.

    Raises:
        ContractViolation: If the selected contract breaks.
    """
    if data:
        CHECKS[data[0] % len(CHECKS)](data[1:])


SEEDS = (
    encode_seed(0, "https://github.com/org/repo.git", "ghp_token"),
    encode_seed(0, "git@github.com:org/repo.git", ""),
    encode_seed(0, "https://user:pw@[unclosed/r.git", ""),
    # Found by this harness: a port past int()'s 4300-digit limit raised.
    encode_seed(0, "ssh://h:" + "9" * 4301 + "/r", ""),
    # Found by review: redact_url recursed once per remote helper.
    encode_seed(0, "a::" * 1200 + "https://host/repo", ""),
    encode_seed(0, "https://h/r.git?%2567hp_x", "ghp_x"),
    encode_seed(1, "", "ssh", "git", "", "gerrit.example.org", "proj.git"),
    encode_seed(1, "ext,fd", "git+ssh", "u", "s3cret", "host", "a/b"),
    encode_seed(2, "", "HTTPS", "x-access-token", "github.com", "org/r.git"),
    encode_seed(2, "git-remote-https", "h", "ghp_abc", "github.com", "o/r"),
    encode_seed(3, "github.com", "repo.git", "token=abc", ""),
    encode_seed(3, "github.com", "repo.git", "access_token=abc", "q"),
    encode_seed(3, "github.com", "repo.git", "frag", "ff"),
    encode_seed(4, "https://github.com/", "ghp_secret", "/r.git", ""),
    encode_seed(4, "https://github.com/", "ghp_secret", "/r.git", "e"),
    encode_seed(4, "https://", "ghp_secret", "@github.com/r.git", "ee"),
    encode_seed(4, "https://github.com/", "ghp_secret", "/r.git", "eeeeeeeee"),
    encode_seed(5, "", "https", "user:secret", "github.com", "/o/r", "q=1", "f"),
    encode_seed(5, "a,b", "", "t@k", "host", "/p", "", ""),
    encode_seed(6, "git", "github.com", "org/repo.git"),
    encode_seed(7, "fatal: ghp_abc denied", "ghp_abc", "", "denied"),
    encode_seed(8, "fetching", "https", "user:ghp_abc", "github.com/o/r", " done"),
    encode_seed(9, "", "ssh", "git", "gerrit.example.org.", "29418", "p", "q"),
    encode_seed(9, "ext", "https", "", "", "[::1", "", ""),
    encode_seed(10, "a,b+c,d.e", "srv/repo"),
    encode_seed(
        0,
        "https://"
        + "%25" * 40
        + "github.com/"
        + "segment/" * 20
        + "repo.git#"
        + "f" * 40,
        "ghp_0123456789abcdefghijklmnopqrstuvwxyz",
    ),
)


def main() -> None:
    """Fuzz :func:`check` from the seeds, or replay named inputs."""
    import atheris  # noqa: PLC0415
    from atheris.instrument_bytecode import instrument_all  # noqa: PLC0415

    instrument_all()
    atheris.Setup(libfuzzer_arguments(sys.argv, SEEDS), check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
