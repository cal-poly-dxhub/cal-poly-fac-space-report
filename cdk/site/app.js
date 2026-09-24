"use strict";

// Everything this page knows about the deployment comes from config.json, which the
// CDK stack writes next to it: { apiUrl, loginUrl, clientId }.

const TABLES = ["Property", "PropertyDetails", "SpaceUsage", "SpaceStandard", "BaseCodes"];
const NUMERIC_COLUMNS = new Set(["Area 14", "Area 15", "EFFC"]);
const POLL_EVERY_MS = 4000;
const GIVE_UP_AFTER_MS = 16 * 60 * 1000; // the pull function is stopped at 15 minutes
const SILENT_TRIED = "silent-sign-in-tried";

const $ = (id) => document.getElementById(id);

let config;
let idToken = null; // memory only. AWS: "Don't store ID and access tokens in local storage."
let report = null; // { blob, filename }
let dataReady = false;
let busy = false;
let ticker = null;

// ---------------------------------------------------------------- small helpers

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").className = `status ${kind}`.trim();
}

function setBusy(value) {
  busy = value;
  $("refresh").disabled = busy;
  $("generate").disabled = busy || !dataReady;
  // Whichever step comes next is the filled button.
  $("refresh").classList.toggle("primary", !dataReady);
  $("generate").classList.toggle("primary", dataReady);
}

function when(iso) {
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function elapsed(since) {
  const seconds = Math.floor((Date.now() - since) / 1000);
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function base64Url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(byteCount) {
  return base64Url(crypto.getRandomValues(new Uint8Array(byteCount)));
}

// ---------------------------------------------------------------- sign-in
// Authorization code flow with PKCE against Cognito's managed login. Cognito treats
// PKCE as optional, so it is this page that makes it happen.

const redirectUri = () => `${location.origin}/`;

async function signIn({ silent = false } = {}) {
  const verifier = randomString(32);
  const state = randomString(16);
  sessionStorage.setItem("pkce", JSON.stringify({ verifier, state }));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  const query = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri(),
    scope: "openid email",
    state,
    code_challenge_method: "S256",
    code_challenge: base64Url(new Uint8Array(digest)),
  });
  // prompt=none asks Cognito to reuse its own session if there is one and to come
  // straight back with login_required if there is not. It saves a click on reload.
  if (silent) query.set("prompt", "none");
  location.assign(`${config.loginUrl}/oauth2/authorize?${query}`);
}

async function finishSignIn(params) {
  const pkce = JSON.parse(sessionStorage.getItem("pkce") || "null");
  sessionStorage.removeItem("pkce");
  if (!pkce || pkce.state !== params.get("state")) {
    showGate("That sign-in did not start on this page, so it was not accepted. Sign in again.", true);
    return;
  }
  const response = await fetch(`${config.loginUrl}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code: params.get("code"),
      redirect_uri: redirectUri(),
      code_verifier: pkce.verifier,
    }),
  });
  if (!response.ok) {
    showGate(`Cognito would not exchange the sign-in code (${response.status}). Sign in again.`, true);
    return;
  }
  idToken = (await response.json()).id_token; // the refresh token is deliberately dropped
  sessionStorage.removeItem(SILENT_TRIED);
  showWorkbench();
}

function signOut() {
  sessionStorage.setItem(SILENT_TRIED, "1");
  const query = new URLSearchParams({ client_id: config.clientId, logout_uri: redirectUri() });
  location.assign(`${config.loginUrl}/logout?${query}`);
}

function claims() {
  const payload = idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(decodeURIComponent(escape(atob(payload))));
}

// ---------------------------------------------------------------- the two views

function showGate(message, isFailure = false) {
  idToken = null;
  clearInterval(ticker);
  $("workbench").hidden = true;
  $("account").hidden = true;
  $("gate").hidden = false;
  if (message) $("gate-message").textContent = message;
  $("gate-message").classList.toggle("failure", isFailure);
  setStatus(isFailure ? "Not signed in." : "Sign in to begin.");
}

function showWorkbench() {
  const { email, exp } = claims();
  $("gate").hidden = true;
  $("workbench").hidden = false;
  $("account").hidden = false;
  $("account-email").textContent = email;
  $("ref-date").value = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local
  renderDetails({
    "Signed in as": email,
    "Session ends": when(exp * 1000),
    "API": config.apiUrl,
    "Sign-in domain": config.loginUrl,
    "App client": config.clientId,
  });
  renderTables(null);
  setBusy(false);
  loadLastRefresh();
}

function renderDetails(details) {
  $("deployment-details").replaceChildren(
    ...Object.entries(details).flatMap(([name, value]) => {
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = name;
      description.textContent = value;
      return [term, description];
    }),
  );
}

// ---------------------------------------------------------------- API

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(config.apiUrl.replace(/\/$/, "") + path, {
      ...options,
      headers: { Authorization: idToken },
    });
  } catch {
    return { ok: false, status: 0, text: async () => "The API could not be reached. Check the network and try again." };
  }
  if (response.status === 401 || response.status === 403) {
    showGate("Your session ended. Sign in again.");
    return null;
  }
  return response;
}

async function explain(response) {
  if (response.status === 429) return "Too many requests. Wait a few seconds and try again.";
  const body = (await response.text()).trim();
  try {
    const parsed = JSON.parse(body);
    return parsed.message || parsed.error || body;
  } catch {
    return body || `The API answered ${response.status}.`;
  }
}

// ---------------------------------------------------------------- Planon data

function renderTables(rowCounts) {
  $("table-rows").replaceChildren(
    ...TABLES.map((name) => {
      const row = document.createElement("tr");
      const label = document.createElement("td");
      const count = document.createElement("td");
      label.textContent = name;
      count.className = "num";
      if (rowCounts && name in rowCounts) count.textContent = rowCounts[name].toLocaleString();
      else {
        count.textContent = "-";
        count.classList.add("pending");
      }
      row.append(label, count);
      return row;
    }),
  );
}

function showMarker(marker) {
  const summary = $("data-summary");
  summary.classList.remove("failure");
  if (!marker.finished_at) {
    dataReady = false;
    renderTables(null);
    summary.textContent =
      "No finished refresh on record. Refresh from Planon to load the tables. " +
      "If one was started in the last 15 minutes, it may still be running.";
    return;
  }
  if (marker.status === "failed") {
    dataReady = false;
    renderTables(null);
    summary.classList.add("failure");
    summary.textContent = `The last refresh failed on ${when(marker.finished_at)}. ${marker.error}`;
    return;
  }
  dataReady = true;
  renderTables(marker.rows);
  summary.textContent = `Last refreshed ${when(marker.finished_at)}.`;
}

async function readMarker() {
  const response = await api("/refresh");
  if (!response) return null;
  if (!response.ok) throw new Error(await explain(response));
  return JSON.parse(await response.text());
}

async function loadLastRefresh() {
  try {
    const marker = await readMarker();
    if (!marker) return;
    showMarker(marker);
    setStatus(dataReady ? "Ready." : "Planon data is needed before a report can be generated.");
  } catch (error) {
    $("data-summary").textContent = "The last refresh could not be checked.";
    setStatus(`Could not read the refresh status. ${error.message}`, "failure");
  }
  setBusy(false);
}

async function startRefresh() {
  setBusy(true);
  setStatus("Asking for a refresh.");
  const response = await api("/refresh", { method: "POST" });
  if (!response) return;
  if (response.status !== 202) {
    setStatus(`The refresh did not start. ${await explain(response)}`, "failure");
    setBusy(false);
    return;
  }
  const startedAt = Date.now();
  dataReady = false;
  renderTables(null);
  $("refresh-progress").hidden = false;
  $("data-summary").classList.remove("failure");
  $("data-summary").textContent = `Refreshing from Planon. Started ${when(startedAt)}.`;
  const tick = () => setStatus(`Refreshing from Planon. ${elapsed(startedAt)} elapsed.`);
  tick();
  ticker = setInterval(tick, 1000);
  setTimeout(() => poll(startedAt), POLL_EVERY_MS);
}

async function poll(startedAt) {
  let marker;
  try {
    marker = await readMarker();
  } catch (error) {
    return endRefresh(`Lost track of the refresh. ${error.message}`, "failure");
  }
  if (!marker) return endRefresh(); // signed out while waiting
  if (marker.finished_at) {
    showMarker(marker);
    if (marker.status === "failed") return endRefresh("The refresh failed. Details are under Planon data.", "failure");
    const total = Object.values(marker.rows).reduce((sum, count) => sum + count, 0);
    return endRefresh(`Refresh finished. ${total.toLocaleString()} rows in ${TABLES.length} tables.`, "success");
  }
  if (Date.now() - startedAt > GIVE_UP_AFTER_MS) {
    showMarker(marker);
    return endRefresh(
      "No result after 16 minutes. The pull function is stopped at 15, so this refresh did not finish. Its log group has the reason.",
      "failure",
    );
  }
  setTimeout(() => poll(startedAt), POLL_EVERY_MS);
}

function endRefresh(message, kind) {
  clearInterval(ticker);
  $("refresh-progress").hidden = true;
  if (message) setStatus(message, kind);
  if (idToken) setBusy(false);
}

// ---------------------------------------------------------------- report

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char !== '"') field += char;
      else if (text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") field += char;
  }
  if (field !== "" || row.length) rows.push([...row, field]);
  return rows;
}

// The CSV follows Planon's export: a header, then for each CSU center a band row,
// its facilities and an unlabeled subtotal, then an unlabeled grand total. Every
// line ends with a comma, so the last field of each row is an empty extra.
function renderReport(rows) {
  const [header, ...body] = rows.map((row) => row.slice(0, -1));
  const numeric = header.map((name) => NUMERIC_COLUMNS.has(name));
  const nameColumn = header.indexOf("FAC NAME");
  let facilities = 0;

  const headRow = document.createElement("tr");
  header.forEach((name, column) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = name;
    if (numeric[column]) cell.className = "num";
    headRow.append(cell);
  });
  const head = document.createElement("thead");
  head.append(headRow);

  const tbody = document.createElement("tbody");
  body.forEach((cells, index) => {
    const tableRow = document.createElement("tr");
    if (/^\d{4}-\d{2}-\d{2} : /.test(cells[0])) {
      tableRow.className = "center";
      const cell = document.createElement("td");
      cell.colSpan = header.length;
      const label = document.createElement("span");
      label.className = "center-label";
      label.textContent = cells[0];
      cell.append(label);
      tableRow.append(cell);
    } else {
      const isTotal = cells[0] === "";
      if (isTotal) tableRow.className = index === body.length - 1 ? "total" : "subtotal";
      else facilities += 1;
      cells.forEach((value, column) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (numeric[column]) cell.className = "num";
        if (isTotal && column === nameColumn) {
          const label = document.createElement("span");
          label.className = "row-label";
          label.textContent = tableRow.className === "total" ? "Total" : "Subtotal";
          cell.append(label);
        }
        tableRow.append(cell);
      });
    }
    tbody.append(tableRow);
  });

  $("report-grid").replaceChildren(head, tbody);
  $("report-empty").hidden = true;
  $("report-wrap").hidden = false;
  $("grid-note").hidden = false;
  return facilities;
}

function showReportFailure(message) {
  report = null;
  $("download").disabled = true;
  $("report-wrap").hidden = true;
  $("grid-note").hidden = true;
  const empty = $("report-empty");
  empty.hidden = false;
  empty.classList.add("failure");
  empty.replaceChildren(Object.assign(document.createElement("p"), { textContent: message }));
  setStatus("The report was not generated.", "failure");
}

async function generate() {
  const refDate = $("ref-date").value;
  if (!refDate) {
    setStatus("Choose a reference date first.", "failure");
    $("ref-date").focus();
    return;
  }
  setBusy(true);
  setStatus(`Generating the report for ${refDate}.`);
  const response = await api(`/generate?ref_date=${encodeURIComponent(refDate)}`);
  if (!response) return;
  if (!response.ok) {
    showReportFailure(await explain(response));
    setBusy(false);
    // 409: the data changed under this page, e.g. a refresh started in another tab.
    if (response.status === 409) loadLastRefresh();
    return;
  }
  const text = await response.text();
  const named = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
  report = { blob: new Blob([text], { type: "text/csv" }), filename: named ? named[1] : `report-${refDate}.csv` };
  $("report-empty").classList.remove("failure");
  const facilities = renderReport(parseCsv(text));
  $("download").disabled = false;
  setStatus(`Report ready. ${facilities.toLocaleString()} facilities as of ${refDate}.`, "success");
  setBusy(false);
}

function download() {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(report.blob);
  link.download = report.filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

// ---------------------------------------------------------------- start

async function start() {
  $("sign-in").addEventListener("click", () => signIn());
  $("sign-out").addEventListener("click", signOut);
  $("refresh").addEventListener("click", startRefresh);
  $("generate").addEventListener("click", generate);
  $("download").addEventListener("click", download);

  try {
    config = await (await fetch("config.json", { cache: "no-store" })).json();
  } catch {
    showGate("config.json is missing, so this page does not know where its API is. Deploy the stack again.", true);
    $("sign-in").disabled = true;
    return;
  }

  const params = new URLSearchParams(location.search);
  if (params.has("code") || params.has("error")) history.replaceState(null, "", location.pathname);

  if (params.has("code")) return finishSignIn(params);
  if (params.get("error") === "login_required") return showGate(); // the silent attempt found no session
  if (params.has("error")) {
    return showGate(`Sign-in did not complete. ${params.get("error_description") || params.get("error")}`, true);
  }
  if (!sessionStorage.getItem(SILENT_TRIED)) {
    sessionStorage.setItem(SILENT_TRIED, "1");
    return signIn({ silent: true });
  }
  showGate();
}

start();
