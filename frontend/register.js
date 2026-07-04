/**
 * register.js
 * -----------
 * Handles the public signup page's three self-service paths:
 *   - Firm       -> POST /api/auth/register        (creates an 'admin')
 *   - Officer    -> POST /api/auth/register-officer (creates an independent 'employee')
 *   - Client     -> POST /api/auth/register-client  (creates a 'client', optional officer pick)
 *
 * All three log the new user straight into the app shell on success --
 * same sessionStorage keys the SPA (app.js) already reads on load, so
 * /login boots directly into the dashboard with no second login step.
 */

// ---- Tab switching ---------------------------------------------------------
function activateTab(tabName) {
  const btn = document.querySelector(`#registerTabs .nav-link[data-tab="${tabName}"]`);
  if (!btn) return;
  document.querySelectorAll("#registerTabs .nav-link").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  document.querySelectorAll(".register-pane").forEach((p) => p.classList.add("d-none"));
  document.getElementById(`pane-${tabName}`).classList.remove("d-none");
}

document.querySelectorAll("#registerTabs .nav-link").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

// Marketing links can deep-link a tab, e.g. /register?tab=officer or ?tab=client.
const requestedTab = new URLSearchParams(window.location.search).get("tab");
if (requestedTab && ["firm", "officer", "client"].includes(requestedTab)) {
  activateTab(requestedTab);
}

// ---- Shared submit helper ---------------------------------------------------
async function submitRegistration({ endpoint, payload, errorBox, submitBtn, busyText, idleText }) {
  errorBox.classList.add("d-none");
  submitBtn.disabled = true;
  submitBtn.textContent = busyText;

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const contentType = res.headers.get("content-type") || "";
    const body = contentType.includes("application/json") ? await res.json() : null;

    if (!res.ok) {
      throw new Error((body && body.detail) || `Registration failed (${res.status})`);
    }

    sessionStorage.setItem("acc_token", body.access_token);
    sessionStorage.setItem("acc_user", JSON.stringify(body.user));
    window.location.href = "/login";
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("d-none");
    submitBtn.disabled = false;
    submitBtn.textContent = idleText;
  }
}

function passwordsOk(password, confirm, errorBox) {
  if (password !== confirm) {
    errorBox.textContent = "Passwords don't match.";
    errorBox.classList.remove("d-none");
    return false;
  }
  if (password.length < 6) {
    errorBox.textContent = "Password must be at least 6 characters.";
    errorBox.classList.remove("d-none");
    return false;
  }
  return true;
}

// ---- Firm (Admin) -----------------------------------------------------------
document.getElementById("registerFirmForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("firmError");
  const submitBtn = document.getElementById("firmSubmitBtn");
  errorBox.classList.add("d-none");

  const password = document.getElementById("firmPassword").value;
  const passwordConfirm = document.getElementById("firmPasswordConfirm").value;
  if (!passwordsOk(password, passwordConfirm, errorBox)) return;

  await submitRegistration({
    endpoint: "/api/auth/register",
    payload: {
      company_name: document.getElementById("firmCompany").value.trim(),
      name: document.getElementById("firmName").value.trim(),
      email: document.getElementById("firmEmail").value.trim(),
      password,
    },
    errorBox,
    submitBtn,
    busyText: "Creating your firm's account…",
    idleText: "Create my firm's account",
  });
});

// ---- Account Officer (independent Employee) ---------------------------------
document.getElementById("registerOfficerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("officerError");
  const submitBtn = document.getElementById("officerSubmitBtn");
  errorBox.classList.add("d-none");

  const password = document.getElementById("officerPassword").value;
  const passwordConfirm = document.getElementById("officerPasswordConfirm").value;
  if (!passwordsOk(password, passwordConfirm, errorBox)) return;

  await submitRegistration({
    endpoint: "/api/auth/register-officer",
    payload: {
      name: document.getElementById("officerName").value.trim(),
      email: document.getElementById("officerEmail").value.trim(),
      company_name: document.getElementById("officerCompany").value.trim() || null,
      password,
    },
    errorBox,
    submitBtn,
    busyText: "Setting up your officer profile…",
    idleText: "Create my Account Officer profile",
  });
});

// ---- Client -------------------------------------------------------------------
async function loadOfficerOptions() {
  const select = document.getElementById("clientOfficerSelect");
  try {
    const res = await fetch("/api/public/independent-officers");
    const data = await res.json();
    data.officers.forEach((o) => {
      const opt = document.createElement("option");
      opt.value = o.id;
      opt.textContent = o.company_name ? `${o.name} — ${o.company_name}` : o.name;
      select.appendChild(opt);
    });
  } catch {
    // Directory is a nice-to-have; signup still works unassigned if this fails.
  }
}
loadOfficerOptions();

document.getElementById("registerClientForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errorBox = document.getElementById("clientError");
  const submitBtn = document.getElementById("clientSubmitBtn");
  errorBox.classList.add("d-none");

  const password = document.getElementById("clientPassword").value;
  const passwordConfirm = document.getElementById("clientPasswordConfirm").value;
  if (!passwordsOk(password, passwordConfirm, errorBox)) return;

  const officerValue = document.getElementById("clientOfficerSelect").value;

  await submitRegistration({
    endpoint: "/api/auth/register-client",
    payload: {
      name: document.getElementById("clientName").value.trim(),
      email: document.getElementById("clientEmail").value.trim(),
      officer_id: officerValue ? Number(officerValue) : null,
      password,
    },
    errorBox,
    submitBtn,
    busyText: "Creating your client account…",
    idleText: "Create my client account",
  });
});
