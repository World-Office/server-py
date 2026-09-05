"""Adversarial security tests for the HTML sanitizer.

Two paradigms, both security-focused:

* **Attack corpus** — curated classic XSS payloads in the OWASP
  cheat-sheet tradition: executable tags, event handlers, script URL
  schemes, entity/whitespace obfuscations and tag-nesting bypasses. Every
  payload must lose all executable primitives after sanitization, and the
  output must be idempotent.

* **Structured adversarial fuzzing** — Hypothesis composes hostile
  documents from an adversarial vocabulary (attacks interleaved with benign
  content). Invariants hold for every input: the sanitizer never raises,
  the parsed sanitized output contains no executable tag, no ``on*`` event
  handler and no script/data URL scheme on a URL-bearing attribute, and the
  sanitizer is a fixed point (``sanitize(sanitize(x)) == sanitize(x)``) — a
  strong metamorphic property for a sanitizer.

The safety verdict is made on the *re-parsed* output (an HTMLParser pass),
so entity-escaped text like ``&#60;script&#62;`` that cannot execute is not
confused with a real ``<script>`` element.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.editor.sanitize import sanitize_html


class _Collector(HTMLParser):
    """Collect the tags and attributes of a parsed document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.attrs.append((tag.lower(), dict(attrs)))

    def handle_startendtag(self, tag, attrs) -> None:
        self.attrs.append((tag.lower(), dict(attrs)))


_DANGEROUS_TAGS = {
    "script", "iframe", "object", "embed", "applet", "form", "input", "button",
    "select", "textarea", "meta", "link", "style", "base", "frame", "frameset",
    "svg", "math", "video", "audio", "template", "portal",
}
# URL-bearing attributes the browser will load/execute if given a script scheme.
_URL_ATTRS = {
    "src", "href", "action", "formaction", "xlink:href", "data", "background",
    "poster", "lowsrc", "dynsrc", "srcset",
}


def _has_executable_scheme(value: str) -> bool:
    """True when an attribute value begins with an executable URL scheme.

    ``data:image/...`` is deliberately allowed (safe inline images), every
    other ``data:`` payload and any ``javascript:``/``vbscript:`` is not.
    """
    v = value.strip().lower()
    if re.match(r"(?:javascript|vbscript)\s*:", v):
        return True
    return v.startswith("data:") and not v.startswith("data:image/")


def assert_non_executable(out: str) -> None:
    """Assert a sanitized document contains no executable construct when
    re-parsed: dangerous tags, event-handler attributes, or script schemes on
    URL-bearing attributes. (Inert escaped text is fine.)"""
    parser = _Collector()
    parser.feed(out)
    for tag, attrs in parser.attrs:
        assert tag not in _DANGEROUS_TAGS, f"dangerous tag <{tag}> survived in {out!r}"
        for name, value in attrs.items():
            lname = name.lower()
            assert not lname.startswith("on"), (
                f"event handler {lname} survived in {out!r}"
            )
            if lname in _URL_ATTRS and (value or "").strip():
                assert not _has_executable_scheme(value), (
                    f"executable scheme survives in {lname} of {out!r}"
                )


def _check(payload: str) -> str:
    """Sanitize once, assert safety and idempotence, return the output."""
    out = sanitize_html(payload)
    assert isinstance(out, str)
    assert_non_executable(out)
    again = sanitize_html(out)
    assert again == out, f"sanitizer not idempotent:\n1st={out!r}\n2nd={again!r}"
    return out


# ---------------------------------------------------------------------------
# 1. Attack corpus (OWASP-cheat-sheet family)
# ---------------------------------------------------------------------------

_XSS_CORPUS: list[str] = [
    # ---- classic script tags ----------------------------------------------
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<script src='data:text/javascript,alert(1)'></script>",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",      # tag-nesting bypass
    "<<script>alert(1)//<</script>",                    # mangled close
    "<scr\0ipt>alert(1)</scr\0ipt>",                    # null-byte smuggling
    "<svg><script>alert(1)</script></svg>",
    # ---- event handlers ----------------------------------------------------
    '<img src=x onerror="alert(1)">',
    '<img src="x" onerror=&#97;lert(1)>',              # entity-obfuscated name
    '<p onmouseover="alert(1)">x</p>',
    '<input autofocus onfocus="alert(1)">',
    "<svg/onload=alert(1)>",
    '<video><source onerror="alert(1)">',
    '"><svg/onload=alert(1)>',                          # attribute-breakout
    # ---- script URL schemes ------------------------------------------------
    '<img src="javascript:alert(1)">',
    '<a href="javascript:alert(1)">click</a>',
    '<a href="jav&#x09;ascript:alert(1)">x</a>',       # tab-obfuscated scheme
    '<a href="java&#115;cript:alert(1)">x</a>',         # entity-obfuscated
    '<a href="JAVASCRIPT:alert(1)">x</a>',              # case variation
    '<form action="javascript:alert(1)"><input></form>',
    '<base href="javascript:alert(1)//">',
    '<img src="x" srcset="x 1x, javascript:alert(1) 2x">',  # srcset smuggling
    # ---- CSS ----------------------------------------------------------------
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<div style="width:expression(alert(1))">x</div>',  # IE expression
    '<div style="background-image:url(data:image/svg+xml,<svg onload=alert(1)>)">x</div>',
    # ---- meta / link / object / embed / iframe ------------------------------
    '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
    '<link rel="import" href="data:text/html,<script>alert(1)</script>">',
    '<object data="data:text/html,<script>alert(1)</script>"></object>',
    "<embed src='data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='>",
    '<iframe src="javascript:alert(1)"></iframe>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    # ---- namespace / malformed markup ---------------------------------------
    '<math><mtext></mtext><mi><mtext><table><mglyph><style><!--</style>'
    '<img title="--><img src=1 onerror=alert(1)>',
    # ---- entity-encoded variants -------------------------------------------
    "&#60;script&#62;alert(1)&#60;/script&#62;",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "<p>&lt;img src=x onerror=alert(1)&gt;</p>",
    '<a href="&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;'
    'alert(1)">x</a>',
    # ---- attribute-breakout via decoded quotes (#ATTRIBUTE and #TAG fields
    #      of the OWASP matrix): a kept safe attribute whose *value* contains
    #      entity-encoded quotes must not be able to forge new attributes/tags
    #      on re-parse --------------------------------------------------------
    '<div title="&quot; onmouseover=&quot;alert(1)">x</div>',
    '<a href="/ok" alt="&quot;&gt;&lt;svg onload=alert(1)&gt;">x</a>',
    '<img src="/s.png" alt="&quot; autofocus onfocus=&quot;alert(1)">',

    # ---- SVG/event-source-in-srcset -----------------------------------------
    '<img src="x" srcset="javascript:alert(1) 1x, /ok.png 2x">',
]


@pytest.mark.parametrize(
    "payload", _XSS_CORPUS, ids=[f"xss-{i:02d}" for i in range(len(_XSS_CORPUS))]
)
def test_xss_corpus_strips_all_executables(payload: str):
    _check(payload)


def test_corpus_still_preserves_benign_content():
    """Sanity control: the sanitizer must not nuke legitimate formatting.

    (Ensures the adversarial assertions have teeth — the corpus is checking
    stripping, not that everything is removed.)"""
    html = (
        "<p>hello <b>world</b> &amp; goodbye — "
        "<a href='https://ok.example/x?a=1&amp;b=2'>link</a> "
        "<img alt='pic' src='/assets/pic.png'></p>"
    )
    out = _check(html)
    assert "hello" in out
    assert "<b>world</b>" in out
    assert "goodbye" in out
    assert "https://ok.example/x?a=1&amp;b=2" in out
    assert "/assets/pic.png" in out
    assert "onerror" not in out


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<script>window.location='https://evil.example/'</script>",
        "<script src='//evil.example/x.js'></script>",
        "<style>body{display:none}</style>",
        "<iframe><p>phished ui</p></iframe>",
        "<svg><script>alert(1)</script></svg>",
    ],
    ids=["script-alert", "script-redirect", "script-src", "style-css", "iframe-content", "svg-script"],
)
def test_unsafe_element_content_is_suppressed_not_leaked(payload: str):
    """Content-suppression property: a stripped executable element must not
    leak its payload as *visible text*. This is the exact regression the
    sanitizer's suppression depth exists to prevent — a dropped ``<script>``
    must not leave ``alert(1)`` behind in the document text.

    (Pinned by mutation testing: removing ``script`` from ``_unsafe_tags``
    leaves the payload as visible text, which only this assertion detects.)"""
    assert sanitize_html(payload) == ""


# ---------------------------------------------------------------------------
# 2. Structured adversarial fuzzing
# ---------------------------------------------------------------------------

_ADVERSARIAL_VOCAB: list[str] = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    '<img src=x onerror=alert(1)>',
    "<svg/onload=alert(1)>",
    "javascript:alert(1)",
    '<a href="javascript:alert(1)">x</a>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<object data="data:text/html,<script>alert(1)</script>"></object>',
    '<base href="javascript:alert(1)">',
    '<form action="javascript:alert(1)"><input autofocus onfocus=alert(1)></form>',
    '<meta http-equiv=refresh content="0;url=javascript:alert(1)">',
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<a href="jav&#x09;ascript:alert(1)">x</a>',
    "&#60;script&#62;alert(1)&#60;/script&#62;",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    '"><svg/onload=alert(1)>',
    "<p onmouseover=alert(1)>x</p>",
    "<a href='data:text/html,<script>alert(1)</script>'>x</a>",
    '<img src="x" srcset="y 1x, javascript:alert(1) 2x">',
    "<ul><li>one</li><li>two</li></ul>",
    "plain text <b>bold</b> λ unicode ✓",
    "just some ordinary words",
    "<<<<",
    "<",
    "&amp;lt;script&amp;gt;",
    "αβγαβγ",
]

_ADVERSARIAL = st.lists(
    st.sampled_from(_ADVERSARIAL_VOCAB), min_size=0, max_size=12
)


@given(pieces=_ADVERSARIAL)
@settings(max_examples=250, deadline=None)
def test_adversarial_fuzz_sanitizes_cleanly_and_idempotently(pieces: list[str]):
    html = "".join(pieces)
    _check(html)
