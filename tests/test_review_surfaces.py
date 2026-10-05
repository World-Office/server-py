"""Review surfaces: track changes, accept/reject, comments, version history.

feature register: F-100 F-101 F-102 F-104

Audit trail: these four review features were `missing` in the register; each
is now closed on real evidence, per the 4-step audit (surfaces in
web/index.html -> wiring in web/editor.js -> serialization in
src/editor/converter.py + odt_converter.py -> endpoints in
src/editor/router.py + src/lib/store.py):

  F-100 track changes on/off  -- btn-track-changes toggles a `track-on` mode;
                                beforeinput routes edits into
                                <ins class="track-insert"> / <del class=
                                "track-delete"> (data-author). Those markers
                                round-trip through DOCX (w:ins / w:del +
                                w:delText) and ODT change-marks -> L1.
  F-101 accept / reject       -- btn-review-changes opens the review panel,
                                which lists each tracked region with Accept /
                                Reject (acceptChange / rejectChange). The
                                accepted/rejected content serializes to real
                                DOCX -> L1.
  F-102 comment threads       -- add + delete plus anchored serialization are
                                proven (span.comment <-> word/comments.xml with
                                commentRangeStart/End/Reference; ODT
                                office:annotation). REPLY threading and a
                                RESOLVE lifecycle do NOT exist in the editor UI
                                -> partial (see features.yaml divergence).
  F-104 version history       -- btn-history lists snapshots (one per save)
                                from GET /api/documents/{id}/versions and
                                restores via POST .../versions/{ts}/restore;
                                exercised through the real endpoints ->
                                L4 behaviour.

Static surface assertions read the shipped web assets straight from disk
(the exact files the seeder inventories), so a button/wiring rename breaks
the pin.
"""

from __future__ import annotations

import io
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt, odt_to_html
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir

# ---------------------------------------------------------------------------
# Shipped web assets (the seeder inventories the same paths)
# ---------------------------------------------------------------------------

_WEB = Path(__file__).resolve().parent.parent / "web"
INDEX_HTML = (_WEB / "index.html").read_text(encoding="utf-8")
EDITOR_JS = (_WEB / "editor.js").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    """TestClient with lifespan running; backing store on ``client.test_store``."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")
    store = DocumentStore(db, content)
    cfg = Config(database=db, content_dir=content, jwt_secret="test-secret")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(editor_router)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    store.close()
    wipe_db(db)
    wipe_dir(content)


# ---------------------------------------------------------------------------
# F-100 Track changes on/off
# ---------------------------------------------------------------------------


def test_track_changes_toggle_surface_wired():
    """btn-track-changes exists and is wired as a track-mode toggle.

    The toggle flips a `track-on` editor class and aria-pressed state; while
    on, beforeinput routes insertions/deletions into tracked markers.
    """
    assert 'id="btn-track-changes"' in INDEX_HTML
    # click wiring: the button toggles the mode, not a one-shot command
    assert "setTrackChanges(!trackChangesOn)" in EDITOR_JS
    assert 'editor.classList.toggle("track-on", on)' in EDITOR_JS
    assert 'btn.setAttribute("aria-pressed", on ? "true" : "false")' in EDITOR_JS
    # authoring: input events become ins/del tracked elements
    assert 'insertTracked("ins", e.data)' in EDITOR_JS
    assert "wrapRangeInDel(ranges[0])" in EDITOR_JS
    assert 'node.className = tag === "ins" ? "track-insert" : "track-delete"' in EDITOR_JS


def test_track_changes_docx_roundtrip_preserves_markers():
    """Tracked ins/del markers survive HTML -> DOCX -> HTML (F-100, F-101)."""
    html = (
        '<p>Before <ins class="track-insert" data-author="Alice">new words</ins> '
        'and <del class="track-delete" data-author="Bob">old words</del> after.</p>'
    )
    docx = html_to_docx(html)
    out = docx_to_html(docx)
    assert '<ins class="track-insert" data-author="Alice">new words</ins>' in out, out
    assert '<del class="track-delete" data-author="Bob">old words</del>' in out, out
    assert "Before" in out and "after." in out, out


def test_track_changes_docx_physical_ooxml():
    """The DOCX physically carries w:ins / w:del with author + w:delText.

    Deletions store their removed run as w:delText (never w:t), so Word
    renders them as struck-through rather than as normal text.
    """
    html = (
        '<p>Before <ins class="track-insert" data-author="Alice">new words</ins> '
        'and <del class="track-delete" data-author="Bob">old words</del> after.</p>'
    )
    docx = html_to_docx(html)
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    assert "<w:ins " in xml and 'w:author="Alice"' in xml, xml
    assert "<w:del " in xml and 'w:author="Bob"' in xml, xml
    assert "<w:delText" in xml, xml
    # the removed text lives in delText, not in a live w:t run
    assert re.search(r"<w:delText[^>]*>old words</w:delText>", xml), xml
    assert not re.search(r"<w:t[^>]*>old words</w:t>", xml), xml


def test_track_changes_odt_roundtrip_preserves_markers():
    """Tracked markers survive HTML -> ODT -> HTML (change-starts registry)."""
    html = (
        '<p>A <ins class="track-insert" data-author="Alice">new text</ins> '
        'B <del class="track-delete" data-author="Carol">removed</del> C</p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert '<ins class="track-insert" data-author="Alice">new text</ins>' in out, out
    assert '<del class="track-delete" data-author="Carol">removed</del>' in out, out
    assert "A " in out and " B" in out and " C" in out, out


# ---------------------------------------------------------------------------
# F-101 Accept / reject change
# ---------------------------------------------------------------------------

# The review panel renders exactly these actions; accept/reject transform the
# DOM the way editor.js's acceptChange/rejectChange do (mirrored below at the
# HTML-contract level, then pushed through the real DOCX writer):
#   accept  insert  -> unwrap (text stays, marker goes)
#   reject  insert  -> drop (text and marker go)
#   accept  delete  -> drop (text and marker go)
#   reject  delete  -> unwrap (text stays, marker goes)


def _accept_insert(html: str) -> str:  # mirrors acceptChange(kind=insert)
    return re.sub(r'<ins class="track-insert"[^>]*>(.*?)</ins>', r"\1", html, flags=re.S)


def _reject_insert(html: str) -> str:  # mirrors rejectChange(kind=insert)
    return re.sub(r'<ins class="track-insert"[^>]*>.*?</ins>', "", html, flags=re.S)


def _accept_delete(html: str) -> str:  # mirrors acceptChange(kind=delete)
    return re.sub(r'<del class="track-delete"[^>]*>.*?</del>', "", html, flags=re.S)


def _reject_delete(html: str) -> str:  # mirrors rejectChange(kind=delete)
    return re.sub(r'<del class="track-delete"[^>]*>(.*?)</del>', r"\1", html, flags=re.S)


def _body_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


_TRACKED_PARAGRAPH = (
    '<p>Start <ins class="track-insert" data-author="Alice">new</ins> '
    'middle <del class="track-delete" data-author="Bob">gone</del> end.</p>'
)


def test_review_panel_accept_reject_surface_wired():
    """btn-review-changes opens a panel; each tracked region gets Accept/Reject.

    The verdict hinges on the Accept/Reject actions existing and being wired
    to acceptChange/rejectChange (the exact functions we mirror in the
    serialization tests below).
    """
    assert 'id="btn-review-changes"' in INDEX_HTML
    assert 'id="review-panel"' in INDEX_HTML
    assert 'id="review-list"' in INDEX_HTML
    assert "btn-review-changes" in EDITOR_JS
    assert "function renderReviewList" in EDITOR_JS
    assert 'acc.textContent = "Accept"' in EDITOR_JS
    assert 'rej.textContent = "Reject"' in EDITOR_JS
    assert "function acceptChange(el, kind)" in EDITOR_JS
    assert "function rejectChange(el, kind)" in EDITOR_JS


def test_accept_change_semantics_serialize_to_docx():
    """Accepting both changes: inserted text kept, deleted text dropped.

    The resulting plain document round-trips through the DOCX writer with no
    tracked markers left behind.
    """
    accepted = _accept_delete(_accept_insert(_TRACKED_PARAGRAPH))
    assert "track-insert" not in accepted and "track-delete" not in accepted

    docx = html_to_docx(accepted)
    out = docx_to_html(docx)
    text = _body_text(out)
    assert "new" in text, text          # inserted text persists
    assert "gone" not in text, text     # deleted text is dropped
    assert "track-insert" not in out and "track-delete" not in out, out


def test_reject_change_semantics_serialize_to_docx():
    """Rejecting both changes: inserted text dropped, deleted text restored.

    Rejecting a deletion restores the removed run as normal text in the
    serialized document.
    """
    rejected = _reject_delete(_reject_insert(_TRACKED_PARAGRAPH))
    assert "track-insert" not in rejected and "track-delete" not in rejected

    docx = html_to_docx(rejected)
    out = docx_to_html(docx)
    text = _body_text(out)
    assert "gone" in text, text         # rejected deletion text is restored
    assert "new" not in text, text      # rejected insertion text is dropped
    assert "track-insert" not in out and "track-delete" not in out, out


def test_accept_insert_reject_delete_mix_serialize_to_docx():
    """Mixed review: accepted insertion + rejected deletion both persist."""
    mixed = _reject_delete(_accept_insert(_TRACKED_PARAGRAPH))
    docx = html_to_docx(mixed)
    out = docx_to_html(docx)
    text = _body_text(out)
    assert "new" in text and "gone" in text, text
    assert "track-insert" not in out and "track-delete" not in out, out


# ---------------------------------------------------------------------------
# F-102 Comment threads (add / reply / resolve)
# ---------------------------------------------------------------------------


def test_comment_add_delete_surface_wired():
    """btn-comment adds an anchored comment; btn-comments toggles the list.

    The panel supports locating (Go to) and deleting a comment — flat add /
    delete, no thread replies (see test_comment_thread_reply_resolve_missing).
    """
    assert 'id="btn-comment"' in INDEX_HTML
    assert 'id="btn-comments"' in INDEX_HTML
    assert 'id="comment-dialog"' in INDEX_HTML
    assert 'id="comments-panel"' in INDEX_HTML
    assert "function confirmCommentDialog" in EDITOR_JS
    assert 'span.className = "comment"' in EDITOR_JS
    assert 'span.setAttribute("data-comment", body)' in EDITOR_JS
    assert "function renderCommentsList" in EDITOR_JS
    assert 'del.textContent = "Delete"' in EDITOR_JS
    assert "function deleteComment(el)" in EDITOR_JS


def test_comment_docx_roundtrip_part_and_markers():
    """An anchored comment round-trips DOCX with a real word/comments.xml part.

    Physical assertions: comments.xml carries the w:comment with author +
    body; document.xml wraps the anchored runs in commentRangeStart/End and
    terminates them with a commentReference marker.
    """
    html = (
        '<p>Main text <span class="comment" data-author="Alice Smith" '
        'data-comment="Please check this, thanks.">anchored words</span> continues.</p>'
    )
    docx = html_to_docx(html)
    out = docx_to_html(docx)
    assert (
        '<span class="comment" data-author="Alice Smith" '
        'data-comment="Please check this, thanks.">anchored words</span>'
    ) in out, out

    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert "word/comments.xml" in z.namelist(), z.namelist()
        comments_xml = z.read("word/comments.xml").decode("utf-8", "replace")
        document_xml = z.read("word/document.xml").decode("utf-8", "replace")
    assert "<w:comment " in comments_xml, comments_xml
    assert 'w:author="Alice Smith"' in comments_xml, comments_xml
    assert "Please check this, thanks." in comments_xml, comments_xml
    assert "w:commentRangeStart" in document_xml, document_xml
    assert "w:commentRangeEnd" in document_xml, document_xml
    assert "w:commentReference" in document_xml, document_xml


def test_comment_odt_roundtrip_annotation():
    """An anchored comment round-trips ODT via a real office:annotation."""
    from odf import teletype
    from odf.office import Annotation
    from odf.opendocument import load

    html = (
        '<p><span class="comment" data-author="Alice Smith" '
        'data-comment="Review note, please fix.">anchored text</span> after.</p>'
    )
    odt = html_to_odt(html)
    doc = load(io.BytesIO(odt))
    anns = doc.text.getElementsByType(Annotation)
    assert len(anns) == 1, [a.qname for a in anns]
    assert "Review note, please fix." in teletype.extractText(anns[0]), teletype.extractText(anns[0])

    out = odt_to_html(odt)
    assert (
        '<span class="comment" data-author="Alice Smith" '
        'data-comment="Review note, please fix.">anchored text</span>'
    ) in out, out


def test_comment_thread_reply_resolve_missing():
    """# NOTE: existing behaviour — comments are flat add/delete only.

    The comments panel renders Go to + Delete actions and nothing else; there
    is no reply threading and no resolve lifecycle in the editor UI, so
    OnlyOffice's "comment thread" (add/reply/resolve) parity stays partial —
    see the F-102 divergence entry in features.yaml. Pinning the boundary so
    a future reply/resolve implementation has to actively move this pin.
    """
    start = EDITOR_JS.index("function renderCommentsList")
    end = EDITOR_JS.index("function deleteComment")
    panel_impl = EDITOR_JS[start:end]
    assert "Go to" in panel_impl and "Delete" in panel_impl, panel_impl
    for label in ("Reply", "Resolve", "Mark resolved", "Unresolve"):
        assert label not in panel_impl, label


# ---------------------------------------------------------------------------
# F-104 Version history
# ---------------------------------------------------------------------------


def test_version_history_surface_wired():
    """btn-history (menu) opens the version dialog backed by the /versions API.

    Each non-current snapshot gets a Restore button; restoring POSTs to
    /versions/{ts}/restore and reloads the editor.
    """
    assert 'id="btn-history"' in INDEX_HTML
    assert 'id="version-history-dialog"' in INDEX_HTML
    assert 'id="version-list"' in INDEX_HTML
    assert 'id="btn-version-close"' in INDEX_HTML
    assert "function openVersionHistory" in EDITOR_JS
    assert "function renderVersionList" in EDITOR_JS
    assert "function restoreVersion" in EDITOR_JS
    assert 'api("versions")' in EDITOR_JS
    assert 'api("versions/" + encodeURIComponent(ts) + "/restore")' in EDITOR_JS
    assert 'btn.textContent = t("VersionHistory.Restore")' in EDITOR_JS


def test_version_history_snapshot_per_save_newest_first(client):
    """Every save snapshots a version; GET /versions returns newest first."""
    res = client.post("/api/documents/new", params={"format": "docx"})
    assert res.status_code == 200
    doc_id = res.json()["doc_id"]

    sizes: list[int] = []
    for html in ("<p>v one</p>", "<p>v two</p>", "<p>v three</p>"):
        r = client.post(f"/api/documents/{doc_id}/save", json={"html": html})
        assert r.status_code == 200, r.text
        sizes.append(r.json()["size"])

    versions = client.get(f"/api/documents/{doc_id}/versions").json()["versions"]
    # blank doc from /new + 3 saves
    assert len(versions) == 4, versions
    # newest first: the three saves appear head-first in reverse save order
    assert [v["size"] for v in versions[:3]] == list(reversed(sizes)), versions


def test_version_history_restore_rewinds_tracked_content(client):
    """Restoring a snapshot rewinds content — including tracked markers.

    Links F-104 to F-100: the restored snapshot is the DOCX produced from a
    save whose HTML contained a tracked insertion, and the restored bytes
    still carry the w:ins marker when read back.
    """
    res = client.post("/api/documents/new", params={"format": "docx"})
    doc_id = res.json()["doc_id"]

    tracked_html = (
        '<p>Start <ins class="track-insert" data-author="Alice">added</ins> end.</p>'
    )
    client.post(f"/api/documents/{doc_id}/save", json={"html": tracked_html})
    client.post(f"/api/documents/{doc_id}/save", json={"html": "<p>final</p>"})

    versions = client.get(f"/api/documents/{doc_id}/versions").json()["versions"]
    assert len(versions) == 3, versions
    ts_tracked = versions[-2]["ts"]  # oldest-but-one = the tracked save

    r = client.post(f"/api/documents/{doc_id}/versions/{ts_tracked}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    contents = client.get(f"/api/documents/{doc_id}/contents")
    assert contents.status_code == 200
    html = docx_to_html(contents.content)
    assert "track-insert" in html and "added" in html, html

    # restore appends pre-restore + restored snapshots (3 -> 5)
    after = client.get(f"/api/documents/{doc_id}/versions").json()["versions"]
    assert len(after) == 5, after
    assert after[0]["size"] == len(contents.content), after


def test_version_history_restore_unknown_returns_404(client):
    """Restoring a snapshot id that does not exist is a typed 404."""
    res = client.post("/api/documents/new", params={"format": "docx"})
    doc_id = res.json()["doc_id"]
    r = client.post(f"/api/documents/{doc_id}/versions/9999999999/restore")
    assert r.status_code == 404
    assert "version" in r.json()["error"].lower()
