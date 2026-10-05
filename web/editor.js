/**
 * opencloud-docserver editor — vanilla JS, zero dependencies.
 *
 * - Loads DOCX/ODT as HTML from the server
 * - Lets the user edit in a contenteditable div
 * - Saves HTML back to the server (which converts to DOCX/ODT)
 * - Minimal toolbar via document.execCommand (deprecated but universal)
 * - Bullet/numbered lists: toolbar, Ctrl+Shift+7/8 and markdown-style
 *   auto-conversion ("- ", "* ", "1. ") all route through toggleList()
 * - Lists persist through the production path: a successful save that
 *   carries <ul>/<ol> markup fires a `lists-persisted` event and logs
 *   "LIST-PERSISTENCE: OK" for browser-level E2E to wait on
 * - Heading styles H1/H2/H3 (+ paragraph reset): toolbar buttons and
 *   Ctrl+Alt+1/2/3/0 route formatBlock through the `wo-command` event
 *   bus; each conversion is captured as a single undoable snapshot step
 * - Undo/redo: explicit innerHTML snapshot chain (20+ steps, survives
 *   saves), not the flaky native execCommand stack
 * - Insert image: toolbar button opens an upload dialog (local file -> data
 *   URI -> preview), then inserts a self-contained <img> at the caret
 * - Find and replace: toolbar button / Ctrl+F opens a dialog that walks the
 *   live DOM (native-selection highlight, no DOM mutation), jumps between
 *   matches and replaces one occurrence or all of them via
 *   document.execCommand("insertText") so native undo + the snapshot chain
 *   stay consistent. Find works in read-only documents; replace is disabled.
 * - Full toolbar: paragraph alignment (justifyLeft/Center/Right/Full),
 *   font size/family, text color + highlight, strikethrough, indent/
 *   outdent and line spacing. Style-producing commands run with
 *   styleWithCSS so they emit span[style] (which the server sanitizer
 *   keeps) instead of <font> tags (which it strips); every formatting
 *   command routes through the wo-command event bus into runCommand().
 * - Alignment + spacing also have Word/LibreOffice-style shortcuts:
 *   Ctrl+E center, Ctrl+J justify, Ctrl+R right, Ctrl+Shift+L left.
 * - Internationalized via /static/i18n.js
 */

"use strict";

(function () {
  const DOC_ID = window.__DOC_ID__ || "unknown";
  const DOC_NAME = window.__DOC_NAME__ || "document.docx";
  const docNameEl = document.querySelector(".doc-name");
  if (docNameEl) docNameEl.textContent = DOC_NAME;
  // The server routes conversion by extension (see _document_format in
  // src/editor/router.py); ODT files round-trip through the odfpy converter.
  const DOC_FORMAT = /\.odt$/i.test(DOC_NAME) ? "odt" : "docx";
  const READ_ONLY = window.__READ_ONLY__ === true;
  // Agent command recorder: opt-in via ?record=1 (see emitCommand).
  window.__RECORD_COMMANDS__ = /[?&]record=1/.test(window.location.search);
  window.__COMMAND_LOG__ = [];
  let trackChangesOn = false;
  const TRACK_AUTHOR = window.__USER_NAME__ || "You";
  let commentRange = null;
  const SESSION = window.__SESSION__ || "";
  const api = (path) => `/api/documents/${encodeURIComponent(DOC_ID)}/${path}?session=${encodeURIComponent(SESSION)}`;
  // Resolve the UI language from the browser (falls back to English).
  const detectedLng = window.detectLocale ? window.detectLocale() : (navigator.language || "en");
  // New toolbar strings (heading H1-H3 button and insert-image dialog) are
  // not in the shipped i18n catalog yet (i18n.js is outside this editor's
  // file scope), so seed them here. Only missing keys are filled, so real
  // catalog entries still win once they are added. English is the
  // DEFAULT_TRANSLATIONS baseline; the German Heading3 pair matches the
  // existing Heading1/Heading2 entries.
  const IMAGE_UI_STRINGS = {
    "Toolbar.InsertImage": "Insert image",
    "Toolbar.InsertImageTitle": "Insert image",
    "Image.ChooseFile": "Image file",
    "Image.Insert": "Insert",
    "Image.Cancel": "Cancel",
    "Image.NoFile": "Choose an image file first",
    "Image.UnsupportedType": "Unsupported file type — use PNG, JPEG, GIF, BMP, WebP or SVG",
    "Image.TooLarge": "Image too large (max 10 MB)",
    "Image.ReadFailed": "Could not read the image",
  };
  const HEADING3_UI_STRINGS = Object.assign(
    { "Toolbar.Heading3": "Heading 3", "Toolbar.Heading3Title": "Heading 3" },
    detectedLng.indexOf("de") === 0
      ? { "Toolbar.Heading3": "Überschrift 3", "Toolbar.Heading3Title": "Überschrift 3" }
      : {}
  );
  function seedUiStrings(tFn) {
    const bucket = tFn && tFn.resources && tFn.resources[tFn.lng] && tFn.resources[tFn.lng].translation;
    if (!bucket) return;
    Object.keys(IMAGE_UI_STRINGS).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = IMAGE_UI_STRINGS[k];
    });
    Object.keys(HEADING3_UI_STRINGS).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = HEADING3_UI_STRINGS[k];
    });
  }
  // Same pattern for the find-and-replace strings: the catalog (i18n.js) is
  // updated separately, so seed English fallbacks here so the data-i18n
  // markers render real text instead of raw keys.
  const FIND_UI_STRINGS = {
    "Toolbar.Find": "Find and replace",
    "Toolbar.FindTitle": "Find and replace (Ctrl+F)",
    "Find.SearchLabel": "Find",
    "Find.SearchPlaceholder": "Search in document",
    "Find.ReplaceLabel": "Replace with",
    "Find.MatchCase": "Match case",
    "Find.Next": "Next (Enter)",
    "Find.Prev": "Previous (Shift+Enter)",
    "Find.Replace": "Replace",
    "Find.ReplaceAll": "Replace all",
    "Find.Close": "Close",
    "Find.NoMatches": "No matches",
  };
  function seedFindStrings(tFn) {
    const bucket = tFn && tFn.resources && tFn.resources[tFn.lng] && tFn.resources[tFn.lng].translation;
    if (!bucket) return;
    Object.keys(FIND_UI_STRINGS).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = FIND_UI_STRINGS[k];
    });
  }
  // Same pattern for the full-toolbar strings (alignment, font, layout):
  // the catalog (i18n.js) is updated separately, so seed English fallbacks
  // here so the data-i18n-title markers render real labels.
  const TOOLBAR_UI_STRINGS = {
    "Toolbar.Strikethrough": "Strikethrough",
    "Toolbar.StrikethroughTitle": "Strikethrough",
    "Toolbar.FontSize": "Font size",
    "Toolbar.FontSizeTitle": "Font size",
    "Toolbar.FontFamily": "Font family",
    "Toolbar.FontFamilyTitle": "Font family",
    "Toolbar.TextColor": "Text color",
    "Toolbar.TextColorTitle": "Text color",
    "Toolbar.Highlight": "Highlight color",
    "Toolbar.HighlightTitle": "Highlight color",
    "Toolbar.AlignLeft": "Align left",
    "Toolbar.AlignLeftTitle": "Align left (Ctrl+Shift+L)",
    "Toolbar.AlignCenter": "Center",
    "Toolbar.AlignCenterTitle": "Center (Ctrl+E)",
    "Toolbar.AlignRight": "Align right",
    "Toolbar.AlignRightTitle": "Align right (Ctrl+R)",
    "Toolbar.AlignJustify": "Justify",
    "Toolbar.AlignJustifyTitle": "Justify (Ctrl+J)",
    "Toolbar.Indent": "Increase indent",
    "Toolbar.IndentTitle": "Increase indent",
    "Toolbar.Outdent": "Decrease indent",
    "Toolbar.OutdentTitle": "Decrease indent",
    "Toolbar.LineSpacing": "Line spacing",
    "Toolbar.LineSpacingTitle": "Line spacing",
  };
  const TOOLBAR_UI_STRINGS_DE = {
    "Toolbar.Strikethrough": "Durchgestrichen",
    "Toolbar.StrikethroughTitle": "Durchgestrichen",
    "Toolbar.FontSize": "Schriftgröße",
    "Toolbar.FontSizeTitle": "Schriftgröße",
    "Toolbar.FontFamily": "Schriftart",
    "Toolbar.FontFamilyTitle": "Schriftart",
    "Toolbar.TextColor": "Schriftfarbe",
    "Toolbar.TextColorTitle": "Schriftfarbe",
    "Toolbar.Highlight": "Hervorhebungsfarbe",
    "Toolbar.HighlightTitle": "Hervorhebungsfarbe",
    "Toolbar.AlignLeft": "Linksbündig",
    "Toolbar.AlignLeftTitle": "Linksbündig (Strg+Umschalt+L)",
    "Toolbar.AlignCenter": "Zentriert",
    "Toolbar.AlignCenterTitle": "Zentriert (Strg+E)",
    "Toolbar.AlignRight": "Rechtsbündig",
    "Toolbar.AlignRightTitle": "Rechtsbündig (Strg+R)",
    "Toolbar.AlignJustify": "Blocksatz",
    "Toolbar.AlignJustifyTitle": "Blocksatz (Strg+J)",
    "Toolbar.Indent": "Einzug vergrößern",
    "Toolbar.IndentTitle": "Einzug vergrößern",
    "Toolbar.Outdent": "Einzug verkleinern",
    "Toolbar.OutdentTitle": "Einzug verkleinern",
    "Toolbar.LineSpacing": "Zeilenabstand",
    "Toolbar.LineSpacingTitle": "Zeilenabstand",
  };
  function seedToolbarStrings(tFn) {
    const bucket = tFn && tFn.resources && tFn.resources[tFn.lng] && tFn.resources[tFn.lng].translation;
    if (!bucket) return;
    const pair = detectedLng.indexOf("de") === 0 ? TOOLBAR_UI_STRINGS_DE : TOOLBAR_UI_STRINGS;
    Object.keys(pair).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = pair[k];
    });
  }
  // Accessibility strings (skip link, document label, toolbar region)
  // referenced by data-i18n / data-i18n-aria-label markers that are not yet
  // in the shipped catalog. Same seed pattern as the toolbar/find strings;
  // only missing keys are filled, so catalog entries still win when added.
  const A11Y_UI_STRINGS = {
    "A11y.SkipToDocument": "Skip to document",
    "A11y.EditorLabel": "Document",
    "Toolbar.Region": "Formatting toolbar",
  };
  const A11Y_UI_STRINGS_DE = {
    "A11y.SkipToDocument": "Zum Dokument springen",
    "A11y.EditorLabel": "Dokument",
    "Toolbar.Region": "Formatierungsleiste",
  };
  function seedA11yStrings(tFn) {
    const bucket = tFn && tFn.resources && tFn.resources[tFn.lng] && tFn.resources[tFn.lng].translation;
    if (!bucket) return;
    const pair = detectedLng.indexOf("de") === 0 ? A11Y_UI_STRINGS_DE : A11Y_UI_STRINGS;
    Object.keys(pair).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = pair[k];
    });
  }
  const MENU_UI_STRINGS = {
    "MenuBar.Region": "Application menu",
    "Menu.File": "File",
    "Menu.New": "New",
    "Menu.Open": "Open…",
    "Menu.Export": "Export",
    "Menu.ExportPdf": "PDF",
    "Menu.ExportOdt": "ODT",
    "Menu.ExportHtml": "HTML",
    "Menu.ExportDocx": "DOCX",
    "Menu.Print": "Print",
    "Menu.History": "History…",
    "FileMenu.NewConfirm": "Discard the current document and start a new one?",
    "FileMenu.Exporting": "Exporting…",
    "FileMenu.ExportError": "Export failed",
  };
  const MENU_UI_STRINGS_DE = {
    "MenuBar.Region": "Anwendungsmenü",
    "Menu.File": "Datei",
    "Menu.New": "Neu",
    "Menu.Open": "Öffnen…",
    "Menu.Export": "Exportieren",
    "Menu.ExportPdf": "PDF",
    "Menu.ExportOdt": "ODT",
    "Menu.ExportHtml": "HTML",
    "Menu.ExportDocx": "DOCX",
    "Menu.Print": "Drucken",
    "Menu.History": "Verlauf…",
    "FileMenu.NewConfirm": "Aktuelles Dokument verwerfen und ein neues beginnen?",
    "FileMenu.Exporting": "Exportiere…",
    "FileMenu.ExportError": "Export fehlgeschlagen",
  };
  const VERSION_UI_STRINGS = {
    "VersionHistory.Title": "Version history",
    "VersionHistory.Empty": "No versions saved yet — save the document to create one.",
    "VersionHistory.Current": "Current",
    "VersionHistory.Restore": "Restore",
    "VersionHistory.Restoring": "Restoring…",
    "VersionHistory.Restored": "Version restored ✓",
    "VersionHistory.RestoreError": "Restore failed: ",
    "VersionHistory.ListError": "Could not load version history: ",
    "VersionHistory.Close": "Close",
  };
  const VERSION_UI_STRINGS_DE = {
    "VersionHistory.Title": "Versionen",
    "VersionHistory.Empty": "Noch keine Versionen gespeichert — speichern Sie das Dokument, um eine zu erzeugen.",
    "VersionHistory.Current": "Aktuell",
    "VersionHistory.Restore": "Wiederherstellen",
    "VersionHistory.Restoring": "Stelle wieder her…",
    "VersionHistory.Restored": "Version wiederhergestellt ✓",
    "VersionHistory.RestoreError": "Wiederherstellung fehlgeschlagen: ",
    "VersionHistory.ListError": "Versionen konnten nicht geladen werden: ",
    "VersionHistory.Close": "Schließen",
  };
  function seedVersionStrings(tFn) {
    const bucket = tFn && tFn.resources && tFn.resources[tFn.lng] && tFn.resources[tFn.lng].translation;
    if (!bucket) return;
    const pair = detectedLng.indexOf("de") === 0 ? VERSION_UI_STRINGS_DE : VERSION_UI_STRINGS;
    Object.keys(pair).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = pair[k];
    });
  }
  function seedMenuStrings(tFn) {
    const bucket = tFn && tFn.resources && tFn.resources[tFn.lng] && tFn.resources[tFn.lng].translation;
    if (!bucket) return;
    const pair = detectedLng.indexOf("de") === 0 ? MENU_UI_STRINGS_DE : MENU_UI_STRINGS;
    Object.keys(pair).forEach((k) => {
      if (bucket[k] === undefined) bucket[k] = pair[k];
    });
  }
  const t = (window.createI18n && window.createI18n({ lng: detectedLng })) || ((k) => k);
  seedUiStrings(t);
  seedFindStrings(t);
  seedToolbarStrings(t);
  seedA11yStrings(t);
  seedMenuStrings(t);
  seedVersionStrings(t);
  // Localize static HTML (toolbar tooltips, Save label, ready status) and
  // keep the <html lang> attribute in sync for a11y & spell-check.
  if (window.applyTranslations) {
    window.applyTranslations(document, t);
  }
  const htmlEl = document.documentElement;
  if (htmlEl) htmlEl.setAttribute("lang", t.lng);
  const editor = document.getElementById("editor");
  const status = document.getElementById("status");
  const saveBtn = document.getElementById("btn-save");

  if (READ_ONLY) {
    editor.contentEditable = "false";
    editor.setAttribute("aria-readonly", "true");
    saveBtn.disabled = true;
    const toolbar = document.getElementById("toolbar");
    if (toolbar) toolbar.querySelectorAll("button").forEach((b) => (b.disabled = true));
    // The full-toolbar selects (font size/family, line spacing) and color
    // pickers are form controls, not buttons — disable them too.
    if (toolbar) toolbar.querySelectorAll("select, input[type='color']").forEach((el) => (el.disabled = true));
    // Finding (Ctrl+F) never mutates the document, so it stays available in
    // read-only documents; the dialog's replace controls handle the rest.
    const findBtnReadonly = document.getElementById("btn-find");
    if (findBtnReadonly) findBtnReadonly.disabled = false;
    setStatus(t("Status.ReadOnly"));
  }

  // ------------------------------------------------------------------
  // Status helpers
  // ------------------------------------------------------------------
  function setStatus(text, isError) {
    status.textContent = text;
    status.style.color = isError ? "#f87171" : "";
  }

  // ------------------------------------------------------------------
  // Load
  // ------------------------------------------------------------------
  async function loadDocument() {
    setStatus(t("Status.Loading"));
    try {
      const res = await fetch(api("html"));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "load failed");
      // Anchor: an empty/blank document still needs a block element so
      // typing produces <p>…</p> (bare text would be lost in DOCX conversion).
      editor.innerHTML = data.html || "<p><br></p>";
      hydrateVectorObjects();
      refreshToc();
      paginateQuiet();  // paginate the flow into LO/OO-style sheets
      // Fresh load resets the snapshot chain: the loaded state becomes the
      // baseline the Undo/Redo-Kette walks back to.
      undoStack.length = 0;
      redoStack.length = 0;
      lastSnapshot = editor.innerHTML;
      applyPageView();  // map the page-setup marker (if any) to the canvas
      refreshProtectionState();  // reflect protect.* state (real gate is server-side)
      setStatus(data.blank ? t("Status.EmptyDocument") : t("Status.Ready"));
      updateUndoRedoState();
      updateCounts();
      // An offline snapshot queued by a previous session in this browser
      // overrides the freshly fetched (stale) document and is marked dirty
      // so it is re-pushed on the next save.
      restoreOfflineQueue();
    } catch (err) {
      editor.innerHTML = "<p><em>" + t("Status.LoadFailed") + err.message + "</em></p>";
      setStatus(t("Status.LoadFailed") + err.message, true);
      restoreOfflineQueue();
    }
  }

  // ------------------------------------------------------------------
  // Offline queue
  // ------------------------------------------------------------------
  // The service worker keeps the app shell usable offline; this queue keeps
  // the LATEST unsaved snapshot in localStorage when a save cannot reach the
  // host (network error), restores it on a later reload, and flushes it back
  // to the server when the browser reports the connection again. Only the
  // newest snapshot is kept — a queue of one, never a pile.
  const OFFLINE_KEY = "wo-offline-queue";
  let isOffline = false;
  function updateOfflineIndicator() {
    const el = document.getElementById("offline-indicator");
    if (!el) return;
    el.hidden = !isOffline;
  }
  function queueLocalEdit() {
    try {
      localStorage.setItem(OFFLINE_KEY, JSON.stringify({
        ts: Date.now(), docId: DOC_ID, html: flatHtml(),
      }));
    } catch (e) {}
    isOffline = true;
    updateOfflineIndicator();
    setStatus(t("Status.OfflineQueued"), true);
  }
  function clearOfflineQueue() {
    try { localStorage.removeItem(OFFLINE_KEY); } catch (e) {}
    isOffline = false;
    updateOfflineIndicator();
  }
  // Called after each successful load: if this browser holds an unpushed
  // snapshot for THIS document, an offline editing session survives a reload
  // (the freshly fetched doc would otherwise be stale and orphan the queue).
  function restoreOfflineQueue() {
    let queued = null;
    try { queued = JSON.parse(localStorage.getItem(OFFLINE_KEY) || "null"); } catch (e) {}
    if (!queued || queued.docId !== DOC_ID || !queued.html) {
      isOffline = false;
      updateOfflineIndicator();
      return;
    }
    editor.innerHTML = queued.html;
    paginateQuiet();  // normalize legacy flat offline queues
    captureHistory();
    updateUndoRedoState();
    updateCounts();
    isOffline = true;
    markDirty();
    updateOfflineIndicator();
    setStatus(t("Status.OfflineQueued"), true);
  }
  async function flushOfflineQueue() {
    if (READ_ONLY) return;
    try {
      const queued = JSON.parse(localStorage.getItem(OFFLINE_KEY) || "null");
      if (!queued || queued.docId !== DOC_ID) return;
    } catch (e) { return; }
    // The editor holds the newest state; a successful save supersedes and
    // clears the queue (saveDocument calls clearOfflineQueue).
    await saveDocument();
  }
  window.addEventListener("online", () => { if (isOffline) flushOfflineQueue(); });
  window.addEventListener("offline", () => {
    if (!navigator.onLine) { isOffline = true; updateOfflineIndicator(); }
  });

  // ------------------------------------------------------------------
  // Save
  // ------------------------------------------------------------------
  async function saveDocument() {
    if (READ_ONLY) return;
    setStatus(t("Status.Saving"));
    try {
      const res = await fetch(
        api("save"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ html: flatHtml() }),
        }
      );
      if (!res.ok) {
        let msg = "save failed";
        try { msg = (await res.json()).error || msg; } catch (e) {}
        throw new Error(msg);
      }
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "save failed");
      // A save reached the host: any queued offline snapshot is superseded.
      clearOfflineQueue();
      setStatus(t("Status.Saved"));
      notifyHost("saved");
      if (DOC_FORMAT === "odt") {
        // ODT persistence round-trip confirmed by the server: the editor's
        // HTML was converted back to ODT and PUT to the WOPI host. Dispatch
        // an event (plus console marker) that browser-level E2E (Playwright)
        // waits on before verifying content.xml in the stored file.
        window.dispatchEvent(new CustomEvent("odt-persisted", {
          detail: { docId: DOC_ID, name: DOC_NAME },
        }));
        console.info("ODT-PERSISTENCE: OK", DOC_NAME);
      }
      // List persistence round-trip confirmed by the server: the saved body
      // contained bullet/numbered list markup and came back as a valid save
      // (converted + PUT to the WOPI host). Dispatch an event (plus console
      // marker) that browser-level E2E (Playwright) waits on before verifying
      // the list items in the stored document.
      if (/<([uo]l)[\s>]/i.test(editor.innerHTML)) {
        window.dispatchEvent(new CustomEvent("lists-persisted", {
          detail: { docId: DOC_ID, name: DOC_NAME },
        }));
        console.info("LIST-PERSISTENCE: OK", DOC_NAME);
      }
      // Image persistence round-trip confirmed by the server: the saved
      // body carried a self-contained <img> (data: URI) and came back as a
      // valid save (converted + PUT to the WOPI host). Dispatch an event
      // (plus console marker) that browser-level E2E (Playwright) waits on
      // before verifying the embedded picture in the stored document.
      if (/<img[\s>]/i.test(editor.innerHTML)) {
        window.dispatchEvent(new CustomEvent("images-persisted", {
          detail: { docId: DOC_ID, name: DOC_NAME },
        }));
        console.info("IMAGE-PERSISTENCE: OK", DOC_NAME);
      }
      setTimeout(() => setStatus(t("Status.Ready")), 2000);
    } catch (err) {
      if (err instanceof TypeError) {
        // The host is unreachable (fetch rejects with TypeError on network
        // errors; server-side failures surface as HTTP responses instead and
        // are deliberately NOT queued — they would just fail again online).
        queueLocalEdit();
        return;
      }
      setStatus(t("Status.SaveFailed") + err.message, true);
    }
  }

  // ------------------------------------------------------------------
  // File menu commands (New / Open / Export / Print)
  // ------------------------------------------------------------------
  // Start a blank document. Guarded by READ_ONLY because it mutates the
  // editing surface; a confirm() prevents accidental data loss. After
  // clearing, snapshot history + flag dirty so the change is undoable and
  // autosaved like any other edit.
  function doNewDocument() {
    if (READ_ONLY) return;
    if (!window.confirm(t("FileMenu.NewConfirm"))) return;
    editor.innerHTML = "";
    captureHistory();
    updateUndoRedoState();
    markDirty();
    // Persist the blank document immediately (autosave would wait ~30s);
    // the empty editor HTML is a valid blank document for the converter.
    saveDocument();
  }

  // Open is delegated to the surrounding host application (OpenCloud / WOPI
  // frame), which owns file browsing and selection. There is no local file
  // picker because all documents are served by the docserver. The host
  // listens for this custom event and presents its own open dialog.
  function doOpen() {
    window.dispatchEvent(new CustomEvent("wo-open-file", { detail: { docId: DOC_ID } }));
  }

  // Export the current document in a target format. The server performs the
  // conversion (router.py routes /export/<fmt>), returns a downloadable
  // blob, and we trigger a browser download with a sensible filename.
  async function doExport(format) {
    if (!format) return;
    try {
      setStatus(t("FileMenu.Exporting"));
      // The server exposes POST /api/documents/{doc_id}/export?format=<fmt>
      // (router.py export_document). Build the URL directly — the api()
      // helper appends ?session, so a ?format= here would create a broken
      // double-? query string.
      const exportUrl = `/api/documents/${encodeURIComponent(DOC_ID)}/export?format=${encodeURIComponent(format)}&session=${encodeURIComponent(SESSION)}`;
      const res = await fetch(exportUrl, { method: "POST" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = DOC_NAME.replace(/\.[^.]+$/, "") + "." + format;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus(t("Status.Ready"));
    } catch (err) {
      setStatus(t("FileMenu.ExportError") + ": " + err.message, true);
    }
  }

  // Print the current document through the browser's print pipeline.
  function doPrint() {
    window.print();
  }

  // ------------------------------------------------------------------
  // Toolbar
  // ------------------------------------------------------------------
  // Toggle the bullet/numbered list at the caret. The native commands
  // both wrap the current block in a list item AND unwrap/remove a list
  // when toggled a second time, so this single path serves the toolbar
  // buttons, the Ctrl+Shift+7/8 shortcuts and the smart-list converter.
  // Lists mutate the DOM but don't always fire an `input` event
  // (notably on toggle-off), so arm autosave and snapshot the history
  // explicitly on success.
  function toggleList(command) {
    if (READ_ONLY) return false;
    editor.focus();
    const ok = document.execCommand(command, false, null);
    if (ok) {
      markDirty();
      captureHistory();
    }
    updateActiveStates();
    updateUndoRedoState();
    return ok;
  }

  // Contenteditable tends to leave the caret INSIDE a freshly inserted
  // structural marker (<hr>, div.page-break): the marker then swallows
  // whatever is typed next. Re-check at command time and nudge the caret
  // just past any marker the selection sits inside, so inserts/typing start
  // a fresh block instead of landing in the rule/break.
  function moveCaretPastStructuralMarkers() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const focus = sel.focusNode;
    if (!focus || !editor.contains(focus)) return;
    const el = focus.nodeType === 1 ? focus : focus.parentElement;
    if (!el || !el.closest) return;
    const marker = el.closest("hr, .page-break, .section-break, [data-columns], nav.toc, div.object");
    if (!marker) return;
    const r = document.createRange();
    r.setStartAfter(marker);
    r.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r);
  }
  // Wrap the selection in (or unwrap it from) an inline <span style>. Used
  // by the small-caps / all-caps buttons, which have no execCommand. The
  // original selection is cloned (cloneContents) so nested formatting
  // inside the selection survives the wrap; toggling off unwraps the exact
  // span that wraps the whole selection.
  function toggleInlineCSS(prop, value) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return false;
    const range = sel.getRangeAt(0);
    if (range.collapsed || range.toString() === "") return false;
    let node = range.commonAncestorContainer;
    if (node.nodeType === 3) node = node.parentElement;
    let span = null;
    const text = range.toString();
    while (node && node !== editor) {
      if (node.nodeType === 1 && /^span$/i.test(node.tagName) &&
          node.style && node.style[prop] === value && text === node.textContent) {
        span = node;
        break;
      }
      node = node.parentNode;
    }
    try {
      const frag = range.cloneContents();
      const tmp = document.createElement("div");
      tmp.appendChild(frag);
      const inner = tmp.innerHTML;
      const kebab = prop.replace(/([A-Z])/g, "-$1").toLowerCase();
      if (span) {
        // Toggle off: replace with the (unwrapped) inner markup.
        document.execCommand("insertHTML", false, inner);
      } else {
        document.execCommand("insertHTML", false,
          `<span style="${kebab}:${value}">${inner}</span>`);
      }
      return true;
    } catch (err) {
      return false;
    }
  }

  // Code toggle via the native fontName command (styleWithCSS on). The
  // converters map any monospace family to <code> on save, so applying
  // Consolas here round-trips as inline code; applying it again steps
  // back to the plain font.
  function toggleMonospace() {
    try {
      document.execCommand("styleWithCSS", false, "true");
      const current = fontIsMono();
      document.execCommand("fontName", false, current ? "" : "Consolas");
      document.execCommand("styleWithCSS", false, "false");
      return true;
    } catch (err) {
      return false;
    }
  }

  function fontIsMono() {
    try {
      return /consolas|courier|mono/i.test(
        String(document.queryCommandValue("fontName") || ""));
    } catch (err) {
      return false;
    }
  }

  function spanStyleActive(prop, value) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return false;
    let node = sel.anchorNode;
    if (node && node.nodeType === 3) node = node.parentElement;
    while (node && node !== editor) {
      if (node.nodeType === 1 && node.style && node.style[prop] === value) return true;
      node = node.parentNode;
    }
    return false;
  }

  // Toggle right-to-left on the block(s) touched by the selection: set or
  // remove ``style="direction:rtl"``. Both converters round-trip the
  // property (DOCX w:bidi / ODF style:writing-mode) and the sanitizer's
  // style whitelist keeps ``direction`` on save.
  function blocksUnderSelection(sel) {
    // Shared block resolver for block-level commands (line spacing, RTL
    // direction). select-all yields startContainer === editor so the walk
    // below finds nothing; fall back to every top-level block then — the
    // select-all intent is "apply to the whole document".
    const range = sel.getRangeAt(0);
    const blocks = [];
    [range.startContainer, range.endContainer].forEach((node) => {
      const b = blockElementAt(node);
      if (b && b !== editor && blocks.indexOf(b) === -1) blocks.push(b);
    });
    if (blocks.length === 0 && sel.isCollapsed === false && range.startContainer === editor) {
      for (const b of editor.children) {
        if (blockElementAt(b) === b && b !== editor) blocks.push(b);
      }
    }
    return blocks;
  }

  function toggleBlockDirection() {
    if (READ_ONLY) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!editor.contains(range.startContainer)) return;
    const blocks = blocksUnderSelection(sel);
    if (blocks.length === 0) return;
    const anyRtl = blocks.some((b) => b.style.direction === "rtl");
    blocks.forEach((el) => {
      if (anyRtl) el.style.removeProperty("direction");
      else el.style.setProperty("direction", "rtl");
    });
    markDirty();
    captureHistory();
    scheduleCollabSync();
    notifyHost("editing");
  }

  function runCommand(cmd, value) {
    // Loud guard: with restrict-editing on (contentEditable off) the
    // execCommand-based mutations silently fail — say so instead (the
    // protection controls themselves must stay reachable to un-restrict).
    const editBlocked = () => {
      if (editor.contentEditable !== "false") return false;
      setStatus(t("Status.NotAvailable") + " " + cmd, true);
      return true;
    };
    if (cmd === "insertUnorderedList" || cmd === "insertOrderedList") {
      toggleList(cmd);
      return;
    }
    // Undo/redo walk our explicit snapshot chain (below) instead of the
    // undocumented native execCommand stack, so the chain keeps working
    // for 20+ steps and across the list/table DOM rewrites and saves.
    if (cmd === "undo") {
      undoHistory();
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    if (cmd === "redo") {
      redoHistory();
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    // Line spacing is not an execCommand; it is applied to the block(s)
    // under the selection directly (see applyLineHeight below).
    if (cmd === "lineHeight") {
      applyLineHeight(value);
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    // RTL is a block-level style, not an execCommand: toggle direction on
    // the block(s) under the selection (parity with applyLineHeight).
    if (cmd === "directionRtl") {
      toggleBlockDirection();
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    // code / small-caps / all-caps have no native execCommand; they wrap
    // the selection in (or unwrap it from) a CSS span. The converters map
    // monospace families to <code> and these CSS props to the same style
    // strings, so everything round-trips through DOCX and ODT.
    // Underline/strike style variants wrap the selection in a span with a
    // doubled text-decoration (round-trips as plain decoration, so DOCX/ODT
    // downgrade gracefully to single).
    if (cmd === "underlineDouble" || cmd === "strikeDouble") {
      editor.focus();
      toggleInlineCSS("textDecoration",
        cmd === "underlineDouble" ? "underline double" : "line-through double");
      updateActiveStates();
      return;
    }
    // List-style-type restyles the list under the selection (bullet disc /
    // circle / square, numbering decimal / alpha / roman). Creates the list
    // first when the selection is not inside one. Style-only change: no
    // input event fires, so the save/collab pipeline is armed explicitly.
    if (cmd === "listStyle") {
      editor.focus();
      applyListStyle(value || "disc");
      updateActiveStates();
      return;
    }
    if (cmd === "code" || cmd === "smallCaps" || cmd === "allCaps") {
      const applied = cmd === "code"
        ? toggleMonospace()
        : toggleInlineCSS(cmd === "smallCaps" ? "fontVariant" : "textTransform",
                          cmd === "smallCaps" ? "small-caps" : "uppercase");
      if (!applied) {
        // No selection: fall back to a native command so the caret path
        // still does something reasonable instead of silently doing nothing.
        try { document.execCommand(cmd === "code" ? "fontName" : "strikeThrough", false,
                                   cmd === "code" ? "Consolas" : null); } catch (err) {}
      }
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    // Font size grow/shrink steps (F-131): the toolbar has a size picker;
    // Word/OO also offer discrete +/- steps. Step along the SAME 1..7 HTML
    // ladder the picker's options map to (queryCommandValue returns the HTML
    // size for an applied size), then fall through to the fontSize branch.
    if (cmd === "fontSizeInc" || cmd === "fontSizeDec") {
      let step = 3;
      try { step = parseInt(document.queryCommandValue("fontSize"), 10) || 3; }
      catch (err) { /* best effort */ }
      step = Math.min(7, Math.max(1, step + (cmd === "fontSizeInc" ? 1 : -1)));
      runCommand("fontSize", String(step));
      return;
    }
    // Change case (F-129): transform the selected text in place via
    // execCommand insertText — the documented editing path (native undo +
    // real input event -> dirty/history/collab all just work).
    if (cmd === "changeCase") {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
        setStatus(t("Status.NoSelection") || "Select text first");
        return;
      }
      const text = sel.toString();
      let out = text;
      const v = String(value || "");
      if (v === "upper") out = text.toUpperCase();
      else if (v === "lower") out = text.toLowerCase();
      else if (v === "title")
        out = text.replace(/\S+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
      else if (v === "sentence")
        out = text.toLowerCase().replace(/(^\s*|[.!?]\s+)(\w)/g, (m, p, c) => p + c.toUpperCase());
      if (out === text) return;
      try { document.execCommand("insertText", false, out); } catch (err) {}
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    editor.focus();
    // Custom inserts: horizontal rule, page break and picker symbols.
    // They route through execCommand insertHTML/insertText so each becomes
    // an ordinary editable step (undoable via captureHistory, collab-synced
    // via the input event, persisted because the editor HTML carries the
    // <hr>/div.page-break markers and the plain-text symbols).
    if (cmd === "insertHR" || cmd === "insertPageBreak") {
      // A page break inserts a marker PLUS a trailing empty paragraph: with
      // nothing after the marker, Chromium appends subsequent typed text
      // INTO the page-break div (block-boundary behaviour), corrupting it.
      // The trailing <p> is the target block for whatever the user types or
      // inserts next (Word keeps a paragraph after a page break too).
      const html = cmd === "insertHR"
        ? "<hr/>"
        : '<div class="page-break"><br></div><p><br></p>';
      try { document.execCommand("insertHTML", false, html); } catch (err) {}
      moveCaretPastStructuralMarkers();
      markDirty();
      captureHistory();
      scheduleCollabSync();
      notifyHost("editing");
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    if (cmd === "insertSectionBreak") {
      // Mirror the page-break insert: a marker PLUS a trailing empty paragraph
      // so subsequent typed text lands in a fresh block instead of inside the
      // <hr> (block-boundary swallowing). Round-trips to DOCX w:sectPr and ODT
      // nested text:section.
      const html = '<hr class="section-break"><p><br></p>';
      try { document.execCommand("insertHTML", false, html); } catch (err) {}
      moveCaretPastStructuralMarkers();
      markDirty();
      captureHistory();
      scheduleCollabSync();
      notifyHost("editing");
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    if (cmd === "insertFootnote" || cmd === "insertEndnote") {
      // Note authoring (F-073/F-074): the HTML contract the converters
      // round-trip — a citation <sup> immediately followed by the body
      // <span> (see _InlineRunBuilder._start_note in converter.py). Inline
      // insertion, so no trailing-paragraph block-boundary handling.
      const cls = cmd === "insertFootnote" ? "footnote" : "endnote";
      const body = cmd === "insertFootnote" ? "Footnote text" : "Endnote text";
      const html = '<sup class="' + cls + '-citation">[1]</sup><span class="' + cls + '">' + body + '</span>';
      try { document.execCommand("insertHTML", false, html); } catch (err) {}
      markDirty();
      captureHistory();
      scheduleCollabSync();
      notifyHost("editing");
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    if (cmd === "insertPageNumber") {
      // PAGE-field authoring (F-085): the span the converters map to
      // w:fldSimple PAGE / text:page-number.
      if (editBlocked()) return;
      try { document.execCommand("insertHTML", false, '<span class="page-number"></span>'); } catch (err) {}
      markDirty();
      captureHistory();
      scheduleCollabSync();
      notifyHost("editing");
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    if (cmd === "insertHeader" || cmd === "insertFooter") {
      // Header/footer authoring (F-084): the converters parse a
      // <header class="page-header"> at body start and a
      // <footer class="page-footer"> at body end into real page parts.
      // One of each per document; pressing again focuses the existing one.
      const tag = cmd === "insertHeader" ? "header" : "footer";
      const sel = tag + ".page-" + tag;
      const existing = editor.querySelector(":scope > " + sel);
      if (existing) { existing.focus(); return; }
      const el = document.createElement(tag);
      el.className = "page-" + tag;
      el.textContent = cmd === "insertHeader" ? "Header text" : "Footer text";
      if (cmd === "insertHeader") editor.insertBefore(el, editor.firstChild);
      else editor.appendChild(el);
      el.focus();
      markDirty();
      captureHistory();
      scheduleCollabSync();
      notifyHost("editing");
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    if (cmd === "insertSymbol" || cmd === "insertDate") {
      if (editBlocked()) return;
      // execCommand("insertText") needs editable focus; if the click left
      // focus on the ribbon button it silently fails — restore the editor
      // (and with it the caret) first.
      if (document.activeElement !== editor) {
        editor.focus();
        if (window.getSelection().isCollapsed) restoreFocus();
      }
      // Never let a symbol/date-land inside an <hr> or page-break marker
      // whose caret Chromium re-restored on focus.
      moveCaretPastStructuralMarkers();
      const text = cmd === "insertDate"
        ? new Date().toISOString().slice(0, 10)
        : String(value || "");
      try { document.execCommand("insertText", false, text); } catch (err) {}
      markDirty();
      captureHistory();
      scheduleCollabSync();
      notifyHost("editing");
      updateActiveStates();
      updateUndoRedoState();
      return;
    }
    // Headings (formatBlock) replace the block under the caret. Pass the
    // spec-canonical lowercase tag name (a few engines are case-sensitive)
    // and record the result as an explicit undo step, because execCommand
    // does not fire an `input` event on every engine. The captureHistory()
    // html===lastSnapshot guard keeps this a no-op when the event already ran.
    // --- WS-B promoted commands -------------------------------------
    if (cmd === "insertObject") { openObjectDialog(String(value || "shape")); return; }
    if (cmd === "updateToc") { openUpdateTocDialog(); return; }
    if (cmd === "aiTranslate") {
      openAiPropose("Translate the document into " + String(value || "German") + ".");
      return;
    }
    if (cmd === "aiRewrite") {
      openAiPropose("Rewrite the document to improve clarity, flow, and style while preserving its meaning and facts.");
      return;
    }
    if (cmd === "aiSummarize") {
      openAiPropose("Summarize the document into a concise summary.");
      return;
    }
    if (cmd === "ocrRun") {
      openAiPropose("Perform OCR on the document, extract all text, and return it as plain text.");
      return;
    }
    if (cmd === "displayMode") { openDisplayModeDialog(); return; }
    if (cmd === "link") { insertLink(); return; }
    if (cmd === "toggleGridlines") { toggleGridlines(); return; }
    if (cmd === "toggleNavigation") { toggleNavigation(); return; }
    if (cmd === "toggleChat") { toggleChat(); return; }
    if (cmd === "insertCaption") { insertCaptionCommand(); return; }
    if (cmd === "insertCitation") { insertCitation(); return; }
    if (cmd === "insertIndexEntry") { insertIndexEntry(); return; }
    if (cmd === "compareVersion") { openCompareView(); return; }
    if (cmd === "prevTrackedChange") { prevTrackedChange(); return; }
    if (cmd === "nextTrackedChange") { nextTrackedChange(); return; }
    if (cmd === "toggleHyphenation") { openHyphenationDialog(); return; }
    if (cmd === "toggleLineNumbers") { openLineNumbersDialog(); return; }
    if (cmd === "toggleWatermark") { openWatermarkDialog(); return; }
    if (cmd === "toggleDifferentFirst") { toggleSectionMarker("different-first", ""); return; }
    if (cmd === "toggleOddEven") { toggleSectionMarker("odd-even", ""); return; }
    // Distances use a non-default value (0.8") so the marker round-trips:
    // pgMar w:header/w:footer at exactly 720 twips (0.5") equals the OOXML
    // default and reads back marker-free. A value control (input in the
    // page-setup dialog) would be the natural upgrade.
    if (cmd === "toggleHeaderFromTop") { toggleSectionMarker("header-from-top", "data-inches=\"0.8\""); return; }
    if (cmd === "toggleFooterFromBottom") { toggleSectionMarker("footer-from-bottom", "data-inches=\"0.8\""); return; }
    if (cmd === "toggleDropcap") { openDropcapDialog(); return; }
    if (cmd === "openBorders") { openBordersDialog(); return; }
    if (cmd === "multilevel") { multilevelItem(); return; }
    if (cmd === "insertToF") { insertToFCommand(); return; }
    if (cmd === "openCrossref") { openCrossrefDialog(); return; }
    if (cmd === "protectDialog") { protectDialog(); return; }
    if (cmd === "restrictEditing") { toggleRestrictEditing(); return; }
    if (cmd === "toggleSameAsPrev") { toggleSameAsPrevCommand(); return; }
    if (cmd === "browsePlugins") { browsePlugins(); return; }
    if (cmd === "toggleInk") { toggleInk(value); return; }
    if (cmd === "inkMode") { setInkMode(value); return; }
    if (cmd === "inkSelect") { inkSelect(); return; }
    if (cmd === "readAloud") { readAloud(); return; }
    if (cmd === "dictate") { dictate(); return; }
    if (cmd === "inkColor") { setInkColor(); return; }
    if (cmd === "inkThickness") { setInkThickness(); return; }
    if (cmd === "managePlugins") { managePlugins(); return; }
    if (cmd === "photoEditor") { openPhotoEditorDialog(); return; }
    const isBlock =
      cmd === "formatBlock" && /^(H[1-6]|P)$/i.test(String(value || ""));
    // Font size/family and color have no semantic tags (the sanitizer strips
    // <font>), so run them with styleWithCSS on: the browser then emits
    // span[style] with font-size/font-family/color/background-color, all
    // properties the server sanitizer's whitelist keeps on save.
    const isSpanStyle =
      cmd === "fontSize" || cmd === "fontName" || cmd === "foreColor" ||
      cmd === "hiliteColor" || cmd === "backColor";
    if (isSpanStyle) {
      try { document.execCommand("styleWithCSS", false, "true"); } catch (err) { /* best effort */ }
    }
    // Loud-stub doctrine at the root: any command that reaches this
    // fallthrough but is not a real browser execCommand (e.g. a stubbed
    // ribbon button) must SAY so instead of failing silently — silent
    // no-ops read as "the app is broken".
    let supported = false;
    if (editBlocked()) return;
    try { supported = document.queryCommandSupported(cmd); } catch (err) { /* some engines throw on odd names */ }
    if (!supported) {
      setStatus(t("Status.NotAvailable") + " " + cmd, true);
      console.warn("wo: command not available:", cmd);
      return;
    }
    document.execCommand(cmd, false, isBlock ? String(value).toLowerCase() : value || null);
    if (isSpanStyle) {
      try { document.execCommand("styleWithCSS", false, "false"); } catch (err) { /* best effort */ }
    }
    // Structural + span-style commands don't fire a guaranteed `input`
    // event on every engine, so arm dirty/history explicitly; the
    // html===lastSnapshot guard keeps capture a no-op when the event ran.
    if (isBlock || isSpanStyle || cmd === "indent" || cmd === "outdent" ||
        cmd === "superscript" || cmd === "subscript" || cmd === "strikeThrough" ||
        /^justify/.test(cmd)) {
      markDirty();
      captureHistory();
    }
    updateActiveStates();
    updateUndoRedoState();
  }

  // Find the block-level element containing `node` (or null). Used by
  // applyLineHeight and by updateActiveStates to reflect the line spacing
  // at the caret. Same tag set as the find/replace BLOCK_TAGS constant.
  function blockElementAt(node) {
    let el = node;
    while (el && el !== editor) {
      if (el.nodeType === 1 && BLOCK_TAGS.indexOf(el.tagName) !== -1) return el;
      el = el.parentNode;
    }
    return null;
  }

  // Line spacing: set the CSS line-height of the block(s) touched by the
  // selection. The block gets `style="line-height: <n>;"`, which the server
  // sanitizer whitelist allows; picking "1.0" removes the property (single
  // spacing = the document default). Recorded as a single undoable step.
  // Restyle (or create) the list containing the selection, then arm the
  // save/collab pipeline — a style attribute change fires no input event.
  function applyListStyle(type) {
    if (READ_ONLY) return;
    const ordered = ["decimal", "lower-alpha", "lower-roman", "upper-alpha"].includes(type);
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    let node = sel.anchorNode;
    if (node && node.nodeType === 3) node = node.parentElement;
    let list = node && node.closest ? node.closest("#editor ul, #editor ol") : null;
    if (!list) {
      try { document.execCommand(ordered ? "insertOrderedList" : "insertUnorderedList"); } catch (err) { return; }
      node = sel.anchorNode;
      if (node && node.nodeType === 3) node = node.parentElement;
      list = node && node.closest ? node.closest("#editor ul, #editor ol") : null;
      if (!list) return;
    }
    if (list.tagName === (ordered ? "OL" : "UL")) list.style.listStyleType = type;
    markDirty();
    captureHistory();
    scheduleCollabSync();
    notifyHost("editing");
  }

  function applyLineHeight(value) {
    if (READ_ONLY) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!editor.contains(range.startContainer)) return;
    const blocks = blocksUnderSelection(sel);
    if (blocks.length === 0) return;
    const css = parseFloat(String(value));
    const clear = !(css > 0) || css === 1; // "" or "1" -> reset to default
    blocks.forEach((el) => {
      if (clear) el.style.removeProperty("line-height");
      else el.style.setProperty("line-height", String(css));
    });
    markDirty();
    captureHistory();
  }

  // ------------------------------------------------------------------
  // Undo/redo history — explicit innerHTML snapshot chain (US-31).
  //
  // document.execCommand("undo"/"redo") relies on an undocumented,
  // browser-dependent native stack with a small capacity, and it breaks
  // after DOM rewrites (smart-list conversion, table insert, reloads).
  // So we keep our own bounded snapshot history: every user edit pushes
  // the previous DOM state, Ctrl+Z / toolbar-undo walk back through it,
  // Ctrl+Y / toolbar-redo walk forward, and a fresh edit truncates the
  // redo branch. The chain is client-side only and survives saves: an
  // explicit save never clears it, so "undo after save" restores the
  // pre-save state — the exact contract of the Undo/Redo-Kette.
  // ------------------------------------------------------------------
  const HISTORY_LIMIT = 100; // 20+ steps required; headroom for comfort
  const undoStack = [];      // states we can go BACK to (oldest first)
  const redoStack = [];      // states we can go FORWARD to (newest first)
  let lastSnapshot = null;   // DOM state the chain currently reflects

  // Capture the state we are LEAVING, then remember the new one. Call
  // after the DOM has settled (input, list toggle, table insert) so
  // multi-step native commands form a single undoable step.
  function captureHistory() {
    const html = editor.innerHTML;
    if (html === lastSnapshot) return; // nothing changed: keep the stack
    if (lastSnapshot !== null) {
      undoStack.push(lastSnapshot);
      if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    }
    lastSnapshot = html;
    redoStack.length = 0; // a fresh edit discards the redo branch
    updateUndoRedoState();
  }

  function restoreSnapshot(html) {
    editor.innerHTML = html;
    hydrateVectorObjects();
    paginateQuiet();
    lastSnapshot = editor.innerHTML;
    // Park the caret at the end so the user can keep typing right away.
    try {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (err) {
      /* selection restore is best-effort */
    }
    markDirty();
    updateActiveStates();
    updateUndoRedoState();
  }

  function undoHistory() {
    if (READ_ONLY || undoStack.length === 0) return false;
    redoStack.push(lastSnapshot);
    lastSnapshot = undoStack.pop();
    restoreSnapshot(lastSnapshot);
    return true;
  }

  function redoHistory() {
    if (READ_ONLY || redoStack.length === 0) return false;
    undoStack.push(lastSnapshot);
    lastSnapshot = redoStack.pop();
    restoreSnapshot(lastSnapshot);
    return true;
  }

  // Grey out the toolbar buttons when the chain is exhausted (or the
  // document is read-only) instead of serving no-op clicks; the "can
  // undo/redo" state is the SIZE of our explicit stacks now, not the
  // opaque queryCommandEnabled query.
  function updateUndoRedoState() {
    const undoBtn = document.getElementById("btn-undo");
    const redoBtn = document.getElementById("btn-redo");
    if (undoBtn) undoBtn.disabled = READ_ONLY || undoStack.length === 0;
    if (redoBtn) redoBtn.disabled = READ_ONLY || redoStack.length === 0;
  }

  function updateActiveStates() {
    document.querySelectorAll("button[data-cmd]").forEach((btn) => {
      const cmd = btn.dataset.cmd;
      if (!cmd || cmd === "undo" || cmd === "redo") return;
      let active = false;
      try {
        if (cmd === "smallCaps" || cmd === "allCaps") {
          active = spanStyleActive(cmd === "smallCaps" ? "fontVariant" : "textTransform",
                                   cmd === "smallCaps" ? "small-caps" : "uppercase");
        } else if (cmd === "code") {
          active = fontIsMono();
        } else if (cmd === "directionRtl") {
          const sel = window.getSelection();
          const blk = sel && sel.anchorNode ? blockElementAt(sel.anchorNode) : null;
          active = !!(blk && blk.style.direction === "rtl");
        } else if (cmd === "toggleInk") {
          // Ink tool buttons reflect the active tool (pen/highlighter/eraser).
          active = inkMode === (btn.dataset.value || null);
        } else if (cmd === "inkMode") {
          // Draw master toggle is active while any ink tool is engaged.
          active = !!inkMode;
        } else if (cmd === "inkSelect") {
          // Select tool button reflects the select stroke-tool.
          active = inkMode === "select";
        } else {
          active = cmd === "formatBlock"
            ? (btn.dataset.value || "P") === currentBlockTag()
            : document.queryCommandState(cmd);
        }
      } catch (err) {
        // A few engines throw for queryCommandState on unsupported commands;
        // treat those as inactive instead of aborting the whole loop.
        active = false;
      }
      btn.classList.toggle("active", !!active);
      // Mirror the active state on the accessible name so screen readers
      // announce toggle-format buttons (bold, align, lists, headings) as
      // pressed/released. Only buttons that declare aria-pressed in the
      // markup are touched (indent/outdent etc. are not toggles).
      if (btn.hasAttribute("aria-pressed")) {
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      }
    });
    // Mirror the formatting at the caret in the full-toolbar dropdowns
    // (best effort — engines disagree on queryCommandValue formats, so a
    // failed read simply leaves the current value in place).
    const sizeEl = document.getElementById("font-size");
    if (sizeEl) {
      let size = "";
      try { size = document.queryCommandValue("fontSize"); } catch (err) { /* best effort */ }
      if (size && sizeEl.querySelector('option[value="' + size + '"]')) sizeEl.value = size;
    }
    const famEl = document.getElementById("font-family");
    if (famEl) {
      let fam = "";
      try { fam = document.queryCommandValue("fontName"); } catch (err) { /* best effort */ }
      fam = String(fam || "").replace(/^["']|["']$/g, "");
      // Match against the option list by value, not by building a CSS
      // selector: fontName can return a full font stack like
      // `system-ui, -apple-system, "Segoe UI", ...` whose embedded quotes
      // would make a querySelector value argument invalid (and spam errors
      // on every selectionchange).
      if (fam) {
        for (let i = 0; i < famEl.options.length; i++) {
          if (famEl.options[i].value === fam) { famEl.value = fam; break; }
        }
      }
    }
    // Line spacing: resolve the block's computed line-height into the
    // nearest preset in the dropdown (default 1.0 / single = placeholder).
    const lsEl = document.getElementById("line-spacing");
    if (lsEl) {
      const sel = window.getSelection();
      const blk = sel && sel.anchorNode ? blockElementAt(sel.anchorNode) : null;
      let preset = "";
      if (blk) {
        try {
          const cs = window.getComputedStyle(blk);
          const fs = parseFloat(cs.fontSize) || 16;
          const lh = cs.lineHeight;
          if (lh && lh !== "normal") {
            const mult = Math.round((parseFloat(lh) / fs) * 20) / 20;
            if (lsEl.querySelector('option[value="' + mult + '"]')) preset = String(mult);
          }
        } catch (err) { /* best effort */ }
      }
      lsEl.value = preset;
    }
  }

  function currentBlockTag() {
    let node = window.getSelection().anchorNode;
    while (node && node !== editor) {
      if (node.nodeType === 1 && /^H[1-6]$/.test(node.tagName)) {
        return node.tagName;
      }
      node = node.parentNode;
    }
    return "P";
  }

  // Accessibility: track which element opened a modal so closing returns
  // focus to it (WCAG 2.4.3 Focus Order), and keep Tab inside the open
  // dialog (modal-dialog pattern, WCAG 2.1.1/1.3.2). restoreFocus() is also
  // the fallback target when no dialog trigger is known.
  let lastFocusedEl = null;
  function rememberFocus() {
    lastFocusedEl = document.activeElement;
  }
  function restoreFocus() {
    const el = lastFocusedEl && document.body.contains(lastFocusedEl) ? lastFocusedEl : editor;
    lastFocusedEl = null;
    el.focus();
  }
  // Anchored insert popovers (table/image): sit under their ribbon button
  // (OO dropdown shape), clamped to the viewport. Both are non-modal, so
  // they are NOT in DIALOG_IDS (no modal Tab trap / overlay focus logic).
  function anchorInsertPop(popId, triggerId) {
    const pop = document.getElementById(popId);
    const trig = document.getElementById(triggerId);
    if (!pop || !trig) return;
    const tr = trig.getBoundingClientRect();
    const p = pop.getBoundingClientRect();
    let left = tr.left;
    let top = tr.bottom + 6;
    if (left + p.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - p.width - 8);
    if (top + p.height > window.innerHeight - 8) top = Math.max(8, tr.top - p.height - 6);
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
  }
  const DIALOG_IDS = ["find-dialog", "link-dialog", "symbol-dialog", "table-ops-dialog", "version-history-dialog", "ai-review-dialog", "ai-propose-dialog", "page-setup-dialog", "borders-dialog", "protect-dialog", "photo-editor-dialog", "notes-dialog", "pagenumber-dialog", "headerfooter-dialog", "trackchanges-dialog", "dropcap-dialog", "linenumbers-dialog", "hyphenation-dialog", "watermark-dialog", "pagecolor-dialog", "colors-dialog", "addtext-dialog", "updatetoc-dialog", "displaymode-dialog"];
  function getOpenDialog() {
    for (let i = 0; i < DIALOG_IDS.length; i++) {
      const d = document.getElementById(DIALOG_IDS[i]);
      if (d && d.classList.contains("open")) return d;
    }
    return null;
  }
  // Trap Tab / Shift+Tab inside the open modal overlay. Fully manual:
  // every Tab is intercepted and focus is moved within the dialog's own
  // focusables (with wrap-around). A boundary-only trap still lets native
  // tabbing skip dialog controls and escape the modal (observed in Chromium
  // with the statusbar footer present), so focus is clamped here instead.
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Tab") return;
    const dialog = getOpenDialog();
    if (!dialog) return;
    const focusables = Array.from(
      dialog.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"]):not([disabled])'
      )
    ).filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (focusables.length === 0) return;
    ev.preventDefault(); // a dialog is open: Tab must never leave it
    const idx = focusables.indexOf(document.activeElement);
    if (idx === -1) {
      // Focus escaped (or dialog just opened): clamp back inside.
      focusables[0].focus();
      return;
    }
    const n = focusables.length;
    const next = ev.shiftKey
      ? focusables[(idx - 1 + n) % n]
      : focusables[(idx + 1) % n];
    next.focus();
  });

  // ------------------------------------------------------------------
  // Insert-table dialog
  // ------------------------------------------------------------------
  // The toolbar's table button opens a small modal asking for row/column
  // counts, then inserts a real <table> at the saved cursor position.
  // The selection is captured before the dialog steals focus and restored
  // on confirm, so the table lands exactly where the user opened it.
  let tableSelRange = null;

  function insertTable() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("table-dialog");
    const rowsInput = document.getElementById("table-rows");
    const colsInput = document.getElementById("table-cols");
    if (!dialog || !rowsInput || !colsInput) return;
    rowsInput.value = "2";
    colsInput.value = "3";
    saveTableSelection();
    rememberFocus();
    dialog.classList.add("open");
    anchorInsertPop("table-dialog", "btn-table");
    colsInput.focus();
  }

  function saveTableSelection() {
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && editor.contains(sel.anchorNode)) {
      tableSelRange = sel.getRangeAt(0).cloneRange();
    } else {
      tableSelRange = null;
    }
  }

  function restoreTableSelection() {
    if (!tableSelRange) return;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(tableSelRange);
    tableSelRange = null;
  }

  function clampInt(raw, min, max, fallback) {
    const n = parseInt(raw, 10);
    if (Number.isNaN(n)) return fallback;
    return Math.min(Math.max(n, min), max);
  }

  function confirmTableDialog() {
    const rows = clampInt(document.getElementById("table-rows").value, 1, 20, 2);
    const cols = clampInt(document.getElementById("table-cols").value, 1, 10, 3);
    closeTableDialog();
    restoreTableSelection();
    editor.focus();
    const cell = "<td><br></td>";
    const row = "<tr>" + cell.repeat(cols) + "</tr>";
    // insertHTML fires an `input` event, which arms autosave and captures
    // history (see the input listener below); captureHistory() here is
    // belt-and-braces — the html === lastSnapshot guard makes it a no-op
    // when the input event already ran, and the fallback covers engines
    // that skip the event.
    document.execCommand("insertHTML", false, "<table>" + row.repeat(rows) + "</table>");
    captureHistory();
  }

  function closeTableDialog() {
    const dialog = document.getElementById("table-dialog");
    if (dialog) dialog.classList.remove("open");
    tableSelRange = null;
    restoreFocus();
  }

  // ------------------------------------------------------------------
  // Table actions: insert/delete row or column, merge/split cells.
  // ------------------------------------------------------------------
  function currentTableCell() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || !sel.focusNode) return null;
    const node = sel.focusNode.nodeType === 1 ? sel.focusNode : sel.focusNode.parentElement;
    if (!node) return null;
    const cell = node.closest ? node.closest("td, th") : null;
    return cell && editor.contains(cell) ? cell : null;
  }
  function tableGridCols(table) {
    let max = 1;
    Array.from(table.rows).forEach((r) => { max = Math.max(max, r.cells.length); });
    return max;
  }
  function insertTableRow(above) {
    const cell = currentTableCell(); if (!cell) return;
    const tr = cell.closest("tr");
    const table = tr.closest("table");
    const newTr = document.createElement("tr");
    const width = tableGridCols(table);
    for (let i = 0; i < width; i++) {
      const td = document.createElement("td");
      td.innerHTML = "<br>";
      newTr.appendChild(td);
    }
    if (above) tr.parentNode.insertBefore(newTr, tr);
    else tr.parentNode.insertBefore(newTr, tr.nextSibling);
    finalizeTableChange();
  }
  function insertTableCol(left) {
    const cell = currentTableCell(); if (!cell) return;
    const table = cell.closest("table");
    const idx = cell.cellIndex;
    Array.from(table.rows).forEach((r) => {
      const td = document.createElement("td");
      td.innerHTML = "<br>";
      const target = Math.min(left ? idx : idx + 1, r.cells.length);
      r.insertBefore(td, r.cells[target] || null);
    });
    finalizeTableChange();
  }
  function deleteTableRow() {
    const cell = currentTableCell(); if (!cell) return;
    cell.closest("tr").remove();
    finalizeTableChange();
  }
  function deleteTableCol() {
    const cell = currentTableCell(); if (!cell) return;
    const table = cell.closest("table");
    const idx = cell.cellIndex;
    Array.from(table.rows).forEach((r) => { if (r.cells[idx]) r.cells[idx].remove(); });
    Array.from(table.rows).forEach((r) => { if (!r.cells.length) r.remove(); });
    finalizeTableChange();
  }
  function selectedCells(table) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return [];
    const range = sel.getRangeAt(0);
    const out = [];
    table.querySelectorAll("td, th").forEach((c) => {
      if (range.intersectsNode(c)) out.push(c);
    });
    return out;
  }
  function mergeTableCells() {
    const cell = currentTableCell(); if (!cell) return;
    const table = cell.closest("table");
    const cells = selectedCells(table);
    if (cells.length < 2) return;
    const rows = Array.from(table.rows);
    const pos = cells.map((c) => ({
      cell: c,
      r: rows.indexOf(c.closest("tr")),
      c: c.cellIndex,
    }));
    const r0 = Math.min(...pos.map((p) => p.r));
    const r1 = Math.max(...pos.map((p) => p.r));
    const c0 = Math.min(...pos.map((p) => p.c));
    const c1 = Math.max(...pos.map((p) => p.c));
    // Collect the exact cells in the bounding rectangle BEFORE removing any:
    // the live row.cells collection re-indexes on each removal, so deleting
    // while iterating would shift indices and leave cells behind.
    const rect = [];
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const rc = table.rows[r] && table.rows[r].cells[c];
        if (rc) rect.push(rc);
      }
    }
    if (!rect.length) return;
    const texts = rect.map((c) => c.textContent.trim()).filter(Boolean);
    rect.forEach((cell) => cell.remove());
    const tr = table.rows[r0];
    const merged = document.createElement("td");
    merged.textContent = texts.join(" ");
    if (c1 - c0 > 0) merged.colSpan = c1 - c0 + 1;
    if (r1 - r0 > 0) merged.rowSpan = r1 - r0 + 1;
    tr.insertBefore(merged, tr.cells[c0] || null);
    finalizeTableChange();
  }
  function splitTableCell() {
    const cell = currentTableCell(); if (!cell) return;
    const cs = cell.colSpan || 1, rs = cell.rowSpan || 1;
    if (cs <= 1 && rs <= 1) return;
    cell.colSpan = 1; cell.rowSpan = 1;
    const table = cell.closest("table");
    const row = cell.parentNode;
    for (let c = 1; c < cs; c++) {
      const td = document.createElement("td");
      td.innerHTML = "<br>";
      row.insertBefore(td, cell.nextSibling ? cell.nextSibling : null);
    }
    const rows = Array.from(table.rows);
    const ri = rows.indexOf(row);
    const idx = cell.cellIndex;
    for (let r = ri + 1; r < ri + rs && r < rows.length; r++) {
      const td = document.createElement("td");
      td.innerHTML = "<br>";
      rows[r].insertBefore(td, rows[r].cells[idx] || null);
    }
    finalizeTableChange();
  }
  function finalizeTableChange() {
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }
  function closeTableOpsDialog() {
    const dialog = document.getElementById("table-ops-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function openTableOps() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("table-ops-dialog");
    const hint = document.getElementById("table-ops-hint");
    if (!dialog) return;
    rememberFocus();
    if (hint) hint.hidden = !!currentTableCell();
    dialog.classList.add("open");
  }

  // ------------------------------------------------------------------
  // Insert-image dialog
  // ------------------------------------------------------------------
  // The toolbar's image button opens a small modal asking for a local image
  // file. The file is read into a self-contained data: URI (nothing is
  // uploaded yet — the browser keeps it), previewed, and inserted as an
  // <img> at the saved cursor position on confirm. The server's sanitizer
  // allows data:image/ URIs and the ODT converter embeds the binary into
  // the stored document, so the picture persists across save/reload.
  // The selection is captured before the dialog steals focus and restored
  // on confirm, exactly like the table dialog.
  const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // 10 MB data-URI budget
  const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp", "image/svg+xml"];
  let imageSelRange = null;
  let imageDataUrl = null;

  function insertImage() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("image-dialog");
    const fileInput = document.getElementById("image-file");
    const okBtn = document.getElementById("btn-image-ok");
    const previewWrap = document.getElementById("image-preview-wrap");
    const errEl = document.getElementById("image-error");
    if (!dialog) return;
    saveImageSelection();
    imageDataUrl = null;
    if (fileInput) fileInput.value = "";
    if (okBtn) okBtn.disabled = true;
    if (previewWrap) previewWrap.hidden = true;
    if (errEl) errEl.textContent = "";
    const sizeFields = document.getElementById("image-size-fields");
    if (sizeFields) sizeFields.hidden = true;
    const wIn = document.getElementById("image-width");
    const hIn = document.getElementById("image-height");
    if (wIn) wIn.value = "";
    if (hIn) hIn.value = "";
    rememberFocus();
    dialog.classList.add("open");
    anchorInsertPop("image-dialog", "btn-image");
    if (fileInput) fileInput.focus();
  }

  // --- insert hyperlink (toolbar button + dialog) -------------------
  function insertLink() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("link-dialog");
    const urlInput = document.getElementById("link-url");
    if (!dialog || !urlInput) return;
    rememberFocus();
    urlInput.value = "";
    dialog.classList.add("open");
    urlInput.focus();
  }
  function confirmLinkDialog() {
    const urlInput = document.getElementById("link-url");
    const dialog = document.getElementById("link-dialog");
    const url = (urlInput && urlInput.value || "").trim();
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    if (!url) return;
    editor.focus();
    const sel = window.getSelection();
    if (sel && sel.toString().trim() !== "") {
      document.execCommand("createLink", false, url);
    } else {
      document.execCommand("insertHTML", false,
        '<a href="' + escapeAttr(url) + '">' + escapeAttr(url) + "</a>");
    }
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
  }

  function saveImageSelection() {
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && editor.contains(sel.anchorNode)) {
      imageSelRange = sel.getRangeAt(0).cloneRange();
    } else {
      imageSelRange = null;
    }
  }

  function restoreImageSelection() {
    if (!imageSelRange) return;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(imageSelRange);
    imageSelRange = null;
  }

  function escapeAttr(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Read the picked file into a data: URI, validate it (type + size), show
  // a preview and arm the Insert button. Runs on every file-input change.
  function onImageFileChange() {
    const fileInput = document.getElementById("image-file");
    const okBtn = document.getElementById("btn-image-ok");
    const errEl = document.getElementById("image-error");
    const previewWrap = document.getElementById("image-preview-wrap");
    const preview = document.getElementById("image-preview");
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (okBtn) okBtn.disabled = true;
    if (previewWrap) previewWrap.hidden = true;
    if (errEl) errEl.textContent = "";
    if (!file) {
      imageDataUrl = null;
      if (errEl) errEl.textContent = t("Image.NoFile");
      return;
    }
    if (IMAGE_TYPES.indexOf(file.type) === -1) {
      imageDataUrl = null;
      if (errEl) errEl.textContent = t("Image.UnsupportedType");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      imageDataUrl = null;
      if (errEl) errEl.textContent = t("Image.TooLarge");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      imageDataUrl = String(reader.result || "");
      if (preview) {
        preview.src = imageDataUrl;
        preview.alt = escapeAttr(file.name);
      }
      if (previewWrap) previewWrap.hidden = false;
      const sizeFields = document.getElementById("image-size-fields");
      if (sizeFields) sizeFields.hidden = false;
      if (errEl) errEl.textContent = "";
      if (okBtn) okBtn.disabled = false;
    };
    reader.onerror = () => {
      imageDataUrl = null;
      if (errEl) errEl.textContent = t("Image.ReadFailed");
    };
    reader.readAsDataURL(file);
  }

  // Insert the previewed image at the saved caret. insertHTML fires an
  // `input` event (arming autosave + capturing history); captureHistory()
  // here is belt-and-braces, exactly like the table insertion — the
  // html === lastSnapshot guard makes it a no-op when the event already
  // ran.
  function confirmImageDialog() {
    if (!imageDataUrl) return;
    const fileInput = document.getElementById("image-file");
    const file = fileInput && fileInput.files && fileInput.files[0];
    const alt = file ? escapeAttr(file.name) : "image";
    const src = imageDataUrl; // capture before closeImageDialog() clears it
    closeImageDialog();
    restoreImageSelection();
    editor.focus();
    const wIn = document.getElementById("image-width");
    const hIn = document.getElementById("image-height");
    const dims = [];
    const wRaw = wIn ? wIn.value.trim() : "";
    const hRaw = hIn ? hIn.value.trim() : "";
    if (/^\d+$/.test(wRaw) && Number(wRaw) > 0) dims.push(" width=\"" + Number(wRaw) + "\"");
    if (/^\d+$/.test(hRaw) && Number(hRaw) > 0) dims.push(" height=\"" + Number(hRaw) + "\"");
    document.execCommand(
      "insertHTML",
      false,
      '<img src="' + src + '" alt="' + alt + '"' + dims.join("") + '>'
    );
    captureHistory();
  }

  function closeImageDialog() {
    const dialog = document.getElementById("image-dialog");
    if (dialog) dialog.classList.remove("open");
    imageSelRange = null;
    imageDataUrl = null;
    restoreFocus();
  }

  // --- insert columns (toolbar button + dialog) ---------------------
  function openColumnsDialog() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("columns-dialog");
    const count = document.getElementById("columns-count");
    const gap = document.getElementById("columns-gap");
    if (!dialog) return;
    if (count) count.value = "2";
    if (gap) gap.value = "36";
    rememberFocus();
    dialog.classList.add("open");
    if (count) count.focus();
  }
  function closeColumnsDialog() {
    const dialog = document.getElementById("columns-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmColumnsDialog() {
    const dialog = document.getElementById("columns-dialog");
    const count = document.getElementById("columns-count");
    const gap = document.getElementById("columns-gap");
    const cols = Math.max(1, Math.min(6, parseInt((count && count.value) || "2", 10) || 2));
    const gapPx = Math.max(0, parseInt((gap && gap.value) || "36", 10) || 0);
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    document.execCommand("insertHTML", false,
      '<section data-columns="' + cols + '" data-column-gap="' + gapPx + '"><p><br></p></section>');
    moveCaretPastStructuralMarkers();
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
    updateUndoRedoState();
  }

  // --- insert table of contents (toolbar button + dialog) ------------
  function openTocDialog() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("toc-dialog");
    const title = document.getElementById("toc-title");
    if (!dialog) return;
    if (title) title.value = "Table of Contents";
    rememberFocus();
    dialog.classList.add("open");
    if (title) title.focus();
  }
  function closeTocDialog() {
    const dialog = document.getElementById("toc-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmTocDialog() {
    const dialog = document.getElementById("toc-dialog");
    const title = document.getElementById("toc-title");
    const t = ((title && title.value) || "Table of Contents").trim() || "Table of Contents";
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    document.execCommand("insertHTML", false,
      '<nav class="toc" data-title="' + escapeAttr(t) + '"></nav><p><br></p>');
    moveCaretPastStructuralMarkers();
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
    updateUndoRedoState();
  }

  // --- insert object (shape / text box / chart / equation) ----------
  function openObjectDialog(preset) {
    if (READ_ONLY) return;
    const dialog = document.getElementById("object-dialog");
    const type = document.getElementById("object-type");
    const label = document.getElementById("object-label");
    const content = document.getElementById("object-content");
    if (!dialog) return;
    const presets = type ? Array.from(type.options).map((o) => o.value) : [];
    if (preset && presets.includes(preset)) {
      if (type) type.value = preset;
    } else if (type) type.value = "shape";
    if (label) label.value = "";
    if (content) content.value = "";
    rememberFocus();
    dialog.classList.add("open");
    if (type) type.focus();
  }
  function closeObjectDialog() {
    const dialog = document.getElementById("object-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmObjectDialog() {
    const dialog = document.getElementById("object-dialog");
    const type = document.getElementById("object-type");
    const label = document.getElementById("object-label");
    const content = document.getElementById("object-content");
    const typ = (type && type.value) || "shape";
    const lbl = (label && label.value || "").trim();
    const c = (content && content.value) || "";
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    let html = '<div class="object" data-type="' + escapeAttr(typ) + '"';
    if (lbl) html += ' data-label="' + escapeAttr(lbl) + '"';
    const safe = String(c).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    html += '>' + safe + '</div><p><br></p>';
    document.execCommand("insertHTML", false, html);
    moveCaretPastStructuralMarkers();
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
    updateUndoRedoState();
  }

  function openBookmarkDialog() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("bookmark-dialog");
    const name = document.getElementById("bookmark-name");
    if (!dialog) return;
    if (name) name.value = "";
    rememberFocus();
    dialog.classList.add("open");
    if (name) name.focus();
  }
  function closeBookmarkDialog() {
    const dialog = document.getElementById("bookmark-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmBookmarkDialog() {
    const dialog = document.getElementById("bookmark-dialog");
    const nameEl = document.getElementById("bookmark-name");
    const name = (nameEl && nameEl.value || "").trim();
    if (!name) { if (nameEl) nameEl.focus(); return; }
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    const sel = window.getSelection();
    const text = (sel && sel.toString()) || name;
    const html = '<span class="bookmark" data-name="' + escapeAttr(name) + '">' + escapeAttr(text) + '</span>';
    document.execCommand("insertHTML", false, html);
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
    updateUndoRedoState();
  }

  function openCrossrefDialog() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("crossref-dialog");
    const target = document.getElementById("crossref-target");
    const textEl = document.getElementById("crossref-text");
    if (!dialog) return;
    if (target) {
      target.innerHTML = "";
      const names = new Set();
      document.querySelectorAll("span.bookmark[data-name]").forEach((s) => {
        const n = s.getAttribute("data-name");
        if (n) names.add(n);
      });
      if (names.size === 0) {
        const opt = document.createElement("option");
        opt.value = ""; opt.textContent = "(no bookmarks yet)";
        target.appendChild(opt);
      } else {
        Array.from(names).sort().forEach((n) => {
          const opt = document.createElement("option");
          opt.value = n; opt.textContent = n;
          target.appendChild(opt);
        });
      }
    }
    if (textEl) textEl.value = "";
    rememberFocus();
    dialog.classList.add("open");
    if (target) target.focus();
  }
  function closeCrossrefDialog() {
    const dialog = document.getElementById("crossref-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmCrossrefDialog() {
    const dialog = document.getElementById("crossref-dialog");
    const target = document.getElementById("crossref-target");
    const textEl = document.getElementById("crossref-text");
    const name = (target && target.value) || "";
    if (!name) { if (target) target.focus(); return; }
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    const sel = window.getSelection();
    let label = (textEl && textEl.value || "").trim();
    if (!label) label = (sel && sel.toString()) || name;
    const html = '<a href="#' + escapeAttr(name) + '">' + escapeAttr(label) + '</a>';
    document.execCommand("insertHTML", false, html);
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
    updateUndoRedoState();
  }

  function setTrackChanges(on) {
    if (READ_ONLY) return;
    trackChangesOn = on;
    editor.classList.toggle("track-on", on);
    const btn = document.getElementById("btn-track-changes");
    if (btn) {
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.classList.toggle("active", on);
    }
    renderReviewList();
  }

  function insertTracked(tag, text) {
    if (!text) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const node = document.createElement(tag);
    node.className = tag === "ins" ? "track-insert" : "track-delete";
    node.setAttribute("data-author", TRACK_AUTHOR);
    node.textContent = text;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    range.insertNode(node);
    const r = document.createRange();
    r.setStartAfter(node);
    r.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r);
  }

  function wrapRangeInDel(range) {
    if (!range || range.collapsed) return;
    const del = document.createElement("del");
    del.className = "track-delete";
    del.setAttribute("data-author", TRACK_AUTHOR);
    del.appendChild(range.extractContents());
    range.insertNode(del);
    const r = document.createRange();
    r.setStartBefore(del);
    r.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
  }

  function onBeforeInput(e) {
    if (!trackChangesOn || READ_ONLY) return;
    try {
      const it = e.inputType;
      if (it === "insertText" && e.data) {
        e.preventDefault();
        insertTracked("ins", e.data);
        afterTrackEdit();
      } else if (it === "insertFromPaste") {
        e.preventDefault();
        const text = (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "";
        if (text) { insertTracked("ins", text); afterTrackEdit(); }
      } else if (it.indexOf("delete") === 0) {
        e.preventDefault();
        const ranges = (typeof e.getTargetRanges === "function") ? e.getTargetRanges() : [];
        if (ranges.length) wrapRangeInDel(ranges[0]);
        afterTrackEdit();
      }
    } catch (err) { /* never let tracking break normal editing */ }
  }

  function afterTrackEdit() {
    markDirty();
    captureHistory();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
    updateUndoRedoState();
    renderReviewList();
  }

  function openReviewPanel() {
    const panel = document.getElementById("review-panel");
    if (panel) panel.hidden = false;
    renderReviewList();
  }
  function closeReviewPanel() {
    const panel = document.getElementById("review-panel");
    if (panel) panel.hidden = true;
  }
  function renderReviewList() {
    const list = document.getElementById("review-list");
    const empty = document.getElementById("review-empty");
    if (!list) return;
    const items = Array.from(editor.querySelectorAll("ins.track-insert, del.track-delete"));
    list.innerHTML = "";
    if (empty) empty.style.display = items.length ? "none" : "block";
    items.forEach((el) => {
      const kind = el.tagName.toLowerCase() === "ins" ? "insert" : "delete";
      const author = el.getAttribute("data-author") || "";
      const text = (el.textContent || "").slice(0, 120);
      const item = document.createElement("div");
      item.className = "review-item review-" + kind;
      const label = document.createElement("div");
      label.className = "review-meta";
      label.textContent = (kind === "insert" ? "Insertion" : "Deletion") +
        (author ? " — " + author : "");
      const body = document.createElement("div");
      body.className = "review-text";
      body.textContent = text;
      const actions = document.createElement("div");
      actions.className = "review-actions";
      const acc = document.createElement("button");
      acc.type = "button"; acc.className = "primary"; acc.textContent = "Accept";
      acc.addEventListener("click", () => acceptChange(el, kind));
      const rej = document.createElement("button");
      rej.type = "button"; rej.textContent = "Reject";
      rej.addEventListener("click", () => rejectChange(el, kind));
      actions.appendChild(acc); actions.appendChild(rej);
      item.appendChild(label); item.appendChild(body); item.appendChild(actions);
      list.appendChild(item);
    });
  }
  function acceptChange(el, kind) {
    const parent = el.parentNode;
    if (!parent) return;
    if (kind === "insert") { while (el.firstChild) parent.insertBefore(el.firstChild, el); }
    parent.removeChild(el);
    closeReviewPanelIfEmpty();
    afterTrackEdit();
  }
  function rejectChange(el, kind) {
    const parent = el.parentNode;
    if (!parent) return;
    if (kind === "delete") { while (el.firstChild) parent.insertBefore(el.firstChild, el); }
    parent.removeChild(el);
    closeReviewPanelIfEmpty();
    afterTrackEdit();
  }
  function closeReviewPanelIfEmpty() {
    const items = editor.querySelectorAll("ins.track-insert, del.track-delete");
    if (items.length === 0) closeReviewPanel();
  }

  // Tracked-change prev/next navigation (collab.prev-change /
  // collab.next-change). View-only: scrolls the change into view and
  // selects it — no converter touch, mirroring the #nav-panel heading-jump
  // precedent. Anchored on the caret so repeated clicks walk the changes in
  // document order; selecting a change moves the caret into it, so the next
  // click advances to the following change (sequential walk for free).
  function navigateTrackedChange(dir) {
    const changes = Array.from(
      editor.querySelectorAll("ins.track-insert, del.track-delete"));
    if (!changes.length) {
      setStatus(t("Status.NoTrackedChanges"));
      return;
    }
    // Collapsed caret range at the selection anchor; null when nothing is
    // selected (next -> first change, prev -> last change).
    const sel = window.getSelection();
    let caret = null;
    if (sel && sel.rangeCount > 0) {
      caret = sel.getRangeAt(0).cloneRange();
      caret.collapse(true);
    }
    let target = -1;
    if (dir > 0) {
      // next: first change whose start is strictly after the caret
      for (let i = 0; i < changes.length; i++) {
        const cr = document.createRange();
        cr.selectNode(changes[i]);
        if (!caret || caret.compareBoundaryPoints(Range.END_TO_START, cr) < 0) {
          target = i;
          break;
        }
      }
      if (target === -1) { setStatus(t("Status.NoNextChange")); return; }
    } else {
      // prev: last change whose end is strictly before the caret
      for (let i = changes.length - 1; i >= 0; i--) {
        const cr = document.createRange();
        cr.selectNode(changes[i]);
        if (!caret || caret.compareBoundaryPoints(Range.START_TO_END, cr) > 0) {
          target = i;
          break;
        }
      }
      if (target === -1) { setStatus(t("Status.NoPrevChange")); return; }
    }
    const el = changes[target];
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("nav-flash");
    void el.offsetWidth;  // restart the flash animation
    el.classList.add("nav-flash");
    setTimeout(() => el.classList.remove("nav-flash"), 1600);
    const r = document.createRange();
    r.selectNodeContents(el);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
    setStatus(dir > 0 ? t("Collab.NextChange") : t("Collab.PrevChange"));
  }
  function prevTrackedChange() { navigateTrackedChange(-1); }
  function nextTrackedChange() { navigateTrackedChange(1); }

  function openCommentDialog() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("comment-dialog");
    const bodyEl = document.getElementById("comment-body");
    if (!dialog) return;
    const sel = window.getSelection();
    commentRange = (sel && sel.rangeCount) ? sel.getRangeAt(0).cloneRange() : null;
    if (bodyEl) bodyEl.value = "";
    rememberFocus();
    dialog.classList.add("open");
    if (bodyEl) bodyEl.focus();
  }
  function closeCommentDialog() {
    const dialog = document.getElementById("comment-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmCommentDialog() {
    const dialog = document.getElementById("comment-dialog");
    const bodyEl = document.getElementById("comment-body");
    const body = (bodyEl && bodyEl.value || "").trim();
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    if (!commentRange) { renderCommentsList(); openCommentsPanel(); return; }
    const span = document.createElement("span");
    span.className = "comment";
    span.setAttribute("data-author", TRACK_AUTHOR);
    span.setAttribute("data-comment", body);
    try {
      commentRange.surroundContents(span);
    } catch (err) {
      const text = commentRange.toString();
      const html = '<span class="comment" data-author="' + escapeAttr(TRACK_AUTHOR) +
        '" data-comment="' + escapeAttr(body) + '">' + escapeAttr(text) + '</span>';
      try { document.execCommand("insertHTML", false, html); } catch (e2) {}
      afterTrackEdit();
      renderCommentsList();
      openCommentsPanel();
      return;
    }
    afterTrackEdit();
    renderCommentsList();
    openCommentsPanel();
  }
  function openCommentsPanel() {
    const p = document.getElementById("comments-panel");
    if (p) p.hidden = false;
    renderCommentsList();
  }
  function closeCommentsPanel() {
    const p = document.getElementById("comments-panel");
    if (p) p.hidden = true;
  }
  function closeCommentsPanelIfEmpty() {
    const items = editor.querySelectorAll("span.comment");
    if (items.length === 0) closeCommentsPanel();
  }
  function renderCommentsList() {
    const list = document.getElementById("comments-list");
    const empty = document.getElementById("comments-empty");
    if (!list) return;
    const items = Array.from(editor.querySelectorAll("span.comment"));
    list.innerHTML = "";
    if (empty) empty.style.display = items.length ? "none" : "block";
    items.forEach((el) => {
      const author = el.getAttribute("data-author") || "";
      const body = el.getAttribute("data-comment") || "";
      const item = document.createElement("div");
      item.className = "review-item";
      const meta = document.createElement("div");
      meta.className = "review-meta";
      meta.textContent = author ? "Comment — " + author : "Comment";
      const b = document.createElement("div");
      b.className = "review-text";
      b.textContent = body || (el.textContent || "").slice(0, 120);
      const actions = document.createElement("div");
      actions.className = "review-actions";
      const locate = document.createElement("button");
      locate.type = "button"; locate.textContent = "Go to";
      locate.addEventListener("click", () => {
        el.scrollIntoView({ block: "center" });
        const r = document.createRange();
        r.selectNodeContents(el);
        const s = window.getSelection();
        s.removeAllRanges();
        s.addRange(r);
      });
      const del = document.createElement("button");
      del.type = "button"; del.className = "primary"; del.textContent = "Delete";
      del.addEventListener("click", () => deleteComment(el));
      actions.appendChild(locate); actions.appendChild(del);
      item.appendChild(meta); item.appendChild(b); item.appendChild(actions);
      list.appendChild(item);
    });
  }
  function deleteComment(el) {
    const parent = el.parentNode;
    if (!parent) return;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    parent.removeChild(el);
    afterTrackEdit();
    renderCommentsList();
    closeCommentsPanelIfEmpty();
  }

  // wo-command event bus (project-wide invariant)
  // ------------------------------------------------------------------
  // Every formatting edit flows through a single channel:
  //   window.dispatchEvent(new CustomEvent("wo-command", {detail:{command,
  //   value}}))
  // The toolbar buttons, keyboard shortcuts and the markdown auto-converter
  // all emit here; the listener below is the one executor, so the future
  // mutation-engine router can drive the same editor without forking code
  // paths. Undo/redo stay internal (they walk the snapshot chain, they do
  // not mutate content); everything else funnels into runCommand().
  function emitCommand(cmd, value) {
    window.dispatchEvent(new CustomEvent("wo-command", {
      detail: { command: cmd, value: value == null ? null : String(value) },
    }));
  }
  window.addEventListener("wo-command", (ev) => {
    const detail = ev.detail || {};
    if (typeof detail.command !== "string" || !detail.command) return;
    if (window.__RECORD_COMMANDS__) recordCommand(detail.command, detail.value);
    runCommand(detail.command, detail.value == null ? null : String(detail.value));
  });

  // --- agent command recorder (opt-in: ?record=1) -------------------
  // Every command ON THE BUS is logged as one replayable JSON line — the
  // listener records, so externally dispatched agent commands are captured
  // too, not just UI button presses. Plain-text selection offsets ride
  // along so replay can restore the selection context before
  // re-dispatching (commands are selection-scoped). The log is the session
  // artifact: JSON.stringify each entry for the .jsonl file.
  function recordCommand(cmd, value) {
    const sel = window.getSelection();
    const selLen = sel && sel.rangeCount ? sel.toString().length : 0;
    const end = caretOffset(editor);
    window.__COMMAND_LOG__.push({
      t: Math.round(performance.now()),
      command: cmd,
      value: value == null ? null : String(value),
      at: Math.max(0, end - selLen),
      len: selLen,
    });
  }
  // Replays entries (objects or JSONL strings) against the current document
  // and returns the resulting DOM hash for the record->replay equality gate.
  function replayCommands(entries) {
    (entries || []).forEach((e) => {
      if (typeof e === "string") e = JSON.parse(e);
      const at = e.at || 0, len = e.len || 0;
      const r = logicalRange(at, at + len);
      if (r) {
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
      }
      window.dispatchEvent(new CustomEvent("wo-command", {
        detail: { command: e.command, value: e.value == null ? null : String(e.value) },
      }));
    });
    return commandDomHash();
  }
  function commandDomHash() {
    const html = flatHtml();
    let h = 5381;
    for (let i = 0; i < html.length; i++) h = ((h << 5) + h + html.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16);
  }
  // Agent entry points (the rest of the editor is IIFE-private).
  window.replayCommands = replayCommands;
  window.commandDomHash = commandDomHash;

  // ------------------------------------------------------------------
  // Find and replace
  // ------------------------------------------------------------------
  // Vanilla-JS search over the live contenteditable DOM, zero dependencies.
  // Matches are located per text node (with cross-node spanning inside the
  // same block, so "Hel" + "lo" in separate inline elements still match
  // "Hello"), flattened into document order and highlighted with the native
  // Selection — the DOM is never mutated for highlighting, so the undo
  // snapshot chain stays intact. Replace routes through
  // document.execCommand("insertText") (native undo + a real `input` event);
  // Replace-all runs in reverse document order so earlier node references
  // stay valid, and collapses the whole batch into a single undoable step.
  const BLOCK_TAGS = ["P", "DIV", "H1", "H2", "H3", "H4", "H5", "H6", "LI", "TD", "TH", "TABLE", "UL", "OL", "BLOCKQUOTE", "PRE"];
  const findState = {
    textNodes: null,  // ordered text-node list of the last collectMatches()
    matches: [],      // [{startNode,start,endNode,end,startPos,endPos}]
    current: -1,      // index of the highlighted match
    anchorPos: null,  // {ni,off}: “don’t step back before this” search anchor
    lastQuery: null,
    lastMatchCase: false,
  };
  let bulkEdit = false;          // replace-all batch: suppress per-step history
  let updatingFindSelection = false; // guard the selectionchange tracker

  function blockAncestor(node) {
    let el = node && node.parentNode;
    while (el && el !== editor) {
      if (el.nodeType === 1 && BLOCK_TAGS.indexOf(el.tagName) !== -1) return el;
      el = el.parentNode;
    }
    return editor;
  }

  function collectTextNodes() {
    const nodes = [];
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    return nodes;
  }

  function posCompare(a, b) {
    if (a.ni !== b.ni) return a.ni < b.ni ? -1 : 1;
    if (a.off !== b.off) return a.off < b.off ? -1 : 1;
    return 0;
  }

  // Pure match engine over an ordered text-node list. `sameBlock(i,j)`
  // decides whether two successive nodes may bridge into one match (both
  // inside the same paragraph/cell/heading — never across the end of one
  // block into the next, so "foo" in <p>1</p><p>2</p> does not match
  // "12"). Every start offset of every node is tried — this is what finds
  // "cat" inside "concatenate" — and overlapping hits are dropped ("aa"
  // in "aaa" matches once), matching how replace-all behaves.
  function buildMatches(textNodes, sameBlock, query, matchCase) {
    const q = matchCase ? query : query.toLowerCase();
    const ql = q.length;
    const fold = (s) => (matchCase ? s : s.toLowerCase());
    const all = [];
    const n = textNodes.length;
    for (let i = 0; i < n; i++) {
      const startFold = fold(textNodes[i].data);
      const maxStart = startFold.length; // one-past-end lets a match start at
                                         // a node boundary and span into the
                                         // next node
      for (let off = 0; off <= maxStart; off++) {
        let ri = i;
        let ro = off;
        let ki = 0;
        let endNi = -1;
        let endOff = -1;
        let ok = true;
        while (ki < ql) {
          if (ri >= n) { ok = false; break; }
          const nodeFold = fold(textNodes[ri].data);
          const avail = nodeFold.length - ro;
          if (avail <= 0) {
            const next = ri + 1;
            if (next >= n || !sameBlock(ri, next)) { ok = false; break; }
            ri = next;
            ro = 0;
            continue;
          }
          const take = Math.min(avail, ql - ki);
          if (q.substr(ki, take) !== nodeFold.substr(ro, take)) { ok = false; break; }
          ki += take;
          ro += take;
          if (ki >= ql) { endNi = ri; endOff = ro; }
        }
        if (ok && endNi >= 0) {
          all.push({
            startNode: textNodes[i],
            start: off,
            endNode: textNodes[endNi],
            end: endOff,
            startPos: { ni: i, off },
            endPos: { ni: endNi, off: endOff },
          });
        }
      }
    }
    const clean = [];
    let lastEnd = null;
    for (let k = 0; k < all.length; k++) {
      const r = all[k];
      if (lastEnd === null || posCompare(r.startPos, lastEnd) >= 0) {
        clean.push(r);
        lastEnd = r.endPos;
      }
    }
    return clean;
  }

  function collectMatches(query, matchCase) {
    const textNodes = collectTextNodes();
    findState.textNodes = textNodes;
    const cache = new Map();
    const sameBlock = (a, b) => {
      let ba = cache.get(a);
      if (!ba) { ba = blockAncestor(textNodes[a]); cache.set(a, ba); }
      let bb = cache.get(b);
      if (!bb) { bb = blockAncestor(textNodes[b]); cache.set(b, bb); }
      return ba === bb;
    };
    return buildMatches(textNodes, sameBlock, query, matchCase);
  }

  function getTextNodes() {
    if (!findState.textNodes) findState.textNodes = collectTextNodes();
    return findState.textNodes;
  }

  // Map a DOM position (container + offset) onto the ordered text-node
  // list as {ni, off}, or null when it points outside the editor. Element
  // containers (caret on a block boundary) resolve to the first text node
  // at/after the boundary.
  function positionToIndex(container, offset) {
    const textNodes = getTextNodes();
    if (container.nodeType === 3) {
      const ni = textNodes.indexOf(container);
      return ni === -1 ? null : { ni, off: offset };
    }
    if (container.nodeType !== 1) return null;
    const child = offset < container.childNodes.length ? container.childNodes[offset] : null;
    for (let i = 0; i < textNodes.length; i++) {
      const t = textNodes[i];
      if (child) {
        const precedes = (t.compareDocumentPosition(child) & Node.DOCUMENT_POSITION_PRECEDING) !== 0;
        if (!precedes) return { ni: i, off: 0 };
      } else {
        const rel = t.compareDocumentPosition(container);
        const inside = (rel & Node.DOCUMENT_POSITION_CONTAINED_BY) !== 0;
        const precedes = (rel & Node.DOCUMENT_POSITION_PRECEDING) !== 0;
        if (!inside && !precedes) return { ni: i, off: 0 };
      }
    }
    return null;
  }

  function caretTextPos() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    if (!editor.contains(range.startContainer)) return null;
    return positionToIndex(range.startContainer, range.startOffset);
  }

  function editorSelectionText() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return "";
    const range = sel.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return "";
    return range.toString();
  }

  // First match at/after (forward) or at/before (reverse) `from`; wraps
  // around when the document is exhausted.
  function firstMatchFrom(matches, from, forward) {
    if (!matches.length) return -1;
    if (!from) return forward ? 0 : matches.length - 1;
    if (forward) {
      for (let i = 0; i < matches.length; i++) {
        if (posCompare(matches[i].startPos, from) >= 0) return i;
      }
      return 0;
    }
    for (let i = matches.length - 1; i >= 0; i--) {
      if (posCompare(matches[i].startPos, from) <= 0) return i;
    }
    return matches.length - 1;
  }

  function setCurrentMatch(idx) {
    findState.current = idx;
    const m = idx >= 0 && idx < findState.matches.length ? findState.matches[idx] : null;
    if (!m) {
      findState.anchorPos = null;
      const sel = window.getSelection();
      if (sel) sel.removeAllRanges();
      updateFindUI();
      return;
    }
    findState.anchorPos = m.startPos;
    const range = document.createRange();
    range.setStart(m.startNode, m.start);
    range.setEnd(m.endNode, m.end);
    updatingFindSelection = true;
    try {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } finally {
      updatingFindSelection = false;
    }
    try {
      const rect = range.getBoundingClientRect();
      const vh = window.innerHeight || 600;
      if (rect && (rect.top < 40 || rect.bottom > vh - 40)) {
        const el = m.startNode.parentElement;
        if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", inline: "nearest" });
      }
    } catch (err) { /* scroll is best-effort */ }
    updateFindUI();
  }

  // The one search entry point. `relative` (= next/prev stepping) continues
  // from the current match; otherwise the search starts from the anchor
  // (previous match, else the caret, else the top of the document).
  function performSearch(opts) {
    opts = opts || {};
    const forward = !!opts.forward;
    const relative = !!opts.relative;
    const qInput = document.getElementById("find-query");
    const query = qInput ? qInput.value : "";
    const caseInput = document.getElementById("find-match-case");
    const matchCase = !!(caseInput && caseInput.checked);
    const queryChanged = query !== findState.lastQuery || matchCase !== findState.lastMatchCase;
    findState.lastQuery = query;
    findState.lastMatchCase = matchCase;
    if (query === "") {
      findState.matches = [];
      setCurrentMatch(-1);
      return false;
    }
    findState.matches = collectMatches(query, matchCase);
    let idx = -1;
    if (relative && !queryChanged) {
      const total = findState.matches.length;
      if (total === 0) idx = -1;
      else if (findState.current < 0) idx = 0;
      else idx = (findState.current + (forward ? 1 : -1) + total) % total;
    } else {
      const from = findState.anchorPos || caretTextPos() || null;
      idx = firstMatchFrom(findState.matches, from, forward);
    }
    setCurrentMatch(idx);
    return idx >= 0;
  }

  function updateFindUI() {
    const countEl = document.getElementById("find-count");
    const statusEl = document.getElementById("find-status");
    const replaceBtn = document.getElementById("btn-find-replace");
    const replaceAllBtn = document.getElementById("btn-find-replace-all");
    const replaceInput = document.getElementById("find-replace");
    const nextBtn = document.getElementById("btn-find-next");
    const prevBtn = document.getElementById("btn-find-prev");
    const total = findState.matches.length;
    const noMatch = total === 0;
    if (countEl) {
      countEl.textContent = noMatch ? "0 / 0" : findState.current + 1 + " / " + total;
      countEl.classList.toggle("no-match", noMatch);
    }
    if (statusEl) {
      statusEl.textContent = noMatch ? t("Find.NoMatches") : "";
      statusEl.classList.toggle("no-match", noMatch);
    }
    if (replaceBtn) replaceBtn.disabled = READ_ONLY || noMatch || findState.current < 0;
    if (replaceAllBtn) replaceAllBtn.disabled = READ_ONLY || noMatch;
    if (replaceInput) replaceInput.disabled = READ_ONLY;
    if (nextBtn) nextBtn.disabled = noMatch;
    if (prevBtn) prevBtn.disabled = noMatch;
  }

  function openFindDialog() {
    const dialog = document.getElementById("find-dialog");
    if (!dialog) return;
    const qInput = document.getElementById("find-query");
    // First open: prefill the query with the selected text, like real
    // word processors do. Only when the user has not typed a query yet.
    if (qInput && !findState.lastQuery) {
      const selText = editorSelectionText();
      if (selText) qInput.value = selText.slice(0, 200);
    }
    rememberFocus();
    dialog.classList.add("open");
    if (qInput) {
      qInput.focus();
      qInput.select();
    }
    findState.anchorPos = null; // start from the caret/selection this time
    performSearch({ forward: true, relative: false });
    updateFindUI();
  }

  function closeFindDialog() {
    const dialog = document.getElementById("find-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }

  function findNav(forward) {
    const qInput = document.getElementById("find-query");
    const fresh = !qInput || qInput.value !== findState.lastQuery;
    performSearch({ forward, relative: !fresh });
  }

  function onFindInput() {
    performSearch({ forward: true, relative: false });
  }

  function onFindQueryKeydown(ev) {
    if (ev.key === "Enter") {
      ev.preventDefault();
      findNav(!ev.shiftKey);
    }
  }

  function onFindReplaceKeydown(ev) {
    if (ev.key === "Enter") {
      ev.preventDefault();
      doReplace();
    }
  }

  // Select a recorded match and replace it via execCommand("insertText")
  // (native undo + a real `input` event that arms autosave/history).
  function replaceSelected(m, replacement) {
    const range = document.createRange();
    range.setStart(m.startNode, m.start);
    range.setEnd(m.endNode, m.end);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return document.execCommand("insertText", false, replacement);
  }

  function doReplace() {
    if (READ_ONLY) return;
    const m = findState.matches[findState.current];
    if (!m) return;
    const replaceInput = document.getElementById("find-replace");
    const replacement = replaceInput ? replaceInput.value : "";
    if (!replaceSelected(m, replacement)) return;
    // insertText fired an `input` event, which invalidated the stale match
    // list (see the input listener below) and armed autosave/history.
    // Belt-and-braces: make sure dirty + history are recorded even on the
    // odd engine that skips the event.
    markDirty();
    captureHistory();
    // insertText leaves the caret just AFTER the replacement: re-search from
    // there so the freshly inserted text is never re-matched.
    findState.anchorPos = null;
    performSearch({ forward: true, relative: false });
  }

  function doReplaceAll() {
    if (READ_ONLY) return;
    const replaceInput = document.getElementById("find-replace");
    const replacement = replaceInput ? replaceInput.value : "";
    const matches = findState.matches.slice(); // snapshot: the DOM is about to change
    if (matches.length === 0) return;
    bulkEdit = true;
    try {
      // Reverse document order keeps earlier node references/offsets valid.
      for (let i = matches.length - 1; i >= 0; i--) {
        replaceSelected(matches[i], replacement);
      }
    } finally {
      bulkEdit = false;
    }
    markDirty();
    captureHistory(); // the whole batch becomes ONE undoable step
    updateUndoRedoState();
    findState.anchorPos = null;
    performSearch({ forward: true, relative: false });
  }

  // A document edit makes every stored node/offset reference stale: drop
  // the search state so the next search starts fresh from the caret.
  function invalidateFindState() {
    findState.textNodes = null;
    findState.matches = [];
    findState.current = -1;
    findState.anchorPos = null;
    findState.lastQuery = null;
    findState.lastMatchCase = false;
    updateFindUI();
  }

  function selectionEqualsCurrentMatch() {
    const m = findState.matches[findState.current];
    if (!m) return false;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return false;
    const range = sel.getRangeAt(0);
    return (
      range.startContainer === m.startNode &&
      range.startOffset === m.start &&
      range.endContainer === m.endNode &&
      range.endOffset === m.end
    );
  }

  document.querySelectorAll("button[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => emitCommand(btn.dataset.cmd, btn.dataset.value));
  });
  document.getElementById("btn-table").addEventListener("click", insertTable);
  document.getElementById("btn-table-ops").addEventListener("click", openTableOps);
  const tableOps = {
    "op-row-above": () => insertTableRow(true),
    "op-row-below": () => insertTableRow(false),
    "op-col-left": () => insertTableCol(true),
    "op-col-right": () => insertTableCol(false),
    "op-del-row": deleteTableRow,
    "op-del-col": deleteTableCol,
    "op-merge": mergeTableCells,
    "op-split": splitTableCell,
  };
  Object.keys(tableOps).forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener("click", () => { tableOps[id](); closeTableOpsDialog(); });
  });
  const tableOpsClose = document.getElementById("btn-table-ops-close");
  if (tableOpsClose) tableOpsClose.addEventListener("click", closeTableOpsDialog);
  document.getElementById("btn-image").addEventListener("click", insertImage);
  const linkBtn = document.getElementById("btn-link");
  if (linkBtn) linkBtn.addEventListener("click", insertLink);
  const linkOk = document.getElementById("btn-link-ok");
  if (linkOk) linkOk.addEventListener("click", confirmLinkDialog);
  const linkCancel = document.getElementById("btn-link-cancel");
  if (linkCancel) linkCancel.addEventListener("click", () => {
    const d = document.getElementById("link-dialog");
    if (d) d.classList.remove("open");
    restoreFocus();
  });
  const linkInput = document.getElementById("link-url");
  if (linkInput) linkInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmLinkDialog(); }
  });

  // --- version history -----------------------------------------------
  // Lists the server's snapshots (one per save) newest-first with a Restore
  // button per entry; restoring rewinds the stored document and reloads the
  // editor so the change is immediately visible and undoable via History.
  const versionList = document.getElementById("version-list");
  const versionError = document.getElementById("version-error");
  const versionDialog = document.getElementById("version-history-dialog");
  let versionEntries = [];

  function formatVersionDate(ts) {
    try {
      return new Date(ts).toLocaleString();
    } catch (e) {
      return String(ts);
    }
  }
  function formatVersionAuthor(author) {
    return author ? String(author) : "";
  }
  function renderVersionList() {
    if (!versionList) return;
    versionList.textContent = "";
    if (!versionEntries || versionEntries.length === 0) {
      const empty = document.createElement("p");
      empty.className = "version-empty";
      empty.textContent = t("VersionHistory.Empty");
      versionList.appendChild(empty);
      return;
    }
    versionEntries.forEach((v, idx) => {
      const item = document.createElement("div");
      item.className = "version-item" + (idx === 0 ? " current" : "");
      item.setAttribute("role", "listitem");
      const meta = document.createElement("span");
      meta.className = "version-meta";
      const when = formatVersionDate(v.ts);
      const who = formatVersionAuthor(v.author);
      const sizeLabel = v.size != null ? ` · ${v.size} B` : "";
      meta.textContent = when + (who ? ` · ${who}` : "") + (sizeLabel ? sizeLabel : "");
      item.appendChild(meta);
      if (idx === 0) {
        const badge = document.createElement("span");
        badge.className = "version-badge";
        badge.textContent = t("VersionHistory.Current");
        item.appendChild(badge);
      } else {
        const btns = document.createElement("span");
        btns.className = "version-actions";
        const cmp = document.createElement("button");
        cmp.type = "button";
        cmp.className = "version-compare";
        cmp.textContent = t("VersionHistory.Compare");
        cmp.addEventListener("click", () => compareToVersion(v.ts, cmp));
        btns.appendChild(cmp);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "version-restore";
        btn.textContent = t("VersionHistory.Restore");
        btn.addEventListener("click", () => restoreVersion(v.ts, btn));
        btns.appendChild(btn);
        item.appendChild(btns);
      }
      versionList.appendChild(item);
    });
  }
  async function openVersionHistory() {
    closeAllMenus();
    if (versionError) versionError.textContent = "";
    if (versionDialog) {
      rememberFocus();
      versionDialog.classList.add("open");
    }
    setStatus(t("Status.Loading"));
    try {
      const res = await fetch(api("versions"));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "versions failed");
      versionEntries = data.versions || [];
      renderVersionList();
      setStatus(t("Status.Ready"));
    } catch (err) {
      if (versionError) versionError.textContent = t("VersionHistory.ListError") + err.message;
      setStatus(t("VersionHistory.ListError") + err.message, true);
    }
  }
  function closeVersionHistory() {
    if (versionDialog) versionDialog.classList.remove("open");
    restoreFocus();
  }
  async function restoreVersion(ts, btn) {
    if (!btn) return;
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = t("VersionHistory.Restoring");
    try {
      const res = await fetch(api("versions/" + encodeURIComponent(ts) + "/restore"), { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "restore failed");
      closeVersionHistory();
      setStatus(t("VersionHistory.Restored"));
      await loadDocument();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = label;
      if (versionError) versionError.textContent = t("VersionHistory.RestoreError") + err.message;
      setStatus(t("VersionHistory.RestoreError") + err.message, true);
    }
  }
  const btnHistory = document.getElementById("btn-history");
  if (btnHistory) btnHistory.addEventListener("click", () => { closeAllMenus(); openVersionHistory(); });
  const btnVersionClose = document.getElementById("btn-version-close");
  if (btnVersionClose) btnVersionClose.addEventListener("click", closeVersionHistory);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && versionDialog && versionDialog.classList.contains("open")) {
      ev.preventDefault();
      closeVersionHistory();
    }
  });

  // --- AI review (op-stream diff + per-op reject) ---------------------
  // The agent edits through the same op pipeline as humans; its ops are
  // attributable (site "agent=<name>"), so the review pane lists exactly
  // those ops with their revisions. Reject emits the inverse op as the
  // "reviewer" client — a normal, attributable, undoable op itself. Roll
  // back to any prior revision stays in the version-history dialog.
  const aiReviewDialog = document.getElementById("ai-review-dialog");
  const aiReviewList = document.getElementById("ai-review-list");
  const aiReviewError = document.getElementById("ai-review-error");
  let aiReviewEntries = [];

  function closeAIReview() {
    if (aiReviewDialog) aiReviewDialog.classList.remove("open");
    restoreFocus();
  }

  function renderAIReview() {
    if (!aiReviewList) return;
    aiReviewList.textContent = "";
    if (!aiReviewEntries.length) {
      const empty = document.createElement("p");
      empty.className = "version-empty";
      empty.textContent = "No AI changes in this document.";
      aiReviewList.appendChild(empty);
      return;
    }
    aiReviewEntries.forEach((op) => {
      const item = document.createElement("div");
      item.className = "version-item ai-review-item";
      item.setAttribute("role", "listitem");
      const meta = document.createElement("span");
      meta.className = "version-meta";
      meta.textContent = `#${op.rev} · ${op.agent} · ${op.summary}`;
      item.appendChild(meta);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "version-restore ai-reject";
      btn.textContent = "Reject";
      btn.setAttribute("aria-label", `Reject AI change #${op.rev} (${op.summary})`);
      btn.addEventListener("click", () => rejectAIOp(op.rev, btn));
      item.appendChild(btn);
      aiReviewList.appendChild(item);
    });
  }

  async function rejectAIOp(rev, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(api("ai/review/reject"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revs: [rev] }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "reject failed");
      setStatus("AI change rejected");
      await openAIReview(); // re-render from the live op stream
      await loadDocument();
    } catch (err) {
      if (btn) btn.disabled = false;
      if (aiReviewError) aiReviewError.textContent = "Reject failed: " + err.message;
    }
  }

  async function rejectAllAIOps(btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(api("ai/review/reject"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ all: true }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "reject failed");
      setStatus("All AI changes rejected");
      await openAIReview();
      await loadDocument();
    } catch (err) {
      if (btn) btn.disabled = false;
      if (aiReviewError) aiReviewError.textContent = "Reject failed: " + err.message;
    }
  }

  async function openAIReview() {
    closeAllMenus();
    if (aiReviewError) aiReviewError.textContent = "";
    if (aiReviewDialog) {
      rememberFocus();
      aiReviewDialog.classList.add("open");
    }
    try {
      const res = await fetch(api("ai/review"));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "ai review failed");
      aiReviewEntries = data.ops || [];
      renderAIReview();
      setStatus(t("Status.Ready"));
    } catch (err) {
      if (aiReviewError) aiReviewError.textContent = "Could not load AI changes: " + err.message;
    }
  }

  const btnAIReview = document.getElementById("btn-ai-review");
  if (btnAIReview) btnAIReview.addEventListener("click", () => { closeAllMenus(); openAIReview(); });
  // AI tab surface reuses the File-menu AI review
  const btnAIReviewTab = document.getElementById("btn-ai-review-tab");
  if (btnAIReviewTab) btnAIReviewTab.addEventListener("click", openAIReview);
  // AI propose: instruction -> /ai/propose (server-side model registry;
  // the server itself never calls a vendor). Applied proposals arrive via
  // the collab poll as agent ops and are projected as tracked-change spans
  // (see pollCollab / applyTrackedDiff), so they surface in the existing
  // review-changes flow for per-change accept/reject.
  const aiProposeDialog = document.getElementById("ai-propose-dialog");
  const aiProposeInstruction = document.getElementById("ai-propose-instruction");
  const aiProposeError = document.getElementById("ai-propose-error");
  function openAiPropose(instruction) {
    closeAllMenus();
    if (aiProposeError) aiProposeError.textContent = "";
    // Selection-scoped proposals: the picked text rides along in the task
    // so the model edits that span instead of the whole document.
    let task = instruction || "";
    const sel = String(getSelection() || "").trim();
    if (sel) task += "\n\nSelected text:\n" + sel.slice(0, 500);
    if (aiProposeInstruction) aiProposeInstruction.value = task;
    if (aiProposeDialog) {
      rememberFocus();
      aiProposeDialog.classList.add("open");
    }
    if (aiProposeInstruction) aiProposeInstruction.focus();
  }

  // ------------------------------------------------------------------
  // Page setup (F-090 size / F-091 orientation / F-092 margins)
  // ------------------------------------------------------------------
  // The document's page geometry lives in the .page-setup marker div at
  // body start (converter contract: DOCX w:sectPr / ODT page layout).
  // Applying it also maps to the live canvas via CSS vars on <html>; the
  // style.css fallbacks equal the previous fixed A4 look, so documents
  // without a marker render exactly as before.
  const PS_TWIPS_PER_PX = 15;  // 1440 twips/inch at 96 dpi
  const PS_SIZES = { A4: [11906, 16838], Letter: [12240, 15840], Legal: [12240, 20160] };

  function psMarker() {
    return editor.querySelector(":scope > div.page-setup");
  }

  function applyPageView() {
    const m = psMarker();
    const rootStyle = document.documentElement.style;
    if (!m) {
      ["--wo-page-w", "--wo-pad-top", "--wo-pad-bottom", "--wo-pad-x"].forEach(
        (p) => rootStyle.removeProperty(p));
      return;
    }
    const px = (name) => (parseInt(m.getAttribute(name), 10) || 0) / PS_TWIPS_PER_PX;
    rootStyle.setProperty("--wo-page-w", px("data-page-w").toFixed(1) + "px");
    rootStyle.setProperty("--wo-page-h", px("data-page-h").toFixed(1) + "px");
    rootStyle.setProperty("--wo-pad-top", px("data-margin-top").toFixed(1) + "px");
    rootStyle.setProperty("--wo-pad-bottom", px("data-margin-bottom").toFixed(1) + "px");
    rootStyle.setProperty("--wo-pad-x",
      Math.max(px("data-margin-left"), px("data-margin-right")).toFixed(1) + "px");
  }

  // --- pagination (LO/OO/Word page model) ------------------------------
  // The canonical document flow stays flat for converters, undo/redo and
  // save (see flatHtml). paginateView wraps the flow into stacked fixed A4
  // sheets for display, honours explicit .page-break markers and heading
  // keep-with-next. Idempotent: it first unwraps any existing page layer.
  function mergeSplits(root) {
    // fold visual split continuations (.wo-cont) back into the element they
    // came from — pagination start and every serialization funnel call this
    // so the canonical flow never sees view-layer fragments. Tables merge
    // their rows into the previous table's last tbody (the continuation's
    // cloned thead/colgroup are display-only and get dropped with it).
    root.querySelectorAll(".wo-cont").forEach((c) => {
      const prev = c.previousElementSibling;
      if (prev && prev.tagName === c.tagName) {
        if (c.tagName === "TABLE" && prev.tBodies) {
          // a leading .wo-row-cont tr is the tail of a row split mid-row —
          // fold its cells' children into the previous table's last row so
          // the canonical flow keeps ONE row
          const ctb0 = c.tBodies[0];
          const ftr = ctb0 && ctb0.firstElementChild;
          if (ftr && ftr.classList.contains("wo-row-cont")) {
            const ptb = prev.tBodies.length
              ? prev.tBodies[prev.tBodies.length - 1]
              : prev.appendChild(document.createElement("tbody"));
            const prow = ptb.lastElementChild;
            if (prow && prow.tagName === "TR") {
              Array.from(ftr.children).forEach((fc, i) => {
                const pc = prow.children[i];
                if (pc) {
                  while (fc.firstChild) pc.appendChild(fc.firstChild);
                } else {
                  prow.appendChild(fc); // extra tail cells: append whole
                }
              });
              ftr.remove();
            } else {
              ftr.classList.remove("wo-row-cont"); // orphaned: keep its content
            }
          }
          const tgt = prev.tBodies.length
            ? prev.tBodies[prev.tBodies.length - 1]
            : prev.appendChild(document.createElement("tbody"));
          Array.from(c.tBodies).forEach((tb) => {
            while (tb.firstChild) tgt.appendChild(tb.firstChild);
          });
        } else {
          // a leading .wo-li-cont is the tail of an item split mid-text —
          // fold its children into the previous list's last LI so the
          // canonical flow keeps ONE item
          if (c.firstElementChild && c.firstElementChild.classList.contains("wo-li-cont")) {
            const lc = c.firstElementChild;
            const lastLi = prev.lastElementChild && prev.lastElementChild.tagName === "LI"
              ? prev.lastElementChild : null;
            if (lastLi) {
              while (lc.firstChild) lastLi.appendChild(lc.firstChild);
              lc.remove();
            } else {
              lc.classList.remove("wo-li-cont"); // orphaned: keep as its own item
            }
          }
          while (c.firstChild) prev.appendChild(c.firstChild);
        }
        c.remove();
      } else {
        c.classList.remove("wo-cont"); // orphaned fragment: keep its content
      }
    });
  }

  function trySplitList(blk, avail, zoom) {
    // Split OL/UL at an item boundary: items whose bottom fits in `avail`
    // stay; the tail moves to a continuation list. OL numbering continues
    // via the start attribute. A single item taller than the remaining
    // space falls back to a mid-item line split (Word splits the item's
    // paragraph across the page break; the tail LI carries wo-li-cont so
    // mergeSplits folds it back into its head LI).
    const items = Array.from(blk.children).filter((el) => el.tagName === "LI");
    if (!items.length) return null;
    // rect-relative measurement: li/offsetTop frames vary (offsetParent is
    // the page, the list, or the table depending on styling), rects don't
    const baseTop = blk.getBoundingClientRect().top;
    const limit = avail;
    let fit = 0;
    for (const it of items) {
      if ((it.getBoundingClientRect().bottom - baseTop) / zoom <= limit + 0.5) fit++;
      else break;
    }
    const firstOverflow = items[fit];
    if (fit >= 1 && items.length - fit >= 1) {
      const cont = blk.cloneNode(false);
      cont.classList.add("wo-cont");
      if (blk.tagName === "OL") {
        cont.setAttribute("start",
          (parseInt(blk.getAttribute("start"), 10) || 1) + fit);
      }
      for (let i = fit; i < items.length; i++) cont.appendChild(items[i]);
      return cont;
    }
    // line-granular fallback: the next overflowing item is itself taller
    // than what remains of the sheet — split that LI like a paragraph
    if (!firstOverflow) return null;
    const itemTop = (firstOverflow.getBoundingClientRect().top - baseTop) / zoom;
    const liCont = trySplitLines(firstOverflow, avail - itemTop, zoom);
    if (!liCont) return null;
    liCont.classList.add("wo-li-cont");
    const cont = blk.cloneNode(false);
    cont.classList.add("wo-cont");
    if (blk.tagName === "OL") {
      // the tail LI continues the SAME item: number it like its head
      cont.setAttribute("start",
        (parseInt(blk.getAttribute("start"), 10) || 1) + fit);
    }
    cont.appendChild(liCont);
    for (let i = fit + 1; i < items.length; i++) cont.appendChild(items[i]);
    return cont;
  }

  function splitCellAt(cell, avail, baseTop, zoom) {
    // Move a table cell's content whose lines start below `avail` px (from
    // baseTop, the ROW's top) into a cloned cell. A paragraph spanning the
    // break splits mid-text via trySplitLines; unsplittable children stay
    // whole in whichever side holds their first line.
    const cont = cell.cloneNode(false);
    let moved = false;
    Array.from(cell.children).forEach((ch) => {
      const r = ch.getBoundingClientRect ? ch.getBoundingClientRect() : null;
      if (!r) return;
      const top = (r.top - baseTop) / zoom;
      const bottom = (r.bottom - baseTop) / zoom;
      if (bottom <= avail + 0.5) return;                      // fully above: keep
      if (top >= avail - 0.5) { cont.appendChild(ch); moved = true; return; }
      // spans the break: split it at the line boundary like a paragraph
      const tail = trySplitLines(ch, avail - top, zoom);
      if (tail) { cont.appendChild(tail); moved = true; }
    });
    return moved ? cont : null;
  }

  function trySplitRow(row, avail, zoom) {
    // Split a tr at a horizontal break line (Word's "allow row to break
    // across pages"): every cell keeps what fits, the tails land in a
    // continuation tr with cells aligned by index. Returns the tail tr or
    // null when no cell can honestly split.
    // normalize bare-text cells first (td > p is the converter's canonical
    // shape) so children exist to measure and move
    Array.from(row.children).forEach((cell) => {
      if (!cell.children.length && cell.textContent.trim()) {
        const wrap = document.createElement("p");
        while (cell.firstChild) wrap.appendChild(cell.firstChild);
        cell.appendChild(wrap);
      }
    });
    const baseTop = row.getBoundingClientRect().top;
    // refuse when no cell content starts above the break (avail ≤ 0, or a
    // head row that would be empty): the layout loop then moves the whole
    // row to the next sheet instead — and an everything-moves "split"
    // would re-queue an identical block forever
    const startsAbove = Array.from(row.children).some((cell) =>
      Array.from(cell.children).some((ch) => {
        const r = ch.getBoundingClientRect ? ch.getBoundingClientRect() : null;
        return r && (r.top - baseTop) / zoom < avail - 0.5;
      }));
    if (!startsAbove) return null;
    let any = false;
    const tails = Array.from(row.children).map((cell) => {
      const t = splitCellAt(cell, avail, baseTop, zoom);
      if (t) any = true;
      return t;
    });
    if (!any) return null;
    const trCont = row.cloneNode(false);
    trCont.classList.add("wo-row-cont");
    Array.from(row.children).forEach((cell, i) =>
      trCont.appendChild(tails[i] || document.createElement(cell.tagName)));
    return trCont;
  }

  function trySplitTable(blk, avail, zoom) {
    // Split TABLE at a row boundary: fitting tbody rows stay; the tail
    // moves to a continuation table that repeats the thead (Word keeps
    // column headers across the page break).
    const rows = [];
    Array.from(blk.tBodies).forEach((tb) =>
      Array.from(tb.children).forEach((tr) => rows.push(tr)));
    if (!rows.length) return null;
    // tr.offsetTop is TABLE-relative while the sheet math is page-relative —
    // measure bottoms against the table's own top instead (frame-safe)
    const baseTop = blk.getBoundingClientRect().top;
    const limit = avail;
    let fit = 0;
    for (const tr of rows) {
      if ((tr.getBoundingClientRect().bottom - baseTop) / zoom <= limit + 0.5) fit++;
      else break;
    }
    if (fit >= 1 && rows.length - fit >= 1) {
      const cont = blk.cloneNode(false);
      cont.classList.add("wo-cont");
      const colg = blk.querySelector("colgroup");
      if (colg) cont.appendChild(colg.cloneNode(true));
      if (blk.tHead) cont.appendChild(blk.tHead.cloneNode(true));
      const tb = document.createElement("tbody");
      for (let i = fit; i < rows.length; i++) tb.appendChild(rows[i]);
      cont.appendChild(tb);
      return cont;
    }
    // mid-row fallback: the next overflowing row is itself taller than
    // what remains of the sheet — split it across the break like Word
    const firstOverflow = rows[fit];
    if (!firstOverflow) return null;
    const trCont = trySplitRow(firstOverflow, avail, zoom);
    if (!trCont) return null;
    const cont = blk.cloneNode(false);
    cont.classList.add("wo-cont");
    const colg = blk.querySelector("colgroup");
    if (colg) cont.appendChild(colg.cloneNode(true));
    if (blk.tHead) cont.appendChild(blk.tHead.cloneNode(true));
    const tb = document.createElement("tbody");
    tb.appendChild(trCont);
    for (let i = fit + 1; i < rows.length; i++) tb.appendChild(rows[i]);
    cont.appendChild(tb);
    return cont;
  }

  function trySplitBlock(blk, avail, zoom) {
    // Split blk at its last child line that fits in `avail` px of sheet
    // space; returns the continuation element holding the tail, or null
    // when the block is too short to split honestly. Lists split at item
    // and tables at row boundaries; paragraphs at line boundaries with
    // widow/orphan control (keep at least 2 lines on each side). Purely
    // visual: callers merge the fragments back before serializing
    // (mergeSplits).
    const tag = blk.tagName;
    if (tag === "OL" || tag === "UL") return trySplitList(blk, avail, zoom);
    if (tag === "TABLE") return trySplitTable(blk, avail, zoom);
    if (!/^(P|H[1-6]|BLOCKQUOTE)$/.test(tag)) return null;
    return trySplitLines(blk, avail, zoom);
  }

  function trySplitLines(blk, avail, zoom) {
    // Split blk at its last child line that fits in `avail` px (widow/
    // orphan control keeps at least 2 lines per side). Mutates blk: the
    // tail moves into the returned continuation element.
    const cs = getComputedStyle(blk);
    const extras = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.borderTopWidth) || 0)
      + (parseFloat(cs.paddingBottom) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
    const texts = [];
    const tw = document.createTreeWalker(blk, NodeFilter.SHOW_TEXT);
    let tn, total = 0;
    while ((tn = tw.nextNode())) { texts.push({ node: tn, start: total }); total += tn.data.length; }
    if (total < 2) return null;
    // line boxes of the whole block (viewport px, zoom-scaled)
    const r = document.createRange();
    r.selectNodeContents(blk);
    const lineMap = [];
    Array.from(r.getClientRects()).forEach((rc) => {
      const top = rc.top / zoom, bottom = rc.bottom / zoom;
      const hit = lineMap.find((l) => Math.abs(l.top - top) < 1.5);
      if (hit) { if (bottom > hit.bottom) hit.bottom = bottom; }
      else lineMap.push({ top, bottom });
    });
    if (lineMap.length < 2) return null;
    lineMap.sort((a, b) => a.top - b.top);
    const origin = lineMap[0].top;
    let fit = 0;
    while (fit < lineMap.length && lineMap[fit].bottom - origin + extras <= avail) fit++;
    if (fit < 2) return null;                       // orphan control
    if (lineMap.length - fit < 2) { fit -= 1; if (fit < 2) return null; } // widow control
    // global char offset of the first character on line `fit`
    const at = (off) => {
      let seg = texts[0];
      for (let i = 0; i < texts.length; i++) { if (texts[i].start <= off) seg = texts[i]; }
      return { node: seg.node, off: off - seg.start };
    };
    const lineOf = (off) => {
      const p = at(off);
      r.setStart(p.node, p.off);
      r.setEnd(p.node, Math.min(p.off + 1, p.node.data.length));
      const rc = r.getClientRects()[0];
      if (!rc) return -1;
      const t = rc.top / zoom;
      for (let i = 0; i < lineMap.length; i++) if (Math.abs(lineMap[i].top - t) < 1.5) return i;
      return -1;
    };
    let lo = 0, hi = total - 1, splitAt = -1; // first char on line `fit`
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (lineOf(mid) >= fit) { splitAt = mid; hi = mid - 1; } else lo = mid + 1;
    }
    if (splitAt <= 0) return null;
    const sp = at(splitAt);
    r.setStart(sp.node, sp.off);
    r.setEndAfter(blk.lastChild);
    const frag = r.extractContents();
    if (!frag.textContent.trim() && !frag.querySelector("img")) {
      blk.appendChild(frag); // nothing to move — restore
      return null;
    }
    const cont = blk.cloneNode(false);
    cont.classList.add("wo-cont");
    cont.appendChild(frag);
    return cont;
  }

  function paginateView() {
    if (!editor || !document.body.contains(editor)) return;
    // unwrap a previous page layer back to the flat flow: drop display-only
    // header/footer clones, fold split continuations (.wo-cont) back, and
    // restore header/footer to their canonical authoring positions
    editor.querySelectorAll(".wo-hf-clone").forEach((el) => el.remove());
    editor.querySelectorAll(":scope > .wo-page").forEach((pg) => {
      while (pg.firstChild) editor.insertBefore(pg.firstChild, pg);
      pg.remove();
    });
    mergeSplits(editor);
    const hdrSrc = editor.querySelector(":scope > header.page-header");
    const ftrSrc = editor.querySelector(":scope > footer.page-footer");
    if (hdrSrc) {
      const firstBlock = Array.from(editor.children).find((el) =>
        el.tagName !== "STYLE" && !el.matches(
          "div.page-setup, div.hyphenation, div.line-numbers," +
          " header.page-header, footer.page-footer"));
      if (firstBlock) editor.insertBefore(hdrSrc, firstBlock);
    }
    if (ftrSrc) editor.appendChild(ftrSrc);
    const keep = new Set();
    editor.querySelectorAll(
      ":scope > div.page-setup, :scope > div.hyphenation, :scope > div.line-numbers, " +
      ":scope > header.page-header, :scope > footer.page-footer"
    ).forEach((el) => keep.add(el));
    const blocks = Array.from(editor.children).filter(
      (el) => !keep.has(el) && el.tagName !== "STYLE");
    if (blocks.length === 0) return;

    const gs = document.documentElement.style;
    const pageW = parseFloat(gs.getPropertyValue("--wo-page-w")) || 794;
    const pageH = parseFloat(gs.getPropertyValue("--wo-page-h")) ||
      Math.round(pageW * 16838 / 11906);
    const padT = parseFloat(gs.getPropertyValue("--wo-pad-top")) || 96;
    const padB = parseFloat(gs.getPropertyValue("--wo-pad-bottom")) || 96;
    const maxH = pageH - padT - padB;
    const zoom = parseFloat(editor.style.zoom) || 1; // offsetHeight is zoom-scaled

    let cur = null;
    const openPage = () => {
      cur = document.createElement("div");
      cur.className = "wo-page";
      editor.appendChild(cur);
    };
    openPage();
    let used = 0;
    let prevBottom = 0; // previous block's bottom edge (page-relative)
    const q = blocks.slice();
    while (q.length) {
      const blk = q.shift();
      if (blk.classList.contains("page-break")) {
        // explicit break: marker stays as the last child of this page so
        // flatHtml's unwrap puts it back in the flow for DOCX conversion
        cur.appendChild(blk);
        used = 0; prevBottom = 0;
        openPage();
        continue;
      }
      cur.appendChild(blk);
      const bottom = blk.offsetTop + blk.offsetHeight; // includes page padding offset
      // advance vs the previous block captures margins, unlike offsetHeight alone
      const adv = (cur.children.length === 1) ? blk.offsetHeight : (bottom - prevBottom) / zoom;
      if (used + adv > maxH) {
        const avail = (cur.children.length === 1) ? maxH : maxH - used;
        const cont = trySplitBlock(blk, avail, zoom);
        if (cont) {
          // sheet filled up to the split; the tail re-enters the queue and
          // may itself split again on the next sheet (mega-paragraphs)
          q.unshift(cont);
          used = maxH;
          prevBottom = blk.offsetTop + blk.offsetHeight;
          continue;
        }
        if (cur.children.length > 1) {
          cur.removeChild(blk);
          used = 0; prevBottom = 0;
          openPage();
          q.unshift(blk);
          continue;
        }
        // alone on a fresh sheet and unsplittable: tolerate the overflow
      }
      used += adv;
      prevBottom = bottom;
    }
    // heading keep-with-next: a heading stranded at a sheet bottom moves with
    // the paragraph it governs to the next sheet (Word/LO behaviour)
    const pages = Array.from(editor.querySelectorAll(":scope > .wo-page"));
    for (let i = 0; i < pages.length - 1; i++) {
      const hd = pages[i].lastElementChild;
      if (hd && /^H[1-3]$/.test(hd.tagName) && pages[i].children.length > 1) {
        pages[i + 1].insertBefore(hd, pages[i + 1].firstChild);
      }
    }
    // Word page furniture: the header repeats on EVERY sheet, so does the
    // footer. The authoring originals live in the first/last sheet's margin
    // (single editing point); the other sheets get display-only clones that
    // unwrap/serialization strip (.wo-hf-clone).
    if (hdrSrc || ftrSrc) {
      pages.forEach((pg, i) => {
        if (hdrSrc) {
          if (i === 0) pg.insertBefore(hdrSrc, pg.firstChild);
          else {
            const hc = hdrSrc.cloneNode(true);
            hc.classList.add("wo-hf-clone");
            hc.setAttribute("aria-hidden", "true");
            pg.insertBefore(hc, pg.firstChild);
          }
        }
        if (ftrSrc) {
          if (i === pages.length - 1) pg.appendChild(ftrSrc);
          else {
            const fc = ftrSrc.cloneNode(true);
            fc.classList.add("wo-hf-clone");
            fc.setAttribute("aria-hidden", "true");
            pg.appendChild(fc);
          }
        }
      });
    }
  }

  function flatHtml() {
    // Serialize the flat flow (page layer unwrapped, splits merged) so
    // converters, undo/redo and the save payload never see the display-only
    // page layer.
    const tmp = editor.cloneNode(true);
    tmp.querySelectorAll(".wo-hf-clone").forEach((el) => el.remove());
    tmp.querySelectorAll(".wo-page").forEach((pg) => {
      while (pg.firstChild) pg.parentNode.insertBefore(pg.firstChild, pg);
      pg.remove();
    });
    mergeSplits(tmp);
    return tmp.innerHTML;
  }
  // Export hook for the bridge (fresh-content export); additive, no behavior change.
  window.__WO_FLAT_HTML__ = flatHtml;

  // --- reflow on mutation ------------------------------------------------
  // Re-paginate after editing/typing pauses so growing content re-snaps to
  // the sheet grid (bleed below a sheet bottom is moved to the next sheet).
  // Debounced so a typing burst doesn't churn the DOM; selection survives
  // because paginateView MOVES block nodes (insertBefore) — they keep their
  // identity, so a saved range is still valid after the rewrap. Paragraphs
  // split at line boundaries across sheets (trySplitBlock) and the fragments
  // merge back (mergeSplits) before any serialization.
  let reflowQuiet = false;
  let reflowTimer = null;
  function saveSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const r = sel.getRangeAt(0);
    return { a: r.startContainer, ao: r.startOffset, f: r.endContainer, fo: r.endOffset };
  }
  function restoreSelection(s) {
    if (!s || !s.a || !s.f) return;
    try {
      const doc = s.a.ownerDocument;
      if (!doc.contains(s.a) || !doc.contains(s.f)) return; // node moved out
      const maxOff = (n) => n.nodeType === 3 ? n.length : n.childNodes.length;
      const r = doc.createRange();
      r.setStart(s.a, Math.min(s.ao, maxOff(s.a)));
      r.setEnd(s.f, Math.min(s.fo, maxOff(s.f)));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
    } catch (e) { /* best-effort selection restore */ }
  }
  function paginateQuiet() {
    // quiet window: the observer skips mutations this pagination itself causes
    reflowQuiet = true;
    paginateView();
    setTimeout(() => { reflowQuiet = false; }, 0); // macrotask: after observer microtasks
  }
  function scheduleReflow() {
    if (reflowTimer) return; // a reflow is already pending or just ran
    reflowTimer = setTimeout(() => {
      reflowTimer = null;
      if (!editor || editor.innerHTML.trim() === "") return;
      const saved = saveSelection();
      const sc = window.scrollY;
      paginateQuiet();
      restoreSelection(saved);
      window.scrollTo(0, sc);
    }, 600);
  }
  if (window.MutationObserver) {
    new MutationObserver(() => {
      if (reflowQuiet) return;
      scheduleReflow();
    }).observe(editor, { childList: true, subtree: true, characterData: true });
  }

  function writePageSetupMarker(w, h, orient, mt, mb, ml, mr) {
    let m = psMarker();
    if (!m) {
      m = document.createElement("div");
      m.className = "page-setup";
      editor.insertBefore(m, editor.firstChild);
    }
    const attrs = { "data-page-w": w, "data-page-h": h, "data-orient": orient,
                    "data-margin-top": mt, "data-margin-bottom": mb,
                    "data-margin-left": ml, "data-margin-right": mr };
    Object.keys(attrs).forEach((k) => m.setAttribute(k, String(attrs[k])));
    applyPageView();
    markDirty();
    captureHistory();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  const pageSetupDialog = document.getElementById("page-setup-dialog");
  const psSize = document.getElementById("ps-size");
  if (pageSetupDialog) {
    document.getElementById("btn-page-setup").addEventListener("click", () => {
      const m = psMarker();
      const g = (name, dflt) => (m ? parseInt(m.getAttribute(name), 10) || dflt : dflt);
      const w = g("data-page-w", 11906), h = g("data-page-h", 16838);
      const known = Object.keys(PS_SIZES).find(
        (k) => PS_SIZES[k][0] === w && PS_SIZES[k][1] === h);
      psSize.value = known || "custom";
      const orient = m ? (m.getAttribute("data-orient") || "portrait") : "portrait";
      const radio = pageSetupDialog.querySelector('input[name="ps-orient"][value="' + orient + '"]');
      if (radio) radio.checked = true;
      const inches = (name) => (g(name, 1440) / 1440).toFixed(1);
      document.getElementById("ps-mt").value = inches("data-margin-top");
      document.getElementById("ps-mb").value = inches("data-margin-bottom");
      document.getElementById("ps-ml").value = inches("data-margin-left");
      document.getElementById("ps-mr").value = inches("data-margin-right");
      rememberFocus();
      pageSetupDialog.classList.add("open");
    });
    psSize.addEventListener("change", () => {
      if (psSize.value !== "custom") {
        const [w, h] = PS_SIZES[psSize.value];
        pageSetupDialog.dataset.w = w;
        pageSetupDialog.dataset.h = h;
      }
    });
    document.getElementById("btn-ps-cancel").addEventListener("click", () => {
      pageSetupDialog.classList.remove("open");
      restoreFocus();
    });
    document.getElementById("btn-ps-apply").addEventListener("click", () => {
      let w, h;
      if (psSize.value === "custom") {
        w = parseInt(pageSetupDialog.dataset.w, 10) || 11906;
        h = parseInt(pageSetupDialog.dataset.h, 10) || 16838;
      } else {
        [w, h] = psSize.value.split("x").map(Number);
      }
      const orient = (pageSetupDialog.querySelector('input[name="ps-orient"]:checked') || {}).value || "portrait";
      if (orient === "landscape" && w <= h) { const t = w; w = h; h = t; }
      if (orient === "portrait" && w > h) { const t = w; w = h; h = t; }
      const inches = (id) => Math.round((parseFloat(document.getElementById(id).value) || 1) * 1440);
      writePageSetupMarker(w, h, orient,
        inches("ps-mt"), inches("ps-mb"), inches("ps-ml"), inches("ps-mr"));
      pageSetupDialog.classList.remove("open");
      restoreFocus();
    });
  }
  async function runAiPropose() {
    if (!aiProposeDialog) return;
    const instruction = (aiProposeInstruction && aiProposeInstruction.value || "").trim();
    if (!instruction) return;
    if (aiProposeError) aiProposeError.textContent = "";
    try {
      const res = await fetch(api("ai/propose"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: instruction, model: "default" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "propose failed");
      aiProposeDialog.classList.remove("open");
      setStatus(t("AI.Propose.Applied"));
    } catch (err) {
      if (aiProposeError) aiProposeError.textContent = String(err.message || err);
    }
  }
  const btnAiGrammar = document.getElementById("btn-ai-grammar");
  if (btnAiGrammar) btnAiGrammar.addEventListener("click", () => openAiPropose("Fix grammar and spelling in the document."));
  const btnAiAssistant = document.getElementById("btn-ai-assistant");
  if (btnAiAssistant) btnAiAssistant.addEventListener("click", () => openAiPropose("Improve the writing style of the document."));
  const btnAiProposeRun = document.getElementById("btn-ai-propose-run");
  if (btnAiProposeRun) btnAiProposeRun.addEventListener("click", runAiPropose);
  const btnAiProposeCancel = document.getElementById("btn-ai-propose-cancel");
  if (btnAiProposeCancel) btnAiProposeCancel.addEventListener("click", () => { if (aiProposeDialog) aiProposeDialog.classList.remove("open"); restoreFocus(); });
  // View tab: reuse the statusbar/home controls (single source of truth)
  const viewFullscreen = document.getElementById("btn-view-fullscreen");
  if (viewFullscreen) viewFullscreen.addEventListener("click", () => document.getElementById("btn-fullscreen")?.click());
  const viewTheme = document.getElementById("btn-view-theme");
  if (viewTheme) viewTheme.addEventListener("click", () => document.getElementById("btn-theme")?.click());
  const viewFit = document.getElementById("btn-view-fit");
  if (viewFit) viewFit.addEventListener("click", () => document.getElementById("btn-zoom-fit")?.click());
  // View tab ruler toggle: hide/show the horizontal ruler
  const rulerToggle = document.getElementById("btn-ruler-toggle");
  if (rulerToggle) rulerToggle.addEventListener("click", () => {
    const ruler = document.querySelector(".ruler");
    if (!ruler) return;
    const hidden = ruler.style.display === "none";
    ruler.style.display = hidden ? "" : "none";
    rulerToggle.setAttribute("aria-pressed", String(hidden));
  });
  // View tab: page gridlines are a view-only overlay (like the ruler);
  // nothing enters the document, so no converter contract is involved.
  function toggleGridlines() {
    const on = editor.classList.toggle("show-gridlines");
    const btn = document.getElementById("btn-gridlines");
    if (btn) btn.setAttribute("aria-pressed", String(on));
    setStatus(on ? "Gridlines on" : "Gridlines off");
  }
  const gridlinesBtn = document.getElementById("btn-gridlines");
  if (gridlinesBtn) gridlinesBtn.addEventListener("click", toggleGridlines);

  // View tab: formatting marks are another view-only overlay (like gridlines);
  // the ¶ glyphs come from CSS on #editor.show-marks, nothing enters the doc.
  const fmtMarksBtn = document.getElementById("btn-view-formatmarks");
  if (fmtMarksBtn) fmtMarksBtn.addEventListener("click", () => {
    const on = editor.classList.toggle("show-marks");
    fmtMarksBtn.setAttribute("aria-pressed", String(on));
    setStatus(on ? "Formatting marks on" : "Formatting marks off");
  });

  // Layout: page color (native picker -> --paper) and theme color schemes
  // (OO Layout>Colors: named palettes swapping the paper/ink vars). These are
  // document-surface settings, scoped to #editor so the golden chrome stays put.
  const pageColor = document.getElementById("pagecolor");
  if (pageColor) pageColor.addEventListener("change", () => {
    if (READ_ONLY) return;
    editor.style.setProperty("--paper", pageColor.value);
  });
  // OO-parity: Page Color opens a palette dialog (in-dialog swatches + custom
  // color) instead of the bare native picker; the preview input stays for the
  // dialog's custom-color row.
  if (pageColor) pageColor.addEventListener("click", (ev) => {
    ev.preventDefault();
    openPageColorDialog();
  });
  const COLOR_SCHEMES = {
    default: {},
    gold: { "--paper": "#fdf8ec", "--ink": "#352a17" },
    grayscale: { "--paper": "#ffffff", "--ink": "#1a1a1a" },
    "blue-warm": { "--paper": "#f6faff", "--ink": "#1b2b3a" },
    "green-yellow": { "--paper": "#feffea", "--ink": "#2a3314" },
  };
  const themeColorsSel = document.getElementById("themecolors");
  if (themeColorsSel) themeColorsSel.addEventListener("change", () => {
    if (READ_ONLY) return;
    const vars = COLOR_SCHEMES[themeColorsSel.value] || {};
    for (const [k, v] of Object.entries(vars)) editor.style.setProperty(k, v);
  });
  // OO-parity: Colors opens a scheme dialog (palette modal) instead of the
  // bare native select.
  if (themeColorsSel) themeColorsSel.addEventListener("click", (ev) => {
    ev.preventDefault();
    openColorsDialog();
  });

  // View tab: navigation sidebar lists the document outline; clicking a
  // heading scrolls to it and flashes it. View-only UI, rebuilt on open.
  const navPanel = document.getElementById("nav-panel");
  const navList = navPanel && navPanel.querySelector(".nav-panel-list");
  function buildNavigation() {
    if (!navList) return;
    navList.textContent = "";
    const frag = document.createDocumentFragment();
    editor.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((h) => {
      const a = document.createElement("a");
      a.textContent = (h.textContent || "").trim() || "(untitled heading)";
      a.className = "nav-h" + h.tagName[1];
      a.href = "#";
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        h.scrollIntoView({ behavior: "smooth", block: "start" });
        h.classList.remove("nav-flash");
        void h.offsetWidth;  // restart the flash animation
        h.classList.add("nav-flash");
        setTimeout(() => h.classList.remove("nav-flash"), 1600);
      });
      const li = document.createElement("li");
      li.appendChild(a);
      frag.appendChild(li);
    });
    navList.appendChild(frag);
  }
  function toggleNavigation() {
    if (!navPanel) return;
    const btn = document.getElementById("btn-nav-toggle");
    const opening = navPanel.hidden;
    if (opening) {
      buildNavigation();
      if (!navList.childElementCount) {
        const li = document.createElement("li");
        li.className = "nav-empty";
        li.textContent = "No headings in this document";
        navList.appendChild(li);
      }
    }
    navPanel.hidden = !opening;
    if (btn) btn.setAttribute("aria-expanded", String(opening));
    setStatus(opening ? "Navigation open" : "Navigation closed");
  }

  // Collaboration chat panel (broadcasting via the collab ops channel).
  const chatPanel = document.getElementById("chat-panel");
  const chatList = chatPanel && chatPanel.querySelector(".chat-panel-list");
  function toggleChat() {
    if (!chatPanel) return;
    const btn = document.getElementById("btn-chat-toggle");
    const opening = chatPanel.hidden;
    if (opening && chatList && !chatList.childElementCount) {
      const li = document.createElement("li");
      li.className = "chat-empty";
      li.textContent = t("ChatPanel.Empty") || "No messages yet";
      chatList.appendChild(li);
    }
    chatPanel.hidden = !opening;
    if (btn) btn.setAttribute("aria-expanded", String(opening));
    setStatus(opening ? t("Status.ChatOpen") || "Chat open" : t("Status.ChatClosed") || "Chat closed");
  }

  // Multilevel list: nest the current list item under its previous
  // sibling, growing a real <li><ol> subtree (the canonical shape the
  // converters round-trip; Chromium's execCommand("indent") can emit a
  // content-dropping <ol><ol>, so we build the nesting explicitly).
  function multilevelItem() {
    editor.focus();
    const sel = window.getSelection();
    const sc = sel && sel.rangeCount ? sel.getRangeAt(0).startContainer : null;
    const li = sc
      ? (sc.nodeType === 1 ? (sc.closest ? sc.closest("li") : null)
         : (sc.parentElement && sc.parentElement.closest("li")))
      : null;
    if (!li) {
      try { document.execCommand("insertOrderedList"); } catch (err) {}
      markDirty(); captureHistory(); scheduleCollabSync(); notifyHost("editing");
      updateActiveStates(); updateUndoRedoState();
      return;
    }
    const list = li.parentNode;
    if (!list || !/^[ou]l$/i.test(list.tagName)) return;
    const prev = li.previousElementSibling && /^li$/i.test(li.previousElementSibling.tagName)
      ? li.previousElementSibling : null;
    if (!prev) return;  // topmost item: no previous sibling to nest under
    let inner = Array.prototype.find.call(prev.children, (el) => /^[ou]l$/i.test(el.tagName));
    if (!inner) {
      inner = document.createElement(list.tagName);
      prev.appendChild(inner);
    }
    inner.appendChild(li);
    if (list.children.length === 0) list.remove();
    markDirty(); captureHistory(); scheduleCollabSync(); notifyHost("editing");
    updateActiveStates(); updateUndoRedoState();
  }

  // Drop cap: wrap the paragraph's first character in <span class="dropcap">

  // --- promoted section markers / TOC / display / caption / compare ------
  // Section flag markers (hyphenation / line numbers / watermark) ride at
  // body start right after any page-setup marker, in the fixed order the
  // converters strip them in (marker absence = feature off).
  function sectionMarkerEl(klass) {
    return Array.from(editor.querySelectorAll(":scope > div"))
      .find((el) => el.className === klass);
  }
  // Canonical body-start order — MUST match the converter strip order
  // (html_to_docx: hyphenation, line-numbers, different-first, odd-even,
  // header-from-top, footer-from-bottom, watermark) and the reader's
  // emission order, or markers are lost on save.
  const SECTION_MARKER_ORDER = ["page-setup", "hyphenation", "line-numbers",
    "different-first", "odd-even", "header-from-top", "footer-from-bottom", "watermark"];
  function toggleSectionMarker(klass, attrs) {
    let m = sectionMarkerEl(klass);
    if (m) {
      m.remove();
    } else {
      m = document.createElement("div");
      m.className = klass;
      attrs.split(/\s+/).forEach((kv) => {
        const [k, v] = kv.split("=");
        if (k && v) m.setAttribute(k, v.replace(/"/g, ""));
      });
      // Keep the body-start marker block in the canonical order the
      // converters strip in (page-setup, hyphenation, line-numbers,
      // watermark) — insert after the last marker already present.
      let ref = editor.firstChild;
      SECTION_MARKER_ORDER.forEach((k) => {
        const el = sectionMarkerEl(k);
        if (el) ref = el.nextSibling;
      });
      editor.insertBefore(m, ref);
    }
    markDirty();
    captureHistory();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  // TOC live preview: fill every <nav class="toc"> with links to the
  // current h1..h6 (the converters strip the children on save — the nav is
  // a marker, data-title is what persists).
  function refreshToc() {
    const headings = editor.querySelectorAll("h1, h2, h3, h4, h5, h6");
    const captions = Array.from(editor.querySelectorAll("figcaption")).map((c) => c.textContent);
    let n = 0;
    editor.querySelectorAll("nav.toc").forEach((nav) => {
      nav.textContent = "";
      const isFigs = nav.getAttribute("data-kind") === "figures";
      const src = isFigs ? captions : Array.from(headings);
      src.forEach((entry) => {
        n += 1;
        const a = document.createElement("a");
        if (isFigs) {
          a.className = "toc-fig";
          a.href = "#";
          a.textContent = entry || ("\u00a7" + n);
        } else {
          const h = entry;
          const lvl = parseInt(h.tagName[1], 10);
          a.className = "toc-l" + lvl;
          a.href = "#";
          a.textContent = h.textContent || ("\u00a7" + n);
          a.addEventListener("click", (e) => { e.preventDefault(); h.scrollIntoView({ block: "center" }); });
        }
        nav.appendChild(a);
      });
    });
  }

  // View modes Original / Markup / Final: Markup shows both tracked
  // insertions and deletions (the default; CSS for ins/del applies),
  // Original hides insertions, Final hides deletions. The class rides on
  // contentEditable's host so saved HTML is unaffected.
  const VIEW_MODES = ["markup", "original", "final"];
  function cycleDisplayMode() {
    const cur = editor.dataset.viewMode || "markup";
    const next = VIEW_MODES[(VIEW_MODES.indexOf(cur) + 1) % VIEW_MODES.length];
    editor.dataset.viewMode = next;
    setStatus("Display mode: " + next);
  }

  // Caption: inside a table the caption wraps the table in
  // <figure><figcaption> (maps to w:tblCaption); elsewhere it drops a
  // centered caption paragraph (honest L1: plain styled paragraph).
  function insertCaptionCommand() {
    editor.focus();
    const sel = document.getSelection();
    let cell = null;
    if (sel && sel.rangeCount) {
      const node = sel.getRangeAt(0).startContainer;
      cell = node && node.nodeType === 1
        ? node.closest("td, th")
        : node.parentElement && node.parentElement.closest("td, th");
    }
    if (cell) {
      const table = cell.closest("table");
      const fig = document.createElement("figure");
      const cap = document.createElement("figcaption");
      cap.textContent = "Caption";
      table.parentNode.insertBefore(fig, table);
      fig.appendChild(table);
      fig.appendChild(cap);
      refreshToc();
    } else {
      document.execCommand("insertHTML", false,
        '<p style="text-align:center"><em>Caption</em></p>');
    }
    moveCaretPastStructuralMarkers();
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  function insertCitation() {
    editor.focus();
    const sel = document.getSelection();
    if (!sel || !sel.rangeCount) return;
    // Direct DOM insertion (not execCommand insertHTML): Chromium's
    // insertHTML sanitizer strips the class/tag of content-bearing markup
    // (it rewrote <sup class="ref-citation"> into a bare styled span), so
    // the canonical marker must be built node-by-node to round-trip.
    const range = sel.getRangeAt(0);
    const sup = document.createElement("sup");
    sup.className = "ref-citation";
    sup.setAttribute("data-key", "");
    sup.textContent = "[1]";
    range.deleteContents();
    range.insertNode(sup);
    range.setStartAfter(sup);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    moveCaretPastStructuralMarkers();
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  function insertIndexEntry() {
    editor.focus();
    const sel = document.getSelection();
    if (!sel || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const selectedText = range.toString();
    // Direct DOM insertion (see insertCitation): execCommand insertHTML
    // would drop the ref-index class from content-bearing markup.
    const span = document.createElement("span");
    span.className = "ref-index";
    span.textContent = selectedText || "Index Entry";
    range.deleteContents();
    range.insertNode(span);
    range.setStartAfter(span);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    moveCaretPastStructuralMarkers();
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  // Compare: opens the version history; each row carries a Compare button
  // that pulls the version's plain text and applies it as tracked changes
  // over the current document (F-103 entry point).
  async function compareToVersion(ts, btn) {
    if (!btn) return;
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = t("VersionHistory.Comparing");
    try {
      const res = await fetch(api("versions/" + encodeURIComponent(ts) + "/content"));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "compare failed");
      const current = editor.innerText || "";
      const other = data.text || "";
      if (current === other) { setStatus(t("VersionHistory.NoDiff")); btn.disabled = false; btn.textContent = label; return; }
      const ok = applyTrackedDiff(other, current);
      // applyTrackedDiff(base, next): base=other(version), next=current ->
      // version-only content wraps as deletions, current-only as insertions.
      btn.disabled = false;
      btn.textContent = label;
      if (ok) setStatus("Compared with version from " + formatVersionDate(ts), false);
      else setStatus("Compare: range could not be diffed", true);
    } catch (err) {
      btn.disabled = false;
      btn.textContent = label;
      if (versionError) versionError.textContent = "Compare error: " + err.message;
      setStatus("Compare error: " + err.message, true);
    }
  }
  function openCompareView() {
    closeAllMenus();
    openVersionHistory();
  }

  // Drop cap: wrap the paragraph's first character in <span class="dropcap">
  // (serializes via w:framePr w:dropCap — WS-A byte contract); toggles off.
  function firstTextNode(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) { if (n.data.trim()) return n; }
    return null;
  }
  function toggleDropcap() {
    editor.focus();
    const sel = document.getSelection();
    if (!sel || !sel.rangeCount) return;
    const sc = sel.getRangeAt(0).startContainer;
    let p = sc.nodeType === 1
      ? (sc.closest ? sc.closest("p") : null)
      : (sc.parentElement && sc.parentElement.closest("p"));
    if (!p) {
      // select-all / whole-editor selection: fall back to the first
      // paragraph so dropcap still applies (parity with blocksUnderSelection).
      if (editor.contains(sc)) p = editor.querySelector("p");
      if (!p) return;
    }
    const existing = p.querySelector(":scope > span.dropcap");
    if (existing) {
      existing.remove();
    } else {
      const tn = firstTextNode(p);
      if (!tn) return;
      const ch = tn.data[0];
      if (!ch || /\s/.test(ch)) return;
      const span = document.createElement("span");
      span.className = "dropcap";
      span.textContent = ch;
      tn.data = tn.data.slice(1);
      tn.parentNode.insertBefore(span, tn);
    }
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  // Borders: 4-side box painter for the current paragraph (w:pBdr / ODT
  // fo:border-* via inline border-* styles — WS-A byte contract).
  function openBordersDialog() {
    closeAllMenus();
    const dialog = document.getElementById("borders-dialog");
    if (!dialog) return;
    rememberFocus();
    dialog.classList.add("open");
  }
  function closeBordersDialog() {
    const dialog = document.getElementById("borders-dialog");
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
  }
  function confirmBordersDialog() {
    const dialog = document.getElementById("borders-dialog");
    const g = (id) => document.getElementById(id);
    const w = parseFloat((g("bd-width") && g("bd-width").value) || "1") || 1;
    const color = (g("bd-color") && g("bd-color").value) || "#000000";
    const sides = ["top", "bottom", "left", "right"].filter(
      (side) => g("bd-" + side) && g("bd-" + side).checked);
    if (dialog) dialog.classList.remove("open");
    restoreFocus();
    editor.focus();
    const sel = document.getSelection();
    if (!sel || !sel.rangeCount || !sides.length) return;
    const sc = sel.getRangeAt(0).startContainer;
    const block = sc.nodeType === 1
      ? (sc.closest ? sc.closest("p") : null)
      : (sc.parentElement && sc.parentElement.closest("p"));
    if (!block) return;
    const setStyle = { "border-top": null, "border-bottom": null, "border-left": null, "border-right": null };
    sides.forEach((side) => { setStyle["border-" + side] = w + "pt solid " + color; });
    // keep any existing per-side borders not being edited, drop the ones unchecked
    const cur = block.getAttribute("style") || "";
    const next = [];
    cur.split(";").forEach((decl) => {
      const kv = decl.split(":");
      if (kv.length < 2) return;
      const prop = kv[0].trim();
      if (setStyle[prop] !== undefined && setStyle[prop] === null) return; // unchecked side removed
      const idx = next.map((x) => x.split(":")[0]).indexOf(prop);
      if (idx >= 0) next[idx] = decl.trim();
      else next.push(decl.trim());
    });
    sides.forEach((side) => {
      const decl = "border-" + side + ":" + setStyle["border-" + side];
      const prop = "border-" + side;
      const idx = next.map((x) => x.split(":")[0]).indexOf(prop);
      if (idx >= 0) next[idx] = decl; else next.push(decl);
    });
    if (next.length) block.setAttribute("style", next.join(";"));
    else block.removeAttribute("style");
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }

  // Table of figures: a toc-marker nav whose live preview lists figcaption
  // entries (the converter persists it as a plain TOC marker + title).
  function insertToFCommand() {
    editor.focus();
    document.execCommand("insertHTML", false,
      '<nav class="toc" data-kind="figures" data-title="List of Figures"></nav><p><br></p>');
    moveCaretPastStructuralMarkers();
    refreshToc();
    captureHistory(); markDirty(); scheduleCollabSync(); notifyHost("editing");
    updateActiveStates(); updateUndoRedoState();
  }
  // Header/footer 'same as previous': single-section docs have no previous
  // section to inherit, so this is a live affordance + attribute only —
  // serialization is a no-op (ponytail: honest marking, per-section header
  // linkage is a future iteration).
  function toggleSameAsPrevCommand() {
    const hdr = editor.querySelector(":scope > header.page-header, header.page-header");
    if (!hdr) { setStatus("Same as previous: no header to link"); return; }
    if (hdr.hasAttribute("data-same-prev")) { hdr.removeAttribute("data-same-prev"); }
    else { hdr.setAttribute("data-same-prev", "1"); }
    setStatus(hdr.hasAttribute("data-same-prev")
      ? "Same as previous (header linked)"
      : "Same as previous (off)");
    markDirty(); captureHistory(); scheduleCollabSync(); notifyHost("editing");
    updateActiveStates();
  }

  // --- Ink canvas drawing (Draw tab) --------------------------------
  // OO-parity ink layer: canvas overlay, pen/highlighter/eraser tools,
  // color/thickness pickers and — WO-FEA-DRAW-2 — stroke select: click a
  // stroke to select it, drag to move it, Delete/Backspace to remove it.
  // All strokes live in a registry (inkStrokes) replayed by redrawInk(), so
  // the eraser is a recorded destination-out pass in the replay, not a
  // permanent pixel burn. Ink stays ephemeral (not persisted to DOCX) for
  // this MVP — view-only overlay like the navigation sidebar. Future:
  // serialize the registry to image or SVG.
  let inkMode = null; // null | "select" | "pen" | "highlighter" | "eraser"
  let inkColor = "#000000";
  let inkThickness = 3;
  let inkStrokes = []; // completed strokes: {points:[{x,y}], color, thickness, mode}
  let inkStroke = null; // in-progress stroke (null when not drawing)
  let selectedInk = -1; // index into inkStrokes of the selected stroke (-1 = none)
  let selectDrag = null; // {dx,dy} grab offset when moving the selected stroke
  let isDrawing = false;
  const inkCanvas = document.getElementById("ink-canvas");
  const inkCtx = inkCanvas ? inkCanvas.getContext("2d") : null;

  function initInkCanvas() {
    if (!inkCanvas || !inkCtx) return;
    // Set canvas size to match editor on first use (resize clears the
    // canvas, so re-render the stroke registry afterwards).
    const editorRect = editor.getBoundingClientRect();
    inkCanvas.width = editorRect.width;
    inkCanvas.height = editorRect.height;
    inkCanvas.style.width = editorRect.width + "px";
    inkCanvas.style.height = editorRect.height + "px";
    inkCtx.strokeStyle = inkColor;
    inkCtx.lineWidth = inkThickness;
    inkCtx.lineCap = "round";
    inkCtx.lineJoin = "round";
    redrawInk();
    // Setup event listeners once (init runs on every mode toggle).
    if (!inkCanvas.__inkListeners) {
      inkCanvas.addEventListener("mousedown", startDrawing);
      inkCanvas.addEventListener("mousemove", draw);
      inkCanvas.addEventListener("mouseup", stopDrawing);
      inkCanvas.addEventListener("mouseout", stopDrawing);
      inkCanvas.addEventListener("touchstart", handleTouch);
      inkCanvas.addEventListener("touchmove", handleTouch);
      inkCanvas.addEventListener("touchend", stopDrawing);
      inkCanvas.__inkListeners = true;
    }
  }

  function handleTouch(e) {
    e.preventDefault();
    const touch = e.touches[0];
    const mouseEvent = new MouseEvent(e.type, {
      clientX: touch.clientX,
      clientY: touch.clientY
    });
    if (e.type === "touchstart") startDrawing(mouseEvent);
    else if (e.type === "touchmove") draw(mouseEvent);
  }

  function getCanvasPosition(e) {
    if (!inkCanvas) return { x: 0, y: 0 };
    const rect = inkCanvas.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top
    };
  }

  function startDrawing(e) {
    if (!inkCanvas || !inkCtx) return;
    const pos = getCanvasPosition(e);
    if (inkMode === "select") { handleSelectPointer(pos); return; }
    if (inkMode === null) return;
    isDrawing = true;
    inkStroke = { points: [pos], color: inkColor, thickness: inkThickness, mode: inkMode };
    inkCtx.beginPath();
    inkCtx.moveTo(pos.x, pos.y);
    if (inkMode === "eraser") {
      // Destination-out compositing erases what is already on the canvas
      // (replayed in order on redraw, so selection edits stay consistent).
      inkCtx.globalCompositeOperation = "destination-out";
      inkCtx.strokeStyle = "rgba(0,0,0,1)";
    } else {
      inkCtx.globalCompositeOperation = "source-over";
      inkCtx.strokeStyle = inkColor;
      // Highlighter is semi-transparent
      inkCtx.globalAlpha = inkMode === "highlighter" ? 0.4 : 1.0;
    }
    draw(e);
  }

  function draw(e) {
    if (!inkCanvas || !inkCtx) return;
    const pos = getCanvasPosition(e);
    // Select mode: dragging after a grab moves the selected stroke.
    if (selectDrag && selectedInk >= 0) {
      moveSelectedStroke(pos);
      return;
    }
    if (!isDrawing || !inkStroke) return;
    inkStroke.points.push(pos);
    inkCtx.lineTo(pos.x, pos.y);
    inkCtx.stroke();
  }

  function stopDrawing() {
    selectDrag = null;
    if (isDrawing && inkStroke && inkStroke.points.length) {
      inkStrokes.push(inkStroke);
      redrawInk();
    }
    isDrawing = false;
    inkStroke = null;
    if (inkCtx) {
      inkCtx.closePath();
      // Reset compositing
      inkCtx.globalCompositeOperation = "source-over";
      inkCtx.globalAlpha = 1.0;
      inkCtx.strokeStyle = inkColor;
    }
  }

  // Replay the full stroke registry (creation order) so the canvas always
  // reflects inkStrokes — the single source of truth for select/move/delete.
  function redrawInk() {
    if (!inkCanvas || !inkCtx) return;
    inkCtx.clearRect(0, 0, inkCanvas.width, inkCanvas.height);
    inkCtx.lineCap = "round";
    inkCtx.lineJoin = "round";
    inkStrokes.forEach((s) => {
      if (!s.points.length) return;
      inkCtx.globalCompositeOperation = s.mode === "eraser" ? "destination-out" : "source-over";
      inkCtx.globalAlpha = s.mode === "highlighter" ? 0.4 : 1.0;
      inkCtx.strokeStyle = s.mode === "eraser" ? "rgba(0,0,0,1)" : (s.color || inkColor);
      inkCtx.lineWidth = Math.max(1, s.thickness || 1);
      inkCtx.beginPath();
      inkCtx.moveTo(s.points[0].x, s.points[0].y);
      for (let i = 1; i < s.points.length; i++) inkCtx.lineTo(s.points[i].x, s.points[i].y);
      inkCtx.stroke();
    });
    inkCtx.globalCompositeOperation = "source-over";
    inkCtx.globalAlpha = 1.0;
    inkCtx.strokeStyle = inkColor;
    if (selectedInk >= 0 && selectedInk < inkStrokes.length) drawSelectionBox(selectedInk);
  }

  // Dashed accent rectangle around the selected stroke's bounds.
  function drawSelectionBox(idx) {
    const s = inkStrokes[idx];
    if (!s || !s.points.length) return;
    let minX = s.points[0].x, minY = s.points[0].y, maxX = minX, maxY = minY;
    for (const p of s.points) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    const pad = Math.max(4, s.thickness);
    const accent = getComputedStyle(document.documentElement)
      .getPropertyValue("--accent").trim() || "#2563eb";
    inkCtx.strokeStyle = accent;
    inkCtx.lineWidth = 1;
    inkCtx.setLineDash([4, 3]);
    inkCtx.strokeRect(minX - pad, minY - pad, (maxX - minX) + 2 * pad, (maxY - minY) + 2 * pad);
    inkCtx.setLineDash([]);
  }

  // Smallest-axis distance from a point to a segment.
  function pointSegmentDistance(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    const qx = ax + t * dx, qy = ay + t * dy;
    return Math.hypot(px - qx, py - qy);
  }

  // Closest stroke within a 12px grab radius, or -1 when clicking empty area.
  function hitTestStroke(pos) {
    let best = -1, bestDist = 12;
    for (let i = 0; i < inkStrokes.length; i++) {
      const pts = inkStrokes[i].points;
      const last = pts.length - 1;
      if (last < 0) continue;
      for (let k = 0; k < last; k++) {
        const d = pointSegmentDistance(pos.x, pos.y,
          pts[k].x, pts[k].y, pts[k + 1].x, pts[k + 1].y);
        if (d < bestDist) { bestDist = d; best = i; }
      }
      // Cover single-point strokes (a click with no drag) via the end point.
      const dl = Math.hypot(pos.x - pts[last].x, pos.y - pts[last].y);
      if (dl < bestDist) { bestDist = dl; best = i; }
    }
    return best;
  }

  // Select-mode pointer down: pick a stroke (grab offset for the move) or
  // clear the selection when clicking empty canvas.
  function handleSelectPointer(pos) {
    const idx = hitTestStroke(pos);
    if (idx < 0) {
      if (selectedInk >= 0) {
        selectedInk = -1;
        redrawInk();
        setStatus("Ink: selection cleared");
      }
      return;
    }
    selectedInk = idx;
    selectDrag = {
      dx: pos.x - inkStrokes[idx].points[0].x,
      dy: pos.y - inkStrokes[idx].points[0].y
    };
    redrawInk();
    setStatus(`Ink: stroke ${idx + 1} selected — drag to move, Delete to erase`);
  }

  // Drag: translate every point of the selected stroke by the grab delta.
  function moveSelectedStroke(pos) {
    const s = inkStrokes[selectedInk];
    if (!s || !s.points.length || !selectDrag) return;
    const dx = pos.x - selectDrag.dx - s.points[0].x;
    const dy = pos.y - selectDrag.dy - s.points[0].y;
    if (dx === 0 && dy === 0) return;
    for (const p of s.points) { p.x += dx; p.y += dy; }
    redrawInk();
  }

  // Delete key (or Backspace): drop the selected stroke from the registry.
  function deleteSelectedInk() {
    if (selectedInk < 0 || selectedInk >= inkStrokes.length) return;
    inkStrokes.splice(selectedInk, 1);
    selectedInk = -1;
    redrawInk();
    setStatus("Ink: selected stroke deleted");
  }

  function setInkModeOff() {
    inkMode = null;
    selectedInk = -1;
    selectDrag = null;
    if (inkCanvas) {
      inkCanvas.hidden = true;
      inkCanvas.classList.remove("drawing");
    }
    setStatus("Ink mode: off");
  }

  // setInkMode: canonical ink-mode setter (Draw tab). A specific mode
  // ("select"|"pen"|"highlighter"|"eraser") activates that tool; no
  // argument toggles drawing on (pen) / off — the "Draw" master button.
  // Pure set: re-selecting the active tool does not toggle off (toggleInk
  // does that for the per-tool buttons). Drawing state is ephemeral (not
  // persisted to DOCX) — view-only overlay like the navigation sidebar.
  function setInkMode(mode) {
    if (!inkCanvas) {
      setStatus("Ink canvas not found", true);
      return;
    }
    initInkCanvas();
    // No argument: master toggle (Draw button) — enter pen or turn off.
    if (!mode) {
      mode = inkMode ? null : "pen";
    }
    if (mode === null) {
      setInkModeOff();
      updateActiveStates();
      return;
    }
    inkMode = mode;
    selectedInk = -1;
    selectDrag = null;
    inkCanvas.hidden = false;
    inkCanvas.classList.add("drawing");
    inkCanvas.style.cursor = (mode === "select") ? "default" : "crosshair";
    setStatus("Ink mode: " + mode);
    updateActiveStates();
  }

  // toggleInk: per-tool toggle for the tool buttons (pen/highlighter/
  // eraser). Re-selecting the active tool turns ink off; otherwise sets the
  // mode. Delegates to setInkMode so there is one canvas-state code path.
  // (Kept as the data-cmd="toggleInk" handler so the per-tool buttons keep
  // their toggle-off affordance.) The select tool uses its own inkSelect
  // command (see below).
  function toggleInk(mode) {
    if (inkMode === mode) setInkMode(null);
    else setInkMode(mode);
  }

  // Select tool (draw.select): strokes become pickable — click selects,
  // drag moves, Delete/Backspace deletes, Escape clears the selection.
  function inkSelect() {
    if (!inkCanvas) {
      setStatus("Ink canvas not found", true);
      return;
    }
    initInkCanvas();
    if (inkMode === "select") { setInkModeOff(); return; }
    inkMode = "select";
    selectedInk = -1;
    selectDrag = null;
    inkCanvas.hidden = false;
    inkCanvas.classList.add("drawing");
    inkCanvas.style.cursor = "default";
    setStatus("Ink select: click a stroke to select, drag to move, Delete to erase");
    updateActiveStates();
  }

  // Delete/Backspace removes the selected stroke, Escape clears the
  // selection — both only while a stroke is actually selected in select mode.
  document.addEventListener("keydown", (ev) => {
    if (inkMode !== "select" || selectedInk < 0) return;
    if (ev.key === "Delete" || ev.key === "Backspace") {
      ev.preventDefault();
      deleteSelectedInk();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      selectedInk = -1;
      redrawInk();
      setStatus("Ink: selection cleared");
    }
  });

  function setInkColor() {
    // For now, use a simple color picker dialog or default color
    // In a real implementation, this would open a color picker
    // For this MVP, cycle through some colors
    const colors = ["#000000", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF"];
    const currentIndex = colors.indexOf(inkColor);
    inkColor = colors[(currentIndex + 1) % colors.length];
    if (inkCtx) inkCtx.strokeStyle = inkColor;
    setStatus(`Ink color: ${inkColor}`);
  }

  function setInkThickness() {
    // Cycle through thickness values
    const thicknesses = [1, 3, 5, 8, 12];
    const currentIndex = thicknesses.indexOf(inkThickness);
    inkThickness = thicknesses[(currentIndex + 1) % thicknesses.length];
    if (inkCtx) inkCtx.lineWidth = inkThickness;
    setStatus(`Ink thickness: ${inkThickness}px`);
  }

  // ------------------------------------------------------------------
  // Document protection (protect.password / protect.restrict)
  // ------------------------------------------------------------------
  // The real gate is server-side: POST /api/documents/{id}/protect hashes
  // the password per-document (PBKDF2, salt stored in the DOCX's
  // w:documentProtection) and verifies the current password before ANY
  // change; POST /save refuses content writes while the stored document is
  // restricted. The controls here are the UX — the 403 is the enforcement.
  // While restricted the editor behaves like a read-only viewer (editing
  // disabled, save disabled), mirroring Word's enforced protection.
  const protectDialogEl = document.getElementById("protect-dialog");
  let protectionState = { restrict_editing: false, password_set: false };
  let protectionError = "";

  // Mirror the stored document's protection on the editing surface.
  // READ_ONLY (external lock) always wins: never re-enable a locked editor.
  function applyProtectionView() {
    if (READ_ONLY) return;
    const restricted = !!protectionState.restrict_editing;
    editor.contentEditable = restricted ? "false" : "true";
    editor.setAttribute("aria-readonly", restricted ? "true" : "false");
    saveBtn.disabled = restricted;
  }

  async function refreshProtectionState() {
    try {
      const res = await fetch(api("protect"));
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        protectionError = data.error || ("protect: HTTP " + res.status);
        return;
      }
      protectionError = "";
      const data = await res.json();
      protectionState = {
        restrict_editing: !!data.restrict_editing,
        password_set: !!data.password_set,
      };
      applyProtectionView();
    } catch (err) {
      protectionError = "protect unavailable";
    }
  }

  function protectStateLabel() {
    if (protectionState.restrict_editing && protectionState.password_set)
      return t("Prot.StatePassword");
    if (protectionState.restrict_editing) return t("Prot.StateRestricted");
    return t("Prot.StateNone");
  }

  function protectDialog() {
    if (protectionError) { setStatus(protectionError, true); return; }
    if (READ_ONLY) { setStatus("Document protection is managed by the host", true); return; }
    if (!protectDialogEl) return;
    const stateEl = document.getElementById("protect-state");
    const restrictChk = document.getElementById("protect-check-restrict");
    const newPw = document.getElementById("protect-new-password");
    const curPw = document.getElementById("protect-current-password");
    const curField = document.getElementById("protect-current-field");
    const clearChk = document.getElementById("protect-check-clear");
    const pwOn = !!protectionState.password_set;
    if (stateEl) stateEl.textContent = protectStateLabel();
    if (restrictChk) restrictChk.checked = !!protectionState.restrict_editing;
    if (newPw) { newPw.value = ""; newPw.disabled = false; }
    if (curPw) curPw.value = "";
    if (clearChk) clearChk.checked = false;
    if (curField) curField.hidden = !pwOn;
    rememberFocus();
    protectDialogEl.classList.add("open");
    if (newPw) newPw.focus();
  }

  function closeProtectDialog() {
    if (protectDialogEl) protectDialogEl.classList.remove("open");
    restoreFocus();
  }

  async function confirmProtectDialog() {
    if (!protectDialogEl) return;
    const restrictChk = document.getElementById("protect-check-restrict");
    const newPw = document.getElementById("protect-new-password");
    const curPw = document.getElementById("protect-current-password");
    const clearChk = document.getElementById("protect-check-clear");
    const clearing = !!(clearChk && clearChk.checked);
    const newPwVal = (newPw && newPw.value) ? newPw.value : "";
    const body = {
      restrict_editing: !!(restrictChk && restrictChk.checked),
      password: (!clearing && newPwVal) ? newPwVal : null,
      clear_password: clearing,
      current_password: (curPw && curPw.value) ? curPw.value : null,
    };
    try {
      const res = await fetch(api("protect"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus(res.status === 403 ? t("Prot.ErrorWrongPassword") : (data.error || "protection failed"), true);
        return; // keep the dialog open so the password can be re-entered
      }
      protectionState = {
        restrict_editing: !!data.restrict_editing,
        password_set: !!data.password_set,
      };
      applyProtectionView();
      closeProtectDialog();
      setStatus(protectionState.restrict_editing
        ? t("Prot.StatusRestricted") : t("Prot.StatusUnrestricted"));
      notifyHost("editing");
    } catch (err) {
      setStatus("Protection failed: " + err.message, true);
    }
  }

  // Restrict-editing button: toggles directly when no password gates the
  // change; un-restricting a password-protected document needs the password,
  // so that path lands in the dialog (where the state line shows why).
  async function toggleRestrictEditing() {
    if (protectionError) { setStatus(protectionError, true); return; }
    if (READ_ONLY) { setStatus("Document protection is managed by the host", true); return; }
    const turningOn = !protectionState.restrict_editing;
    if (!turningOn && protectionState.password_set) { protectDialog(); return; }
    try {
      const res = await fetch(api("protect"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          restrict_editing: turningOn,
          password: null,
          clear_password: false,
          current_password: null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus(res.status === 403 ? t("Prot.ErrorWrongPassword") : (data.error || "protection failed"), true);
        return;
      }
      protectionState = {
        restrict_editing: !!data.restrict_editing,
        password_set: !!data.password_set,
      };
      applyProtectionView();
      setStatus(turningOn ? t("Prot.StatusRestricted") : t("Prot.StatusUnrestricted"));
      notifyHost("editing");
    } catch (err) {
      setStatus("Protection failed: " + err.message, true);
    }
  }

  // --- OO-parity stubs (documented iteration backlog) --------------------
  // Controls for OO features WO has not implemented carry data-stub="<ref>"
  // in index.html. Clicking reports the ref loudly via setStatus — nothing
  // is a silent no-op. Grep data-stub in index.html to promote a stub to a
  // real feature (id + handler + remove the attribute).
  document.querySelectorAll("button[data-stub]").forEach((b) => {
    b.addEventListener("click", () => {
      setStatus((b.dataset.stub || "feature") + ": " + t("Stub.NotImplemented"), true);
    });
  });
  const btnAIReviewClose = document.getElementById("btn-ai-review-close");
  if (btnAIReviewClose) btnAIReviewClose.addEventListener("click", closeAIReview);
  const btnAIRejectAll = document.getElementById("btn-ai-reject-all");
  if (btnAIRejectAll) btnAIRejectAll.addEventListener("click", () => rejectAllAIOps(btnAIRejectAll));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && aiReviewDialog && aiReviewDialog.classList.contains("open")) {
      ev.preventDefault();
      closeAIReview();
    }
  });

  // --- insert misc: horizontal rule / page break / symbol picker -----
  const hrBtn = document.getElementById("btn-hr");
  if (hrBtn) hrBtn.addEventListener("click", () => emitCommand("insertHR"));
  const pbBtn = document.getElementById("btn-page-break");
  if (pbBtn) pbBtn.addEventListener("click", () => emitCommand("insertPageBreak"));
  const sbBtn = document.getElementById("btn-section-break");
  if (sbBtn) sbBtn.addEventListener("click", () => emitCommand("insertSectionBreak"));
  const colsBtn = document.getElementById("btn-columns");
  if (colsBtn) colsBtn.addEventListener("click", openColumnsDialog);
  const tocBtn = document.getElementById("btn-toc");
  if (tocBtn) tocBtn.addEventListener("click", openTocDialog);
  const objBtn = document.getElementById("btn-object");
  if (objBtn) objBtn.addEventListener("click", openObjectDialog);
  // Columns dialog
  const colsOk = document.getElementById("btn-columns-ok");
  if (colsOk) colsOk.addEventListener("click", confirmColumnsDialog);
  const colsCancel = document.getElementById("btn-columns-cancel");
  if (colsCancel) colsCancel.addEventListener("click", closeColumnsDialog);
  const colsCount = document.getElementById("columns-count");
  if (colsCount) colsCount.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmColumnsDialog(); }
  });
  const colsGap = document.getElementById("columns-gap");
  if (colsGap) colsGap.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmColumnsDialog(); }
  });
  // TOC dialog
  const tocOk = document.getElementById("btn-toc-ok");
  if (tocOk) tocOk.addEventListener("click", confirmTocDialog);
  const tocCancel = document.getElementById("btn-toc-cancel");
  if (tocCancel) tocCancel.addEventListener("click", closeTocDialog);
  const tocTitle = document.getElementById("toc-title");
  if (tocTitle) tocTitle.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmTocDialog(); }
  });
  // Object dialog
  const bdOk = document.getElementById("btn-borders-ok");
  if (bdOk) bdOk.addEventListener("click", confirmBordersDialog);
  const bdCancel = document.getElementById("btn-borders-cancel");
  if (bdCancel) bdCancel.addEventListener("click", closeBordersDialog);
  const bdColor = document.getElementById("bd-color");
  if (bdColor) bdColor.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmBordersDialog(); }
  });
  const objOk = document.getElementById("btn-object-ok");
  if (objOk) objOk.addEventListener("click", confirmObjectDialog);
  const objCancel = document.getElementById("btn-object-cancel");
  if (objCancel) objCancel.addEventListener("click", closeObjectDialog);
  const bmBtn = document.getElementById("btn-bookmark");
  if (bmBtn) bmBtn.addEventListener("click", openBookmarkDialog);
  const bmOk = document.getElementById("btn-bookmark-ok");
  if (bmOk) bmOk.addEventListener("click", confirmBookmarkDialog);
  const bmCancel = document.getElementById("btn-bookmark-cancel");
  if (bmCancel) bmCancel.addEventListener("click", closeBookmarkDialog);
  const bmName = document.getElementById("bookmark-name");
  if (bmName) bmName.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmBookmarkDialog(); }
  });
  // Protect dialog wiring (see the document protection section below).
  const btnProtectApply = document.getElementById("btn-protect-apply");
  if (btnProtectApply) btnProtectApply.addEventListener("click", confirmProtectDialog);
  const btnProtectCancel = document.getElementById("btn-protect-cancel");
  if (btnProtectCancel) btnProtectCancel.addEventListener("click", closeProtectDialog);
  const protectNewPw = document.getElementById("protect-new-password");
  if (protectNewPw) protectNewPw.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmProtectDialog(); }
  });
  const protectClear = document.getElementById("protect-check-clear");
  if (protectClear) protectClear.addEventListener("change", () => {
    const np = document.getElementById("protect-new-password");
    if (np) np.disabled = protectClear.checked;
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && protectDialogEl && protectDialogEl.classList.contains("open")) {
      ev.preventDefault();
      closeProtectDialog();
    }
  });
  const xrefBtn = document.getElementById("btn-crossref");
  if (xrefBtn) xrefBtn.addEventListener("click", openCrossrefDialog);
  const xrefOk = document.getElementById("btn-crossref-ok");
  if (xrefOk) xrefOk.addEventListener("click", confirmCrossrefDialog);
  const xrefCancel = document.getElementById("btn-crossref-cancel");
  if (xrefCancel) xrefCancel.addEventListener("click", closeCrossrefDialog);
  const tcBtn = document.getElementById("btn-track-changes");
  if (tcBtn) tcBtn.addEventListener("click", () => { const cb = document.getElementById("tc-enabled"); if (cb) cb.checked = !trackChangesOn; openOverlayDialog("trackchanges-dialog"); });
  const revBtn = document.getElementById("btn-review-changes");
  if (revBtn) revBtn.addEventListener("click", openReviewPanel);
  const revClose = document.getElementById("btn-review-close");
  if (revClose) revClose.addEventListener("click", closeReviewPanel);
  editor.addEventListener("beforeinput", onBeforeInput);
  const commentBtn = document.getElementById("btn-comment");
  if (commentBtn) commentBtn.addEventListener("click", openCommentDialog);
  const commentOk = document.getElementById("btn-comment-ok");
  if (commentOk) commentOk.addEventListener("click", confirmCommentDialog);
  const commentCancel = document.getElementById("btn-comment-cancel");
  if (commentCancel) commentCancel.addEventListener("click", closeCommentDialog);
  const commentBody = document.getElementById("comment-body");
  if (commentBody) commentBody.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); confirmCommentDialog(); }
  });
  const commentsBtn = document.getElementById("btn-comments");
  if (commentsBtn) commentsBtn.addEventListener("click", () => {
    const p = document.getElementById("comments-panel");
    if (p) p.hidden = !p.hidden;
    renderCommentsList();
  });
  const commentsClose = document.getElementById("btn-comments-close");
  if (commentsClose) commentsClose.addEventListener("click", closeCommentsPanel);
  // Statusbar comment shortcuts: reuse the review-tab handlers.
  const sbComments = document.querySelector(".sb-comments");
  if (sbComments && commentsBtn) sbComments.addEventListener("click", () => commentsBtn.click());
  const sbAddComment = document.querySelector(".sb-add-comment");
  if (sbAddComment && commentBtn) sbAddComment.addEventListener("click", () => commentBtn.click());
  // Document language: drives spellcheck + the editor lang attribute, and
  // the plain-language label at the statusbar's far-left OO-golden slot.
  const docLang = document.getElementById("doc-lang");
  const docLangLabelFrags = document.querySelectorAll("#doc-lang-label b, #doc-lang-label-right b");
  function syncDocLangLabel() {
    const label = docLang ? docLang.selectedOptions[0]?.label || "" : "";
    for (const b of docLangLabelFrags) {
      const [a, z] = b.dataset.slice.split(":").map(Number);
      b.textContent = label.slice(a, z);
    }
  }
  if (docLang) docLang.addEventListener("change", () => {
    const ed = document.getElementById("editor");
    if (ed) ed.setAttribute("lang", docLang.value);
    syncDocLangLabel();
  });
  syncDocLangLabel();
  // The statusbar caret discloses the language select (appearance:none).
  const docLangCaret = document.getElementById("doc-lang-caret");
  if (docLangCaret && docLang) docLangCaret.addEventListener("click", () => {
    try { docLang.showPicker(); } catch (err) { docLang.focus(); }
  });
  const SYMBOLS = ["§", "¶", "°", "±", "×", "÷", "≈", "≠", "≤", "≥", "∞", "√",
                   "€", "£", "¥", "¢", "©", "®", "™", "→", "←", "↑", "↓", "•",
                   "–", "—", "…", "«", "»", "½", "¼", "¾", "α", "β", "μ", "π",
                   "Ω", "∆", "∑", "♥", "★", "☺", "☎", "✂", "☞", "†"];
  let symbolGridBuilt = false;
  function openSymbolDialog() {
    if (READ_ONLY) return;
    const dialog = document.getElementById("symbol-dialog");
    const grid = document.getElementById("symbol-grid");
    if (!dialog || !grid) return;
    if (!symbolGridBuilt) {
      SYMBOLS.forEach((sym) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "symbol-btn";
        btn.textContent = sym;
        btn.setAttribute("aria-label", "Insert " + sym);
        btn.addEventListener("click", () => {
          dialog.classList.remove("open");
          restoreFocus();
          emitCommand("insertSymbol", sym);
        });
        grid.appendChild(btn);
      });
      symbolGridBuilt = true;
    }
    rememberFocus();
    dialog.classList.add("open");
    const first = grid.querySelector(".symbol-btn");
    if (first) first.focus();
  }
  const symbolBtn = document.getElementById("btn-symbol");
  if (symbolBtn) symbolBtn.addEventListener("click", openSymbolDialog);
  const dtBtn = document.getElementById("btn-datetime");
  if (dtBtn) dtBtn.addEventListener("click", () => emitCommand("insertDate"));

  // --- OO-parity option modals (p2 divergence fixes) ----------------------
  // Each dialog opens from its ribbon button, offers the options OO offers,
  // and Apply invokes the existing command (then post-processes the inserted
  // element so the chosen options have a visible, honest effect).
  function openOverlayDialog(id) {
    const d = document.getElementById(id);
    if (!d) return;
    d.classList.add("open");
    const first = d.querySelector("select, input, button");
    if (first) first.focus();
  }
  function closeOverlayDialog(id) {
    const d = document.getElementById(id);
    if (d) d.classList.remove("open");
  }
  for (const [btnId, dlgId] of [["btn-footnote", "notes-dialog"], ["btn-endnote", "notes-dialog"],
                                ["btn-pagenumber", "pagenumber-dialog"],
                                ["btn-header", "headerfooter-dialog"], ["btn-footer", "headerfooter-dialog"]]) {
    const b = document.getElementById(btnId);
    if (!b) continue;
    const kind = btnId === "btn-footnote" || btnId === "btn-endnote" ? btnId.slice(4) : null;
    b.addEventListener("click", () => {
      if (kind) {
        const sel = document.getElementById("notes-type");
        if (sel) sel.value = kind;
      }
      openOverlayDialog(dlgId);
    });
  }
  const notesOk = document.getElementById("btn-notes-ok");
  if (notesOk) notesOk.addEventListener("click", () => {
    closeOverlayDialog("notes-dialog");
    const typeSel = document.getElementById("notes-type");
    const fmtSel = document.getElementById("notes-format");
    emitCommand(typeSel && typeSel.value === "endnote" ? "insertEndnote" : "insertFootnote");
    // Marker reflects the chosen number format (1 / a / i).
    const fmt = fmtSel ? fmtSel.value : "1";
    const cls = typeSel && typeSel.value === "endnote" ? "endnote" : "footnote";
    const sup = editor.querySelector("sup." + cls + "-citation:last-of-type");
    if (sup) {
      sup.dataset.format = fmt;
      sup.textContent = "[" + (fmt === "a" ? "a" : fmt === "i" ? "i" : "1") + "]";
    }
  });
  const pnOk = document.getElementById("btn-pagenumber-ok");
  if (pnOk) pnOk.addEventListener("click", () => {
    closeOverlayDialog("pagenumber-dialog");
    const pos = (document.getElementById("pagenumber-position") || {}).value || "body";
    const align = (document.getElementById("pagenumber-align") || {}).value || "center";
    if (pos === "top") emitCommand("insertHeader");
    else if (pos === "bottom") emitCommand("insertFooter");
    if (pos === "top" || pos === "bottom") {
      const tag = pos === "top" ? "header" : "footer";
      const host = editor.querySelector(":scope > " + tag + ".page-" + tag);
      if (host) {
        const span = document.createElement("span");
        span.className = "page-number";
        span.style.display = "block";
        span.style.textAlign = align;
        host.textContent = "";
        host.appendChild(span);
        host.focus();
        markDirty();
        captureHistory();
        scheduleCollabSync();
        notifyHost("editing");
      }
      return;
    }
    emitCommand("insertPageNumber");
    const span = editor.querySelector("span.page-number:last-of-type");
    if (span) { span.style.display = "block"; span.style.textAlign = align; }
  });
  const hfOk = document.getElementById("btn-headerfooter-ok");
  if (hfOk) hfOk.addEventListener("click", () => {
    closeOverlayDialog("headerfooter-dialog");
    const diffFirst = !!(document.getElementById("hf-different-first") || {}).checked;
    const dist = parseFloat((document.getElementById("hf-distance") || {}).value) || 12.5;
    for (const tag of ["header", "footer"]) {
      const existing = editor.querySelector(":scope > " + tag + ".page-" + tag);
      if (existing) continue;
      emitCommand("insert" + (tag === "header" ? "Header" : "Footer"));
    }
    for (const tag of ["header", "footer"]) {
      const el = editor.querySelector(":scope > " + tag + ".page-" + tag);
      if (!el) continue;
      if (diffFirst) el.dataset.differentFirst = "true";
      else delete el.dataset.differentFirst;
      if (tag === "header") el.style.paddingTop = dist + "mm";
      else el.style.paddingBottom = dist + "mm";
    }
    markDirty();
    captureHistory();
    scheduleCollabSync();
    notifyHost("editing");
  });
  const tcOk = document.getElementById("btn-trackchanges-ok");
  if (tcOk) tcOk.addEventListener("click", () => {
    closeOverlayDialog("trackchanges-dialog");
    const want = !!(document.getElementById("tc-enabled") || {}).checked;
    if (want !== trackChangesOn) setTrackChanges(want);
  });
  for (const id of ["notes", "pagenumber", "headerfooter", "trackchanges"]) {
    const c = document.getElementById("btn-" + id + "-close");
    if (c) c.addEventListener("click", () => closeOverlayDialog(id + "-dialog"));
  }

  // ── OO-parity option modals round 2 ──────────────────────────────────────
  // drop-cap / line numbers / hyphenation / watermark / page color / theme
  // colors / add-text / update-TOC / display-mode previously acted directly;
  // like OO, each now opens an options dialog and only OK touches the
  // document. Section-marker values are OOXML-canonical so the round-trip
  // writes w:restart="newPage" etc. (not the old "eachPage" shorthand).
  function removeSectionMarker(klass) {
    const m = sectionMarkerEl(klass);
    if (m) { m.remove(); markDirty(); captureHistory(); scheduleCollabSync(); notifyHost("editing"); updateActiveStates(); }
  }
  function setSectionMarker(klass, attrs) {
    if (sectionMarkerEl(klass)) removeSectionMarker(klass);
    toggleSectionMarker(klass, attrs);
  }

  // Drop Cap (Insert). Replaces the fixed 2.6em drop with parameterized
  // CSS vars so lines/distance/in-margin actually change the render.
  function applyDropcap(opts) {
    opts = opts || {};
    editor.focus();
    const sel = document.getSelection();
    if (!sel || !sel.rangeCount) return;
    const sc = sel.getRangeAt(0).startContainer;
    let p = sc.nodeType === 1
      ? (sc.closest ? sc.closest("p") : null)
      : (sc.parentElement && sc.parentElement.closest("p"));
    if (!p) {
      if (editor.contains(sc)) p = editor.querySelector("p");
      if (!p) return;
    }
    const existing = p.querySelector(":scope > span.dropcap");
    if (existing) existing.remove();
    const tn = firstTextNode(p);
    if (!tn) return;
    const ch = tn.data[0];
    if (!ch || /\s/.test(ch)) return;
    const span = document.createElement("span");
    span.className = "dropcap" + (opts.inMargin ? " in-margin" : "");
    if (opts.lines) span.style.setProperty("--drop-lines", String(opts.lines));
    if (opts.distance) span.style.setProperty("--drop-distance", opts.distance + "pt");
    span.textContent = ch;
    tn.data = tn.data.slice(1);
    tn.parentNode.insertBefore(span, tn);
    captureHistory();
    markDirty();
    scheduleCollabSync();
    notifyHost("editing");
    updateActiveStates();
  }
  function toggleDropcap() { applyDropcap({}); }
  function confirmDropcapDialog() {
    const pos = (document.getElementById("dropcap-position") || {}).value || "dropped";
    if (pos === "none") { if (!READ_ONLY) removeDropcapOnly(); return; }
    if (READ_ONLY) return;
    const lines = parseInt((document.getElementById("dropcap-lines") || {}).value, 10);
    const distance = parseFloat((document.getElementById("dropcap-distance") || {}).value);
    applyDropcap({ inMargin: pos === "in-margin",
                   lines: Number.isFinite(lines) ? Math.max(2, Math.min(5, lines)) : 3,
                   distance: Number.isFinite(distance) ? Math.max(0, Math.min(50, distance)) : 0 });
  }
  function removeDropcapOnly() {
    editor.focus();
    const sel = document.getSelection();
    if (!sel || !sel.rangeCount) return;
    const sc = sel.getRangeAt(0).startContainer;
    const p = sc.nodeType === 1 ? (sc.closest ? sc.closest("p") : null)
      : (sc.parentElement && sc.parentElement.closest("p"));
    if (!p) return;
    const existing = p.querySelector(":scope > span.dropcap");
    if (!existing) return;
    existing.remove();
    captureHistory(); markDirty(); scheduleCollabSync(); notifyHost("editing"); updateActiveStates();
  }

  // Line Numbers (Layout) -> data-restart="continuous|newPage|newSection".
  function confirmLineNumbersDialog() {
    const mode = (document.getElementById("ln-mode") || {}).value || "restart-each-page";
    if (READ_ONLY) return;
    if (mode === "none") { removeSectionMarker("line-numbers"); setStatus("Line numbers off"); return; }
    const restart = mode === "continuous" ? "continuous"
      : mode === "restart-each-section" ? "newSection"
      : "newPage";
    setSectionMarker("line-numbers", `data-restart="${restart}"`);
    setStatus("Line numbers: " + restart);
  }

  // Hyphenation (Layout) -> data-auto="1".
  function confirmHyphenationDialog() {
    const mode = (document.getElementById("hy-mode") || {}).value || "auto";
    if (READ_ONLY) return;
    if (mode === "none") { removeSectionMarker("hyphenation"); setStatus("Hyphenation off"); return; }
    setSectionMarker("hyphenation", 'data-auto="1"');
    setStatus("Hyphenation on");
  }

  // Watermark (Layout) -> data-text + data-color.
  function confirmWatermarkDialog() {
    const mode = (document.getElementById("wm-mode") || {}).value || "draft";
    const color = (document.getElementById("wm-color") || {}).value || "#C0C0C0";
    if (READ_ONLY) return;
    if (mode === "none") { removeSectionMarker("watermark"); setStatus("Watermark removed"); return; }
    const text = (mode === "custom" ? ((document.getElementById("wm-text") || {}).value || "CONFIDENTIAL") : mode.toUpperCase())
      .replace(/"/g, "");
    setSectionMarker("watermark", `data-text="${text}" data-color="${color}"`);
    setStatus("Watermark: " + text);
  }

  // Page Color (Layout) -> paper swatch palette (native picker replaced).
  let pageColorPicked = null;
  function openPageColorDialog() {
    closeAllMenus();
    pageColorPicked = null;
    openOverlayDialog("pagecolor-dialog");
  }
  function confirmPageColorDialog() {
    if (READ_ONLY) return;
    const custom = document.getElementById("pagecolor-custom");
    const color = pageColorPicked || (custom && custom.value) || "#ffffff";
    editor.style.setProperty("--paper", color);
    const pageColor = document.getElementById("pagecolor");
    if (pageColor) pageColor.value = color.toLowerCase();
    setStatus("Page color set");
  }

  // Theme colors (Layout) -> scheme dialog (native select replaced).
  function openColorsDialog() {
    closeAllMenus();
    const sel = document.getElementById("cs-scheme");
    const cur = document.getElementById("themecolors");
    if (sel && cur) sel.value = cur.value || "default";
    openOverlayDialog("colors-dialog");
  }
  function confirmColorsDialog() {
    if (READ_ONLY) return;
    const scheme = (document.getElementById("cs-scheme") || {}).value || "default";
    const cur = document.getElementById("themecolors");
    if (cur) cur.value = scheme;
    const vars = COLOR_SCHEMES[scheme] || {};
    for (const [k, v] of Object.entries(vars)) editor.style.setProperty(k, v);
    setStatus("Color scheme: " + scheme);
  }

  // Update TOC (References) -> whole-table vs page-numbers-only choice.
  function openUpdateTocDialog() {
    closeAllMenus();
    openOverlayDialog("updatetoc-dialog");
  }
  function confirmUpdateTocDialog() {
    const choice = (document.getElementById("toc-update") || {}).value || "entire";
    if (READ_ONLY) return;
    if (!editor.querySelector("nav.toc")) {
      editor.focus();
      document.execCommand("insertHTML", false,
        '<nav class="toc" data-title="Table of Contents"></nav><p><br></p>');
      moveCaretPastStructuralMarkers();
    }
    refreshToc();
    captureHistory(); markDirty(); scheduleCollabSync(); notifyHost("editing");
    // ponytail: the live preview links headings, not rendered page numbers,
    // so "page numbers only" re-links the same entries (honest, no fake diff).
    setStatus(choice === "pages" ? "Page numbers updated" : "TOC updated");
  }

  // Display mode (Collaboration / View) -> Markup / Original / Final choice.
  function openDisplayModeDialog() {
    closeAllMenus();
    const sel = document.getElementById("viewmode");
    if (sel) sel.value = editor.dataset.viewMode || "markup";
    openOverlayDialog("displaymode-dialog");
  }
  function confirmDisplayModeDialog() {
    const mode = (document.getElementById("viewmode") || {}).value || "markup";
    editor.dataset.viewMode = mode;
    setStatus("Display mode: " + mode);
  }

  function openDropcapDialog() { closeAllMenus(); openOverlayDialog("dropcap-dialog"); }
  function openLineNumbersDialog() { closeAllMenus(); openOverlayDialog("linenumbers-dialog"); }
  function openHyphenationDialog() { closeAllMenus(); openOverlayDialog("hyphenation-dialog"); }
  function openWatermarkDialog() { closeAllMenus(); openOverlayDialog("watermark-dialog"); }

  const ROUND2 = [
    ["dropcap", confirmDropcapDialog], ["linenumbers", confirmLineNumbersDialog],
    ["hyphenation", confirmHyphenationDialog], ["watermark", confirmWatermarkDialog],
    ["pagecolor", confirmPageColorDialog], ["colors", confirmColorsDialog],
    ["addtext", null], ["updatetoc", confirmUpdateTocDialog],
    ["displaymode", confirmDisplayModeDialog],
  ];
  for (const [name, confirm] of ROUND2) {
    const ok = document.getElementById("btn-" + name + "-ok");
    const close = document.getElementById("btn-" + name + "-close");
    if (ok && confirm) ok.addEventListener("click", () => { closeOverlayDialog(name + "-dialog"); confirm(); });
    if (close) close.addEventListener("click", () => closeOverlayDialog(name + "-dialog"));
  }
  const wmMode = document.getElementById("wm-mode");
  if (wmMode) wmMode.addEventListener("change", () => {
    const row = document.getElementById("wm-custom-row");
    if (row) row.hidden = wmMode.value !== "custom";
  });
  const pageSwatches = document.getElementById("pagecolor-swatches");
  if (pageSwatches) pageSwatches.addEventListener("click", (ev) => {
    const btn = ev.target.closest && ev.target.closest(".color-swatch");
    if (!btn) return;
    pageColorPicked = btn.dataset.color || null;
    pageSwatches.querySelectorAll(".color-swatch").forEach((s) => s.classList.toggle("sel", s === btn));
    const custom = document.getElementById("pagecolor-custom");
    if (custom && pageColorPicked && pageColorPicked.startsWith("#")) custom.value = pageColorPicked;
  });
  // Note / page-field / header-footer authoring buttons (F-073/F-074/F-084/F-085)
  for (const [id, cmd] of [["btn-footnote", "insertFootnote"], ["btn-endnote", "insertEndnote"], ["btn-pagenumber", "insertPageNumber"], ["btn-header", "insertHeader"], ["btn-footer", "insertFooter"]]) {
    const b = document.getElementById(id);
    if (b) b.addEventListener("click", () => emitCommand(cmd));
  }
  const symbolClose = document.getElementById("btn-symbol-close");
  if (symbolClose) symbolClose.addEventListener("click", () => {
    const d = document.getElementById("symbol-dialog");
    if (d) d.classList.remove("open");
    restoreFocus();
  });
  document.getElementById("btn-find").addEventListener("click", openFindDialog);
  saveBtn.addEventListener("click", saveDocument);

  // --- view controls: zoom / theme / fullscreen ----------------------
  let zoomLevel = parseFloat(localStorage.getItem("wo-zoom") || "1") || 1;
  function applyZoom() {
    zoomLevel = Math.min(2, Math.max(0.5, zoomLevel));
    editor.style.zoom = String(zoomLevel);
    const reset = document.getElementById("btn-zoom-reset");
    if (reset) reset.textContent = Math.round(zoomLevel * 100) + "%";
    const slider = document.getElementById("zoom-slider");
    if (slider) slider.value = String(Math.round(zoomLevel * 100));
    localStorage.setItem("wo-zoom", String(zoomLevel));
  }
  function applyTheme() {
    // Light is the default (matches the OnlyOffice light golden); stored value
    // only flips it when the user explicitly chose dark.
    const dark = localStorage.getItem("wo-theme") === "dark";
    document.documentElement.classList.toggle("light", !dark);
    const themeBtn = document.getElementById("btn-theme");
    if (themeBtn) themeBtn.setAttribute("aria-pressed", String(dark));
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#1e1e28" : "#f2f2f2");
  }
  function toggleTheme() {
    const isLight = document.documentElement.classList.contains("light");
    localStorage.setItem("wo-theme", isLight ? "dark" : "light");
    applyTheme();
  }
  // --- ribbon tabs: switch the visible control group ----------------
  document.querySelectorAll(".ribbon-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".ribbon-tab").forEach((t) =>
        t.setAttribute("aria-selected", String(t === tab)));
      document.querySelectorAll(".ribbon-page").forEach((p) =>
        p.classList.toggle("active", p.dataset.tab === tab.dataset.tab));
    });
  });

  // --- contextual Header & Footer tab (OO parity) ---------------------
  // Double-clicking the page header/footer reveals the contextual tab at
  // OO's strip position; closing it (button, another tab, Escape, or a
  // double-click in the body) hides it again.
  let hfMode = false;
  const hfTab = document.querySelector('.ribbon-tab[data-tab="header-footer"]');
  function exitHFMode() {
    if (!hfMode || !hfTab) return;
    hfMode = false;
    hfTab.hidden = true;
    document.querySelector('.ribbon-tab[data-tab="home"]')?.click();
  }
  function enterHFMode() {
    if (!hfTab) return;
    hfMode = true;
    hfTab.hidden = false;
    hfTab.click();
  }
  document.getElementById("editor")?.addEventListener("dblclick", (ev) => {
    const inHF = ev.target.closest && ev.target.closest(".page-header, .page-footer");
    if (inHF) enterHFMode();
    else if (hfMode) exitHFMode();
  });
  document.querySelectorAll(".ribbon-tab").forEach((tab) => {
    if (tab === hfTab) return;
    tab.addEventListener("click", () => { if (hfMode) { hfMode = false; hfTab.hidden = true; } });
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && hfMode) { ev.preventDefault(); exitHFMode(); }
  });
  const hfClose = document.getElementById("btn-hf-close");
  if (hfClose) hfClose.addEventListener("click", exitHFMode);
  const hfPageNumber = document.getElementById("btn-hf-pagenumber");
  if (hfPageNumber) hfPageNumber.addEventListener("click", () => emitCommand("insertPageNumber"));
  const hfDateTime = document.getElementById("btn-hf-datetime");
  if (hfDateTime) hfDateTime.addEventListener("click", () => emitCommand("insertDate"));

  // --- right-click context menu on the editing surface ----------------
  // OO parity: same geometry (210px, 26px rows) and item order as OO's
  // document context menu, restricted to the honest subset of actions WO
  // can perform (cut/copy/paste, page break, comment, link). Cut/copy/
  // paste go through execCommand so the existing input-event autosave and
  // snapshot chain arm exactly as for keyboard edits.
  const ctxMenu = document.getElementById("ctx-menu");
  if (ctxMenu) {
    const editorEl = document.getElementById("editor");
    const closeCtx = () => { ctxMenu.hidden = true; };
    editorEl.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      ctxMenu.hidden = false;
      const r = ctxMenu.getBoundingClientRect();
      ctxMenu.style.left = Math.max(4, Math.min(e.clientX + 2, window.innerWidth - r.width - 4)) + "px";
      ctxMenu.style.top = Math.max(4, Math.min(e.clientY + 2, window.innerHeight - r.height - 4)) + "px";
    });
    // keep the editor selection alive when pressing menu rows
    ctxMenu.addEventListener("mousedown", (e) => e.preventDefault());
    ctxMenu.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-ctx]");
      if (!btn) return;
      closeCtx();
      const action = btn.dataset.ctx;
      if (action === "cut" || action === "copy") {
        document.execCommand(action);
      } else if (action === "paste") {
        navigator.clipboard.readText().then((text) => {
          if (text) document.execCommand("insertText", false, text);
        }).catch(() => {});
      } else if (action === "pagebreak") {
        emitCommand("insertPageBreak");
      } else if (action === "comment") {
        openCommentDialog();
      } else if (action === "link") {
        insertLink();
      }
    });
    document.addEventListener("mousedown", (e) => {
      if (!ctxMenu.hidden && !ctxMenu.contains(e.target)) closeCtx();
    });
    window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtx(); });
    window.addEventListener("blur", closeCtx);
  }

  function toggleFullscreen() {
    document.body.classList.toggle("fullscreen");
    if (document.fullscreenElement) {
      if (document.exitFullscreen) document.exitFullscreen();
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  }
  const zoomInBtn = document.getElementById("btn-zoom-in");
  const zoomOutBtn = document.getElementById("btn-zoom-out");
  const zoomResetBtn = document.getElementById("btn-zoom-reset");
  const zoomSlider = document.getElementById("zoom-slider");
  const zoomFitBtn = document.getElementById("btn-zoom-fit");
  const themeBtn = document.getElementById("btn-theme");
  const fsBtn = document.getElementById("btn-fullscreen");
  if (zoomInBtn) zoomInBtn.addEventListener("click", () => { zoomLevel += 0.1; applyZoom(); });
  if (zoomOutBtn) zoomOutBtn.addEventListener("click", () => { zoomLevel -= 0.1; applyZoom(); });
  if (zoomResetBtn) zoomResetBtn.addEventListener("click", () => { zoomLevel = 1; applyZoom(); });
  if (zoomSlider) {
    zoomSlider.value = String(Math.round(zoomLevel * 100));
    zoomSlider.addEventListener("input", () => { zoomLevel = parseInt(zoomSlider.value, 10) / 100; applyZoom(); });
  }
  if (zoomFitBtn) zoomFitBtn.addEventListener("click", () => {
    // Fit page width: scale so the 794px page (plus margins) fills the viewport.
    const avail = window.innerWidth - 96;
    zoomLevel = Math.min(2, Math.max(0.5, avail / 830));
    applyZoom();
  });
  const printBtn = document.getElementById("btn-print-qa");
  if (printBtn) printBtn.addEventListener("click", () => window.print());
  const rcBtn = document.getElementById("btn-ribbon-collapse");
  if (rcBtn) rcBtn.addEventListener("click", () => {
    const row2 = document.getElementById("ribbon-row-2");
    if (!row2) return;
    const hide = !row2.hasAttribute("hidden");
    row2.toggleAttribute("hidden", hide);
    rcBtn.setAttribute("aria-expanded", String(!hide));
  });
  if (themeBtn) themeBtn.addEventListener("click", toggleTheme);
  // --- user identity chip (WOPI UserFriendlyName from CheckFileInfo) ---
  const chip = document.getElementById("user-chip");
  const uname = (window.__USER__ || "").trim();
  if (chip && uname) {
    chip.hidden = false;
    document.getElementById("user-chip-name").textContent = uname.split(/\s+/)[0];
    document.getElementById("user-chip-full").textContent = uname;
    document.getElementById("user-chip-doc").textContent = window.__DOC_NAME__ || "";
    const chipBtn = document.getElementById("user-chip-btn");
    const chipPanel = document.getElementById("user-chip-panel");
    chipBtn.title = uname;
    chipBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = chipPanel.hidden;
      closeAllMenus();
      chipPanel.hidden = !willOpen;
      chipBtn.setAttribute("aria-expanded", String(willOpen));
    });
  }
  if (fsBtn) fsBtn.addEventListener("click", toggleFullscreen);
  const fsTitlebar = document.querySelector(".titlebar-fs");
  if (fsTitlebar) fsTitlebar.addEventListener("click", toggleFullscreen);
  applyZoom();
  applyTheme();

  // ------------------------------------------------------------------
  // File menu wiring (dropdown disclose + command dispatch)
  // ------------------------------------------------------------------
  const fileTrigger = document.getElementById("btn-file");
  const fileMenu = document.getElementById("file-menu");
  const exportTrigger = document.getElementById("btn-export");
  const exportSub = exportTrigger ? exportTrigger.parentElement.querySelector(".menu-sublist") : null;

  function setMenu(menu, trigger, open) {
    if (!menu) return;
    menu.hidden = !open;
    if (trigger) trigger.setAttribute("aria-expanded", String(open));
  }
  function closeAllMenus() {
    // Closes every dropdown (File menu, export sublist, ribbon caret menus).
    document.querySelectorAll(".menu-list").forEach((m) => { m.hidden = true; });
    document.querySelectorAll("[aria-haspopup='true']").forEach((t) => t.setAttribute("aria-expanded", "false"));
    const ucp = document.getElementById("user-chip-panel");
    if (ucp) ucp.hidden = true;
  }

  // Generic ribbon caret menus: trigger discloses, items dispatch commands
  // through the same runCommand pipeline as the toolbar buttons.
  document.querySelectorAll(".rb-menu").forEach((holder) => {
    const trig = holder.querySelector(".menu-trigger");
    const list = holder.querySelector(".menu-list");
    if (!trig || !list) return;
    // Keep the editor's live selection active: without this, the mousedown
    // moves focus off the contenteditable and the menu item's command runs
    // on a collapsed (empty) selection.
    trig.addEventListener("mousedown", (ev) => ev.preventDefault());
    trig.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = list.hidden;
      closeAllMenus();
      if (willOpen) setMenu(list, trig, true);
    });
    list.querySelectorAll("button[data-cmd]").forEach((item) => {
      item.addEventListener("click", () => {
        closeAllMenus();
        runCommand(item.dataset.cmd, item.dataset.value || null);
      });
    });
    // Color menu items (F-125/F-126/F-127): open the matching toolbar
    // color input's native picker — a real user gesture, so showPicker()
    // is allowed; the input's change handler (above) then emits the
    // foreColor/hiliteColor/backColor command.
    list.querySelectorAll("button[data-picker]").forEach((item) => {
      item.addEventListener("click", () => {
        closeAllMenus();
        const input = document.getElementById(item.dataset.picker);
        if (input && input.showPicker) input.showPicker();
      });
    });
  });

  if (fileTrigger && fileMenu) {
    // Toggle the File menu; stopPropagation so the document click handler
    // doesn't immediately close it again on the same click.
    fileTrigger.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = fileMenu.hidden;
      closeAllMenus();
      if (willOpen) setMenu(fileMenu, fileTrigger, true);
    });
  }
  if (exportTrigger && exportSub) {
    // Hover reveals the export submenu; click toggles it (keyboard path).
    exportTrigger.addEventListener("mouseenter", () => {
      if (!fileMenu.hidden) setMenu(exportSub, exportTrigger, true);
    });
    exportTrigger.addEventListener("click", (ev) => {
      ev.stopPropagation();
      setMenu(exportSub, exportTrigger, exportSub.hidden);
    });
  }
  // Dismiss on any outside click or Escape.
  document.addEventListener("click", closeAllMenus);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeAllMenus();
  });

  const btnNew = document.getElementById("btn-new");
  const btnOpen = document.getElementById("btn-open");
  const btnPrint = document.getElementById("btn-print");
  if (btnNew) btnNew.addEventListener("click", () => { closeAllMenus(); doNewDocument(); });
  if (btnOpen) btnOpen.addEventListener("click", () => { closeAllMenus(); doOpen(); });
  if (btnPrint) btnPrint.addEventListener("click", () => { closeAllMenus(); doPrint(); });
  if (exportSub) {
    exportSub.querySelectorAll("button[data-export]").forEach((b) => {
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeAllMenus();
        doExport(b.dataset.export);
      });
    });
  }

  // Full-toolbar controls: font size/family selects, text/highlight color
  // pickers and the line-spacing select all emit through the wo-command
  // event bus, so they share the exact same runCommand() code path as the
  // buttons above. Color pickers fire on "change" (picker closed), not
  // "input", so dragging inside the picker does not spam undo steps.
  const fontSizeSel = document.getElementById("font-size");
  if (fontSizeSel) fontSizeSel.addEventListener("change", () => {
    if (READ_ONLY || !fontSizeSel.value) return;
    emitCommand("fontSize", fontSizeSel.value);
  });
  const fontFamilySel = document.getElementById("font-family");
  if (fontFamilySel) fontFamilySel.addEventListener("change", () => {
    if (READ_ONLY || !fontFamilySel.value) return;
    emitCommand("fontName", fontFamilySel.value);
  });
  // Quick-access duplicates in the app row: same commands, mirrored state.
  const fontSizeQa = document.getElementById("font-size-qa");
  if (fontSizeQa) fontSizeQa.addEventListener("change", () => {
    if (READ_ONLY || !fontSizeQa.value) return;
    emitCommand("fontSize", fontSizeQa.value);
  });
  const fontFamilyQa = document.getElementById("font-family-qa");
  if (fontFamilyQa) fontFamilyQa.addEventListener("change", () => {
    if (READ_ONLY || !fontFamilyQa.value) return;
    emitCommand("fontName", fontFamilyQa.value);
  });
  const textColor = document.getElementById("text-color");
  if (textColor) textColor.addEventListener("change", () => {
    if (READ_ONLY) return;
    emitCommand("foreColor", textColor.value);
  });
  const highlightColor = document.getElementById("highlight-color");
  if (highlightColor) highlightColor.addEventListener("change", () => {
    if (READ_ONLY) return;
    emitCommand("hiliteColor", highlightColor.value);
  });
  const shadingColor = document.getElementById("shading-color");
  if (shadingColor) shadingColor.addEventListener("change", () => {
    if (READ_ONLY) return;
    emitCommand("backColor", shadingColor.value);
  });
  const lineSpacingSel = document.getElementById("line-spacing");
  if (lineSpacingSel) lineSpacingSel.addEventListener("change", () => {
    if (READ_ONLY) return;
    emitCommand("lineHeight", lineSpacingSel.value);
    lineSpacingSel.value = ""; // next updateActiveStates() re-reflects it
  });

  // Find-and-replace dialog controls
  const findClose = document.getElementById("btn-find-close");
  const findReplaceBtn = document.getElementById("btn-find-replace");
  const findReplaceAllBtn = document.getElementById("btn-find-replace-all");
  const findQueryInput = document.getElementById("find-query");
  const findReplaceInput = document.getElementById("find-replace");
  const findCaseInput = document.getElementById("find-match-case");
  const btnFindNext = document.getElementById("btn-find-next");
  const btnFindPrev = document.getElementById("btn-find-prev");
  if (findClose) findClose.addEventListener("click", closeFindDialog);
  if (findReplaceBtn) findReplaceBtn.addEventListener("click", doReplace);
  if (findReplaceAllBtn) findReplaceAllBtn.addEventListener("click", doReplaceAll);
  if (findQueryInput) findQueryInput.addEventListener("input", onFindInput);
  if (findQueryInput) findQueryInput.addEventListener("keydown", onFindQueryKeydown);
  if (findReplaceInput) findReplaceInput.addEventListener("keydown", onFindReplaceKeydown);
  if (findCaseInput) findCaseInput.addEventListener("change", () => performSearch({ forward: true, relative: false }));
  if (btnFindNext) btnFindNext.addEventListener("click", () => findNav(true));
  if (btnFindPrev) btnFindPrev.addEventListener("click", () => findNav(false));

  // Ctrl/Cmd+F opens find & replace; F3 / Shift+F3 steps next / previous
  // (classic word-processor shortcuts, also with the dialog closed).
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === "f") {
      ev.preventDefault();
      openFindDialog();
      return;
    }
    if (ev.key === "F3") {
      ev.preventDefault();
      const dialog = document.getElementById("find-dialog");
      const wasOpen = dialog && dialog.classList.contains("open");
      openFindDialog();
      if (wasOpen) findNav(!ev.shiftKey);
      return;
    }
  });

  // The user moved the caret in the editor: drop the match-derived anchor so
  // the next search picks up from the new caret position. The highlight the
  // find dialog sets is excluded via the updatingFindSelection flag.
  document.addEventListener("selectionchange", () => {
    if (updatingFindSelection) return;
    if (findState.anchorPos && !selectionEqualsCurrentMatch()) findState.anchorPos = null;
  });

  // Insert-image dialog controls
  const imageFile = document.getElementById("image-file");
  const btnImageOk = document.getElementById("btn-image-ok");
  const btnImageCancel = document.getElementById("btn-image-cancel");
  if (imageFile) imageFile.addEventListener("change", onImageFileChange);
  if (btnImageOk) btnImageOk.addEventListener("click", confirmImageDialog);
  if (btnImageCancel) btnImageCancel.addEventListener("click", closeImageDialog);

  // Insert-table dialog controls
  const btnTableOk = document.getElementById("btn-table-ok");
  const btnTableCancel = document.getElementById("btn-table-cancel");
  if (btnTableOk) btnTableOk.addEventListener("click", confirmTableDialog);
  if (btnTableCancel) btnTableCancel.addEventListener("click", closeTableDialog);
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const findDialog = document.getElementById("find-dialog");
    if (findDialog && findDialog.classList.contains("open")) {
      closeFindDialog();
      return;
    }
    const tableDialog = document.getElementById("table-dialog");
    if (tableDialog && tableDialog.classList.contains("open")) {
      closeTableDialog();
      return;
    }
    const imageDialog = document.getElementById("image-dialog");
    if (imageDialog && imageDialog.classList.contains("open")) closeImageDialog();
  }, true);

  // Anchored insert popovers are non-modal: an outside mousedown dismisses
  // them (same feel as OO's dropdowns), restoring the saved selection/focus.
  document.addEventListener("mousedown", (ev) => {
    const td = document.getElementById("table-dialog");
    if (td && td.classList.contains("open") && !td.contains(ev.target)) closeTableDialog();
    const id = document.getElementById("image-dialog");
    if (id && id.classList.contains("open") && !id.contains(ev.target)) closeImageDialog();
  });

  // ------------------------------------------------------------------
  // Keyboard shortcuts
  // ------------------------------------------------------------------
  editor.addEventListener("keydown", (ev) => {
    // Headings: Ctrl+Alt+1/2/3 -> H1/H2/H3, Ctrl+Alt+0 -> normal paragraph
    // (Word / LibreOffice / Google Docs convention). Only plain digit keys
    // match, so layouts where Shift+digit yields a symbol are unaffected.
    if ((ev.ctrlKey || ev.metaKey) && ev.altKey && /^[0-3]$/.test(ev.key)) {
      ev.preventDefault();
      emitCommand("formatBlock", ev.key === "0" ? "P" : "H" + ev.key);
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey) {
      const k = ev.key.toLowerCase();
      if (k === "s") {
        ev.preventDefault();
        saveDocument();
        return;
      }
      // Undo: Ctrl+Z. Redo: Ctrl+Y (Windows/Linux) or Ctrl+Shift+Z (macOS).
      // Routed through runCommand() so button states and the dirty/autosave
      // status stay consistent with the toolbar.
      if (k === "z") {
        ev.preventDefault();
        runCommand(ev.shiftKey ? "redo" : "undo");
        return;
      }
      if (k === "y") {
        ev.preventDefault();
        runCommand("redo");
        return;
      }
      // Lists: Ctrl+Shift+7 = ordered, Ctrl+Shift+8 = bulleted
      // (Google Docs / LibreOffice convention). Match on ev.code so the
      // digits resolve independently of keyboard layout, where Shift+digit
      // would report a symbol in ev.key instead of the numeral.
      if (ev.shiftKey && (ev.code === "Digit7" || ev.code === "Digit8")) {
        ev.preventDefault();
        emitCommand(ev.code === "Digit8" ? "insertUnorderedList" : "insertOrderedList");
        return;
      }
      // Paragraph alignment (Word / LibreOffice convention): Ctrl+E center,
      // Ctrl+J justify, Ctrl+Shift+L left, Ctrl+R right. Ctrl+R overrides
      // browser reload while focus is inside the editor — exactly what
      // desktop word processors do; click outside the editor to reload.
      if (k === "e") {
        ev.preventDefault();
        emitCommand("justifyCenter");
        return;
      }
      if (k === "j") {
        ev.preventDefault();
        emitCommand("justifyFull");
        return;
      }
      if (k === "l" && ev.shiftKey) {
        ev.preventDefault();
        emitCommand("justifyLeft");
        return;
      }
      if (k === "r" && !ev.shiftKey) {
        ev.preventDefault();
        emitCommand("justifyRight");
        return;
      }
      if (k === "b") {
        // Bold/italic/underline route through the wo-command bus (project
        // invariant) instead of the browser's native shortcut handling, so
        // the editor's execCommand path records history/active states
        // (aria-pressed included) exactly like the toolbar buttons do.
        ev.preventDefault();
        emitCommand("bold");
        return;
      }
      if (k === "i") {
        ev.preventDefault();
        emitCommand("italic");
        return;
      }
      if (k === "u") {
        ev.preventDefault();
        emitCommand("underline");
        return;
      }
    }
    // Tab / Shift+Tab inside a list item indent / outdent the item (Word /
    // LibreOffice convention). execCommand("indent"/"outdent") nests or
    // un-nests the <li> in a native, undoable edit, and both converters
    // round-trip the resulting nested <ul>/<ol> as "List Bullet/Number 2".
    // Elsewhere Tab keeps its default focus-navigation behaviour (WCAG).
    if (ev.key === "Tab" && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      const sel = window.getSelection();
      const nd = sel && sel.anchorNode;
      const el = nd && nd.nodeType === 1 ? nd : (nd && nd.parentElement);
      if (el && el.closest && el.closest("li")) {
        if (READ_ONLY) return;
        ev.preventDefault();
        try {
          document.execCommand(ev.shiftKey ? "outdent" : "indent");
          markDirty();
          captureHistory();
          scheduleCollabSync();
          notifyHost("editing");
        } catch (err) {
          /* fall back to default */
        }
        return;
      }
    }
  });

  // ------------------------------------------------------------------
  // Smart lists: convert markdown-style markers typed at the start of a
  // paragraph ("- ", "* " or "1. "/"1) ") into a real list. Runs after
  // the input event that appends the trailing space, rewinds to before the
  // marker and lets the native list command do the wrapping, so the whole
  // conversion is a single undoable step.
  // ------------------------------------------------------------------
  function autoConvertListMarker() {
    if (READ_ONLY) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return;
    const textNode = sel.anchorNode;
    // Only respond when the caret sits in a plain text node (typing, not
    // caret navigation with a node-level selection).
    if (!textNode || textNode.nodeType !== 3) return;
    const block = textNode.parentNode;
    if (!block || block.nodeType !== 1 || block.tagName !== "P") return;
    // The marker must be the very first thing in the paragraph and the
    // paragraph must be a free-standing body block (not inside a list or
    // table cell, where the native list commands misbehave).
    if (textNode !== block.firstChild) return;
    if (block.closest("ul,ol,td,th")) return;
    const text = textNode.textContent || "";
    let command = null;
    let marker = null;
    if (/^[-*]\s$/.test(text)) {
      command = "insertUnorderedList";
      marker = text;
    } else {
      const m = /^(\d+)[.)]\s$/.exec(text);
      if (m) {
        command = "insertOrderedList";
        marker = m[0];
      }
    }
    if (!command) return;
    // Rewind to before the marker, delete it and collapse the caret at the
    // start of the (now empty) paragraph before the list command runs.
    const range = document.createRange();
    range.setStart(textNode, 0);
    range.setEnd(textNode, marker.length);
    range.deleteContents();
    const caretRange = document.createRange();
    caretRange.setStart(block, 0);
    caretRange.collapse(true);
    sel.removeAllRanges();
    sel.addRange(caretRange);
    emitCommand(command);
  }

  document.addEventListener("selectionchange", updateActiveStates);

  // ------------------------------------------------------------------
  // Autosave every 30 s of inactivity
  // ------------------------------------------------------------------
  let saveTimer = null;
  function markDirty() {
    setStatus(t("Status.Unsaved"));
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDocument, 30000);
    // Structural edits (insertHeader/Footer, notes) mutate the DOM without
    // an input event, so this — not the input handler — is the single
    // place that arms the collab poll's active-typist guard.
    lastLocalEdit = Date.now();
  }
  editor.addEventListener("input", () => {
    // Replace-all runs a batch of execCommand edits; the whole batch is
    // captured as ONE history step after the loop (see doReplaceAll), so
    // suppress the per-step capture here while it runs.
    if (bulkEdit) return;
    markDirty();
    autoConvertListMarker();
    // Snapshot AFTER the DOM settled so the whole smart-list conversion
    // (marker + native wrap) lands as a single undoable step.
    captureHistory();
    updateUndoRedoState();
    // Any edit invalidates the cached find matches (node refs went stale);
    // a replace re-searches right after, a plain edit just resets the
    // counter until the user searches again.
    invalidateFindState();
    scheduleCollabSync();
    notifyHost("editing");
    updateCounts();
  });

  // Release the WOPI lock on the remote host when the editor is closed
  // (client mode). Best effort: sendBeacon survives navigation/unload.
  window.addEventListener("beforeunload", () => {
    if (typeof navigator.sendBeacon === "function") {
      navigator.sendBeacon(api("unlock"), "");
    }
    notifyHost("closed");
    leavePresence();
  });

  // ------------------------------------------------------------------
  // Real-time collaboration + WOPI host PostMessage bridge
  // ------------------------------------------------------------------
  // Collaboration runs on a server-side character CRDT. The browser only
  // ships its plain-text content (debounced) and applies remote updates
  // pushed over an SSE stream. Rich formatting is preserved locally; the
  // converged plain text is what all editors agree on.
  const CLIENT_ID = "c-" + Math.random().toString(36).slice(2, 10);
  const COLLAB_ENABLED = window.__COLLAB__ !== false;
  let collabTimer = null;
  // Last server text this editor agreed with + op-log length at that point
  // (pollCollab uses them to decide which state deltas are agent edits).
  let lastServerText = null;
  let lastSeenOpCount = -1;
  let pendingRemoteText = null;
  let syncPill = null;
  let lastLocalEdit = 0; // timestamp of the user's last keystroke

  // --- WOPI host PostMessage bridge ---------------------------------
  // The editor is embedded in OpenCloud/Nextcloud via an <iframe>. It tells
  // the host about save/edit/close so the host UI can reflect editing state.
  function notifyHost(action, extra) {
    const msg = Object.assign(
      { type: "woopi", action: action, docId: DOC_ID, session: SESSION, name: DOC_NAME },
      extra || {}
    );
    try { if (window.parent && window.parent !== window) window.parent.postMessage(msg, "*"); } catch (e) {}
    try { if (window.opener) window.opener.postMessage(msg, "*"); } catch (e) {}
  }

  // Host -> editor messages (OpenCloud/Nextcloud WOPI postMessage protocol).
  window.addEventListener("message", (ev) => {
    const d = ev.data;
    if (!d || typeof d !== "object") return;
    if (d.MessageId === "Close" || d.action === "close") {
      saveDocument();
      notifyHost("closed");
    } else if (d.MessageId === "GetDocumentProperty") {
      try {
        const src = ev.source || (window.parent && window.parent !== window ? window.parent : null);
        if (src) src.postMessage({
          type: "woopi", MessageId: "GetDocumentProperty",
          id: d.id, docId: DOC_ID, value: DOC_NAME,
        }, ev.origin || "*");
      } catch (e) {}
    }
  });

  // --- presence badge -----------------------------------------------
  const collabBadge = document.createElement("span");
  collabBadge.id = "collab-badge";
  collabBadge.style.cssText = "margin-left:8px;font-size:12px;color:#22c55e;";
  if (status && status.parentNode) status.parentNode.insertBefore(collabBadge, status);
  // --- presence: peer chips + remote carets -----------------------
  // Stable colour per client id so each collaborator keeps a consistent hue
  // across caret, chip and label.
  function peerColor(id) {
    let h = 0;
    for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 360;
    return "hsl(" + h + ",65%,55%)";
  }
  let collabOverlay = null;
  let lastClients = [];
  function ensureOverlay() {
    if (collabOverlay) return collabOverlay;
    const host = editor.parentElement; // <main>, position:relative
    collabOverlay = document.createElement("div");
    collabOverlay.id = "collab-overlay";
    collabOverlay.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:5;overflow:hidden;";
    host.appendChild(collabOverlay);
    return collabOverlay;
  }
  function caretRectForIndex(index) {
    let remaining = index, node = null, pos = 0;
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null);
    while (walker.nextNode()) {
      const len = walker.currentNode.textContent.length;
      if (remaining <= len) { node = walker.currentNode; pos = remaining; break; }
      remaining -= len;
    }
    const r = document.createRange();
    if (node) r.setStart(node, Math.min(pos, node.textContent.length));
    else { r.selectNodeContents(editor); r.collapse(false); }
    r.collapse(true);
    const rect = r.getClientRects()[0] || (node ? node.getBoundingClientRect() : r.getBoundingClientRect());
    const base = editor.parentElement.getBoundingClientRect();
    return { left: rect.left - base.left, top: rect.top - base.top, height: rect.height || 18 };
  }
  function renderRemoteCaret(client, color, overlay) {
    let marker = overlay.querySelector('[data-peer="' + client.client + '"]');
    if (!marker) {
      marker = document.createElement("div");
      marker.className = "remote-caret";
      marker.dataset.peer = client.client;
      const tag = document.createElement("span");
      tag.className = "remote-caret-label";
      marker.appendChild(tag);
      overlay.appendChild(marker);
    }
    const user = client.user || client.client;
    marker.querySelector(".remote-caret-label").textContent = user;
    marker.style.borderColor = color;
    marker.querySelector(".remote-caret-label").style.background = color;
    const cur = client.cursor;
    if (cur && typeof cur === "object" && typeof cur.index === "number") {
      const rc = caretRectForIndex(cur.index);
      marker.style.display = "";
      marker.style.left = rc.left + "px";
      marker.style.top = rc.top + "px";
      marker.style.height = rc.height + "px";
    } else {
      marker.style.display = "none";
    }
  }
  function renderPresence(clients) {
    const list = clients || [];
    lastClients = list;
    const n = list.length;
    collabBadge.textContent = n ? "● " + n + " editing" : "";
    let panel = document.getElementById("collab-peers");
    if (!panel) {
      panel = document.createElement("span");
      panel.id = "collab-peers";
      panel.className = "collab-peers";
      if (status && status.parentNode) status.parentNode.insertBefore(panel, status);
    }
    panel.innerHTML = "";
    const overlay = ensureOverlay();
    overlay.innerHTML = "";
    list.forEach((c) => {
      const color = peerColor(c.client);
      const chip = document.createElement("span");
      chip.className = "peer-chip";
      chip.style.background = color;
      const label = (c.user || c.client).slice(0, 2).toUpperCase();
      chip.textContent = label + (c.client === CLIENT_ID ? "\u2022" : "");
      chip.title = (c.user || c.client) + (c.client === CLIENT_ID ? " (you)" : "");
      panel.appendChild(chip);
      if (c.client !== CLIENT_ID) renderRemoteCaret(c, color, overlay);
    });
  }
  // Keep remote carets aligned when the surface scrolls or reshapes.
  editor.addEventListener("scroll", () => renderPresence(lastClients));
  window.addEventListener("resize", () => renderPresence(lastClients));

  // --- plain-text helpers (collab is character-CRDT on plain text) -
  function editorPlainText() { return editor.innerText || ""; }
  // Collab text = visible text minus tracked deletions (they stay in the DOM
  // as redlines but are logically deleted; shipping them back through the
  // plain-text CRDT would resurrect them server-side). Un-tracked documents
  // return innerText unchanged (byte-identical to the pre-track behavior).
  // ponytail: first-occurrence subtraction — a del span whose exact text also
  // appears earlier in the doc subtracts the wrong copy; switch to offset
  // arithmetic if that ever matters.
  function collabText() {
    const text = editor.innerText || "";
    const dels = editor.querySelectorAll("del.track-delete");
    if (!dels.length) return text;
    let out = text;
    for (const d of dels) {
      const t = d.innerText || d.textContent || "";
      if (t) out = out.replace(t, "");
    }
    return out;
  }
  // Map logical text offsets (tracked-deletion content excluded) to a DOM
  // Range inside the editor. Returns a collapsed range for start === end.
  function logicalRange(start, end) {
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        return p && p.closest("del.track-delete") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    let pos = 0, startNode = null, startOff = 0, endNode = null, endOff = 0;
    while (walker.nextNode()) {
      const len = walker.currentNode.data.length;
      if (!startNode && pos + len >= start) { startNode = walker.currentNode; startOff = start - pos; }
      if (startNode && pos + len >= end) { endNode = walker.currentNode; endOff = end - pos; break; }
      pos += len;
    }
    if (!startNode) return null;
    const r = document.createRange();
    if (!endNode) { r.setStart(startNode, Math.min(startOff, startNode.data.length)); r.collapse(true); return r; }
    r.setStart(startNode, startOff);
    r.setEnd(endNode, endOff);
    return r;
  }
  // Project one server-side agent edit (a prefix/suffix diff between the
  // previously-agreed text and the converged CRDT text) as tracked changes:
  // deletions wrap in del.track-delete, insertions land as ins.track-insert.
  // Returns false (caller falls back to plain convergence) when the range
  // arithmetic cannot represent the diff.
  function applyTrackedDiff(base, next) {
    let i = 0;
    const maxI = Math.min(base.length, next.length);
    while (i < maxI && base[i] === next[i]) i++;
    let j = 0;
    while (j < base.length - i && j < next.length - i && base[base.length - 1 - j] === next[next.length - 1 - j]) j++;
    const delLen = base.length - j - i;
    const insText = next.slice(i, next.length - j);
    try {
      if (delLen > 0) {
        const r = logicalRange(i, i + delLen);
        if (!r) return false;
        wrapRangeInDel(r);
      }
      if (insText) {
        const r = logicalRange(i, i);
        if (!r) return false;
        const node = document.createElement("ins");
        node.className = "track-insert";
        node.setAttribute("data-author", "AI");
        node.textContent = insText;
        r.insertNode(node);
      }
    } catch (e) { return false; }
    // Proposals surface in the existing review-changes flow (accept/reject
    // per change) — the same spans a human tracked edit produces.
    openReviewPanel();
    afterTrackEdit();
    return true;
  }
  // Live word/character count for the status bar. Words are whitespace-
  // delimited runs; CJK/ligatures are approximated by character count too.
  function updateCounts() {
    const text = (editor.innerText || "").trim();
    const words = text ? text.split(/\s+/).filter(Boolean).length : 0;
    const chars = (editor.innerText || "").replace(/\n/g, "").length;
    const el = document.getElementById("word-count");
    if (el) el.textContent = words + " words · " + chars + " characters";
  }
  function caretOffset(el) {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return 0;
    const range = sel.getRangeAt(0);
    const pre = range.cloneRange();
    pre.selectNodeContents(el);
    pre.setEnd(range.endContainer, range.endOffset);
    return pre.toString().length;
  }
  function setCaretOffset(el, offset) {
    let remaining = offset, node = null, pos = 0;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    while (walker.nextNode()) {
      const len = walker.currentNode.textContent.length;
      if (remaining <= len) { node = walker.currentNode; pos = remaining; break; }
      remaining -= len;
    }
    if (!node) { el.focus(); return; }
    const r = document.createRange();
    r.setStart(node, Math.min(pos, node.textContent.length));
    r.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
  }
  function showSyncPill() {
    if (syncPill) { syncPill.style.display = ""; return; }
    syncPill = document.createElement("button");
    syncPill.textContent = "↓ remote changes — click to sync";
    syncPill.style.cssText =
      "position:fixed;right:12px;bottom:12px;z-index:50;padding:6px 10px;" +
      "background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;";
    syncPill.addEventListener("click", () => {
      if (pendingRemoteText != null) applyRemoteText(pendingRemoteText);
      pendingRemoteText = null;
      syncPill.style.display = "none";
    });
    document.body.appendChild(syncPill);
  }
  // Returns true when the editor now reflects `text` (applied or already
  // equal), false when the update was deferred (open dialog / active typist)
  // so the caller can retry on a later poll instead of treating it as done.
  function applyRemoteText(text) {
    const current = editorPlainText();
    if (current === text) return true;
    // The collab layer is a plain-text CRDT: tables, images, links and
    // formatting spans cannot be represented, and their contribution to the
    // plain-text projection is (nearly) all whitespace. Without this guard,
    // the poll would "converge" the editor back to the server baseline
    // (which predates any structural edit) and erase the table/image/link
    // via innerText=. Treat whitespace-equal projections as no real change
    // so structural content is never destroyed; genuine character edits
    // still differ and converge normally.
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    if (norm(current) === norm(text)) return true;
    // Never clobber an open modal (find/table/image dialog): leave the editor
    // untouched and converge on the next tick after the dialog closes.
    if (getOpenDialog()) return false;
    // Never clobber a user who is actively typing. Once they go idle (even if
    // the editor stays focused) remote edits converge automatically. Focus
    // inside a child (e.g. an inserted page header/footer) counts too —
    // otherwise the 0-300ms window before our own collabSync lands would let
    // the poll flatten the just-inserted structural element into plain text.
    const activelyTyping =
      editor.contains(document.activeElement) && Date.now() - lastLocalEdit < 1500;
    if (activelyTyping) {
      pendingRemoteText = text;
      showSyncPill();
      return false;
    }
    const wasFocused = document.activeElement === editor;
    const offset = caretOffset(editor);
    editor.innerText = text;
    // Only (re)place the caret if the editor was already focused, so the poll
    // never yanks focus away from an unrelated control (e.g. a modal dialog).
    if (wasFocused) {
      try { setCaretOffset(editor, Math.min(offset, text.length)); } catch (e) {}
    }
    captureHistory();
    updateUndoRedoState();
    return true;
  }
  // --- collab sync (debounced) -------------------------------------
  function collabSync() {
    if (!COLLAB_ENABLED) return;
    const text = collabText();
    fetch(api("collab/sync"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: CLIENT_ID, text: text }),
    }).catch(() => {});
  }
  function scheduleCollabSync() {
    if (!COLLAB_ENABLED) return;
    clearTimeout(collabTimer);
    collabTimer = setTimeout(collabSync, 300);
  }

  // --- presence announce / leave -----------------------------------
  function announcePresence(cursor) {
    if (!COLLAB_ENABLED) return;
    fetch(api("collab/presence"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: CLIENT_ID, user: "Editor", cursor: cursor || { index: 0 } }),
    })
      .then((r) => r.json())
      .then((d) => renderPresence(d && d.clients))
      .catch(() => {});
  }
  function leavePresence() {
    if (!COLLAB_ENABLED) return;
    fetch(api("collab/presence"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: CLIENT_ID, cursor: null }),
    }).catch(() => {});
  }

  // --- live collaboration: poll the hub for changes -----------------
  // The browser polls the converged document state. Polling is used over an
  // SSE EventSource for robustness across embedded/headless contexts: a single
  // GET every ~400ms keeps every editor convergent with low latency and zero
  // connection-state fragility. The SSE endpoint remains available for clients
  // that prefer push. applyRemoteText() is idempotent (equal text is a no-op)
  // and never clobbers the active typist (it is skipped while the editor is
  // focused), so re-applying on every tick is safe.
  async function pollCollab() {
    if (!COLLAB_ENABLED) return;
    try {
      const res = await fetch(api("collab/state"));
      const data = await res.json();
      if (data && typeof data.text === "string") {
        // Agent edits (site agent=*) converge as tracked-change spans so the
        // human reviews them in the review-changes flow; human remote edits
        // converge plainly (existing applyRemoteText semantics).
        if (lastServerText === null) lastServerText = collabText();
        if (data.text !== lastServerText) {
          const ops = Array.isArray(data.ops) ? data.ops : [];
          const newOps = lastSeenOpCount < 0 ? [] : ops.slice(Math.max(0, lastSeenOpCount));
          const agentEdits = newOps.some((op) => op && typeof op.s === "string" && op.s.indexOf("agent=") === 0);
          if (agentEdits && !getOpenDialog()) {
            if (applyTrackedDiff(lastServerText, data.text)) lastServerText = data.text;
            // false: fall through to plain convergence on the next tick
          } else if (applyRemoteText(data.text)) {
            // only mark the text as agreed when it actually applied; a
            // deferred update (open dialog / active typist) must retry on
            // the next poll or remote edits would hang behind the sync pill
            lastServerText = data.text;
          }
        }
        const opsAll = Array.isArray(data.ops) ? data.ops : [];
        lastSeenOpCount = opsAll.length;
      }
      // Presence is refreshed from the polled state (the browser uses polling
      // rather than the SSE stream, so peer join/leave must be re-read here).
      if (data && Array.isArray(data.clients)) renderPresence(data.clients);
    } catch (e) {}
    setTimeout(pollCollab, 400);
  }

  pollCollab();
  announcePresence();

  // --- photo editor plugin: canvas filter dialog (view-only UI) ------
  // Decodes a locally picked image into a canvas and previews CSS canvas
  // filters: brightness/contrast/saturation sliders plus one-shot presets
  // (grayscale/sepia/invert/blur), reset clears them, close dismisses.
  // View-only — like #nav-panel it never touches document content or the
  // converters (no markers, no round-trip).
  const photoEditorDialog = document.getElementById("photo-editor-dialog");
  const photoEditorCanvas = document.getElementById("photo-editor-canvas");
  const photoEditorFile = document.getElementById("photo-editor-file");
  const photoEditorEmpty = document.getElementById("photo-editor-empty");
  const photoEditorControls = document.getElementById("photo-editor-controls");
  const photoEditorBrightness = document.getElementById("photo-editor-brightness");
  const photoEditorContrast = document.getElementById("photo-editor-contrast");
  const photoEditorSaturate = document.getElementById("photo-editor-saturate");
  let photoEditorImage = null; // loaded <img> the canvas is drawn from

  function openPhotoEditorDialog() {
    if (!photoEditorDialog) return;
    photoEditorResetFilters();
    rememberFocus();
    photoEditorDialog.classList.add("open");
    if (photoEditorFile) photoEditorFile.focus();
  }
  function closePhotoEditorDialog() {
    if (photoEditorDialog) photoEditorDialog.classList.remove("open");
    restoreFocus();
  }
  // Build the canvas filter string from the sliders + selected preset.
  // Sliders at their neutral 100 stay out, so an all-default strip is empty.
  function photoEditorFilterString() {
    const parts = [];
    if (photoEditorBrightness && photoEditorBrightness.value !== "100")
      parts.push("brightness(" + photoEditorBrightness.value + "%)");
    if (photoEditorContrast && photoEditorContrast.value !== "100")
      parts.push("contrast(" + photoEditorContrast.value + "%)");
    if (photoEditorSaturate && photoEditorSaturate.value !== "100")
      parts.push("saturate(" + photoEditorSaturate.value + "%)");
    const preset = document.querySelector('input[name="photo-editor-preset"]:checked');
    if (preset && preset.value) parts.push(preset.value);
    return parts.join(" ");
  }
  // Redraw the loaded image with the current filter string, scaled to fit
  // the fixed 640x400 preview canvas (which the CSS stretches responsively).
  function photoEditorDraw() {
    const ctx = photoEditorCanvas && photoEditorCanvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, photoEditorCanvas.width, photoEditorCanvas.height);
    if (!photoEditorImage) return;
    const scale = Math.min(
      photoEditorCanvas.width / photoEditorImage.naturalWidth,
      photoEditorCanvas.height / photoEditorImage.naturalHeight, 1);
    const w = Math.max(1, Math.round(photoEditorImage.naturalWidth * scale));
    const h = Math.max(1, Math.round(photoEditorImage.naturalHeight * scale));
    const x = Math.round((photoEditorCanvas.width - w) / 2);
    const y = Math.round((photoEditorCanvas.height - h) / 2);
    ctx.filter = photoEditorFilterString();
    ctx.drawImage(photoEditorImage, x, y, w, h);
    ctx.filter = "none";
  }
  function photoEditorResetFilters() {
    if (photoEditorBrightness) photoEditorBrightness.value = "100";
    if (photoEditorContrast) photoEditorContrast.value = "100";
    if (photoEditorSaturate) photoEditorSaturate.value = "100";
    const presets = document.querySelectorAll('input[name="photo-editor-preset"]');
    if (presets.length) presets[0].checked = true;
    photoEditorDraw();
  }
  function onPhotoEditorFileChange() {
    const file = photoEditorFile && photoEditorFile.files && photoEditorFile.files[0];
    if (!file || !/^image\//.test(file.type)) return;
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        photoEditorImage = img;
        if (photoEditorControls) photoEditorControls.hidden = false;
        if (photoEditorEmpty) photoEditorEmpty.hidden = true;
        photoEditorResetFilters();
      };
      img.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  }
  if (photoEditorFile) photoEditorFile.addEventListener("change", onPhotoEditorFileChange);
  [photoEditorBrightness, photoEditorContrast, photoEditorSaturate].forEach((el) => {
    if (el) el.addEventListener("input", photoEditorDraw);
  });
  document.querySelectorAll('input[name="photo-editor-preset"]').forEach((el) => {
    if (el) el.addEventListener("change", photoEditorDraw);
  });
  const photoEditorResetBtn = document.getElementById("btn-photo-editor-reset");
  if (photoEditorResetBtn) photoEditorResetBtn.addEventListener("click", photoEditorResetFilters);
  const photoEditorCloseBtn = document.getElementById("btn-photo-editor-cancel");
  if (photoEditorCloseBtn) photoEditorCloseBtn.addEventListener("click", closePhotoEditorDialog);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && photoEditorDialog && photoEditorDialog.classList.contains("open")) {
      ev.preventDefault();
      closePhotoEditorDialog();
    }
  });

  // --- plugins browser/manager dialogs (real plugin host) -----------
  // /api/plugins returns the installed catalog (ocr, photoeditor). Browse
  // lists it in a dialog; Manage adds a per-plugin enabled toggle persisted
  // per-user in localStorage (a view preference — no document marker).
  const pluginsDialog = document.getElementById("plugins-dialog");
  const pluginsList = document.getElementById("plugins-list");

  function closePluginsDialog() {
    if (pluginsDialog) pluginsDialog.classList.remove("open");
    restoreFocus();
  }

  async function _fetchPlugins() {
    try {
      const resp = await fetch("/api/plugins");
      if (!resp.ok) return [];
      const data = await resp.json();
      return Array.isArray(data.plugins) ? data.plugins : [];
    } catch (err) {
      return [];
    }
  }

  function _renderPluginsDialog(plugins, manage) {
    if (!pluginsList) return;
    pluginsList.innerHTML = "";
    if (!plugins.length) {
      const li = document.createElement("li");
      li.className = "plugins-empty";
      li.textContent = t("Plugins.None") || "No plugins installed";
      pluginsList.appendChild(li);
      return;
    }
    let enabled = {};
    try {
      enabled = JSON.parse(localStorage.getItem("wo.plugins.enabled") || "{}") || {};
    } catch (err) { /* default: all enabled */ }
    for (const p of plugins) {
      const li = document.createElement("li");
      li.className = "plugins-item";
      const head = document.createElement("div");
      head.className = "plugins-head";
      const name = document.createElement("span");
      name.className = "plugins-name";
      name.textContent = p.name || p.id || "?";
      head.appendChild(name);
      if (p.version) {
        const ver = document.createElement("span");
        ver.className = "plugins-version";
        ver.textContent = "v" + p.version;
        head.appendChild(ver);
      }
      li.appendChild(head);
      if (manage) {
        const label = document.createElement("label");
        label.className = "plugins-toggle";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.dataset.plugin = p.id || p.name || "";
        box.checked = enabled[box.dataset.plugin] !== false; // default enabled
        box.addEventListener("change", (ev) => {
          try {
            const map = JSON.parse(localStorage.getItem("wo.plugins.enabled") || "{}") || {};
            map[ev.target.dataset.plugin] = ev.target.checked;
            localStorage.setItem("wo.plugins.enabled", JSON.stringify(map));
          } catch (err) {}
          setStatus(`Plugin ${ev.target.dataset.plugin} ${ev.target.checked ? "enabled" : "disabled"}`);
        });
        label.appendChild(box);
        label.appendChild(document.createTextNode(" enabled"));
        li.appendChild(label);
      }
      if (p.description) {
        const desc = document.createElement("p");
        desc.className = "plugins-desc";
        desc.textContent = p.description;
        li.appendChild(desc);
      }
      pluginsList.appendChild(li);
    }
  }

  async function browsePlugins() {
    if (READ_ONLY) return;
    rememberFocus();
    const plugins = await _fetchPlugins();
    _renderPluginsDialog(plugins, false);
    if (pluginsDialog) pluginsDialog.classList.add("open");
    setStatus(`Plugins: ${plugins.length} installed`);
  }

  async function managePlugins() {
    if (READ_ONLY) return;
    rememberFocus();
    const plugins = await _fetchPlugins();
    _renderPluginsDialog(plugins, true);
    if (pluginsDialog) pluginsDialog.classList.add("open");
    setStatus(`Plugins: managing ${plugins.length} installed`);
  }

  const pluginsCloseBtn = document.getElementById("btn-plugins-close");
  if (pluginsCloseBtn) pluginsCloseBtn.addEventListener("click", closePluginsDialog);

  // ------------------------------------------------------------------
  // Backstage + Insert/View extensions (OO-parity backlog closure).
  // File > Info/Protect/Settings/Help/Suggest; View > Speech (TTS+STT);
  // Insert > Text from File / Mail merge / Add text; statusbar multi-page
  // view; Plugins > background mode. Same .dialog-overlay + setStatus
  // patterns as the rest of the editor.
  // ------------------------------------------------------------------
  function openDlg(id) { const d = document.getElementById(id); if (d) d.classList.add("open"); }
  function closeDlg(id) { const d = document.getElementById(id); if (d) d.classList.remove("open"); restoreFocus(); }
  function _escHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  // File > Protect: reuse the Protection-tab protection dialog.
  const fileProtectBtn = document.getElementById("btn-fileprotect");
  if (fileProtectBtn) fileProtectBtn.addEventListener("click", () => { closeAllMenus(); protectDialog(); });

  // File > Info: live document statistics when opened.
  function docStats() {
    const text = editor.innerText || "";
    const words = (text.trim().match(/\S+/g) || []).length;
    const chars = text.replace(/\s/g, "").length;
    const paras = editor.querySelectorAll("p, li").length;
    const lang = editor.getAttribute("lang") || "en-US";
    const langNames = { "en-US": "English (United States)", "en-GB": "English (United Kingdom)", "de-DE": "Deutsch (Deutschland)" };
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set("info-name", DOC_NAME || "document");
    set("info-path", "WOPI host (OpenCloud)");
    set("info-lang", langNames[lang] || lang);
    set("info-stats", `${words.toLocaleString()} words · ${chars.toLocaleString()} characters · ${paras} paragraphs`);
    set("info-size", `${(editor.innerHTML.length / 1024).toFixed(1)} kB`);
  }
  const btnFileInfo = document.getElementById("btn-fileinfo");
  if (btnFileInfo) btnFileInfo.addEventListener("click", () => { closeAllMenus(); docStats(); openDlg("info-dialog"); });
  const btnInfoClose = document.getElementById("btn-info-close");
  if (btnInfoClose) btnInfoClose.addEventListener("click", () => closeDlg("info-dialog"));

  // File > Settings: theme / spellcheck / zoom, all applied live.
  const btnFileSettings = document.getElementById("btn-filesettings");
  if (btnFileSettings) btnFileSettings.addEventListener("click", () => {
    closeAllMenus();
    const spell = document.getElementById("settings-spellcheck");
    const theme = document.getElementById("settings-theme");
    const zoom = document.getElementById("settings-zoom");
    if (spell) spell.checked = editor.getAttribute("spellcheck") !== "false";
    if (theme) theme.value = document.documentElement.classList.contains("light") ? "light" : "dark";
    if (zoom) zoom.value = String(Math.round((zoomLevel || 1) * 100));
    openDlg("settings-dialog");
  });
  const btnSettingsOk = document.getElementById("btn-settings-ok");
  if (btnSettingsOk) btnSettingsOk.addEventListener("click", () => {
    const spell = document.getElementById("settings-spellcheck");
    const theme = document.getElementById("settings-theme");
    const zoom = document.getElementById("settings-zoom");
    if (spell) editor.setAttribute("spellcheck", spell.checked ? "true" : "false");
    if (theme) document.documentElement.classList.toggle("light", theme.value === "light");
    if (zoom && !Number.isNaN(+zoom.value)) { zoomLevel = Math.min(2, Math.max(0.5, +zoom.value / 100)); applyZoom(); }
    closeDlg("settings-dialog");
    setStatus("Settings applied");
  });
  const btnSettingsCancel = document.getElementById("btn-settings-cancel");
  if (btnSettingsCancel) btnSettingsCancel.addEventListener("click", () => closeDlg("settings-dialog"));

  // File > Help / Suggest.
  const btnFileHelp = document.getElementById("btn-filehelp");
  if (btnFileHelp) btnFileHelp.addEventListener("click", () => { closeAllMenus(); openDlg("help-dialog"); });
  const btnHelpClose = document.getElementById("btn-help-close");
  if (btnHelpClose) btnHelpClose.addEventListener("click", () => closeDlg("help-dialog"));
  const btnFileSuggest = document.getElementById("btn-filesuggest");
  if (btnFileSuggest) btnFileSuggest.addEventListener("click", () => { closeAllMenus(); openDlg("suggest-dialog"); });
  const suggestSend = document.getElementById("btn-suggest-send");
  if (suggestSend) suggestSend.addEventListener("click", () => {
    const feedback = document.getElementById("suggest-text");
    const email = document.getElementById("suggest-email");
    if (feedback && feedback.value.trim()) {
      const subject = encodeURIComponent("[World-Office] Feature suggestion");
      const body = encodeURIComponent(`${feedback.value.trim()}\n\n(from: ${(email && email.value.trim()) || "anonymous"})`);
      window.location.href = `mailto:hello@worldoffice.example?subject=${subject}&body=${body}`;
      setStatus("Suggestion composed in your mail client");
    } else {
      setStatus("Please enter a suggestion first", true);
    }
    closeDlg("suggest-dialog");
  });
  const btnSuggestCancel = document.getElementById("btn-suggest-cancel");
  if (btnSuggestCancel) btnSuggestCancel.addEventListener("click", () => closeDlg("suggest-dialog"));

  // View > Speech: read the selection/paragraph aloud (TTS) or dictate in (STT).
  // Native Web Speech APIs — no network, no server involvement.
  function readAloud() {
    if (!("speechSynthesis" in window)) { setStatus("Text-to-speech not supported in this browser", true); return; }
    const sel = window.getSelection();
    let text = sel && sel.toString().trim();
    if (!text) {
      const node = sel && sel.anchorNode;
      const block = node && node.nodeType === 3 ? node.parentElement : node;
      const p = block && (block.closest("p, li, h1, h2, h3, h4, h5, h6") || block);
      text = (p && p.innerText || "").trim();
    }
    if (!text) { setStatus("Nothing to read — select text or place the cursor in a paragraph", true); return; }
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = editor.getAttribute("lang") || "en-US";
    u.onend = () => setStatus("Finished reading");
    speechSynthesis.speak(u);
    setStatus("Reading aloud…");
  }
  function dictate() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { setStatus("Speech input not supported in this browser", true); return; }
    const rec = new SR();
    rec.lang = editor.getAttribute("lang") || "en-US";
    rec.interimResults = false;
    rec.onresult = (ev) => {
      const text = ev.results[0][0].transcript;
      editor.focus();
      document.execCommand("insertText", false, text);
      setStatus("Dictated");
    };
    rec.onerror = (ev) => setStatus(`Dictation error: ${ev.error}`, true);
    rec.start();
    setStatus("Listening…");
  }

  // Insert > Text from File: txt/md/html/csv read directly; .docx goes through
  // the host converter (POST /api/documents/{id}/import-docx -> HTML).
  const btnTextFile = document.getElementById("btn-textfile");
  const textFileInput = document.getElementById("textfile-input");
  if (btnTextFile && textFileInput) btnTextFile.addEventListener("click", () => textFileInput.click());
  if (textFileInput) textFileInput.addEventListener("change", async () => {
    const file = textFileInput.files && textFileInput.files[0];
    textFileInput.value = "";
    if (!file) return;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    try {
      let html;
      if (ext === "docx") {
        const fd = new FormData();
        fd.append("file", file);
        const resp = await fetch(`/api/documents/${encodeURIComponent(DOC_ID)}/import-docx`, { method: "POST", body: fd });
        if (!resp.ok) throw new Error(((await resp.json()).error) || "conversion failed");
        html = (await resp.json()).html;
      } else {
        const text = await file.text();
        html = (ext === "html" || ext === "htm")
          ? text
          : text.split(/\r?\n/).map((l) => `<p>${_escHtml(l)}</p>`).join("");
      }
      editor.focus();
      document.execCommand("insertHTML", false, html);
      setStatus(`Inserted text from ${file.name}`);
    } catch (err) {
      setStatus(`Text from file failed: ${err.message}`, true);
    }
  });

  // Insert > Mail merge: CSV source (first row = fields) -> «Field» markers in
  // the document -> preview of the merged copies.
  let mailMergeHeaders = [], mailMergeRows = [];
  const btnMailMerge = document.getElementById("btn-mailmerge");
  const mailCsvInput = document.getElementById("mailmerge-csv");
  if (btnMailMerge && mailCsvInput) btnMailMerge.addEventListener("click", () => mailCsvInput.click());
  const btnMailCsv = document.getElementById("btn-mailmerge-csv");
  if (btnMailCsv && mailCsvInput) btnMailCsv.addEventListener("click", () => mailCsvInput.click());
  if (mailCsvInput) mailCsvInput.addEventListener("change", () => {
    const file = mailCsvInput.files && mailCsvInput.files[0];
    mailCsvInput.value = "";
    if (!file) return;
    file.text().then((txt) => {
      const lines = txt.split(/\r?\n/).filter((l) => l.trim() !== "");
      mailMergeHeaders = lines[0].split(",").map((h) => h.trim());
      mailMergeRows = lines.slice(1).map((l) => {
        const cells = l.split(",");
        const row = {};
        mailMergeHeaders.forEach((h, i) => { row[h] = (cells[i] || "").trim(); });
        return row;
      });
      const sel = document.getElementById("mailmerge-fields");
      if (sel) {
        sel.innerHTML = "";
        mailMergeHeaders.forEach((h) => {
          const o = document.createElement("option");
          o.value = h; o.textContent = h; sel.appendChild(o);
        });
      }
      const prev = document.getElementById("mailmerge-preview");
      if (prev) prev.textContent = `${mailMergeRows.length} record(s), ${mailMergeHeaders.length} field(s). Insert fields, then preview merged copies.`;
      setStatus(`Mail merge source: ${mailMergeRows.length} records`);
      openDlg("mailmerge-dialog");
    });
  });
  const btnMailField = document.getElementById("btn-mailmerge-field");
  if (btnMailField) btnMailField.addEventListener("click", () => {
    const sel = document.getElementById("mailmerge-fields");
    if (!sel || !sel.value) { setStatus("Choose a CSV source first", true); return; }
    editor.focus();
    document.execCommand("insertText", false, `«${sel.value}»`);
    closeDlg("mailmerge-dialog");
  });
  const btnMailPreview = document.getElementById("btn-mailmerge-preview");
  if (btnMailPreview) btnMailPreview.addEventListener("click", () => {
    if (!mailMergeRows.length) { setStatus("Choose a CSV source first", true); return; }
    const base = editor.innerText;
    const out = mailMergeRows.map((row) => {
      let merged = base;
      for (const h of mailMergeHeaders) merged = merged.split(`«${h}»`).join(row[h] || "");
      return merged;
    });
    const prev = document.getElementById("mailmerge-preview");
    if (prev) prev.textContent = out.join("\n\n─── NEXT RECORD ───\n\n");
    setStatus(`Merged ${out.length} copies in the preview`);
  });
  const btnMailClose = document.getElementById("btn-mailmerge-close");
  if (btnMailClose) btnMailClose.addEventListener("click", () => closeDlg("mailmerge-dialog"));

  // Insert > Add text: OO-parity options dialog (level picker) around the
  // selection-mask (view surface only — the marker text stays in the doc).
  const btnAddText = document.getElementById("btn-addtext");
  function confirmAddTextDialog() {
    if (READ_ONLY) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) { setStatus("Select text first", true); return; }
    const already = sel.anchorNode && sel.anchorNode.parentElement
      && (sel.anchorNode.parentElement.closest(".add-text-mask"));
    if (already) {
      const parent = already.parentNode;
      parent.replaceChild(document.createTextNode(already.textContent || ""), already);
      sel.removeAllRanges();
      setStatus("Add-text mask removed");
      return;
    }
    const level = (document.getElementById("addtext-level") || {}).value || "body";
    const wrap = document.createElement("span");
    wrap.className = "add-text-mask";
    wrap.setAttribute("data-addtext", "1");
    if (level !== "body") wrap.setAttribute("data-level", level);
    wrap.title = "Add-text annotation";
    try { sel.getRangeAt(0).surroundContents(wrap); } catch { setStatus("Selection spans a boundary — try a plain text selection", true); return; }
    sel.removeAllRanges();
    setStatus("Text masked as add-text annotation (level " + level + ")");
  }
  if (btnAddText) btnAddText.addEventListener("click", () => { closeAllMenus(); openOverlayDialog("addtext-dialog"); });
  const btnAddTextOk = document.getElementById("btn-addtext-ok");
  if (btnAddTextOk) btnAddTextOk.addEventListener("click", () => { closeOverlayDialog("addtext-dialog"); confirmAddTextDialog(); });

  // Statusbar > multiple pages view: show page-separator gutters in the flow
  // (CSS-only, nothing enters the document).
  const btnMultipage = document.getElementById("btn-multipage");
  if (btnMultipage) btnMultipage.addEventListener("click", () => {
    const on = document.body.classList.toggle("multi-page-view");
    btnMultipage.setAttribute("aria-pressed", String(on));
    setStatus(on ? "Multiple pages view" : "Single page view");
  });

  // Plugins > background mode: run plugins without a foreground panel.
  const btnBgPlugins = document.getElementById("btn-bgplugins");
  if (btnBgPlugins) btnBgPlugins.addEventListener("click", async () => {
    const on = !document.body.classList.contains("bg-plugins");
    document.body.classList.toggle("bg-plugins", on);
    btnBgPlugins.setAttribute("aria-pressed", String(on));
    try {
      const plugins = await _fetchPlugins();
      setStatus(on ? `Background plugins on (${plugins.length} installed)` : "Background plugins off");
    } catch { setStatus(on ? "Background plugins on" : "Background plugins off"); }
  });

  // ------------------------------------------------------------------
  // Wave G2 — fields, content controls, chart editor, equation, SmartArt,
  // object layout (float) + ink group/merge (OO-parity backlog closure).
  // ------------------------------------------------------------------
  function _escXml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c]));
  }
  function svgToDataURI(svgMarkup) {
    return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svgMarkup)));
  }
  function insertSVGImage(svgMarkup, widthPx) {
    insertVectorObject(svgMarkup, "object", { width: widthPx || 420 });
  }
  // Insert an SVG as a crisp 2x PNG that stays POST-INSERT EDITABLE.
  // - data-spec (JSON) drives re-editing in-session (double-click -> dialog).
  // - the alt marker __wo-<kind>__<json> survives the DOCX round-trip (alt is
  //   carried on wp:docPr descr), so a reloaded doc rehydrates the spec and
  //   the object stays editable after save/reopen.
  function insertVectorObject(svgMarkup, kind, spec) {
    const url = URL.createObjectURL(new Blob([svgMarkup], { type: "image/svg+xml" }));
    const src = new Image();
    src.onload = () => {
      const scale = 2;
      const c = document.createElement("canvas");
      c.width = Math.max(1, src.naturalWidth) * scale;
      c.height = Math.max(1, src.naturalHeight) * scale;
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(src, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      const img = document.createElement("img");
      img.src = c.toDataURL("image/png");
      img.classList.add("inline-object", "vector-object");
      img.setAttribute("data-kind", kind);
      img.setAttribute("data-spec", JSON.stringify(spec));
      img.style.width = (spec.width || 420) + "px";
      img.alt = `__wo-${kind}__${JSON.stringify(spec)}`;
      editor.focus();
      const sel = window.getSelection();
      if (sel && sel.rangeCount && !sel.isCollapsed) sel.deleteFromDocument();
      document.execCommand("insertHTML", false, img.outerHTML);
    };
    src.onerror = () => { URL.revokeObjectURL(url); setStatus("Could not render object", true); };
    src.src = url;
  }
  // Rehydrate editability: restored docs carry the spec only in the alt
  // marker (data-spec does not survive the DOCX round-trip).
  function hydrateVectorObjects(root) {
    (root || document).querySelectorAll("img[alt^='__wo-']").forEach((img) => {
      if (img.dataset.kind) return;
      const m = (img.alt || "").match(/^__wo-([a-z]+)__(.+)$/);
      if (!m) return;
      try {
        img.classList.add("vector-object");
        img.setAttribute("data-kind", m[1]);
        img.setAttribute("data-spec", m[2]);
      } catch { img.setAttribute("data-kind", m[1]); }
    });
  }
  // Double-click an inserted chart/equation/smartart -> re-open its dialog
  // pre-filled from the stored spec -> re-render in place.
  editor.addEventListener("dblclick", (ev) => {
    const img = ev.target.closest ? ev.target.closest("img.vector-object") : null;
    if (!img || READ_ONLY) return;
    let spec = {};
    try { spec = JSON.parse(img.dataset.spec || "{}"); } catch { spec = {}; }
    const kind = img.dataset.kind;
    if (kind === "chart" || kind === "equation" || kind === "smartart") {
      ev.preventDefault();
      openVectorEditor(kind, spec, img);
    }
  });
  function openVectorEditor(kind, spec, img) {
    if (kind === "chart") {
      openDlg("chart-dialog");
      document.getElementById("chart-type-select").value = spec.type || "bar";
      document.getElementById("chart-title").value = spec.title || "";
      const rows = spec.rows && spec.rows.length ? spec.rows : [["", 0], ["", 0], ["", 0], ["", 0]];
      document.querySelectorAll("#chart-data input").forEach((el, k) => {
        const row = rows[Math.floor(k / 2)];
        el.value = row ? String(row[k % 2]) : "";
      });
      renderChartPreview();
      pendingVectorReload = { kind, img };
    } else if (kind === "equation") {
      openDlg("equation-dialog");
      document.getElementById("equation-input").value = spec.text || "";
      document.getElementById("equation-preview").innerHTML = equationSVG(spec.text || " ");
      pendingVectorReload = { kind, img };
    } else if (kind === "smartart") {
      openDlg("smartart-dialog");
      document.getElementById("smartart-type").value = spec.kind || "process";
      renderSmartArtPreview();
      pendingVectorReload = { kind, img };
    }
  }
  let pendingVectorReload = null;
  function reloadVectorTarget(svgMarkup, kind, spec) {
    if (!pendingVectorReload) return;
    const img = pendingVectorReload.img;
    pendingVectorReload = null;
    const url = URL.createObjectURL(new Blob([svgMarkup], { type: "image/svg+xml" }));
    const src = new Image();
    src.onload = () => {
      const scale = 2;
      const c = document.createElement("canvas");
      c.width = Math.max(1, src.naturalWidth) * scale;
      c.height = Math.max(1, src.naturalHeight) * scale;
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(src, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      img.src = c.toDataURL("image/png");
      img.setAttribute("data-spec", JSON.stringify(spec));
      img.alt = `__wo-${kind}__${JSON.stringify(spec)}`;
      setStatus("Object updated");
    };
    src.onerror = () => { URL.revokeObjectURL(url); };
    src.src = url;
  }

  // -- Insert > Field (OO Insert>Field; PAGE/DATE/TIME/AUTHOR/FILENAME/WORDS).
  const btnField = document.getElementById("btn-field");
  if (btnField) btnField.addEventListener("click", () => { if (READ_ONLY) return; openDlg("field-dialog"); });
  const btnFieldCancel = document.getElementById("btn-field-cancel");
  if (btnFieldCancel) btnFieldCancel.addEventListener("click", () => closeDlg("field-dialog"));
  const btnFieldOk = document.getElementById("btn-field-ok");
  if (btnFieldOk) btnFieldOk.addEventListener("click", () => {
    const typeEl = document.getElementById("field-type");
    if (!typeEl || !typeEl.value) return;
    const type = typeEl.value;
    const now = new Date();
    let value = "";
    switch (type) {
      case "PAGE": value = "1"; break;
      case "DATE": value = now.toLocaleDateString(); break;
      case "TIME": value = now.toLocaleTimeString(); break;
      case "AUTHOR": value = "editor"; break;
      case "FILENAME": value = DOC_NAME || "document"; break;
      case "WORDS": value = String((editor.innerText.trim().match(/\S+/g) || []).length); break;
    }
    const f = document.createElement("span");
    f.className = "field";
    f.setAttribute("data-field", type);
    f.textContent = value;
    editor.focus();
    document.execCommand("insertHTML", false, f.outerHTML);
    closeDlg("field-dialog");
    setStatus(`Inserted ${type} field`);
  });

  // -- Insert > Content control (plain / rich / dropdown marker boxes).
  const btnCc = document.getElementById("btn-contentcontrol");
  if (btnCc) btnCc.addEventListener("click", () => { if (READ_ONLY) return; openDlg("cc-dialog"); });
  const btnCcCancel = document.getElementById("btn-cc-cancel");
  if (btnCcCancel) btnCcCancel.addEventListener("click", () => closeDlg("cc-dialog"));
  const btnCcOk = document.getElementById("btn-cc-ok");
  if (btnCcOk) btnCcOk.addEventListener("click", () => {
    const t = document.getElementById("cc-type");
    const title = document.getElementById("cc-title");
    const type = (t && t.value) || "plain";
    const label = ((title && title.value.trim()) || "Content control");
    const esc = _escXml(label);
    const html = `<span class="content-control" data-cc="${type}" title="${esc}" contenteditable="false">${type === "dropdown" ? "▾ " : ""}${esc}</span>`;
    editor.focus();
    document.execCommand("insertHTML", false, html);
    closeDlg("cc-dialog");
    setStatus(`Inserted ${type} content control`);
  });

  // -- Chart editor: type select + data grid -> live SVG preview -> image.
  function chartSVG(type, title, rows) {
    const W = 440, H = 260, M = 8;
    const vals = rows.map((r) => parseFloat(r[1])).filter((v) => !isNaN(v));
    const labels = rows.map((r) => r[0] || "");
    const max = Math.max(1, ...vals);
    const colors = ["#3f6fae", "#df8b3c", "#5aa469", "#b04a52", "#8a6fb0", "#c9a33c"];
    let body = "";
    if (type === "pie") {
      const total = vals.reduce((a, b) => a + b, 0) || 1;
      const cx = W / 2, cy = H / 2, R = 92;
      let a0 = -Math.PI / 2;
      vals.forEach((v, i) => {
        if (v <= 0) return;
        const a1 = a0 + (v / total) * Math.PI * 2;
        const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0);
        const x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
        const large = a1 - a0 > Math.PI ? 1 : 0;
        body += `<path d="M${cx},${cy}L${x0.toFixed(1)},${y0.toFixed(1)}A${R},${R} 0 ${large} 1 ${x1.toFixed(1)},${y1.toFixed(1)}Z" fill="${colors[i % colors.length]}" stroke="#fff" stroke-width="1"/>`;
        a0 = a1;
      });
      if (title) body += `<text x="${cx}" y="${M + 12}" text-anchor="middle" font-size="13" font-family="sans-serif">${_escXml(title)}</text>`;
    } else {
      const plotW = W - 130, plotH = H - 46, bx = 58, by = 22;
      const n = Math.max(1, vals.length);
      let lastX = 0, lastY = 0;
      vals.forEach((v, i) => {
        const h = (v / max) * plotH;
        const x = bx + (i * plotW) / n + plotW / n * 0.18;
        const bw = Math.max(8, plotW / n * 0.55);
        const y = by + plotH - h;
        if (type === "bar" || type === "area") body += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${colors[i % colors.length]}" rx="2"/>`;
        const nx = x + bw / 2, ny = y;
        if (type === "line" || type === "area") {
          if (i > 0) body += `<line x1="${lastX.toFixed(1)}" y1="${lastY.toFixed(1)}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="${colors[i % colors.length]}" stroke-width="2"/>`;
          body += `<circle cx="${nx.toFixed(1)}" cy="${ny.toFixed(1)}" r="3" fill="${colors[i % colors.length]}"/>`;
        }
        if (type === "area") body += `<polygon points="${bx + (i * plotW) / n},${by + plotH} ${(bx + (i * plotW) / n + plotW / n).toFixed(1)},${by + plotH} ${nx.toFixed(1)},${ny.toFixed(1)}" fill="${colors[i % colors.length]}" opacity="0.25"/>`;
        lastX = nx; lastY = ny;
        if (labels[i]) body += `<text x="${x + bw / 2}" y="${by + plotH + 14}" text-anchor="middle" font-size="10" font-family="sans-serif">${_escXml(labels[i])}</text>`;
        body += `<text x="${bx + plotW + 8}" y="${y + 3}" font-size="9" font-family="sans-serif" fill="#555">${v}</text>`;
      });
      if (title) body += `<text x="${W / 2}" y="${M + 12}" text-anchor="middle" font-size="13" font-family="sans-serif">${_escXml(title)}</text>`;
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect width="100%" height="100%" fill="#ffffff"/>${body}</svg>`;
  }
  function chartRowsFromDOM() {
    return [...document.querySelectorAll("#chart-data input")]
      .filter((el) => el.dataset.cj === "0")
      .map((el) => [el.value, (document.querySelector(`#chart-data input[data-ci="${el.dataset.ci}"][data-cj="1"]`) || {}).value || "0"]);
  }
  function renderChartPreview() {
    const type = document.getElementById("chart-type-select").value;
    const title = document.getElementById("chart-title").value;
    const prev = document.getElementById("chart-preview");
    if (prev) prev.innerHTML = chartSVG(type, title, chartRowsFromDOM());
  }
  const btnChart = document.getElementById("btn-chart");
  if (btnChart) btnChart.addEventListener("click", () => {
    if (READ_ONLY) return;
    openDlg("chart-dialog");
    renderChartPreview();
  });
  const btnChartCancel = document.getElementById("btn-chart-cancel");
  if (btnChartCancel) btnChartCancel.addEventListener("click", () => closeDlg("chart-dialog"));
  document.getElementById("chart-type-select")?.addEventListener("change", renderChartPreview);
  document.getElementById("chart-title")?.addEventListener("input", renderChartPreview);
  document.querySelectorAll("#chart-data input").forEach((el) => el.addEventListener("input", renderChartPreview));
  const btnChartOk = document.getElementById("btn-chart-ok");
  if (btnChartOk) btnChartOk.addEventListener("click", () => {
    const type = document.getElementById("chart-type-select").value;
    const title = document.getElementById("chart-title").value;
    const spec = { type, title, rows: chartRowsFromDOM(), width: 420 };
    if (pendingVectorReload) reloadVectorTarget(chartSVG(type, title, spec.rows), "chart", spec);
    else insertVectorObject(chartSVG(type, title, spec.rows), "chart", spec);
    closeDlg("chart-dialog");
    setStatus("Inserted chart");
  });

  // -- Equation: linear notation -> SVG (sup/sub/sqrt, symbol passthrough).
  function equationSVG(expr) {
    const tokens = expr.replace(/\s+/g, " ").trim();
    const W = Math.max(140, tokens.length * 13 + 40), H = 76;
    let body = "";
    let x = 12;
    const push = (txt, size, dy) => {
      body += `<text x="${x}" y="${42 + (dy || 0)}" font-size="${size || 18}" font-family="serif" font-style="italic">${_escXml(txt)}</text>`;
      x += txt.length * ((size || 18) * 0.62) + 1;
    };
    let i = 0;
    while (i < tokens.length) {
      const ch = tokens[i];
      if (ch === "^") {
        let j = i + 1, run = "";
        if (tokens[j] === "{") { j++; while (j < tokens.length && tokens[j] !== "}") run += tokens[j++]; i = j + 1; }
        else { run = tokens[j] || ""; i = j + 1; }
        push(run, 13, -12);
      } else if (ch === "_") {
        let j = i + 1, run = "";
        if (tokens[j] === "{") { j++; while (j < tokens.length && tokens[j] !== "}") run += tokens[j++]; i = j + 1; }
        else { run = tokens[j] || ""; i = j + 1; }
        push(run, 13, 10);
      } else if (ch === "*" && tokens[i + 1] === "*") {
        body += `<circle cx="${x + 4}" cy="${38}" r="1.4" fill="currentColor"/>`; x += 10; i += 2;
      } else {
        push(ch, 18, 0); i++;
      }
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect width="100%" height="100%" fill="#ffffff"/>${body}</svg>`;
  }
  const btnEquation = document.getElementById("btn-equation");
  if (btnEquation) btnEquation.addEventListener("click", () => {
    if (READ_ONLY) return;
    openDlg("equation-dialog");
    const input = document.getElementById("equation-input");
    const prev = document.getElementById("equation-preview");
    const refresh = () => { if (prev && input) prev.innerHTML = equationSVG(input.value || " "); };
    input?.addEventListener("input", refresh);
    refresh();
  });
  const btnEquationCancel = document.getElementById("btn-equation-cancel");
  if (btnEquationCancel) btnEquationCancel.addEventListener("click", () => closeDlg("equation-dialog"));
  const btnEquationOk = document.getElementById("btn-equation-ok");
  if (btnEquationOk) btnEquationOk.addEventListener("click", () => {
    const input = document.getElementById("equation-input");
    const text = (input && input.value.trim()) || "x";
    const spec = { text, width: 260 };
    if (pendingVectorReload) reloadVectorTarget(equationSVG(text), "equation", spec);
    else insertVectorObject(equationSVG(text), "equation", spec);
    closeDlg("equation-dialog");
    setStatus("Inserted equation");
  });

  // -- SmartArt gallery: diagram templates -> SVG -> image.
  function smartartSVG(kind) {
    const W = 420, H = 200;
    const box = (x, y, w, h, t, fill) =>
      `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="6" fill="${fill || "#3f6fae"}"/><text x="${x + w / 2}" y="${y + h / 2 + 4}" text-anchor="middle" font-size="12" fill="#fff" font-family="sans-serif">${_escXml(t)}</text>`;
    const arrow = (x1, y1, x2, y2) => `<path d="M${x1},${y1}L${x2},${y2}" stroke="#8a8f98" stroke-width="2" marker-end="url(#a)"/>`;
    let body = "";
    if (kind === "process") {
      for (let k = 0; k < 4; k++) body += box(10 + k * 105, 78, 70, 44, String(k + 1));
      for (let k = 0; k < 3; k++) body += arrow(82 + k * 105, 100, 106 + k * 105, 100);
    } else if (kind === "cycle") {
      const cs = ["#3f6fae", "#df8b3c", "#5aa469", "#b04a52"];
      for (let k = 0; k < 4; k++) {
        const a = k * Math.PI / 2 - Math.PI / 2;
        body += box(192 + 62 * Math.cos(a) - 26, 92 + 62 * Math.sin(a) - 26, 52, 52, String(k + 1), cs[k]);
      }
    } else if (kind === "hierarchy") {
      body += box(160, 16, 100, 36, "Root");
      body += `<line x1="210" y1="52" x2="95" y2="82" stroke="#8a8f98"/><line x1="210" y1="52" x2="210" y2="82" stroke="#8a8f98"/><line x1="210" y1="52" x2="325" y2="82" stroke="#8a8f98"/>`;
      body += box(20, 84, 130, 40, "Child 1", "#5a8fb5") + box(145, 84, 130, 40, "Child 2", "#5a8fb5") + box(270, 84, 130, 40, "Child 3", "#5a8fb5");
    } else if (kind === "matrix") {
      body += box(40, 34, 150, 60, "A", "#3f6fae") + box(230, 34, 150, 60, "B", "#df8b3c")
           + box(40, 112, 150, 60, "C", "#5aa469") + box(230, 112, 150, 60, "D", "#b04a52");
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs><marker id="a" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0L6,3L0,6Z" fill="#8a8f98"/></marker></defs><rect width="100%" height="100%" fill="#ffffff"/>${body}</svg>`;
  }
  const btnSmartArt = document.getElementById("btn-smartart");
  if (btnSmartArt) btnSmartArt.addEventListener("click", () => {
    if (READ_ONLY) return;
    openDlg("smartart-dialog");
    renderSmartArtPreview();
  });
  function renderSmartArtPreview() {
    const kind = document.getElementById("smartart-type").value;
    const prev = document.getElementById("smartart-preview");
    if (prev) prev.innerHTML = smartartSVG(kind);
  }
  document.getElementById("smartart-type")?.addEventListener("change", renderSmartArtPreview);
  const btnSmartArtCancel = document.getElementById("btn-smartart-cancel");
  if (btnSmartArtCancel) btnSmartArtCancel.addEventListener("click", () => closeDlg("smartart-dialog"));
  const btnSmartArtOk = document.getElementById("btn-smartart-ok");
  if (btnSmartArtOk) btnSmartArtOk.addEventListener("click", () => {
    const kind = document.getElementById("smartart-type").value;
    const spec = { kind, width: 420 };
    if (pendingVectorReload) reloadVectorTarget(smartartSVG(kind), "smartart", spec);
    else insertVectorObject(smartartSVG(kind), "smartart", spec);
    closeDlg("smartart-dialog");
    setStatus("Inserted SmartArt diagram");
  });

  // -- Object layout (float): wrap / align / layer popup on the selected image.
  const objPopup = document.getElementById("objlayout-pop");
  let floatImg = null;
  function selectedImage() {
    const sel = window.getSelection();
    let node = null;
    if (sel && sel.anchorNode) node = sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentElement : sel.anchorNode;
    if (node && node.closest && node.closest("img")) return node.closest("img");
    if (document.activeElement && document.activeElement.tagName === "IMG") return document.activeElement;
    return null;
  }
  document.addEventListener("selectionchange", () => {
    const img = selectedImage();
    if (img && objPopup) {
      floatImg = img;
      const r = img.getBoundingClientRect();
      objPopup.style.left = Math.min(r.left, window.innerWidth - 220) + "px";
      objPopup.style.top = (r.bottom + 6) + "px";
      objPopup.hidden = false;
    } else if (objPopup) {
      objPopup.hidden = true;
    }
  });
  function applyObjLayout(attr, value) {
    if (!floatImg) return;
    if (attr === "wrap") {
      floatImg.classList.remove("obj-inline", "obj-square", "obj-right", "obj-behind", "obj-center");
      floatImg.style.position = ""; floatImg.style.zIndex = "";
      if (value === "square") floatImg.classList.add("obj-square");
      else if (value === "behind") floatImg.classList.add("obj-behind");
      else floatImg.classList.add("obj-inline");
    } else if (attr === "align") {
      floatImg.classList.remove("obj-inline", "obj-square", "obj-right", "obj-behind", "obj-center");
      if (value === "left") floatImg.classList.add("obj-square");
      else if (value === "right") floatImg.classList.add("obj-square", "obj-right");
      else floatImg.classList.add("obj-center");
    } else if (attr === "layer") {
      floatImg.style.position = "relative";
      floatImg.style.zIndex = String((parseFloat(floatImg.style.zIndex) || 0) + (value === "back" ? -1 : 1));
      setStatus(`Object layout: layer ${value}`);
      return;
    }
    setStatus(`Object layout: ${attr} ${value}`);
  }
  objPopup?.querySelectorAll("[data-objwrap]").forEach((b) => b.addEventListener("click", () => applyObjLayout("wrap", b.dataset.objwrap)));
  objPopup?.querySelectorAll("[data-objalign]").forEach((b) => b.addEventListener("click", () => applyObjLayout("align", b.dataset.objalign)));
  objPopup?.querySelectorAll("[data-objlayer]").forEach((b) => b.addEventListener("click", () => applyObjLayout("layer", b.dataset.objlayer)));
  document.getElementById("btn-objlayout-close")?.addEventListener("click", () => { if (objPopup) objPopup.hidden = true; });

  // -- Draw > Group / Merge: combine selected ink strokes into one shape.
  function combineInk(asMerge) {
    if (selectedInk < 0) { setStatus("Select a stroke first (Select tool)", true); return; }
    if (inkStrokes.length < 2) { setStatus("Need at least two strokes", true); return; }
    const pts = [];
    for (const s of inkStrokes) for (const p of s.points) pts.push(p);
    inkStrokes = [{
      points: pts,
      color: inkStrokes[selectedInk] ? inkStrokes[selectedInk].color : inkStrokes[0].color,
      thickness: Math.max(...inkStrokes.map((s) => s.thickness || 2)),
      mode: "pen",
    }];
    selectedInk = 0;
    redrawInk();
    setStatus(asMerge ? "Merged strokes into one shape" : "Grouped strokes");
  }
  document.getElementById("btn-group")?.addEventListener("click", () => combineInk(false));
  document.getElementById("btn-mergeshapes")?.addEventListener("click", () => combineInk(true));

  loadDocument();
})();
