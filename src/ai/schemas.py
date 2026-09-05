"""Model-agnostic tool schemas for the World-Office agent surface.

The catalog is plain JSON Schema (draft 2020-12 subset) — every MCP-compatible
agent framework can consume it without vendor-specific server code. Schemas
are versioned: bump :data:`TOOL_CATALOG_VERSION` whenever a tool's contract
changes, so clients can pin and detect drift.

Tool catalog (5 tools, deliberately small):

    read_doc      — document bytes/metadata + current collaborative text
    apply_ops     — apply edits through the collaboration op pipeline
    get_versions  — version history metadata (+ optional snapshot content)
    lock          — WOPI lock/unlock/refresh/get
    presence      — announce/update/leave the presence list
"""

from __future__ import annotations

TOOL_CATALOG_VERSION = "1.0"

#: The doc id argument shared by every tool.
_DOC_ID = {
    "type": "object",
    "properties": {
        "doc_id": {"type": "string", "description": "Document id (WOPI file id)."},
    },
    "required": ["doc_id"],
}

read_doc_schema = {
    "name": "read_doc",
    "description": (
        "Read a document: metadata, current collaborative text, and the tail "
        "of the op log. Unknown ids return a not-found result, never an error."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document id (WOPI file id)."},
            "ops_tail": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "description": "How many of the latest ops to include (default 50).",
            },
            "include_content": {
                "type": "boolean",
                "description": "Include raw stored bytes as base64 (default false).",
            },
        },
        "required": ["doc_id"],
    },
}

apply_ops_schema = {
    "name": "apply_ops",
    "description": (
        "Apply edits through the collaboration op pipeline. Two op kinds are "
        "accepted and they may be mixed: plain text edits "
        '{"t": "ins", "at": <visible char index>, "text": "..."} and '
        '{"t": "del", "at": <start>, "end": <exclusive end>} (indices refer '
        "to the text state after the previous edit in the same call), or raw "
        "CRDT wire ops ({\"t\": \"insert\"|\"delete\", ...}). The call is "
        "rejected with the 409 lock-mismatch contract when another client "
        "holds the lock and no matching lock_token is supplied."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document id (WOPI file id)."},
            "client_id": {
                "type": "string",
                "pattern": "^agent=",
                "description": "Agent identity, must start with 'agent=' — e.g. 'agent=alfie'.",
            },
            "ops": {
                "type": "array",
                "maxItems": 200,
                "items": {"type": "object"},
                "description": "Edits to apply, in order.",
            },
            "base_rev": {
                "type": "integer",
                "description": "Client's last known revision (optional; heals gaps in the reply).",
            },
            "lock_token": {
                "type": "string",
                "description": "WOPI lock token; required to match when the document is locked.",
            },
        },
        "required": ["doc_id", "client_id", "ops"],
    },
}

get_versions_schema = {
    "name": "get_versions",
    "description": "List a document's stored versions, newest first (metadata only).",
    "inputSchema": _DOC_ID,
}

lock_schema = {
    "name": "lock",
    "description": (
        "WOPI lock plane. Actions: 'lock' (first-writer-wins; same token = "
        "refresh), 'unlock', 'refresh', 'get'. A conflicting lock returns the "
        "409 lock-mismatch contract with the current token echoed."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document id (WOPI file id)."},
            "action": {
                "type": "string",
                "enum": ["lock", "unlock", "refresh", "get"],
                "description": "Which lock operation to perform.",
            },
            "token": {
                "type": "string",
                "description": "Lock token (required non-empty for lock/unlock/refresh).",
            },
            "user": {"type": "string", "description": "Display name of the lock owner."},
        },
        "required": ["doc_id", "action"],
    },
}

presence_schema = {
    "name": "presence",
    "description": (
        "Announce/update the agent on a document's presence list, or leave it "
        "(leave=true). Agents appear with an agent badge."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document id (WOPI file id)."},
            "client_id": {
                "type": "string",
                "pattern": "^agent=",
                "description": "Agent identity, must start with 'agent='.",
            },
            "user": {"type": "string", "description": "Display name (defaults to client_id)."},
            "cursor": {
                "type": ["integer", "null"],
                "description": "Visible character index the agent is looking at.",
            },
            "leave": {
                "type": "boolean",
                "description": "True to remove the agent from the presence list.",
            },
        },
        "required": ["doc_id", "client_id"],
    },
}

#: The advertised catalog, in the order tools were added.
TOOL_CATALOG = [
    read_doc_schema,
    apply_ops_schema,
    get_versions_schema,
    lock_schema,
    presence_schema,
]

TOOL_NAMES = tuple(t["name"] for t in TOOL_CATALOG)
