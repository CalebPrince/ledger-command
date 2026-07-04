# Ledger/Command — AI-Powered Accounting Command Center

A command center for accounting and bookkeeping work, built around the
people who actually do it: **Account Officers** run their book of clients
(Smart Inbox, Data Cleaner, collections, real Gmail sends, AI suggestions
they approve), and **Clients** get their own portal (uploads, document
checklist, invoices, messages). Strict Role-Based Access Control is
enforced across three layers:

1. **Frontend** — HTML5 / Bootstrap 5 / Vanilla JS (dynamic offcanvas nav + view masking)
2. **Backend** — Python FastAPI + SQLite (scoped queries, JWT auth, hashed passwords)
3. **Automation** — Node.js/Express (actor-role whitelist + permanent compliance audit trail for Composio calls)

## Roles

| Role | Access |
|---|---|
| **Employee (Account Officer)** | Only their *assigned* clients — Smart Inbox, Data Cleaner, Collections & chasers, AI Suggestions queue (approve/reject), AI client reports, Send via Gmail |
| **Client** | Only their own portal — Upload Center, Document Checklist, Invoices, Contact Officer |
| **Admin** | Create/manage Employees & Clients (not Admins), firm-wide dashboard & logs, Composio scope config |
| **Super Admin** | Everything — Global Settings (Gemini, Composio, Gmail auth config), firm-wide billing/analytics, create/delete Admins, Employees, Clients |

## 1. Run the backend (this also serves the frontend)

```bash
cd backend
pip install -r requirements.txt --break-system-packages   # or use a venv
python server.py
```

The app is now live at **http://localhost:8000**, serving these public pages:

| Route | Page | Purpose |
|---|---|---|
| `/` | `frontend/index.html` | Public landing page — the marketing front door |
| `/register` | `frontend/register.html` | Public self-service signup (three tabs — see below) |
| `/login` | `frontend/app.html` | The authenticated SPA (login screen + role-scoped app shell) |
| `/integrations/callback` | `frontend/integrations-callback.html` | OAuth landing page after a Gmail connection completes |

On first run it auto-creates `accounting_command_center.db` and seeds one
Super Admin account:

```
email:    superadmin@firm.com
password: ChangeMe123!
```

**Change this password immediately** via the User Management screen (or a
direct `PATCH /api/admin/users/{id}` call) after your first login.

### Three self-service signup paths (`/register`)

1. **Firm** — fill in firm name, name, email, and password, and
   `POST /api/auth/register` creates you as that firm's first **Admin**
   (rank 2), `created_by = NULL`. This is the only path that creates an
   Admin without an existing Super Admin or Admin doing it, and it logs
   you straight in.
2. **Account Officer (solo)** — an officer with no firm can sign up
   independently and start adding their own clients immediately; each
   client they create is auto-assigned to them. No Admin required.
3. **Client** — a client can sign up directly, pick their account
   officer (or get matched later), and land in their own portal.

Admins and Super Admins can also create users the managed way: from the
"Create New User" modal or `POST /api/admin/users/create`. Only a
**Super Admin** may create another Admin this way. Clients are assigned
to Employees (Account Officers) from the same modal or `POST /api/assignments`.

## 2. Run the Node.js automation layer (separate process)

```bash
cd automation
npm install
COMPOSIO_API_KEY=your_real_key PORT=4000 node automation.js
```

This service sits between the backend and Composio's tool-execution API
(`POST https://backend.composio.dev/api/v3/tools/execute/{tool_slug}` —
the v1 actions API was retired 2026-07-03). Every call to
`POST /api/v1/execute-action` must include `actor_id` and `actor_role`
(as headers `X-Actor-Id` / `X-Actor-Role`, or in the JSON body), and may
include a `connected_account_id` to execute as a specific connected
account (this is how per-user Gmail sends work). High-level configuration
actions (project keys, folder paths, connector scopes, etc.) are rejected
with a `403` before any network call is made if the actor's role is below
`admin`. Every attempt — allowed, denied, or failed — is appended to
`automation/compliance_audit_log.jsonl` with the actor's id, role, and the
target `entityId`.

The Composio API key can be supplied per-request via the
`X-Composio-Api-Key` header (the FastAPI backend forwards the
Super-Admin-configured key from Global Settings); the `COMPOSIO_API_KEY`
env var is only a standalone/dev fallback.

## 3. Runtime settings (no restarts, no env-var edits)

A Super Admin configures these from **Global Settings** in the app; they
are stored in the database and take effect immediately:

- **Gemini API key & model** — powers the AI Report (a real Gemini call
  narrating a client's actual invoices, checklist, and messages) and the
  AI Suggestions scan (reconciliation flags, reminder drafts, invoice
  drafts — nothing fires until the assigned officer clicks Approve).
  `GEMINI_API_KEY` / `GEMINI_MODEL` env vars are honored as fallbacks.
- **Composio API key** — forwarded per-request to the automation layer.
- **Gmail Auth Config ID** — see below.

## 4. Gmail integration (real email, per-user OAuth)

Each Employee/Admin/Super Admin can connect **their own Gmail** and send
real email to a client from the Manage Client modal's Messages tab. Sent
emails also land on the client's in-app message thread.

One-time setup (dashboard-only — Composio has no API for this):

1. In [app.composio.dev](https://app.composio.dev), open **Auth Configs**
   → create one for the **Gmail** toolkit (Composio-managed auth is the
   fastest option).
2. Paste the generated `ac_…` id into **Global Settings → Gmail Auth
   Config** as a Super Admin.

Then any staff user goes to **Personal Settings → Connect Gmail**,
approves the Google consent screen, and returns connected. Sends go
through the automation layer's RBAC gate like every other Composio call.

## Project layout

```
accounting-command-center/
├── backend/
│   ├── server.py       # FastAPI entrypoint — run this (python server.py)
│   ├── routes.py       # All RBAC-scoped API routes (incl. AI + Gmail integration)
│   ├── auth.py         # Password hashing, JWT, require_role()/require_min_rank()
│   ├── database.py     # SQLite schema + seed data
│   └── requirements.txt
├── frontend/
│   ├── index.html                 # Public landing page (served at "/")
│   ├── register.html              # Public signup page, three tabs (served at "/register")
│   ├── register.js                # Signup form logic -> POST /api/auth/register
│   ├── app.html                   # Login screen + app shell + all role-specific views (served at "/login")
│   ├── app.js                     # Auth, nav rendering, API calls, CRUD for users
│   ├── integrations-callback.html # Post-OAuth landing page (served at "/integrations/callback")
│   ├── styles.css                 # "Ledger/Command" visual theme (shared)
│   └── marketing.css              # Landing/register-only layout, extends styles.css
├── automation/
│   ├── automation.js   # Node/Express actor-role-scoped Composio dispatcher
│   └── package.json
└── README.md
```

## Security notes for production hardening

- Move `SECRET_KEY` in `backend/auth.py` into an environment variable.
- Restrict `allow_origins` in `backend/server.py`'s CORS middleware to your real domain.
- Put the Node automation service behind the same auth gateway as the FastAPI
  backend so `actor_id`/`actor_role` can't be spoofed by a client directly —
  in production this header should be set by your backend after verifying
  the caller's JWT, not trusted from the browser.
- Rotate the seeded Super Admin password before going live.
