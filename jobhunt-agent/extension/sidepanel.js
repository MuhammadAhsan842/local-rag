// Side panel logic: talk to the local bridge + the page's content script.
const BRIDGE = "http://127.0.0.1:8765";
let currentJob = null;

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const res = await fetch(BRIDGE + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function health() {
  try {
    const h = await api("/health");
    $("statusDot").className = "dot on";
    $("modelTag").textContent = h.ollama ? h.model : `${h.model} (keyword mode)`;
    $("connErr").style.display = "none";
    if (!h.profile_loaded) $("msg").textContent = "Tip: create profile.md + profile.yaml for real scoring.";
  } catch {
    $("statusDot").className = "dot off";
    $("connErr").style.display = "block";
  }
}

function getPageJob() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (!tabs[0]) return resolve({ error: "no active tab" });
      chrome.tabs.sendMessage(tabs[0].id, { type: "EXTRACT_JOB" }, (resp) => {
        if (chrome.runtime.lastError || !resp)
          return resolve({ error: "Open a supported job page, then retry." });
        resolve(resp);
      });
    });
  });
}

function chips(el, items, cls) {
  el.innerHTML = items && items.length
    ? items.map((s) => `<span class="chip ${cls || ""}">${s}</span>`).join("")
    : `<span class="muted">none</span>`;
}

$("analyze").onclick = async () => {
  $("msg").textContent = "";
  const job = await getPageJob();
  if (job.error) { $("msg").innerHTML = `<span class="err">${job.error}</span>`; return; }
  currentJob = job;
  $("analyze").disabled = true; $("analyze").textContent = "Analyzing…";
  try {
    const s = await api("/score", job);
    $("results").style.display = "block";
    $("jobTitle").textContent = job.title || "(untitled)";
    $("jobCompany").textContent = job.company || new URL(job.url).hostname;
    $("fit").textContent = s.fit;
    const c = s.components;
    $("components").textContent = `vec ${c.vector} · llm ${c.llm ?? "—"} · kw ${c.keyword}`;
    $("reasons").textContent = s.reasons || "";
    chips($("matched"), s.matched_skills);
    chips($("gap"), s.gap, "gap");
    $("tailorOut").style.display = "none";
  } catch {
    $("msg").innerHTML = `<span class="err">Engine unreachable — run python bridge.py</span>`;
  } finally {
    $("analyze").disabled = false; $("analyze").textContent = "Analyze this job";
  }
};

$("tailorBtn").onclick = async () => {
  if (!currentJob) return;
  $("tailorBtn").disabled = true; $("tailorBtn").textContent = "Tailoring…";
  try {
    const t = await api("/tailor", currentJob);
    if (t.error) { $("msg").innerHTML = `<span class="err">${t.error}</span>`; return; }
    $("tailorOut").style.display = "block";
    $("bullets").innerHTML = (t.bullets || []).map((b) => `<div class="bullet">${b.text}</div>`).join("")
      || `<span class="muted">No verified bullets (need profile.yaml + Ollama running).</span>`;
    const ok = t.faithfulness >= 1.0;
    $("faith").className = "faith " + (ok ? "ok" : "warn");
    $("faith").textContent = `faithfulness ${t.faithfulness} (${t.faithfulness_detail})`;
    $("dm").textContent = t.outreach.dm || "";
    $("cover").textContent = t.outreach.cover || "";
  } catch {
    $("msg").innerHTML = `<span class="err">Tailor failed — is the engine running?</span>`;
  } finally {
    $("tailorBtn").disabled = false; $("tailorBtn").textContent = "Tailor CV + outreach";
  }
};

$("trackBtn").onclick = async () => {
  if (!currentJob) return;
  try {
    const r = await api("/track", { ...currentJob, stage: $("stage").value });
    const s = r.summary || {};
    $("msg").textContent = "Saved → " + Object.entries(s).map(([k, v]) => `${k}:${v}`).join("  ");
  } catch {
    $("msg").innerHTML = `<span class="err">Could not save — is the engine running?</span>`;
  }
};

$("applyBtn").onclick = async () => {
  if (!currentJob) return;
  const submit = $("allowSubmit").checked;
  $("applyBtn").disabled = true;
  $("applyOut").textContent = submit
    ? "Launching Browser Use (will submit on ATS)…"
    : "Launching Browser Use (dry-run pre-fill)…";
  try {
    const r = await api("/apply", { ...currentJob, submit });
    if (r.ok) {
      $("applyOut").innerHTML = `Done — ${r.submitted ? "submitted" : "pre-filled, review & submit in the opened browser"}`
        + ` (${r.steps} steps).`;
    } else {
      $("applyOut").innerHTML = `<span class="err">${r.reason || r.error}</span>`;
    }
  } catch {
    $("applyOut").innerHTML = `<span class="err">Apply failed — is the engine running? Browser Use installed?</span>`;
  } finally {
    $("applyBtn").disabled = false;
  }
};

health();
