# Cloud GUI E2E Suite

Browser-level end-to-end tests for the **full user experience** on the live
stack: OpenCloud web (`cloud.graphwiz.ai`) + embedded World-Office editors
(`editor.cloud.graphwiz.ai`).

## Run

```sh
cd server/opencloud-docserver/e2e
python3 -m pytest              # full suite (~8 min)
python3 -m pytest -m wopi      # protocol tests only (no browser, <1 min)
python3 -m pytest test_word_editing_gui.py -v
```

Configuration via env vars: `E2E_BASE`, `E2E_EDITOR_BASE`, `E2E_USER`,
`E2E_PASS`, `E2E_HEADLESS`.

## Coverage

| Module | # | What it proves |
|---|---|---|
| `test_auth_gui.py` | 1 | login, wrong-password rejection, empty-form rejection, logout |
| `test_files_gui.py` | 5 | GUI folder/file creation (+New), rename, delete, folder navigation, WebDAV↔GUI parity |
| `test_button_sweep_gui.py` | 15 → | every top-level action button (new file/folder/upload/menu/refresh/settings/help) works without JS error; share button leads to share sidebar |
| `test_editors_gui.py` | 6 → | every office type opens an editor from the GUI: docx (WO canvas, stable), odt/xls/ppt/txt/pdf (parity gaps documented ×3 xfail) |
| `test_word_editing_gui.py` | 5 → | **click-into-content regression**, caret focus, typing/backspace → WOPI autosave (Lock→PutFile→Unlock 200); structural keys; reload; only persistence xfail |
| `test_multisession_gui.py` | 2 | two independent sessions render the same document concurrently |
| `test_editor_depth_gui.py` | 5 → | editor shell (canvas + templates), Ctrl+S save, undo/redo stability, viewport resize, direct-URL reload, paragraph bursts |
| `test_files_depth_gui.py` | 6 → | search, details sidebar, view modes, delete → server trash-bin (PROPFIND), GUI trash listing (xfail: folders-only bug), "Open with..." menu end-to-end |
| `test_document_edit_depth.py` *(NEW)* | **6** | WOPI CheckFileInfo field verifications (BaseFileName/Size/UserCanWrite/Version/LastModifiedTime), lock round-trip (LOCK→GetLock→UNLOCK), foreign-lock refresh 409, lock-required save 409, two-client last-write-wins congruence, GUI Ctrl+S preserves OOXML zip structure and seed text |
| `test_editor_menu_gui.py` *(NEW)* | 7 (5 xf) | full File menu inventory via GUI, Create New panel templates listing (all 5 templates scanned + xfail template fetch 404 bugs), Version History sidebar opens, Document Info opens, Close Editor returns to files |
| `test_paper_collaboration.py` *(NEW)* | **14** (2 xf) | full **scientist collaboration scenario**: valid manuscript seed, Ada opens in WO, Ada GUI save preserves all sections, Ada GUI shares folder with Bob (Graph invite), Bob sees the shared project (OCS `shared_with_me`), DAV jail mount listing (xfail: stale-jail bug), Bob opens+edits via his own WOPI token, concurrent Ada+Bob saves last-write-wins, Version History shows prior versions, public review link downloads read-only, GUI journal download functionalities, rename keeps Bob connected, outsider Eve denied access, trash restore via server API |
| `test_share_lifecycle.py` *(NEW)* | **7** | **share lifecycle security**: unshare revokes Bob's access immediately, re-share restores write access, collaborator without the share bit cannot re-share to a third user (Carol via Graph provisioning), password-protected public link blocks anonymous downloads and accepts the link password, read-only link forbids PUT/MKCOL/PROPPATCH, deleted link stops serving bytes, GUI share panel lists the API-invited collaborator |
| `test_file_lifecycle.py` *(NEW)* | **8** (1 xf) | **file lifecycle edge cases**: dotfile storage policy (xfail documents actual behavior), umlauts+spaces+ampersand filename through DAV/GUI/editor, emoji filename GUI rename roundtrip, 180-char filename, overwrite-is-204-update (single GUI row, newest bytes), MOVE to subfolder keeps editor access (CheckFileInfo at new location), MOVE clash with `Overwrite: F` refused (412, target preserved), ≈2 MB document byte-identical roundtrip with WOPI Size match |
| `test_protocol_depth.py` | 7 | no-token negatives, 404-not-500, concurrent discovery, static assets, content-type |
| `test_wopi_protocol.py` | 4 | discovery XML, health, bogus-token rejection (CheckFileInfo + PutFile), editor bundle serving |
| **TOTALS** | **≈102** | **the five new modules run green together: 35 passed + 8 xfail + 1 xpass in 14 min**; full monolithic runs remain limited by the upstream id-cache collapse (run module groups with a restart between) |

> The *→ columns above note the prior counts that contributed to the ~61
> baseline; after adding the five new modules, coverage is about **102
> tests** (xfail counts vary slightly by run).

### GUI list pagination pitfall

The OpenCloud web file list renders **20 rows per page and lazy-loads more
only on scroll**. Once the personal root accumulates >20 folders (days of
E2E debris), freshly created folders sort beyond the first page and never
render — row-locator waits then time out even though the folder exists
(DAV shows it). Clean the root (or navigate INTO the target folder instead
of listing the root). Cleanup one-liner (admin): PROPFIND the root, DELETE
every `E2E-*` / test-prefix folder. The `paper` fixture additionally gates
on the folder appearing in the **spaces** listing (`PROPFIND /dav/spaces/
<drive-id>` — the exact data source the GUI uses; NOT
`/remote.php/dav/files/...`, which is a different, fresher namespace).


## Known gaps (documented as xfail)

1. **Browser model serialization**: typed characters reach the WOPI save
   pipeline (200) but the WASM `serialize_document` output from the browser
   lacks them — the identical flow works in Node. Suspect: browser binding /
   handle identity. Persistence test = `xfail(strict=False)`.
2. **odt app registration**: discovery advertises odt, but OCIS web shows the
   "open with" bar instead of opening the editor directly.
3. **ods/odp parity gap**: docserver discovery covers word documents only —
   spreadsheets/presentations cannot open in the World-Office editor yet
   (OnlyOffice parity target).
4. **GUI trash hides files**: the trash overview lists deleted folders but not
   deleted files, although the server trash-bin contains them (PROPFIND
   verified) — OpenCloud web rendering/filter issue.
5. **'All files' breadcrumb is dead**: the button is enabled and accepts
   clicks/Enter but never navigates; root navigation only works via the left
   rail.
6. **SERVER BUG — share role upgrade fails with grpc 500**
   (`/ocs/v2.php/apps/files_sharing/api/v1/shares/<id>` PUT) — both the
   GUI role dropdown and the OCS API return an internal `grpc: update share`
   error. Workaround: admin re-invites the user with the desired permissions
   (the suite's `_ensure_bob_editor` does exactly that).
7. **SERVER BUG — posixfs id-cache wedge/collapse under load** (reva
   v2.47.0). Two flavours:
   - **409 wedge** (~5 min): an editor teardown (page close with a live
     WOPI session) can poison the run folder's cache entry — PUT/MKCOL
     inside the folder answer `precondition failed: not found <folder>`
     (409) while GET/PROPFIND still work. Heals on its own after ~285 s.
     Mitigations in this suite: `dav_put` retry ladder (PROPFIND-parent
     nudge + backoff), a session-start probe that absorbs the healing
     window, `close_editor` graceful-unload hygiene, and editor_menu's
     sibling-folder fallback.
   - **500 collapse** (sustained load): ~16+ min of mixed DAV+WOPI/TUS
     traffic logs 85+ `record not found in cache` errors; random DAV ops
     start answering 500 until
     `cd ~/opencloud-compose && docker compose restart opencloud`.
     Every module is green on a freshly restarted server; the monolithic
     full-suite run is limited by this upstream defect (run modules in
     groups with a restart between).
8. **OpenCloud web: fresh deletes do not appear in the GUI trash overview**
   (the server trash-bin has them — verified by PROPFIND; the GUI doesn't
   list newly deleted items within 30+ seconds). DAV MOVE restore from the
   server trash-bin remains reliable (test_13); the GUI listing is xfail
   (test_13b).
9. **Template fetch hard-404s** (`/templates/<name>.html` not deployed on
   the editor host) — and the frontend fetch in `CreateNewPanel.tsx` lacks
   a `.catch`, so the 404 body ends up **inside the new document**
   (Blank/Resume/Formal Letter/Invoice/Report all affected; xfail with
   explicit root cause).
10. **SERVER BUG — Bob's DAV Shares jail serves stale snapshots**: the
    `/dav/files/<user>/Shares` root listing keeps entries of long-deleted
    shares and omits freshly accepted ones for minutes (the OCS
    `shared_with_me` record and the per-share direct PROPFIND with
    trailing slash are correct/fresh — use those in tests). xfail:
    `test_05b` in the paper module.
11. **GUI file list paginates at 20 rows** (lazy-loads on scroll only):
    once the personal root holds >20 folders, fresh folders never render
    in row-locator-based GUI tests. Not a server bug — a test-design
    constraint; keep the root clean (see "GUI list pagination pitfall"
    above).

## Rust-side counterpart

The proxy lock lifecycle (LOCK → 409 takeover → PutFile → UNLOCK) is unit-
tested in `server/core/crates/wo-docserver/src/wopi.rs` (`mod lock_tests`,
4 tests against a raw-TCP mock WOPI host) — run via `cargo test -p
wo-docserver --lib`.

## Hygiene

Everything runs inside a per-run folder `E2E-<timestamp>-<rand>` in the
admin's personal space, removed in the session finalizer. No test touches
user data outside that folder.
