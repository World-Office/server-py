# opencloud-docserver

Stoic Linux document server for [OpenCloud](https://opencloud.eu) (OCIS),
integrated via the [WOPI](https://learn.microsoft.com/en-us/microsoft-365/cloud-storage-partner-program/rest/) protocol.

**One process. One job: edit office documents through OpenCloud.**

- Python 3.12 + FastAPI — no Rust, no TypeScript, no WASM, no build step
- Vanilla JS editor — zero npm dependencies
- SQLite — a storage ledger, not a database server
- DOCX **and** ODT editing — one editor, python-docx and odfpy
- Single Docker image or a plain systemd unit

## Quick start (local)

```sh
cd opencloud-docserver
uv sync          # install deps
uv run pytest    # run tests

uv run uvicorn src.main:app --reload
# open http://localhost:8000/docs to see the API
```

### Seed a sample document

```sh
# From a Python shell with `uv run python`:
from docx import Document
Document().add_paragraph("Stoic dogcow test").save("sample.docx")

# Or the same for OpenDocument Text — .odt files work identically:
from odf.opendocument import OpenDocumentText
from odf.text import P
doc = OpenDocumentText()
doc.text.addElement(P(text="Stoic dogcow ODT"))
doc.save("sample.odt")

# Upload it:
curl -s -F "file=@sample.docx" http://localhost:8000/api/upload
# → {"id":"sample.docx","name":"sample.docx"}

# Edit it in the browser:
open http://localhost:8000/editor/sample.docx
```

## WOPI endpoints (host mode)

| Method | Path                     | Purpose                |
|--------|--------------------------|------------------------|
| GET    | `/wopi/files/{id}`       | CheckFileInfo          |
| GET    | `/wopi/files/{id}/contents` | GetFile            |
| POST   | `/wopi/files/{id}/contents` | PutFile            |
| POST   | `/wopi/files/{id}/lock`  | Lock / refresh lock    |
| POST   | `/wopi/files/{id}/unlock`| Unlock                 |
| POST   | `/wopi/files/{id}/getlock`| GetLock               |

## Editor endpoints

| Method | Path                              | Purpose                     |
|--------|-----------------------------------|-----------------------------|
| GET    | `/editor/{id}`                    | The web editor page         |
| GET    | `/api/documents/{id}/html`        | DOCX/ODT as editable HTML   |
| POST   | `/api/documents/{id}/save`        | Save HTML back to DOCX/ODT  |
| POST   | `/api/upload`                     | Create a document           |
| GET    | `/api/documents`                  | List documents              |

## Document formats

The editor is a canvas-native web page — it does **not** attempt
pagination or print fidelity. Documents round-trip through HTML in the
browser and are re-encoded server-side on save. The format is routed by
file extension (resolved from the WOPI `BaseFileName` at launch, or the
local store name); `.docx` is the fallback for unknown extensions.

| Format | Converter pair                 | Library    | MIME type                                              |
|--------|--------------------------------|------------|--------------------------------------------------------|
| DOCX   | `docx_to_html` / `html_to_docx` | python-docx | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| ODT    | `odt_to_html` / `html_to_odt`   | odfpy      | `application/vnd.oasis.opendocument.text`             |

### ODT support

OpenDocument Text (`.odt`) is a first-class citizen:

- **Read** — `GET /api/documents/{id}/html` detects the `.odt` extension
  and converts the ODF package to editable HTML (`odt_to_html`).
- **Write** — `POST /api/documents/{id}/save` re-encodes the edited HTML
  into a valid ODT package (`html_to_odt`) before PUT to the WOPI host.
- **Discovery** — the WOPI discovery XML advertises `view` and `edit`
  actions for the `odt` extension, so OpenCloud offers ODT files to the
  editor (`.docx` was already there).
- **MIME type** — the WOPI host router serves ODT with the proper
  `application/vnd.oasis.opendocument.text` content type.

What survives the ODT round-trip: text and paragraphs, headings,
bold/italic/underline, bullet and numbered lists (nested included),
tables (multi-column, covered cells, ragged rows), left/center/right
alignment, links, and images. Images are self-contained: they live as
`data:` URIs in the browser HTML and as `draw:frame` / `draw:image`
(package-embedded, via `Pictures/` members or `office:binary-data`) in
the ODT package; `alt` text is preserved through the ODF-standard
`svg:title` on the `draw:frame`.

Conversion is deliberately lossy where web HTML is richer than the mapped
ODT subset — content the editor neither produces nor consumes is not
preserved. See `src/editor/odt_converter.py` for the exact mapping.

**Dependency:** `odfpy>=1.4` (declared in `pyproject.toml`). Install with
`uv sync`.

## OpenCloud (OCIS) integration

OpenCloud is the **WOPI host** (files + auth). This server is the **WOPI
client** (the editor). Two modes are supported:

1. **Local host mode (default)** — the docserver stores documents in its
   own SQLite store and implements the full WOPI host surface, so you can
   develop and test with zero external services.
2. **OCIS client mode** — when OCIS launches the editor it redirects the
   browser to `/editor/{id}?access_token=<jwt>&wopi_host=<ocis>`. The
   docserver then reads/writes file content through OCIS's WOPI service
   using that token. Configure `DOCSERVER_WOPI_HOST` (or `WOPI_HOST`)
   so the docserver knows which host to forward to.

## Configuration

All settings live in `config.toml` and can be overridden with environment
variables (`DOCSERVER_*`). See `config.toml` for the full list.

```sh
# Generate a real JWT secret:
openssl rand -base64 48
export DOCSERVER_JWT_SECRET="..."
```

## Deploy

### Docker

```sh
docker compose up -d --build
curl http://localhost:8000/health
```

### systemd

```sh
sudo useradd -r -m docserver
sudo mkdir -p /opt/opencloud-docserver /etc/opencloud-docserver
sudo cp -r src web config.toml /opt/opencloud-docserver/
sudo cp systemd/opencloud-docserver.service /etc/systemd/system/
sudo install -m600 -o root systemd/opencloud-docserver.env /etc/opencloud-docserver/env
sudo systemctl daemon-reload
sudo systemctl enable --now opencloud-docserver
```

## Planning

The product backlog — **epics and user stories** for the editor/docserver —
lives in [`docs/backlog-epics-and-user-stories.md`](docs/backlog-epics-and-user-stories.md).
Epics are promoted into OpenSpec changes (`openspec/changes/`) when scheduled.

## Philosophy

Stoic Linux. Simplicity, clarity, reliability, focus.

- stdlib first — `sqlite3`, `urllib`, `logging` before any dependency
- flat structure — at most 3 levels under `src/`
- small files — nothing over 400 lines
- one job per process — the docserver serves documents, period
- least privilege — non-root user, `NoNewPrivileges`, read-only `/usr`

> *"The best editor is the one you never have to patch at 3 AM."*
