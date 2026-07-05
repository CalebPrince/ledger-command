/**
 * app.js
 * -------
 * Vanilla JS SPA shell for the Accounting Command Center.
 * Handles: login/session, role-based nav rendering + view masking,
 * user management CRUD, and the scoped dashboard/inbox/data-cleaner views.
 *
 * No frameworks, no build step — everything talks to the FastAPI backend
 * via fetch() using a Bearer token stored in sessionStorage.
 */

const API_BASE = ""; // same-origin (server.py serves this frontend)

const state = {
  token: sessionStorage.getItem("acc_token") || null,
  user: JSON.parse(sessionStorage.getItem("acc_user") || "null"),
};

// --------------------------------------------------------------------------
// Nav definitions per role — this is the "dynamic offcanvas" spec.
// Each entry: { id, label, icon, view }
// --------------------------------------------------------------------------
const NAV_BY_ROLE = {
  super_admin: [
    { id: "dashboard", label: "Dashboard", icon: "bi-speedometer2", view: "view-dashboard" },
    { id: "users", label: "User Management", icon: "bi-people", view: "view-users" },
    { id: "assigned-clients", label: "Clients", icon: "bi-person-badge", view: "view-assigned-clients" },
    { id: "audit", label: "Master Audit Logs", icon: "bi-journal-text", view: "view-audit" },
    { id: "settings", label: "Global Settings", icon: "bi-gear", view: "view-settings" },
    { id: "personal-settings", label: "Personal Settings", icon: "bi-sliders", view: "view-personal-settings" },
  ],
  admin: [
    { id: "dashboard", label: "Dashboard", icon: "bi-speedometer2", view: "view-dashboard" },
    { id: "users", label: "User Management", icon: "bi-people", view: "view-users" },
    { id: "assigned-clients", label: "Clients", icon: "bi-person-badge", view: "view-assigned-clients" },
    { id: "audit", label: "Master Audit Logs", icon: "bi-journal-text", view: "view-audit" },
    { id: "settings", label: "Global Settings", icon: "bi-gear", view: "view-settings" },
    { id: "personal-settings", label: "Personal Settings", icon: "bi-sliders", view: "view-personal-settings" },
  ],
  employee: [
    { id: "employee-overview", label: "Overview", icon: "bi-speedometer2", view: "view-employee-overview" },
    { id: "assigned-clients", label: "Assigned Clients", icon: "bi-person-badge", view: "view-assigned-clients" },
    { id: "inbox", label: "Smart Inbox", icon: "bi-inbox", view: "view-inbox" },
    { id: "data-cleaner", label: "Data Cleaner", icon: "bi-funnel", view: "view-data-cleaner" },
    { id: "collections", label: "Collections", icon: "bi-cash-coin", view: "view-collections" },
    { id: "personal-settings", label: "Personal Settings", icon: "bi-sliders", view: "view-personal-settings" },
  ],
  client: [
    { id: "client-overview", label: "Overview", icon: "bi-speedometer2", view: "view-client-overview" },
    { id: "upload", label: "Upload Center", icon: "bi-cloud-arrow-up", view: "view-upload" },
    { id: "checklist", label: "Document Checklist", icon: "bi-check2-square", view: "view-checklist" },
    { id: "invoices", label: "Invoices", icon: "bi-receipt", view: "view-invoices" },
    { id: "contact-officer", label: "Contact Officer", icon: "bi-headset", view: "view-contact-officer" },
    { id: "client-settings", label: "Personal Settings", icon: "bi-sliders", view: "view-client-settings" },
  ],
};

// --------------------------------------------------------------------------
// Fetch helper — always attaches the bearer token, handles 401/403 globally
// --------------------------------------------------------------------------
async function api(path, options = {}) {
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    options.headers || {},
    state.token ? { Authorization: `Bearer ${state.token}` } : {}
  );

  const res = await fetch(API_BASE + path, { ...options, headers });

  // A 401 from the login attempt itself means bad credentials, not an
  // expired session — let it fall through to the normal error path so the
  // server's "Incorrect email or password" message reaches the user.
  if (res.status === 401 && path !== "/api/auth/login") {
    logout();
    throw new Error("Session expired. Please sign in again.");
  }

  const contentType = res.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await res.json() : null;

  if (!res.ok) {
    throw new Error((body && body.detail) || `Request failed (${res.status})`);
  }
  return body;
}

// --------------------------------------------------------------------------
// Auth
// --------------------------------------------------------------------------
function logout() {
  sessionStorage.removeItem("acc_token");
  sessionStorage.removeItem("acc_user");
  state.token = null;
  state.user = null;
  document.getElementById("appShell").classList.add("d-none");
  document.getElementById("loginScreen").classList.remove("d-none");
}

document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("loginError");
  errorBox.classList.add("d-none");

  const email = document.getElementById("loginEmail").value.trim();
  const password = document.getElementById("loginPassword").value;

  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    state.token = data.access_token;
    state.user = data.user;
    sessionStorage.setItem("acc_token", state.token);
    sessionStorage.setItem("acc_user", JSON.stringify(state.user));
    bootApp();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

document.getElementById("logoutBtn").addEventListener("click", logout);

// --------------------------------------------------------------------------
// App boot — renders shell + role-scoped nav once authenticated
// --------------------------------------------------------------------------
function bootApp() {
  document.getElementById("loginScreen").classList.add("d-none");
  document.getElementById("appShell").classList.remove("d-none");

  document.getElementById("userNameLabel").textContent = state.user.name;
  document.getElementById("userEmailLabel").textContent = state.user.email;

  const pill = document.getElementById("rolePill");
  pill.textContent = state.user.role.replace("_", " ").toUpperCase();
  pill.className = `role-pill role-${state.user.role}`;

  const companyLabel = document.getElementById("companyNameLabel");
  if (companyLabel) {
    companyLabel.textContent = state.user.company_name || "";
    companyLabel.classList.toggle("d-none", !state.user.company_name);
  }

  renderNav();
  // User-management-only affordance: only a Super Admin may create/promote
  // accounts to admin rank or above -- hide those options for everyone else.
  if (state.user.role !== "super_admin") {
    document.querySelectorAll('#newUserRole option[value="admin"], #newUserRole option[value="super_admin"]').forEach((o) => (o.disabled = true));
    document.querySelectorAll('#modifyUserRole option[value="admin"], #modifyUserRole option[value="super_admin"]').forEach((o) => (o.disabled = true));
  }

  // Independent Account Officers (self-registered, no firm) may add their
  // own clients directly -- firm-invited Employees still go through their Admin.
  const addOwnClientBtn = document.getElementById("addOwnClientBtn");
  if (addOwnClientBtn) addOwnClientBtn.classList.toggle("d-none", !(state.user.role === "employee" && state.user.is_independent));
}

function renderNav() {
  const nav = NAV_BY_ROLE[state.user.role] || [];
  const container = document.getElementById("navLinks");
  container.innerHTML = "";

  nav.forEach((item, idx) => {
    const li = document.createElement("li");
    li.className = "nav-item";
    li.innerHTML = `<a class="nav-link ${idx === 0 ? "active" : ""}" data-view="${item.view}" data-nav-id="${item.id}">
      <i class="bi ${item.icon}"></i> ${item.label}
    </a>`;
    container.appendChild(li);
  });

  container.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      container.querySelectorAll(".nav-link").forEach((l) => l.classList.remove("active"));
      link.classList.add("active");
      showView(link.dataset.view, link.dataset.navId);
      // collapse mobile offcanvas after navigating
      const offcanvasEl = document.getElementById("sideNav");
      const instance = bootstrap.Offcanvas.getInstance(offcanvasEl);
      if (instance) instance.hide();
    });
  });

  // Show first view by default
  if (nav.length) showView(nav[0].view, nav[0].id);
}

function showView(viewId, navId) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("d-none"));
  const el = document.getElementById(viewId);
  if (el) el.classList.remove("d-none");
  loadViewData(navId);
}

// --------------------------------------------------------------------------
// Per-view data loading
// --------------------------------------------------------------------------
function loadViewData(navId) {
  switch (navId) {
    case "dashboard": return loadDashboard();
    case "users": return loadUsersTable();
    case "audit": return loadAuditLog();
    case "settings": return loadGlobalSettings();
    case "employee-overview": return loadEmployeeOverview();
    case "assigned-clients": return loadAssignedClients();
    case "inbox": return loadInbox();
    case "data-cleaner": return loadDataCleaner();
    case "collections": return loadCollections();
    case "personal-settings": return loadPersonalSettings();
    case "client-overview": return loadClientOverview();
    case "upload": return loadUploadCenter();
    case "checklist": return loadChecklist();
    case "contact-officer": return loadContactOfficer();
    case "invoices": return loadInvoices();
    case "client-settings": return loadClientSettings();
    default: return;
  }
}

function money(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function statusBadge(status) {
  return `<span class="status-badge ${status}">${status.replace("_", " ")}</span>`;
}

// ---- Dashboard (Admin / Super Admin / Employee) --------------------------
async function loadDashboard() {
  const cardsEl = document.getElementById("statCards");
  const activityBody = document.getElementById("dashboardActivityBody");
  cardsEl.innerHTML = "";

  try {
    const [users, audit] = await Promise.all([
      api("/api/admin/users").catch(() => ({ users: [] })),
      api("/api/admin/audit").catch(() => ({ logs: [] })),
    ]);

    const roleCounts = {};
    users.users.forEach((u) => (roleCounts[u.role] = (roleCounts[u.role] || 0) + 1));

    const cards = [
      { label: "Total Users", value: users.users.length },
      { label: "Admins", value: roleCounts.admin || 0 },
      { label: "Employees", value: roleCounts.employee || 0 },
      { label: "Clients", value: roleCounts.client || 0 },
    ];
    cardsEl.innerHTML = cards
      .map(
        (c) => `<div class="col-6 col-lg-3"><div class="stat-card">
          <div class="stat-value">${c.value}</div>
          <div class="stat-label">${c.label}</div>
        </div></div>`
      )
      .join("");

    activityBody.innerHTML = audit.logs.length
      ? audit.logs
          .slice(0, 12)
          .map(
            (log) =>
              `<tr><td>${log.actor_role || "—"} #${log.actor_id ?? "—"}</td><td>${log.action}</td><td class="text-muted">${log.detail || ""}</td><td class="text-muted">${log.created_at}</td></tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="text-muted text-center py-4">No activity yet.</td></tr>`;
  } catch (err) {
    activityBody.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

// ---- User Management (Admin / Super Admin) --------------------------------
let officerCache = [];

async function loadUsersTable() {
  const body = document.getElementById("usersTableBody");
  try {
    const data = await api("/api/admin/users");
    officerCache = data.users.filter((u) => u.role === "employee");
    populateOfficerDropdown();

    body.innerHTML = data.users.length
      ? data.users
          .map(
            (u) => `<tr>
              <td>${u.name}</td>
              <td class="text-muted">${u.email}</td>
              <td><span class="role-pill role-${u.role}">${u.role.replace("_", " ")}</span></td>
              <td>${statusBadge(u.status)}</td>
              <td class="text-end">
                <button class="btn btn-sm btn-outline-light me-1" onclick="openModifyModal(${u.id})">Modify</button>
                <button class="btn btn-sm btn-outline-light me-1" onclick='openResetPasswordModal(${u.id}, ${JSON.stringify(u.name)})'>Reset Password</button>
                <button class="btn btn-sm btn-outline-danger" onclick="revokeAccess(${u.id})" ${u.role === "super_admin" ? "disabled" : ""}>Revoke</button>
              </td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="text-muted text-center py-4">No users yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

function populateOfficerDropdown() {
  const select = document.getElementById("assignOfficerSelect");
  select.innerHTML = '<option value="">— Unassigned —</option>' +
    officerCache.map((o) => `<option value="${o.id}">${o.name}</option>`).join("");
}

document.getElementById("newUserRole").addEventListener("change", (e) => {
  document.getElementById("assignOfficerWrap").style.display = e.target.value === "client" ? "block" : "none";
});

document.getElementById("createUserForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("createUserError");
  errorBox.classList.add("d-none");

  const payload = {
    name: document.getElementById("newUserName").value.trim(),
    email: document.getElementById("newUserEmail").value.trim(),
    password: document.getElementById("newUserPassword").value,
    role: document.getElementById("newUserRole").value,
    assign_to_officer_id: document.getElementById("assignOfficerSelect").value || null,
  };
  if (payload.assign_to_officer_id) payload.assign_to_officer_id = Number(payload.assign_to_officer_id);

  try {
    await api("/api/admin/users/create", { method: "POST", body: JSON.stringify(payload) });
    bootstrap.Modal.getInstance(document.getElementById("createUserModal")).hide();
    document.getElementById("createUserForm").reset();
    loadUsersTable();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

function openModifyModal(userId) {
  // Pull the row straight from the table we already rendered.
  api("/api/admin/users").then((data) => {
    const u = data.users.find((x) => x.id === userId);
    if (!u) return;
    document.getElementById("modifyUserId").value = u.id;
    document.getElementById("modifyUserName").value = u.name;
    document.getElementById("modifyUserEmail").value = u.email;
    document.getElementById("modifyUserRole").value = u.role;
    document.getElementById("modifyUserStatus").value = u.status;
    new bootstrap.Modal(document.getElementById("modifyUserModal")).show();
  });
}

document.getElementById("modifyUserForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("modifyUserError");
  errorBox.classList.add("d-none");
  const userId = document.getElementById("modifyUserId").value;

  const payload = {
    name: document.getElementById("modifyUserName").value.trim(),
    email: document.getElementById("modifyUserEmail").value.trim(),
    role: document.getElementById("modifyUserRole").value,
    status: document.getElementById("modifyUserStatus").value,
  };

  try {
    await api(`/api/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) });
    bootstrap.Modal.getInstance(document.getElementById("modifyUserModal")).hide();
    loadUsersTable();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

function openResetPasswordModal(userId, userName) {
  document.getElementById("resetPasswordUserId").value = userId;
  document.getElementById("resetPasswordUserName").textContent = userName;
  document.getElementById("resetPasswordForm").reset();
  document.getElementById("resetPasswordError").classList.add("d-none");
  new bootstrap.Modal(document.getElementById("resetPasswordModal")).show();
}

document.getElementById("resetPasswordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("resetPasswordError");
  errorBox.classList.add("d-none");
  const userId = document.getElementById("resetPasswordUserId").value;
  const newPassword = document.getElementById("resetPasswordNewValue").value;

  try {
    await api(`/api/admin/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword }),
    });
    bootstrap.Modal.getInstance(document.getElementById("resetPasswordModal")).hide();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

async function revokeAccess(userId) {
  if (!confirm("Revoke this user's access? They will be suspended immediately.")) return;
  try {
    await api(`/api/admin/users/${userId}/revoke`, { method: "POST" });
    loadUsersTable();
  } catch (err) {
    alert(err.message);
  }
}

// ---- Master Audit Logs -----------------------------------------------------
async function loadAuditLog() {
  const body = document.getElementById("auditTableBody");
  try {
    const data = await api("/api/admin/audit");
    body.innerHTML = data.logs.length
      ? data.logs
          .map(
            (log) =>
              `<tr><td><span class="role-pill role-${log.actor_role}">${(log.actor_role || "—").replace("_", " ")}</span></td>
               <td class="text-muted">#${log.actor_id ?? "—"}</td>
               <td>${log.action}</td><td class="text-muted">${log.detail || ""}</td>
               <td class="text-muted">${log.created_at}</td></tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="text-muted text-center py-4">No audit entries yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

// ---- Global Settings (Super Admin sees analytics, Admin sees scope note) --
async function loadGlobalSettings() {
  const el = document.getElementById("globalAnalyticsCards");
  if (state.user.role !== "super_admin") {
    el.innerHTML = `<div class="col-12 text-muted">
      Firm-wide billing and global analytics are visible to Super Admins only.
      As an Admin, you can adjust Composio API scopes for your own agency below.
    </div>`;
  } else {
    try {
      const data = await api("/api/dashboard/global-analytics");
      el.innerHTML = Object.entries(data.user_counts)
        .map(
          ([role, count]) => `<div class="col-6 col-lg-3"><div class="stat-card">
            <div class="stat-value">${count}</div>
            <div class="stat-label">${role.replace("_", " ")}</div>
          </div></div>`
        )
        .join("");
    } catch (err) {
      el.innerHTML = `<div class="col-12 text-danger">${err.message}</div>`;
    }
  }
  loadComposioScopes();

  const geminiCard = document.getElementById("geminiSettingsCard");
  const composioSettingsCard = document.getElementById("composioSettingsCard");
  const gmailAuthConfigCard = document.getElementById("gmailAuthConfigCard");
  if (state.user.role === "super_admin") {
    geminiCard.classList.remove("d-none");
    loadGeminiSettings();
    composioSettingsCard.classList.remove("d-none");
    loadComposioSettings();
    gmailAuthConfigCard.classList.remove("d-none");
    loadGmailAuthConfig();
  } else {
    geminiCard.classList.add("d-none");
    composioSettingsCard.classList.add("d-none");
    gmailAuthConfigCard.classList.add("d-none");
  }
}

// ---- Gmail Auth Config ID (Super Admin only) --------------------------------
async function loadGmailAuthConfig() {
  const statusLine = document.getElementById("gmailAuthConfigStatusLine");
  try {
    const data = await api("/api/admin/settings/composio-gmail");
    statusLine.innerHTML = data.configured
      ? `<span class="text-accent">Configured</span> — <code>${data.auth_config_id}</code>, last updated ${data.updated_at}`
      : `<span class="text-muted">Not configured yet.</span> Employees won't be able to connect Gmail until this is set.`;
  } catch (err) {
    statusLine.innerHTML = `<span class="text-danger">${err.message}</span>`;
  }
}

document.getElementById("gmailAuthConfigForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("gmailAuthConfigError");
  const successBox = document.getElementById("gmailAuthConfigSuccess");
  errorBox.classList.add("d-none");
  successBox.classList.add("d-none");

  const authConfigId = document.getElementById("gmailAuthConfigInput").value.trim();
  if (!authConfigId) {
    errorBox.textContent = "Enter an auth_config_id to save.";
    errorBox.classList.remove("d-none");
    return;
  }

  try {
    await api("/api/admin/settings/composio-gmail", { method: "POST", body: JSON.stringify({ auth_config_id: authConfigId }) });
    document.getElementById("gmailAuthConfigForm").reset();
    successBox.textContent = "Gmail auth config saved.";
    successBox.classList.remove("d-none");
    loadGmailAuthConfig();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

// ---- Composio API key settings (Super Admin only) --------------------------
async function loadComposioSettings() {
  const statusLine = document.getElementById("composioStatusLine");
  try {
    const data = await api("/api/admin/settings/composio");
    statusLine.innerHTML = data.configured
      ? `<span class="text-accent">Configured</span> — <code>${data.masked_key}</code>, last updated ${data.updated_at}`
      : `<span class="text-muted">Not configured yet.</span> Composio dispatches will fall back to automation.js's own COMPOSIO_API_KEY environment variable, if any.`;
  } catch (err) {
    statusLine.innerHTML = `<span class="text-danger">${err.message}</span>`;
  }
}

document.getElementById("composioSettingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("composioSettingsError");
  const successBox = document.getElementById("composioSettingsSuccess");
  errorBox.classList.add("d-none");
  successBox.classList.add("d-none");

  const apiKey = document.getElementById("composioApiKeyInput").value.trim();
  if (!apiKey) {
    errorBox.textContent = "Enter an API key to save.";
    errorBox.classList.remove("d-none");
    return;
  }

  try {
    await api("/api/admin/settings/composio", { method: "POST", body: JSON.stringify({ api_key: apiKey }) });
    document.getElementById("composioSettingsForm").reset();
    successBox.textContent = "Composio settings updated.";
    successBox.classList.remove("d-none");
    loadComposioSettings();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

// ---- Gemini API settings (Super Admin only) --------------------------------
async function loadGeminiSettings() {
  const statusLine = document.getElementById("geminiStatusLine");
  try {
    const data = await api("/api/admin/settings/gemini");
    statusLine.innerHTML = data.configured
      ? `<span class="text-accent">Configured</span> — <code>${data.masked_key}</code>, model <code>${data.model}</code>, last updated ${data.updated_at}`
      : `<span class="text-muted">Not configured yet.</span> The AI Report tool will show an error until a key is set.`;
    document.getElementById("geminiModelInput").placeholder = data.model || "gemini-2.5-flash";
  } catch (err) {
    statusLine.innerHTML = `<span class="text-danger">${err.message}</span>`;
  }
}

document.getElementById("geminiSettingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("geminiSettingsError");
  const successBox = document.getElementById("geminiSettingsSuccess");
  errorBox.classList.add("d-none");
  successBox.classList.add("d-none");

  const apiKey = document.getElementById("geminiApiKeyInput").value.trim();
  const model = document.getElementById("geminiModelInput").value.trim();
  if (!apiKey) {
    errorBox.textContent = "Enter an API key to save.";
    errorBox.classList.remove("d-none");
    return;
  }

  try {
    await api("/api/admin/settings/gemini", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey, model: model || null }),
    });
    document.getElementById("geminiSettingsForm").reset();
    successBox.textContent = "Gemini settings updated.";
    successBox.classList.remove("d-none");
    loadGeminiSettings();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

// ---- Composio Scopes (Admin + Super Admin) --------------------------------
async function loadComposioScopes() {
  const el = document.getElementById("composioScopesList");
  try {
    const data = await api("/api/admin/composio-scopes");
    el.innerHTML = data.scopes
      .map(
        (s) => `<div class="scope-row">
          <div>
            <div>${s.label}</div>
            <div class="text-muted small">${s.scope_key}${s.high_level ? ' <span class="text-warning">— high-level, requires Admin+</span>' : ""}</div>
          </div>
          <div class="form-check form-switch">
            <input class="form-check-input" type="checkbox" role="switch" data-scope-key="${s.scope_key}" ${s.enabled ? "checked" : ""}>
          </div>
        </div>`
      )
      .join("");

    el.querySelectorAll('input[type="checkbox"]').forEach((toggle) => {
      toggle.addEventListener("change", async () => {
        toggle.disabled = true;
        try {
          await api(`/api/admin/composio-scopes/${toggle.dataset.scopeKey}/toggle`, {
            method: "POST",
            body: JSON.stringify({ enabled: toggle.checked }),
          });
        } catch (err) {
          alert(err.message);
          toggle.checked = !toggle.checked;
        } finally {
          toggle.disabled = false;
        }
      });
    });
  } catch (err) {
    el.innerHTML = `<p class="text-danger">${err.message}</p>`;
  }
}

// ---- Employee: Overview ------------------------------------------------------
async function loadEmployeeOverview() {
  const el = document.getElementById("employeeOverviewCards");
  try {
    const data = await api("/api/employee/overview");
    const cards = [
      { label: "Assigned Clients", value: data.assigned_clients },
      { label: "Unread Inbox Items", value: data.unread_inbox },
      { label: "Outstanding Invoices", value: `${data.outstanding_invoices_count} (${money(data.outstanding_invoices_total_cents)})` },
      { label: "Active Chaser Campaigns", value: data.active_campaigns },
      { label: "Pending AI Suggestions", value: data.pending_ai_suggestions },
    ];
    el.innerHTML = cards
      .map(
        (c) => `<div class="col-6 col-lg-3"><div class="stat-card">
          <div class="stat-value">${c.value}</div>
          <div class="stat-label">${c.label}</div>
        </div></div>`
      )
      .join("");
  } catch (err) {
    el.innerHTML = `<div class="col-12 text-danger">${err.message}</div>`;
  }
}

// ---- Employee: Assigned Clients --------------------------------------------
async function loadAssignedClients() {
  const body = document.getElementById("assignedClientsBody");
  try {
    const data = await api("/api/dashboard/assigned-clients");
    body.innerHTML = data.clients.length
      ? data.clients
          .map(
            (c) => `<tr>
              <td>${c.name}</td><td class="text-muted">${c.email}</td><td>${statusBadge(c.status)}</td>
              <td class="text-end">
                <button class="btn btn-sm btn-outline-light" onclick='openManageClientModal(${c.id}, ${JSON.stringify(c.name)})'>Manage</button>
              </td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="text-muted text-center py-4">No clients assigned to you yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

document.getElementById("addOwnClientForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("addOwnClientError");
  errorBox.classList.add("d-none");

  const payload = {
    name: document.getElementById("ownClientName").value.trim(),
    email: document.getElementById("ownClientEmail").value.trim(),
    password: document.getElementById("ownClientPassword").value,
  };

  try {
    await api("/api/employee/clients/create", { method: "POST", body: JSON.stringify(payload) });
    bootstrap.Modal.getInstance(document.getElementById("addOwnClientModal")).hide();
    document.getElementById("addOwnClientForm").reset();
    loadAssignedClients();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

// ---- Employee: Smart Inbox (scoped server-side) ----------------------------
async function loadInbox() {
  const body = document.getElementById("inboxTableBody");
  try {
    const data = await api("/api/dashboard/inbox");
    body.innerHTML = data.items.length
      ? data.items
          .map(
            (i) =>
              `<tr><td>${i.client_name}</td><td>${i.subject}</td><td>${statusBadge(i.status)}</td><td class="text-muted">${i.created_at}</td></tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="text-muted text-center py-4">Nothing here — no items for your assigned clients.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

// ---- Employee: Data Cleaner (scoped server-side) ---------------------------
async function loadDataCleaner() {
  const body = document.getElementById("dataCleanBody");
  try {
    const data = await api("/api/data/clean");
    body.innerHTML = data.rows.length
      ? data.rows
          .map((r) => `<tr><td>${r.client_name}</td><td>${r.row_label}</td><td>${statusBadge(r.flag)}</td></tr>`)
          .join("")
      : `<tr><td colspan="3" class="text-muted text-center py-4">No rows need review for your assigned clients.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="3" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

function loadPersonalSettings() {
  document.getElementById("personalSettingsName").textContent = state.user.name;
  document.getElementById("personalSettingsEmail").textContent = state.user.email;
  loadGmailConnectStatus();
}

let gmailPollTimer = null;

async function loadGmailConnectStatus() {
  const statusLine = document.getElementById("gmailConnectStatusLine");
  const btn = document.getElementById("connectGmailBtn");
  try {
    const data = await api("/api/integrations/gmail/status");
    if (data.status === "active") {
      statusLine.innerHTML = `<span class="text-accent">Connected</span> — you can send real email from any client's Manage panel.`;
      btn.textContent = "Reconnect Gmail";
    } else if (data.status === "pending") {
      statusLine.innerHTML = `<span class="text-muted">Connection started — waiting for you to finish signing in with Google in the other tab…</span>`;
      btn.textContent = "Connect Gmail";
      if (!gmailPollTimer) gmailPollTimer = setInterval(loadGmailConnectStatus, 3000);
    } else {
      statusLine.innerHTML = `<span class="text-muted">Not connected yet.</span>`;
      btn.textContent = "Connect Gmail";
      if (gmailPollTimer) { clearInterval(gmailPollTimer); gmailPollTimer = null; }
    }
    if (data.status === "active" && gmailPollTimer) { clearInterval(gmailPollTimer); gmailPollTimer = null; }
  } catch (err) {
    statusLine.innerHTML = `<span class="text-danger">${err.message}</span>`;
  }
}

document.getElementById("connectGmailBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/integrations/gmail/connect", { method: "POST" });
    window.open(data.redirect_url, "_blank", "noopener");
    loadGmailConnectStatus();
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("changePasswordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("changePasswordError");
  const successBox = document.getElementById("changePasswordSuccess");
  errorBox.classList.add("d-none");
  successBox.classList.add("d-none");

  const payload = {
    current_password: document.getElementById("currentPassword").value,
    new_password: document.getElementById("newPassword").value,
  };

  try {
    await api("/api/auth/change-password", { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("changePasswordForm").reset();
    successBox.textContent = "Password updated successfully.";
    successBox.classList.remove("d-none");
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

// ---- Employee/Admin: Collections & Chaser Campaigns ------------------------
async function loadCollections() {
  const invoicesBody = document.getElementById("collectionsInvoicesBody");
  const campaignsBody = document.getElementById("collectionsCampaignsBody");
  try {
    const data = await api("/api/dashboard/collections");

    invoicesBody.innerHTML = data.overdue_invoices.length
      ? data.overdue_invoices
          .map(
            (inv) => `<tr>
              <td>${inv.client_name}</td><td>${inv.invoice_number}</td><td>${money(inv.amount_cents)}</td>
              <td>${statusBadge(inv.status)}</td><td class="text-muted">${inv.due_date}</td>
              <td class="text-end"><button class="btn btn-sm btn-outline-light" onclick="sendChaser(${inv.client_id}, ${inv.id})">Send Chaser</button></td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="6" class="text-muted text-center py-4">No outstanding invoices for your assigned clients.</td></tr>`;

    campaignsBody.innerHTML = data.campaigns.length
      ? data.campaigns
          .map((c) => {
            const actions = [];
            if (c.status !== "active") actions.push(`<button class="btn btn-sm btn-outline-light me-1" onclick="setCampaignStatus(${c.id}, 'active')">Resume</button>`);
            if (c.status === "active") actions.push(`<button class="btn btn-sm btn-outline-light me-1" onclick="setCampaignStatus(${c.id}, 'paused')">Pause</button>`);
            if (c.status !== "completed") actions.push(`<button class="btn btn-sm btn-outline-light" onclick="setCampaignStatus(${c.id}, 'completed')">Mark Completed</button>`);
            return `<tr><td>${c.client_name}</td><td>${statusBadge(c.status)}</td><td class="text-muted">${c.notes || "—"}</td><td class="text-muted">${c.last_chased_at || "—"}</td><td class="text-end">${actions.join("")}</td></tr>`;
          })
          .join("")
      : `<tr><td colspan="5" class="text-muted text-center py-4">No chaser campaigns yet.</td></tr>`;
  } catch (err) {
    invoicesBody.innerHTML = `<tr><td colspan="6" class="text-danger text-center py-4">${err.message}</td></tr>`;
    campaignsBody.innerHTML = "";
  }
}

async function setCampaignStatus(campaignId, status) {
  try {
    await api(`/api/dashboard/collections/campaigns/${campaignId}`, { method: "PATCH", body: JSON.stringify({ status }) });
    loadCollections();
  } catch (err) {
    alert(err.message);
  }
}

async function sendChaser(clientId, invoiceId) {
  const notes = prompt("Optional note to include with this chaser message:", "");
  if (notes === null) return;
  try {
    await api("/api/dashboard/collections/chase", {
      method: "POST",
      body: JSON.stringify({ client_id: clientId, invoice_id: invoiceId, notes: notes || null }),
    });
    loadCollections();
  } catch (err) {
    alert(err.message);
  }
}

// ---- Client: Upload Center --------------------------------------------------
let selectedUploadFile = null;

document.getElementById("uploadFileInput").addEventListener("change", (e) => {
  selectedUploadFile = e.target.files[0] || null;
  document.getElementById("uploadDropzoneLabel").textContent = selectedUploadFile ? selectedUploadFile.name : "Click to choose a file";
  document.getElementById("uploadSubmitBtn").disabled = !selectedUploadFile;
});

document.getElementById("uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("uploadError");
  errorBox.classList.add("d-none");
  if (!selectedUploadFile) return;

  const formData = new FormData();
  formData.append("file", selectedUploadFile);

  try {
    const res = await fetch("/api/client/documents", {
      method: "POST",
      headers: { Authorization: `Bearer ${state.token}` },
      body: formData,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Upload failed (${res.status})`);

    document.getElementById("uploadForm").reset();
    selectedUploadFile = null;
    document.getElementById("uploadDropzoneLabel").textContent = "Click to choose a file";
    document.getElementById("uploadSubmitBtn").disabled = true;
    loadUploadCenter();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

async function loadUploadCenter() {
  const body = document.getElementById("documentsBody");
  try {
    const data = await api("/api/client/documents");
    body.innerHTML = data.documents.length
      ? data.documents
          .map(
            (d) => `<tr>
              <td>${d.original_name}</td><td class="text-muted">${(d.size_bytes / 1024).toFixed(1)} KB</td>
              <td class="text-muted">${d.created_at}</td>
              <td class="text-end"><button class="btn btn-sm btn-outline-danger" onclick="deleteDocument(${d.id})">Remove</button></td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="text-muted text-center py-4">No documents uploaded yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

async function deleteDocument(id) {
  if (!confirm("Remove this document?")) return;
  try {
    await api(`/api/client/documents/${id}`, { method: "DELETE" });
    loadUploadCenter();
  } catch (err) {
    alert(err.message);
  }
}

// ---- Client: Document Checklist ---------------------------------------------
async function loadChecklist() {
  const el = document.getElementById("checklistCard");
  const progressEl = document.getElementById("checklistProgress");
  try {
    const data = await api("/api/client/checklist");
    const completeCount = data.items.filter((i) => i.is_complete).length;
    progressEl.textContent = data.items.length ? `${completeCount} of ${data.items.length} complete` : "";
    el.innerHTML = data.items.length
      ? `<ul class="list-unstyled mb-0">${data.items
          .map(
            (item) => `<li class="mb-2" style="cursor:pointer" onclick="toggleChecklistItem(${item.id})">
              <i class="bi ${item.is_complete ? "bi-check-square text-accent" : "bi-square text-muted"} me-2"></i>
              <span class="${item.is_complete ? "text-decoration-line-through text-muted" : ""}">${item.label}</span>
            </li>`
          )
          .join("")}</ul>`
      : `<p class="text-muted mb-0">Your account officer hasn't added any checklist items yet.</p>`;
  } catch (err) {
    el.innerHTML = `<p class="text-danger">${err.message}</p>`;
  }
}

async function toggleChecklistItem(id) {
  try {
    await api(`/api/client/checklist/${id}/toggle`, { method: "PATCH" });
    loadChecklist();
  } catch (err) {
    alert(err.message);
  }
}

// ---- Client: Overview ----------------------------------------------------------
async function loadClientOverview() {
  const cardsEl = document.getElementById("clientOverviewCards");
  const officerEl = document.getElementById("clientOverviewOfficerCard");
  try {
    const data = await api("/api/client/overview");
    const cards = [
      { label: "Amount Due", value: money(data.unpaid_invoices_total_cents) },
      { label: "Outstanding Invoices", value: data.unpaid_invoices_count },
      { label: "Checklist Progress", value: `${data.checklist_complete} / ${data.checklist_total}` },
      { label: "Open Items", value: data.open_items },
    ];
    cardsEl.innerHTML = cards
      .map(
        (c) => `<div class="col-6 col-lg-3"><div class="stat-card">
          <div class="stat-value">${c.value}</div>
          <div class="stat-label">${c.label}</div>
        </div></div>`
      )
      .join("");

    officerEl.innerHTML = data.assigned_officer
      ? `<p class="text-muted mb-1">Your account officer</p>
         <p class="mb-1"><strong>${data.assigned_officer.name}</strong> — ${data.assigned_officer.email}</p>
         ${data.latest_message ? `<p class="text-muted small mb-0 mt-2">Latest message from ${data.latest_message.sender_name}: "${data.latest_message.body}"</p>` : ""}`
      : `<p class="text-muted mb-0">No account officer assigned yet.</p>`;
  } catch (err) {
    cardsEl.innerHTML = `<div class="col-12 text-danger">${err.message}</div>`;
  }
}

// ---- Client: Contact Officer + Communications History ------------------------
async function loadContactOfficer() {
  const el = document.getElementById("contactOfficerCard");
  const threadCard = document.getElementById("messageThreadCard");
  try {
    const data = await api("/api/client/overview");
    if (data.assigned_officer) {
      el.innerHTML = `<p class="text-muted mb-1">Your account officer</p>
         <p class="mb-1"><strong>${data.assigned_officer.name}</strong></p>
         <p class="text-muted mb-0">${data.assigned_officer.email}</p>`;
      threadCard.classList.remove("d-none");
      loadMessageThread(state.user.id, "messageThreadBody");
    } else {
      el.innerHTML = `<p class="text-muted mb-0">No account officer has been assigned to you yet. Your firm will notify you once one is assigned.</p>`;
      threadCard.classList.add("d-none");
    }
  } catch (err) {
    el.innerHTML = `<p class="text-danger">${err.message}</p>`;
  }
}

document.getElementById("messageForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("messageInput");
  if (!input.value.trim()) return;
  try {
    await api("/api/messages", { method: "POST", body: JSON.stringify({ client_id: state.user.id, body: input.value.trim() }) });
    input.value = "";
    loadMessageThread(state.user.id, "messageThreadBody");
  } catch (err) {
    alert(err.message);
  }
});

// ---- Shared: message thread renderer (used by Contact Officer + Manage Client modal) --
async function loadMessageThread(clientId, containerId) {
  const el = document.getElementById(containerId);
  el.innerHTML = `<p class="text-muted small mb-0">Loading…</p>`;
  try {
    const data = await api(`/api/messages/${clientId}`);
    el.innerHTML = data.messages.length
      ? data.messages
          .map((m) => {
            const mine = m.sender_id === state.user.id;
            return `<div class="message-bubble ${mine ? "mine" : ""}">
              <div class="meta">${m.sender_name} (${m.sender_role.replace("_", " ")}) — ${m.created_at}</div>
              <div>${m.body}</div>
            </div>`;
          })
          .join("")
      : `<p class="text-muted small mb-0">No messages yet.</p>`;
    el.scrollTop = el.scrollHeight;
  } catch (err) {
    el.innerHTML = `<p class="text-danger small mb-0">${err.message}</p>`;
  }
}

// ---- Client: Invoices ---------------------------------------------------------
async function loadInvoices() {
  const body = document.getElementById("invoicesBody");
  const totalEl = document.getElementById("invoicesTotalDue");
  try {
    const data = await api("/api/client/invoices");
    const totalDueCents = data.invoices
      .filter((inv) => inv.status !== "paid")
      .reduce((sum, inv) => sum + inv.amount_cents, 0);
    totalEl.textContent = totalDueCents > 0 ? `Total due: ${money(totalDueCents)}` : "";

    body.innerHTML = data.invoices.length
      ? data.invoices
          .map(
            (inv) => `<tr>
              <td>${inv.invoice_number}</td><td>${money(inv.amount_cents)}</td><td>${statusBadge(inv.status)}</td><td class="text-muted">${inv.due_date}</td>
              <td class="text-end">${
                inv.status === "paid"
                  ? '<span class="text-muted small">Paid</span>'
                  : `<button class="btn btn-sm btn-accent" onclick="payInvoice(${inv.id})">Pay Now</button>`
              }</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="text-muted text-center py-4">No invoices on file yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

async function payInvoice(id) {
  if (!confirm("Simulate payment for this invoice?")) return;
  try {
    await api(`/api/client/invoices/${id}/pay`, { method: "POST" });
    loadInvoices();
  } catch (err) {
    alert(err.message);
  }
}

// ---- Manage Client modal (Employee/Admin/Super Admin) -------------------------
let manageClientId = null;

function openManageClientModal(clientId, clientName) {
  manageClientId = clientId;
  document.getElementById("manageClientName").textContent = clientName;
  document.getElementById("manageAiReportBody").innerHTML = `<p class="text-muted small mb-0">No report generated yet.</p>`;

  document.querySelectorAll("#manageClientTabs .nav-link").forEach((b) => b.classList.remove("active"));
  document.querySelector('#manageClientTabs .nav-link[data-tab="checklist"]').classList.add("active");
  document.querySelectorAll(".manage-tab-pane").forEach((p) => p.classList.add("d-none"));
  document.getElementById("manageTab-checklist").classList.remove("d-none");

  new bootstrap.Modal(document.getElementById("manageClientModal")).show();
  loadManageChecklist();
}

document.querySelectorAll("#manageClientTabs .nav-link").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#manageClientTabs .nav-link").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".manage-tab-pane").forEach((p) => p.classList.add("d-none"));
    document.getElementById(`manageTab-${btn.dataset.tab}`).classList.remove("d-none");

    if (btn.dataset.tab === "checklist") loadManageChecklist();
    if (btn.dataset.tab === "invoices") loadManageInvoices();
    if (btn.dataset.tab === "documents") loadManageDocuments();
    if (btn.dataset.tab === "ai-suggestions") loadManageAiSuggestions();
    if (btn.dataset.tab === "messages") {
      loadMessageThread(manageClientId, "manageMessageThread");
      loadGmailSendAvailability();
    }
  });
});

async function loadGmailSendAvailability() {
  const statusLine = document.getElementById("gmailSendStatusLine");
  const form = document.getElementById("gmailSendForm");
  try {
    const data = await api("/api/integrations/gmail/status");
    if (data.status === "active") {
      statusLine.textContent = "";
      form.classList.remove("d-none");
    } else {
      statusLine.innerHTML = `Connect your Gmail account in <strong>Personal Settings</strong> to send real email from here.`;
      form.classList.add("d-none");
    }
  } catch (err) {
    statusLine.innerHTML = `<span class="text-danger">${err.message}</span>`;
    form.classList.add("d-none");
  }
}

document.getElementById("gmailSendForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const subject = document.getElementById("gmailSendSubject").value.trim();
  const body = document.getElementById("gmailSendBody").value.trim();
  if (!subject || !body) return;

  try {
    await api("/api/dashboard/integrations/gmail/send", {
      method: "POST",
      body: JSON.stringify({ client_id: manageClientId, subject, body }),
    });
    document.getElementById("gmailSendForm").reset();
    loadMessageThread(manageClientId, "manageMessageThread");
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("generateAiReportBtn").addEventListener("click", async () => {
  const body = document.getElementById("manageAiReportBody");
  const btn = document.getElementById("generateAiReportBtn");
  btn.disabled = true;
  btn.textContent = "Generating…";
  body.innerHTML = `<p class="text-muted small mb-0">Asking Gemini for a summary…</p>`;
  try {
    const data = await api(`/api/dashboard/reports/client-summary?client_id=${manageClientId}`);
    body.innerHTML = `<p class="mb-0" style="white-space: pre-wrap;">${data.summary}</p>`;
  } catch (err) {
    body.innerHTML = `<p class="text-danger small mb-0">${err.message}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate Report";
  }
});

const AI_SUGGESTION_LABELS = {
  reconciliation_flag: "Reconciliation",
  client_reminder: "Client Reminder",
  invoice_draft: "Invoice Draft",
};

async function loadManageAiSuggestions() {
  const list = document.getElementById("manageAiSuggestionsList");
  list.innerHTML = `<p class="text-muted small">Loading…</p>`;
  try {
    const data = await api(`/api/dashboard/ai-suggestions?client_id=${manageClientId}`);
    list.innerHTML = data.suggestions.length
      ? data.suggestions
          .map((s) => {
            const actions =
              s.status === "pending"
                ? `<div class="mt-2">
                     <button class="btn btn-sm btn-accent me-2" onclick="reviewAiSuggestion(${s.id}, 'approve')">Approve</button>
                     <button class="btn btn-sm btn-outline-danger" onclick="reviewAiSuggestion(${s.id}, 'reject')">Reject</button>
                   </div>`
                : `<div class="mt-2">${statusBadge(s.status)}</div>`;
            return `<div class="suggestion-card">
              <div class="suggestion-type">${AI_SUGGESTION_LABELS[s.suggestion_type] || s.suggestion_type}</div>
              <div class="fw-semibold">${s.title}</div>
              <div class="text-muted small">${s.detail}</div>
              ${actions}
            </div>`;
          })
          .join("")
      : `<p class="text-muted small mb-0">No suggestions yet — click "Run AI Scan" to generate some.</p>`;
  } catch (err) {
    list.innerHTML = `<p class="text-danger small">${err.message}</p>`;
  }
}

document.getElementById("runAiScanBtn").addEventListener("click", async () => {
  try {
    await api("/api/dashboard/ai-suggestions/generate", { method: "POST", body: JSON.stringify({ client_id: manageClientId }) });
    loadManageAiSuggestions();
  } catch (err) {
    alert(err.message);
  }
});

async function reviewAiSuggestion(id, action) {
  try {
    await api(`/api/dashboard/ai-suggestions/${id}/${action}`, { method: "POST" });
    loadManageAiSuggestions();
  } catch (err) {
    alert(err.message);
  }
}

async function loadManageDocuments() {
  const body = document.getElementById("manageDocumentsBody");
  try {
    const data = await api(`/api/dashboard/documents?client_id=${manageClientId}`);
    body.innerHTML = data.documents.length
      ? data.documents
          .map(
            (d) => `<tr>
              <td>${d.original_name}</td><td class="text-muted">${(d.size_bytes / 1024).toFixed(1)} KB</td>
              <td class="text-muted">${d.created_at}</td>
              <td class="text-end"><a class="btn btn-sm btn-outline-light" href="/api/dashboard/documents/${d.id}/download" onclick="return downloadManageDocument(event, ${d.id})">Download</a></td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="text-muted text-center py-4">No documents uploaded yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

function downloadManageDocument(event, documentId) {
  event.preventDefault();
  fetch(`/api/dashboard/documents/${documentId}/download`, { headers: { Authorization: `Bearer ${state.token}` } })
    .then(async (res) => {
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Download failed");
      const blob = await res.blob();
      const disposition = res.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : "document";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    })
    .catch((err) => alert(err.message));
  return false;
}

async function loadManageChecklist() {
  const list = document.getElementById("manageChecklistList");
  try {
    const data = await api(`/api/dashboard/checklist?client_id=${manageClientId}`);
    list.innerHTML = data.items.length
      ? data.items
          .map(
            (item) => `<li class="d-flex justify-content-between align-items-center mb-2">
              <span><i class="bi ${item.is_complete ? "bi-check-square text-accent" : "bi-square text-muted"} me-2"></i>${item.label}</span>
              <button class="btn btn-sm btn-outline-danger" onclick="deleteManageChecklistItem(${item.id})">Remove</button>
            </li>`
          )
          .join("")
      : `<li class="text-muted">No checklist items yet.</li>`;
  } catch (err) {
    list.innerHTML = `<li class="text-danger">${err.message}</li>`;
  }
}

document.getElementById("manageChecklistForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("manageChecklistLabel");
  if (!input.value.trim()) return;
  try {
    await api("/api/dashboard/checklist", { method: "POST", body: JSON.stringify({ client_id: manageClientId, label: input.value.trim() }) });
    input.value = "";
    loadManageChecklist();
  } catch (err) {
    alert(err.message);
  }
});

async function deleteManageChecklistItem(id) {
  try {
    await api(`/api/dashboard/checklist/${id}`, { method: "DELETE" });
    loadManageChecklist();
  } catch (err) {
    alert(err.message);
  }
}

async function loadManageInvoices() {
  const body = document.getElementById("manageInvoicesBody");
  try {
    const data = await api(`/api/dashboard/invoices?client_id=${manageClientId}`);
    body.innerHTML = data.invoices.length
      ? data.invoices
          .map((inv) => `<tr><td>${inv.invoice_number}</td><td>${money(inv.amount_cents)}</td><td>${statusBadge(inv.status)}</td><td class="text-muted">${inv.due_date}</td></tr>`)
          .join("")
      : `<tr><td colspan="4" class="text-muted text-center py-4">No invoices yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${err.message}</td></tr>`;
  }
}

document.getElementById("manageInvoiceForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const invoiceNumber = document.getElementById("manageInvoiceNumber").value.trim();
  const amount = parseFloat(document.getElementById("manageInvoiceAmount").value);
  const dueDate = document.getElementById("manageInvoiceDue").value;
  if (!invoiceNumber || !amount || !dueDate) return;

  try {
    await api("/api/dashboard/invoices", {
      method: "POST",
      body: JSON.stringify({
        client_id: manageClientId,
        invoice_number: invoiceNumber,
        amount_cents: Math.round(amount * 100),
        due_date: dueDate,
      }),
    });
    document.getElementById("manageInvoiceForm").reset();
    loadManageInvoices();
  } catch (err) {
    alert(err.message);
  }
});

document.getElementById("manageMessageForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("manageMessageInput");
  if (!input.value.trim()) return;
  try {
    await api("/api/messages", { method: "POST", body: JSON.stringify({ client_id: manageClientId, body: input.value.trim() }) });
    input.value = "";
    loadMessageThread(manageClientId, "manageMessageThread");
  } catch (err) {
    alert(err.message);
  }
});

// ---- Client: Personal Settings ------------------------------------------------
function loadClientSettings() {
  document.getElementById("clientSettingsName").textContent = state.user.name;
  document.getElementById("clientSettingsEmail").textContent = state.user.email;
}

document.getElementById("clientChangePasswordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("clientChangePasswordError");
  const successBox = document.getElementById("clientChangePasswordSuccess");
  errorBox.classList.add("d-none");
  successBox.classList.add("d-none");

  const payload = {
    current_password: document.getElementById("clientCurrentPassword").value,
    new_password: document.getElementById("clientNewPassword").value,
  };

  try {
    await api("/api/auth/change-password", { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("clientChangePasswordForm").reset();
    successBox.textContent = "Password updated successfully.";
    successBox.classList.remove("d-none");
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
  }
});

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------
if (state.token && state.user) {
  bootApp();
}
