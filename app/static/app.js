const $ = (sel) => document.querySelector(sel);

const state = {
  settings: null,
  probe: null,
  selectedFormat: null,
  jobs: new Map(),
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  $("#folderInput").value = state.settings.default_folder;
  renderRecents();
}

function renderRecents() {
  const sel = $("#folderRecents");
  sel.innerHTML = "";
  for (const f of state.settings.recent_folders) {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    sel.appendChild(opt);
  }
}

$("#folderRecents").addEventListener("change", (e) => {
  $("#folderInput").value = e.target.value;
});

let probeTimer = null;
$("#urlInput").addEventListener("input", (e) => {
  const url = e.target.value.trim();
  clearTimeout(probeTimer);
  if (!url) {
    resetProbe();
    return;
  }
  if (!/^https?:\/\//i.test(url)) {
    setProbeStatus("Paste a full https:// URL.", "error");
    return;
  }
  setProbeStatus("Reading…");
  probeTimer = setTimeout(() => doProbe(url), 350);
});

function setProbeStatus(text, kind) {
  const el = $("#probeStatus");
  el.textContent = text || "";
  el.classList.toggle("error", kind === "error");
}

function resetProbe() {
  state.probe = null;
  state.selectedFormat = null;
  setProbeStatus("");
  $("#meta").classList.add("hidden");
  $("#formats").classList.add("hidden");
  $("#folderSection").classList.add("hidden");
  $("#downloadBtn").disabled = true;
}

async function doProbe(url) {
  try {
    const data = await api("/api/probe", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    state.probe = data;
    renderProbe(data);
    setProbeStatus("");
  } catch (e) {
    resetProbe();
    setProbeStatus(e.message, "error");
  }
}

function fmtDuration(s) {
  if (!s) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function fmtBytes(b) {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 ? 1 : 0)} ${u[i]}`;
}

function renderProbe(p) {
  $("#thumb").src = p.thumbnail || "";
  $("#title").textContent = p.title || "(untitled)";
  $("#uploader").textContent = p.uploader || "";
  $("#duration").textContent = fmtDuration(p.duration);
  $("#meta").classList.remove("hidden");

  const list = $("#formatList");
  list.innerHTML = "";
  state.selectedFormat = null;
  for (const f of p.formats) {
    const btn = document.createElement("button");
    btn.className = "format-btn";
    btn.type = "button";
    btn.dataset.id = f.id;
    const sub = f.kind === "video"
      ? `${f.ext.toUpperCase()} · ${f.vcodec || ""}${f.filesize ? " · " + fmtBytes(f.filesize) : ""}`
      : `${f.ext.toUpperCase()}`;
    btn.innerHTML = `<span class="label">${f.label}</span><span class="sub">${sub}</span>`;
    btn.addEventListener("click", () => selectFormat(f, btn));
    list.appendChild(btn);
  }
  $("#formats").classList.remove("hidden");
  $("#folderSection").classList.remove("hidden");
}

function selectFormat(f, btn) {
  state.selectedFormat = f;
  document.querySelectorAll(".format-btn").forEach(b => b.classList.toggle("selected", b === btn));
  $("#downloadBtn").disabled = false;
}

$("#downloadBtn").addEventListener("click", async () => {
  if (!state.probe || !state.selectedFormat) return;
  const out_dir = $("#folderInput").value.trim();
  const url = state.probe.webpage_url;
  $("#downloadBtn").disabled = true;
  try {
    const { job_id } = await api("/api/download", {
      method: "POST",
      body: JSON.stringify({ url, choice: state.selectedFormat, out_dir }),
    });
    addJob(job_id, state.probe.title);
    await loadSettings();
  } catch (e) {
    setProbeStatus(e.message, "error");
  } finally {
    $("#downloadBtn").disabled = false;
  }
});

function addJob(id, title) {
  $("#jobs-heading").classList.remove("hidden");
  const li = document.createElement("li");
  li.className = "job";
  li.dataset.id = id;
  li.innerHTML = `
    <div class="job-head">
      <div class="job-title">${title || "(downloading…)"}</div>
      <div class="job-status">queued</div>
    </div>
    <div class="bar"><div class="bar-fill"></div></div>
    <div class="job-foot"><span class="speed"></span><span class="action"></span></div>
  `;
  $("#jobList").prepend(li);
  state.jobs.set(id, { el: li });
  subscribeJob(id);
}

function subscribeJob(id) {
  const es = new EventSource(`/api/jobs/${id}/events`);
  const job = state.jobs.get(id);

  const updateUI = (snap) => {
    if (!job) return;
    const { el } = job;
    const statusEl = el.querySelector(".job-status");
    const fill = el.querySelector(".bar-fill");
    const speedEl = el.querySelector(".speed");
    const actionEl = el.querySelector(".action");

    statusEl.classList.remove("done", "error");
    statusEl.textContent = snap.status;
    if (snap.status === "done") statusEl.classList.add("done");
    if (snap.status === "error") statusEl.classList.add("error");

    const p = snap.progress || {};
    if (p.total_bytes && p.downloaded_bytes) {
      const pct = Math.min(100, (p.downloaded_bytes / p.total_bytes) * 100);
      fill.style.width = `${pct}%`;
    }
    if (snap.status === "running") {
      const speed = p.speed ? `${fmtBytes(p.speed)}/s` : "";
      const eta = p.eta != null ? `· ETA ${fmtDuration(p.eta)}` : "";
      const sized = p.total_bytes ? `${fmtBytes(p.downloaded_bytes || 0)} / ${fmtBytes(p.total_bytes)}` : "";
      speedEl.textContent = [sized, speed, eta].filter(Boolean).join(" ");
    }
    if (snap.status === "done") {
      fill.style.width = "100%";
      speedEl.textContent = snap.result?.filepath || "";
      actionEl.innerHTML = "";
      if (snap.result?.filepath) {
        const a = document.createElement("button");
        a.className = "linklike";
        a.textContent = "Reveal in Finder";
        a.onclick = () => api("/api/reveal", { method: "POST", body: JSON.stringify({ path: snap.result.filepath }) });
        actionEl.appendChild(a);
      }
    }
    if (snap.status === "error") {
      speedEl.textContent = snap.error || "";
    }
    if (snap.choice && el.querySelector(".job-title").textContent.startsWith("(")) {
      // keep title from result if available
    }
    if (snap.result?.title) {
      el.querySelector(".job-title").textContent = snap.result.title;
    }
  };

  const handle = (ev) => {
    try {
      const snap = JSON.parse(ev.data);
      updateUI(snap);
    } catch {}
  };
  es.addEventListener("snapshot", handle);
  es.addEventListener("progress", handle);
  es.addEventListener("done", (ev) => { handle(ev); es.close(); });
  es.addEventListener("error", (ev) => {
    if (ev.data) handle(ev);
    if (es.readyState === EventSource.CLOSED) return;
  });
}

// Settings dialog
const dlg = $("#settingsDialog");
$("#settingsBtn").addEventListener("click", () => {
  $("#settingsDefaultFolder").value = state.settings.default_folder;
  const ul = $("#settingsRecents");
  ul.innerHTML = "";
  for (const r of state.settings.recent_folders) {
    const li = document.createElement("li");
    li.textContent = r;
    ul.appendChild(li);
  }
  dlg.showModal();
});
dlg.addEventListener("close", async () => {
  if (dlg.returnValue !== "save") return;
  const default_folder = $("#settingsDefaultFolder").value.trim();
  if (!default_folder) return;
  state.settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ default_folder, recent_folders: state.settings.recent_folders }),
  });
  $("#folderInput").value = state.settings.default_folder;
  renderRecents();
});

loadSettings().catch(e => setProbeStatus(`Could not load settings: ${e.message}`, "error"));
