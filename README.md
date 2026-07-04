# AI-Powered Accounting Command Center — RBAC Edition

A firm-management command center with strict Role-Based Access Control
enforced across three layers:

1. **Frontend** — HTML5 / Bootstrap 5 / Vanilla JS (dynamic offcanvas nav + view masking)
2. **Backend** — Python FastAPI + SQLite (scoped queries, JWT auth, hashed passwords)
3. **Automation** — Node.js/Express (actor-role whitelist + permanent compliance audit trail for Composio calls)

## Roles

| Role | Access |
|---|---|
| **Super Admin** | Everything — global settings, firm-wide billing/analytics, create/delete Admins, Employees, Clients |
| **Admin** | Create/manage Employees & Clients (not Admins), firm-wide dashboard logs, Composio scope config |
| **Employee** | Only their *assigned* clients — Smart Inbox, Data Cleaner, Collections |
| **Client** | Only their own portal — Upload Center, Document Checklist, Invoices, Contact Officer |

## 1. Run the backend (this also serves the frontend)

```bash
cd backend
pip install -r requirements.txt --break-system-packages   # or use a venv
python server.py
```

The app is now live at **http://localhost:8000**, serving three public pages:

| Route | Page | Purpose |
|---|---|---|
| `/` | `frontend/index.html` | Public landing page — the marketing front door |
| `/register` | `frontend/register.html` | Public self-service firm signup |
| `/login` | `frontend/app.html` | The authenticated SPA (login screen + role-scoped app shell) |

On first run it auto-creates `accounting_command_center.db` and seeds one
Super Admin account:

```
email:    superadmin@firm.com
password: ChangeMe123!
```

**Change this password immediately** via the User Management screen (or a
direct `PATCH /api/admin/users/{id}` call) after your first login.

### Two ways an Admin account comes to exist

1. **Self-service signup** — anyone can go to `/register`, fill in their firm
   name, name, email, and password, and `POST /api/auth/register` creates
   them as that firm's first **Admin** (rank 2), `created_by = NULL`. This
   is the only path that creates an Admin without an existing Super Admin
   or Admin doing it — it represents a brand-new firm coming onto the
   platform, and it logs them straight in.
2. **Invited by a Super Admin** — from the Super Admin account (or another
   Admin, for Employees/Clients) via the "Create New User" modal or
   `POST /api/admin/users/create`. Only a **Super Admin** may create
   another Admin this way; that rule is unchanged.

From an Admin or Super Admin account you can create Employees and Clients,
and assign each Client to an Employee (Account Officer) from the "Create
New User" modal or `POST /api/assignments`.

## 2. Run the Node.js automation layer (separate process)

```bash
cd automation
npm install
COMPOSIO_API_KEY=your_real_key PORT=4000 node automation.js
```

This service sits between the backend and Composio's action-execution API.
Every call to `POST /api/v1/execute-action` must include `actor_id` and
`actor_role` (as headers `X-Actor-Id` / `X-Actor-Role`, or in the JSON body).
High-level configuration actions (project keys, folder paths, connector
scopes, etc.) are rejected with a `403` before any network call is made if
the actor's role is below `admin`. Every attempt — allowed, denied, or
failed — is appended to `automation/compliance_audit_log.jsonl` with the
actor's id, role, and the target `entityId`.

## Project layout

```
accounting-command-center/
├── backend/
│   ├── server.py      # FastAPI entrypoint — run this (python server.py)
│   ├── routes.py       # All RBAC-scoped API routes
│   ├── auth.py         # Password hashing, JWT, require_role()/require_min_rank()
│   ├── database.py     # SQLite schema + seed data
│   └── requirements.txt
├── frontend/
│   ├── index.html       # Public landing page (served at "/")
│   ├── register.html     # Public firm signup page (served at "/register")
│   ├── register.js       # Signup form logic -> POST /api/auth/register
│   ├── app.html           # Login screen + app shell + all role-specific views (served at "/login")
│   ├── app.js             # Auth, nav rendering, API calls, CRUD for users
│   ├── styles.css        # "Ledger/Command" visual theme (shared)
│   └── marketing.css     # Landing/register-only layout, extends styles.css
├── automation/
│   ├── automation.js     # Node/Express actor-role-scoped Composio dispatcher
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
