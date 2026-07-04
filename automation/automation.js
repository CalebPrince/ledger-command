/**
 * automation.js
 * --------------
 * Node.js automation/execution layer that sits between the FastAPI backend
 * and Composio's action-execution API. This layer is the LAST line of
 * defense before a real external action fires, so every request is
 * re-validated here even if the caller claims to have already checked
 * permissions upstream.
 *
 * Run with:
 *   npm install
 *   node automation.js
 *
 * Required env vars:
 *   COMPOSIO_API_KEY   - your Composio secret key
 *   PORT               - defaults to 4000
 */

const express = require("express");
const fetch = require("node-fetch"); // npm i node-fetch@2
const fs = require("fs");
const path = require("path");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 4000;
const COMPOSIO_API_KEY = process.env.COMPOSIO_API_KEY || "";
const COMPOSIO_BASE_URL = "https://backend.composio.dev/api/v1/actions";
const AUDIT_LOG_PATH = path.join(__dirname, "compliance_audit_log.jsonl");

// --------------------------------------------------------------------------
// RBAC configuration
// --------------------------------------------------------------------------

// Same hierarchy as the Python backend -- keep these in sync.
const ROLE_RANK = {
  client: 0,
  employee: 1,
  admin: 2,
  super_admin: 3,
};

// Actions that touch structural/system configuration (project keys, folder
// paths, connector scopes, webhook targets, etc). These require at least
// 'admin' rank. Extend this list as new high-risk actions are introduced.
const HIGH_LEVEL_ACTION_PATTERNS = [
  /project[_-]?key/i,
  /folder[_-]?path/i,
  /update[_-]?scope/i,
  /delete[_-]?connection/i,
  /modify[_-]?structure/i,
  /webhook[_-]?config/i,
  /entity[_-]?config/i,
];

const MINIMUM_RANK_FOR_HIGH_LEVEL_ACTIONS = ROLE_RANK["admin"];

function isHighLevelAction(actionName) {
  return HIGH_LEVEL_ACTION_PATTERNS.some((pattern) => pattern.test(actionName));
}

function appendAuditRecord(record) {
  fs.appendFile(AUDIT_LOG_PATH, JSON.stringify(record) + "\n", (err) => {
    if (err) {
      // Never let logging failures silently swallow the request; surface it
      // in the server logs even though we don't block the response on it.
      console.error("[compliance-audit] FAILED TO WRITE AUDIT RECORD:", err);
    }
  });
}

// --------------------------------------------------------------------------
// Middleware: require actor identity on every execution request
// --------------------------------------------------------------------------

function requireActorIdentity(req, res, next) {
  // Accept actor info from header OR body, header takes precedence so a
  // trusted upstream gateway can pin it even if the JSON body is spoofable.
  const actorId = req.header("X-Actor-Id") || req.body.actor_id;
  const actorRole = req.header("X-Actor-Role") || req.body.actor_role;

  if (!actorId || !actorRole) {
    return res.status(401).json({
      error: "Missing actor identity. actor_id and actor_role are required.",
    });
  }

  if (!(actorRole in ROLE_RANK)) {
    return res.status(400).json({ error: `Unknown actor_role: ${actorRole}` });
  }

  req.actor = { id: String(actorId), role: actorRole };
  next();
}

// --------------------------------------------------------------------------
// Route: execute a Composio action, scoped by actor_role
// --------------------------------------------------------------------------

app.post("/api/v1/execute-action", requireActorIdentity, async (req, res) => {
  const { action_name, entity_id, params } = req.body;
  const { id: actorId, role: actorRole } = req.actor;

  // The Python backend is a Super-Admin-configurable, DB-backed source of
  // truth for this key (see /api/admin/settings/composio) and forwards it
  // per-request since this Node process has no shared database. Falls back
  // to this process's own COMPOSIO_API_KEY env var for standalone/dev use.
  const composioApiKey = req.header("X-Composio-Api-Key") || COMPOSIO_API_KEY;

  if (!action_name || !entity_id) {
    return res.status(400).json({ error: "action_name and entity_id are required" });
  }

  // ---- Hard whitelist enforcement -------------------------------------
  // High-level configuration actions immediately throw/reject if the
  // triggering actor is below 'admin' rank (i.e. employee or client).
  if (isHighLevelAction(action_name) && ROLE_RANK[actorRole] < MINIMUM_RANK_FOR_HIGH_LEVEL_ACTIONS) {
    const denialRecord = {
      timestamp: new Date().toISOString(),
      actor_id: actorId,
      actor_role: actorRole,
      entity_id,
      action_name,
      result: "DENIED",
      reason: "High-level configuration action attempted below required 'admin' rank",
    };
    appendAuditRecord(denialRecord);

    return res.status(403).json({
      error:
        `Action '${action_name}' is a high-level configuration action and requires ` +
        `at least 'admin' privileges. Actor role '${actorRole}' is not permitted.`,
    });
  }

  try {
    const composioResponse = await fetch(
      `${COMPOSIO_BASE_URL}/${encodeURIComponent(action_name)}/execute`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": composioApiKey,
        },
        body: JSON.stringify({
          entityId: entity_id,
          input: params || {},
        }),
      }
    );

    const responseBody = await composioResponse.json().catch(() => ({}));

    // ---- Permanent compliance audit trail ------------------------------
    // Every direct call to Composio is logged with the initiating user's
    // id/role alongside the entityId, regardless of success or failure.
    appendAuditRecord({
      timestamp: new Date().toISOString(),
      actor_id: actorId,
      actor_role: actorRole,
      entity_id,
      action_name,
      result: composioResponse.ok ? "SUCCESS" : "FAILED",
      status_code: composioResponse.status,
    });

    if (!composioResponse.ok) {
      return res.status(composioResponse.status).json({
        error: "Composio action execution failed",
        details: responseBody,
      });
    }

    return res.status(200).json({
      message: "Action executed successfully",
      data: responseBody,
    });
  } catch (err) {
    appendAuditRecord({
      timestamp: new Date().toISOString(),
      actor_id: actorId,
      actor_role: actorRole,
      entity_id,
      action_name,
      result: "ERROR",
      error: err.message,
    });

    console.error("[automation] Composio execution error:", err);
    return res.status(502).json({ error: "Failed to reach Composio API" });
  }
});

// --------------------------------------------------------------------------
// Route: read-only view of the compliance audit trail (admin+ only)
// --------------------------------------------------------------------------

app.get("/api/v1/audit-log", requireActorIdentity, (req, res) => {
  if (ROLE_RANK[req.actor.role] < ROLE_RANK["admin"]) {
    return res.status(403).json({ error: "Only Admins and Super Admins may view the compliance audit trail" });
  }

  if (!fs.existsSync(AUDIT_LOG_PATH)) {
    return res.json({ records: [] });
  }

  const lines = fs
    .readFileSync(AUDIT_LOG_PATH, "utf-8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .reverse();

  res.json({ records: lines });
});

app.get("/health", (req, res) => res.json({ status: "ok" }));

app.listen(PORT, () => {
  console.log(`[automation] Node automation service listening on port ${PORT}`);
});
