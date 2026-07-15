const state = {
  overview: null,
  profile: window.localStorage.getItem("jobsiphon-profile") || null,
  list: "current",
  query: "",
  minScore: 0,
  lastLogId: 0,
  lastRunning: false,
  lastCurrentCount: null,
  pendingConfirmation: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeJobUrl(value = "") {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch (_) {
    return "#";
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3600);
}

function humanDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function elapsed(value) {
  if (!value) return "Not running";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours) return `${hours}h ${minutes}m elapsed`;
  if (minutes) return `${minutes}m ${remainder}s elapsed`;
  return `${remainder}s elapsed`;
}

function renderCheck(id, good) {
  const node = $(id);
  node.textContent = good ? "✓" : "!";
  node.classList.toggle("good", good);
  node.classList.toggle("bad", !good);
}

function renderOverview(data) {
  const previousRunning = state.lastRunning;
  const previousCurrentCount = state.lastCurrentCount;
  state.overview = data;
  if (!state.profile) state.profile = data.selected_profile.slug;
  const pipeline = data.pipeline;
  state.lastRunning = pipeline.running;
  state.lastCurrentCount = data.counts.current;

  $("#current-count").textContent = data.counts.current.toLocaleString();
  $("#master-count").textContent = data.counts.master.toLocaleString();
  $("#seen-count").textContent = data.counts.seen.count.toLocaleString();
  $("#seen-latest").textContent = data.counts.seen.latest
    ? `Latest evaluation ${humanDate(data.counts.seen.latest)}`
    : "No evaluation history";
  $("#model-name").textContent = data.model;
  renderProfiles(data.profiles, data.selected_profile);
  $("#llm-status").textContent = data.llm.online ? data.llm.label : "Offline";
  $("#llm-dot").className = `mini-dot ${data.llm.online ? "online" : ""}`;

  $("#run-stage").textContent = pipeline.stage.replace("-", " ");
  $("#run-title").textContent = runTitle(pipeline);
  $("#run-detail").textContent = pipeline.detail;
  $("#run-mode").textContent = pipeline.mode
    ? `${pipeline.mode === "full" ? "Full discovery" : "Cached scoring"} · ${pipeline.profile || data.selected_profile.slug}${pipeline.pid ? ` · PID ${pipeline.pid}` : ""}`
    : "No active run";
  const progressBar = $("#progress-bar");
  const isLiveStream = pipeline.running && pipeline.percent == null;
  progressBar.classList.toggle("indeterminate", isLiveStream);
  progressBar.style.width = isLiveStream ? "" : `${pipeline.percent || 0}%`;
  $("#progress-percent").textContent = isLiveStream
    ? "Live"
    : pipeline.percent == null
      ? "—"
      : `${pipeline.percent}%`;
  $("#run-time").textContent = pipeline.running
    ? elapsed(pipeline.started_at)
    : pipeline.ended_at
      ? `Ended ${new Date(pipeline.ended_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
      : "Not running";

  $("#start-full").disabled = pipeline.running;
  $("#start-score").disabled = pipeline.running || !data.cache.available;
  $("#profile-select").disabled = pipeline.running;
  $("#stop-run").hidden = !pipeline.running;
  $("#clean-output").disabled = pipeline.running;
  $("#reset-seen").disabled = pipeline.running;

  const headerDot = $("#header-status-dot");
  headerDot.className = "status-dot";
  if (pipeline.running) headerDot.classList.add("running");
  else if (pipeline.stage === "failed") headerDot.classList.add("failed");
  else headerDot.classList.add("online");
  $("#header-status").textContent = pipeline.running
    ? pipeline.stage.replace("-", " ")
    : pipeline.stage === "failed"
      ? "Failed"
      : "Idle";

  renderCheck("#resume-check", data.resume.configured);
  $("#resume-label").textContent = data.resume.configured
    ? data.resume.filename
    : `Missing: ${data.resume.filename}`;
  renderCheck("#cache-check", data.cache.available);
  $("#cache-label").textContent = data.cache.available
    ? `Updated ${humanDate(data.cache.updated_at)}`
    : "Empty";
  $("#engine-name").textContent = data.llm.label;
  renderCheck("#engine-check", data.llm.online && data.llm.configured);
  $("#engine-label").textContent = !data.llm.configured
    ? "API key missing"
    : data.llm.online
      ? `${data.llm.models.length} model${data.llm.models.length === 1 ? "" : "s"} available`
      : "Offline";
  const wellfound = data.wellfound;
  renderCheck("#wellfound-check", !wellfound || !wellfound.enabled || wellfound.configured);
  $("#wellfound-label").textContent = !wellfound
    ? "Restart to refresh"
    : !wellfound.enabled
      ? "Disabled for profile"
      : wellfound.configured
        ? "Apify connected"
        : "APIFY_TOKEN missing";

  const counters = pipeline.counters;
  $("#run-counters").hidden = !counters || !pipeline.mode;
  if (counters) {
    $("#search-counter").textContent = `${counters.searches_done}/${counters.searches_total}`;
    $("#found-counter").textContent = counters.found.toLocaleString();
    $("#unique-counter").textContent = counters.unique.toLocaleString();
    $("#scored-counter").textContent = `${counters.scored}/${counters.queued}`;
    $("#match-counter").textContent = counters.matches.toLocaleString();
  }

  if (
    (previousRunning && !pipeline.running) ||
    (pipeline.running && previousCurrentCount != null && previousCurrentCount !== data.counts.current)
  ) loadJobs();
}

function renderProfiles(profiles, selected) {
  const select = $("#profile-select");
  const signature = profiles.map((profile) => profile.slug).join("|");
  if (select.dataset.signature !== signature) {
    select.innerHTML = profiles
      .map((profile) => `<option value="${escapeHtml(profile.slug)}">${escapeHtml(profile.name)}</option>`)
      .join("");
    select.dataset.signature = signature;
  }
  select.value = selected.slug;
  $("#profile-description").textContent = selected.description;
  $("#queue-profile-name").textContent = `— ${selected.name}`;
}

function runTitle(pipeline) {
  if (pipeline.running) {
    const titles = {
      starting: "Starting",
      verifying: "Checking saved jobs",
      scraping: "Searching job boards",
      streaming: "Searching and scoring",
      loading: "Loading cache",
      filtering: "Filtering jobs",
      ranking: "Ranking jobs",
      scoring: "Scoring jobs",
      stopping: "Stopping",
    };
    return titles[pipeline.stage] || "Running";
  }
  if (pipeline.stage === "complete") return "Complete";
  if (pipeline.stage === "failed") return "Failed";
  if (pipeline.stage === "stopped") return "Stopped";
  return "Idle";
}

async function refreshOverview() {
  try {
    const query = state.profile ? `?profile=${encodeURIComponent(state.profile)}` : "";
    renderOverview(await api(`/api/overview${query}`));
  } catch (error) {
    if (state.profile && /unknown job profile/i.test(error.message)) {
      state.profile = null;
      window.localStorage.removeItem("jobsiphon-profile");
      try {
        renderOverview(await api("/api/overview"));
        return;
      } catch (_) {
        // Fall through to the visible connection error below.
      }
    }
    $("#header-status").textContent = "Disconnected";
    $("#header-status-dot").className = "status-dot failed";
  }
}

function renderJobs(payload) {
  const tbody = $("#job-rows");
  const empty = $("#job-empty");
  $("#job-result-count").textContent = `${payload.total} result${payload.total === 1 ? "" : "s"}`;
  empty.hidden = payload.items.length > 0;
  tbody.innerHTML = payload.items.map((job) => {
    const score = Number.parseInt(job.score || "0", 10) || 0;
    const title = escapeHtml(job.title || "Untitled role");
    const company = escapeHtml(job.company || "Unknown company");
    const location = escapeHtml(job.location || "Location not listed");
    const source = escapeHtml(job.source || "source");
    const signals = escapeHtml(job.fit_signals || "");
    const reason = escapeHtml(job.reason || "No scoring explanation was recorded.");
    const url = escapeHtml(safeJobUrl(job.url));
    return `
      <tr>
        <td><span class="score-chip ${score >= 70 ? "high" : ""}">${score}</span></td>
        <td>
          <a class="job-title" href="${url}" target="_blank" rel="noopener noreferrer">${title}</a>
          <span class="job-company">${company}</span><br />
          <span class="job-source">${source}</span>
        </td>
        <td><span class="job-location">${location}</span></td>
        <td>
          ${signals ? `<div class="fit-signals">${signals}</div>` : ""}
          <div class="job-reason">${reason}</div>
        </td>
        <td><a class="open-job" href="${url}" target="_blank" rel="noopener noreferrer" aria-label="Open ${title}">↗</a></td>
      </tr>`;
  }).join("");
}

async function loadJobs() {
  const params = new URLSearchParams({
    profile: state.profile || "",
    list: state.list,
    q: state.query,
    min_score: String(state.minScore),
    limit: "150",
  });
  try {
    renderJobs(await api(`/api/jobs?${params}`));
  } catch (error) {
    showToast(error.message, true);
  }
}

async function pollLogs() {
  try {
    const payload = await api(`/api/logs?after=${state.lastLogId}`);
    if (!payload.items.length) return;
    const output = $("#log-output");
    const placeholder = output.querySelector(".log-placeholder");
    if (placeholder) placeholder.remove();
    for (const entry of payload.items) {
      state.lastLogId = Math.max(state.lastLogId, entry.id);
      const row = document.createElement("p");
      const warning = /warning|failed|error/i.test(entry.message);
      row.className = entry.stream === "dashboard" ? "dashboard-log" : warning ? "warning-log" : "";
      const time = new Date(entry.time).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
      row.innerHTML = `<time>${time}</time>${escapeHtml(entry.message)}`;
      output.appendChild(row);
    }
    while (output.children.length > 500) output.firstElementChild.remove();
    if ($("#follow-log").checked) output.scrollTop = output.scrollHeight;
  } catch (_) {
    // Overview polling provides the visible connection state.
  }
}

async function startPipeline(mode) {
  try {
    await api("/api/pipeline/start", {
      method: "POST",
      body: JSON.stringify({ mode, profile: state.profile }),
    });
    showToast(mode === "full" ? "Full discovery started" : "Cached scoring started");
    await refreshOverview();
    await pollLogs();
  } catch (error) {
    showToast(error.message, true);
  }
}

function confirmOperation({ title, copy, label, action }) {
  $("#confirm-title").textContent = title;
  $("#confirm-copy").textContent = copy;
  $("#confirm-action").textContent = label;
  state.pendingConfirmation = action;
  $("#confirm-dialog").returnValue = "";
  $("#confirm-dialog").showModal();
}

async function maintenance(path, successMessage) {
  try {
    await api(path, {
      method: "POST",
      body: JSON.stringify({ profile: state.profile }),
    });
    showToast(successMessage);
    await Promise.all([refreshOverview(), loadJobs()]);
  } catch (error) {
    showToast(error.message, true);
  }
}

function bindEvents() {
  $("#start-full").addEventListener("click", () => startPipeline("full"));
  $("#start-score").addEventListener("click", () => startPipeline("score-only"));
  $("#profile-select").addEventListener("change", async (event) => {
    state.profile = event.target.value;
    window.localStorage.setItem("jobsiphon-profile", state.profile);
    state.lastLogId = 0;
    $("#log-output").innerHTML = '<p class="log-placeholder">No output.</p>';
    await Promise.all([refreshOverview(), loadJobs()]);
  });
  $("#stop-run").addEventListener("click", () => confirmOperation({
    title: "Stop the active run?",
    copy: "The current pipeline process will be terminated. Results already written to disk will remain.",
    label: "Stop run",
    action: async () => {
      await api("/api/pipeline/stop", { method: "POST", body: "{}" });
      showToast("Pipeline stop requested");
      refreshOverview();
    },
  }));

  $$(".list-tab").forEach((tab) => tab.addEventListener("click", () => {
    state.list = tab.dataset.list;
    $$(".list-tab").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    loadJobs();
  }));

  let searchTimer;
  $("#job-search").addEventListener("input", (event) => {
    state.query = event.target.value;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(loadJobs, 220);
  });
  $("#score-filter").addEventListener("change", (event) => {
    state.minScore = Number(event.target.value);
    loadJobs();
  });

  $("#clean-output").addEventListener("click", () => confirmOperation({
    title: "Clean current outputs?",
    copy: "This removes apply_list.csv and apply_list.md. The master list, scrape cache, and seen-job history stay intact.",
    label: "Clean outputs",
    action: () => maintenance("/api/maintenance/clean", "Current output files cleaned"),
  }));
  $("#reset-seen").addEventListener("click", () => confirmOperation({
    title: "Reset seen-job history?",
    copy: "The next full run will treat every discovered URL as new and may rescore many previously evaluated listings.",
    label: "Reset history",
    action: () => maintenance("/api/maintenance/reset-seen", "Seen-job history reset"),
  }));

  $("#confirm-dialog").addEventListener("close", async (event) => {
    if (event.target.returnValue === "confirm" && state.pendingConfirmation) {
      const action = state.pendingConfirmation;
      state.pendingConfirmation = null;
      await action();
    } else {
      state.pendingConfirmation = null;
    }
  });

  $("#clear-log-view").addEventListener("click", () => {
    $("#log-output").innerHTML = '<p class="log-placeholder">No output.</p>';
  });
}

function updateElapsed() {
  if (state.overview?.pipeline.running) $("#run-time").textContent = elapsed(state.overview.pipeline.started_at);
}

async function init() {
  bindEvents();
  updateElapsed();
  await Promise.all([refreshOverview(), loadJobs(), pollLogs()]);
  window.setInterval(refreshOverview, 3000);
  window.setInterval(pollLogs, 1400);
  window.setInterval(updateElapsed, 1000);
}

document.addEventListener("DOMContentLoaded", init);
