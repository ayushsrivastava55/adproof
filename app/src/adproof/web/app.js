/* AdProof review UI.
 *
 * Rendering rules enforced here:
 *   - never render a stage as complete unless its job reached a terminal state;
 *   - never render "no evidence found" for a search that did not run;
 *   - always show absence class alongside a non-pass result;
 *   - always show provenance with every evidence item;
 *   - never receive or display a provider URL: playback goes through the
 *     authorized proxy.
 */

let currentUser = null;
let campaigns = [];

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401) {
    showLogin();
    throw new Error("unauthenticated");
  }
  return res;
}

function showLogin(message) {
  document.getElementById("app-view").hidden = true;
  const view = document.getElementById("login-view");
  view.hidden = false;
  const err = document.getElementById("login-error");
  if (message) {
    err.textContent = message;
    err.hidden = false;
  } else {
    err.hidden = true;
  }
}

function showApp() {
  document.getElementById("login-view").hidden = true;
  document.getElementById("app-view").hidden = false;
}

/* AdProof review UI - Phase 1 slice.
 *
 * Rendering rules enforced here:
 *   - never render a stage as complete unless its job reached a terminal state;
 *   - never render "no evidence found" for a search that did not run;
 *   - always show absence class alongside a non-pass result;
 *   - always show provenance with every evidence item.
 */

const STATE_LABEL = {
  pass: "Pass",
  fail: "Fail",
  uncertain: "Uncertain",
  not_evaluated: "Not evaluated",
  human_review_required: "Human review required",
  processing: "Still processing",
  error: "Error",
};

const ABSENCE_LABEL = {
  not_applicable: "",
  likely_absent: "No match in the completed index (likely absent, not proven)",
  low_confidence_absence: "No match found (low-confidence absence)",
  index_incomplete: "Index did not complete",
  query_insufficient: "Query returned nothing usable",
  unsupported_modality: "Modality not supported",
  media_quality_issue: "Media quality prevented retrieval",
  provider_failure: "Retrieval failed at the provider — nothing was searched",
};

const JOB_STATE_LABEL = {
  queued: "Queued",
  running: "Running",
  succeeded: "Completed",
  failed_retryable: "Failed — will retry",
  failed_terminal: "Failed — needs attention",
};

const STAGE_LABEL = {
  ingest: "Upload & ingestion",
  index_spoken: "Spoken-word indexing",
  index_visual: "Visual indexing",
  retrieval: "Evidence retrieval",
  evaluation: "Deterministic evaluation",
};

let currentSubmissionId = null;
let hls = null;
let pollTimer = null;

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
const fmtTime = (s) => {
  if (s === null || s === undefined) return "—";
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(2).padStart(5, "0");
  return `${m}:${sec}`;
};

async function loadIntegrity() {
  const badge = $("integrity-badge");
  try {
    const data = await (await api("/api/integrity")).json();
    if (data.provider_configured) {
      badge.textContent = "Live provider — no fixture data";
      badge.className = "badge badge-live";
    } else {
      badge.textContent = "VideoDB not configured — processing will fail";
      badge.className = "badge badge-error";
    }
    const list = $("disclosure-list");
    list.innerHTML = data.known_limitations
      .map((l) => `<li>${esc(l)}</li>`)
      .join("");
    list.insertAdjacentHTML(
      "beforeend",
      `<li>${esc(data.confidence_disclosure)}</li>`
    );
    $("disclosure").hidden = false;
  } catch (err) {
    badge.textContent = "Integrity status unavailable";
    badge.className = "badge badge-error";
  }
}

async function loadSubmissions() {
  const list = $("submission-list");
  const subs = await (await api("/api/submissions")).json();
  $("submissions-empty").hidden = subs.length > 0;
  list.innerHTML = subs
    .map(
      (s) => `
      <li>
        <button class="submission-btn ${s.id === currentSubmissionId ? "active" : ""}"
                data-id="${esc(s.id)}">
          <span class="creator">${esc(s.creator_reference)}</span>
          <span class="campaign">${esc(s.campaign_name ?? "")}</span>
          <span class="state-chip state-${esc(s.state)}">${esc(s.state)}</span>
        </button>
      </li>`
    )
    .join("");
  list.querySelectorAll(".submission-btn").forEach((btn) =>
    btn.addEventListener("click", () => selectSubmission(btn.dataset.id))
  );
}

function selectSubmission(id) {
  currentSubmissionId = id;
  loadSubmissions();
  loadReport();
}

function renderStages(stages) {
  $("stages").innerHTML = stages
    .map((s) => {
      const elapsed =
        s.elapsed_seconds !== null && s.elapsed_seconds !== undefined
          ? ` · ${s.elapsed_seconds.toFixed(1)}s`
          : "";
      const attempts =
        s.attempt_count > 1 ? ` · attempt ${s.attempt_count}/${s.max_attempts}` : "";
      const err = s.error_summary
        ? `<div class="stage-error">${esc(s.error_summary)}</div>`
        : "";
      return `
        <li class="stage stage-${esc(s.state)}">
          <span class="stage-name">${esc(STAGE_LABEL[s.stage] ?? s.stage)}</span>
          <span class="stage-state">${esc(
            JOB_STATE_LABEL[s.state] ?? s.state
          )}${elapsed}${attempts}</span>
          ${err}
        </li>`;
    })
    .join("");
}

function renderEvidence(ev) {
  const counted = ev.counted_toward_measurement
    ? `<span class="tag tag-counted">counted in measurement</span>`
    : `<span class="tag tag-context">context only — not counted</span>`;
  const roleTag = `<span class="tag tag-${esc(ev.role)}">${esc(ev.role)}</span>`;
  const score =
    ev.provider_score === null || ev.provider_score === undefined
      ? "not supplied"
      : `${ev.provider_score.toFixed(3)} (uncalibrated)`;
  const p = ev.provenance;
  return `
    <li class="evidence evidence-${esc(ev.role)}">
      <div class="evidence-head">
        <button class="btn btn-play" data-evidence="${esc(ev.id)}"
                data-start="${ev.start_seconds}">
          ▶ ${fmtTime(ev.start_seconds)}${
            ev.end_seconds !== null && ev.end_seconds !== undefined
              ? " – " + fmtTime(ev.end_seconds)
              : ""
          }
        </button>
        ${roleTag}${counted}
        <span class="tag tag-conf-${esc(ev.confidence_band)}">confidence: ${esc(
          ev.confidence_band
        )}</span>
      </div>
      ${ev.text ? `<p class="evidence-text">${esc(ev.text)}</p>` : ""}
      <details class="provenance">
        <summary>Provenance</summary>
        <dl>
          <dt>Origin</dt><dd>${esc(ev.origin)}</dd>
          <dt>Modality</dt><dd>${esc(ev.modality)}</dd>
          <dt>Retrieval query</dt><dd><code>${esc(p.retrieval_query)}</code></dd>
          <dt>Search / index type</dt>
          <dd>${esc(p.search_type)} over ${esc(p.index_type)}</dd>
          <dt>Provider index</dt>
          <dd>${esc(p.provider_index_name ?? "—")} <code>${esc(
            p.provider_index_id ?? "—"
          )}</code></dd>
          <dt>Provider video</dt><dd><code>${esc(p.provider_video_id ?? "—")}</code></dd>
          <dt>Provider score</dt><dd>${esc(score)}</dd>
          <dt>Retrieval plan</dt><dd>${esc(p.plan_version)}</dd>
          <dt>SDK</dt><dd>${esc(p.sdk_version ?? "—")}</dd>
          <dt>Retrieval run</dt><dd><code>${esc(p.retrieval_run_id)}</code></dd>
          <dt>Recorded at</dt><dd>${esc(p.created_at)}</dd>
        </dl>
      </details>
    </li>`;
}

function renderRuns(runs) {
  return runs
    .map((r) => {
      let outcome;
      if (!r.executed && r.error_summary) {
        outcome = `<span class="run-notrun">did not run — ${esc(
          r.error_summary
        )}</span>`;
      } else if (r.result_count === 0) {
        outcome = `<span class="run-empty">ran, returned 0 results</span>`;
      } else if (r.result_truncated) {
        outcome = `<span class="run-truncated">ran, returned ${r.result_count}
          result(s) — HIT THE RETRIEVAL LIMIT of ${r.result_threshold},
          so more evidence may exist and the measurement understates</span>`;
      } else {
        outcome = `<span class="run-ok">ran, returned ${r.result_count} result(s)</span>`;
      }
      return `
        <li>
          <code>${esc(r.query)}</code>
          <span class="run-meta">${esc(r.search_type)} / ${esc(
            r.index_type
          )} · ${esc(r.role)} · ${
            r.counts_toward_measurement ? "counted" : "context only"
          }</span>
          ${outcome}
        </li>`;
    })
    .join("");
}

const REASON_LABELS = {
  false_positive: "False positive — flagged something that isn't there",
  false_negative: "False negative — missed something that is there",
  insufficient_evidence: "Insufficient evidence to decide",
  wrong_timestamp: "Wrong timestamp",
  wrong_rule_interpretation: "Rule interpreted incorrectly",
  unsupported_rule: "Rule cannot be automated reliably",
  policy_disagreement: "Disagree with the policy outcome",
};

const DECISION_LABELS = {
  approve: "Approve",
  reject: "Reject",
  request_changes: "Request changes",
  escalate: "Escalate",
};

function renderAdjudication(report) {
  const adj = report.adjudication;
  const permitted = new Set(adj.permitted_decisions);
  const rec = adj.machine_recommendation;

  const buttons = ["approve", "request_changes", "escalate", "reject"]
    .map((d) => {
      const enabled = permitted.has(d);
      const isRec = rec === d;
      return `<button class="btn decision-btn ${isRec ? "btn-primary" : ""}"
        data-decision="${d}" ${enabled ? "" : "disabled"}
        title="${enabled ? "" : "Not available until every requirement is resolved"}">
        ${esc(DECISION_LABELS[d])}${isRec ? " · suggested" : ""}
      </button>`;
    })
    .join("");

  const history = report.decisions.length
    ? `<ul class="decision-history">${report.decisions
        .map(
          (d) => `<li>
            <strong>${esc(DECISION_LABELS[d.decision] ?? d.decision)}</strong>
            by ${esc(d.decided_by)}
            <span class="muted-inline">· machine suggested ${esc(
              d.machine_recommendation ?? "nothing"
            )} · ${d.agreed_with_machine ? "agreed" : "disagreed"}</span>
            <div class="decision-rationale">${esc(d.rationale)}</div>
          </li>`
        )
        .join("")}</ul>`
    : "";

  return `
    <div class="adjudication">
      <div class="adj-head">
        <h3 class="adj-title">Decision</h3>
        <span class="tag">machine suggestion: ${esc(rec ?? "none")}</span>
      </div>
      <ul class="adj-reasons">${adj.reasons
        .map((r) => `<li>${esc(r)}</li>`)
        .join("")}</ul>
      <div class="decision-actions">${buttons}</div>
      <p class="note">
        The suggestion is advisory. Nothing is approved or rejected
        automatically, and approval stays unavailable until every requirement
        has either passed or been reviewed by a person.
      </p>
      ${history}
    </div>`;
}

function renderRules(rules) {
  $("rules").innerHTML = rules
    .map((rule) => {
      const res = rule.result;
      const state = res ? res.state : "processing";
      const stateLabel = res ? STATE_LABEL[res.state] ?? res.state : "Not yet evaluated";

      let measurement = "";
      if (res && res.measured_value !== null && res.measured_value !== undefined) {
        const res_note = res.measurement_resolution_seconds
          ? ` <span class="resolution">±${res.measurement_resolution_seconds}s sampling resolution</span>`
          : "";
        measurement = `<p class="measurement">Measured
          <strong>${res.measured_value}</strong> ${esc(res.measured_unit ?? "")}
          against a threshold of <strong>${res.threshold_value}</strong>
          ${esc(res.measured_unit ?? "")}${res_note}</p>`;
      }

      const absence =
        res && res.absence_class && res.absence_class !== "not_applicable"
          ? `<p class="absence">${esc(ABSENCE_LABEL[res.absence_class])}</p>`
          : "";

      const intervals =
        res && res.measurement_intervals && res.measurement_intervals.length
          ? `<p class="intervals">Merged intervals used:
             ${res.measurement_intervals
               .map((iv) => `${fmtTime(iv[0])}–${fmtTime(iv[1])}`)
               .join(", ")}</p>`
          : "";

      const supporting = rule.evidence.filter((e) => e.role === "supporting");
      const conflicting = rule.evidence.filter((e) => e.role === "conflicting");

      const evidenceBlock =
        rule.evidence.length === 0
          ? `<p class="empty">No evidence items were returned for this
             requirement. See the retrieval runs below to distinguish
             "searched and found nothing" from "did not search".</p>`
          : `
            <div class="evidence-columns">
              <div>
                <h5>Supporting (${supporting.length})</h5>
                <ul class="evidence-list">${supporting
                  .map(renderEvidence)
                  .join("")}</ul>
              </div>
              <div>
                <h5>Conflicting (${conflicting.length})</h5>
                ${
                  conflicting.length
                    ? `<ul class="evidence-list">${conflicting
                        .map(renderEvidence)
                        .join("")}</ul>`
                    : `<p class="empty">None retrieved.</p>`
                }
              </div>
            </div>`;

      const latest = (rule.reviews || [])[rule.reviews.length - 1];
      const effective = latest ? latest.human_state : state;
      const humanBlock = latest
        ? `<div class="human-verdict">
             <span class="result-chip result-${esc(latest.human_state)}">${esc(
               STATE_LABEL[latest.human_state] ?? latest.human_state
             )}</span>
             <span class="source-tag">reviewer-confirmed</span>
             <span class="muted-inline">${esc(latest.reviewer)} · ${
               latest.action === "override"
                 ? esc(REASON_LABELS[latest.reason_category] ?? latest.reason_category)
                 : "confirmed the machine result"
             }</span>
             ${latest.reason_text ? `<p class="decision-rationale">${esc(latest.reason_text)}</p>` : ""}
           </div>`
        : "";
      const reviewControls = res
        ? `<div class="review-actions">
             <button class="btn btn-small" data-confirm="${esc(rule.id)}">Confirm</button>
             <button class="btn btn-small" data-override="${esc(rule.id)}">Override…</button>
           </div>`
        : "";

      return `
        <article class="rule rule-${esc(effective)}" data-rule="${esc(rule.id)}">
          <header>
            <span class="result-chip result-${esc(state)}">${esc(stateLabel)}</span>
            <h4>${esc(rule.requirement_text)}</h4>
          </header>
          <p class="rule-meta">
            ${esc(rule.rule_type)} · ${esc(rule.modality)} ·
            absence policy: ${esc(rule.absence_policy)}${
              rule.score_threshold !== null && rule.score_threshold !== undefined
                ? ` · relevance cutoff: ${rule.score_threshold}`
                : ""
            }
            ${
              res
                ? ` · <span class="source-tag">${esc(
                    res.source
                  )} result</span> · ${esc(res.evaluator_version)}`
                : ""
            }
          </p>
          ${measurement}
          ${absence}
          ${res ? `<p class="explanation">${esc(res.explanation)}</p>` : ""}
          ${intervals}
          ${humanBlock}
          ${reviewControls}
          <details class="runs">
            <summary>Retrieval runs (${rule.retrieval_runs.length})</summary>
            <ul class="run-list">${renderRuns(rule.retrieval_runs)}</ul>
          </details>
          ${evidenceBlock}
        </article>`;
    })
    .join("");

  document.querySelectorAll(".btn-play").forEach((btn) =>
    btn.addEventListener("click", () => playAt(btn.dataset.evidence))
  );
  document.querySelectorAll("[data-confirm]").forEach((btn) =>
    btn.addEventListener("click", () => submitReview(btn.dataset.confirm, "confirm"))
  );
  document.querySelectorAll("[data-override]").forEach((btn) =>
    btn.addEventListener("click", () => openOverride(btn.dataset.override))
  );
  document.querySelectorAll(".decision-btn").forEach((btn) =>
    btn.addEventListener("click", () => openDecision(btn.dataset.decision))
  );
}

async function submitReview(ruleId, action, body = {}) {
  const res = await api(
    `/api/submissions/${currentSubmissionId}/rules/${ruleId}/review`,
    { method: "POST", body: JSON.stringify({ action, ...body }) }
  );
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    alert(
      typeof detail.detail === "string"
        ? detail.detail
        : "Could not record that review."
    );
    return false;
  }
  loadReport();
  return true;
}

function openOverride(ruleId) {
  const dlg = $("override-dialog");
  dlg.dataset.rule = ruleId;
  $("override-form").reset();
  $("override-error").hidden = true;
  dlg.showModal();
}

function openDecision(decision) {
  if (decision === "request_changes") {
    // Route through the drafted, evidence-grounded message.
    openRevision();
    return;
  }
  const dlg = $("decision-dialog");
  dlg.dataset.decision = decision;
  $("decision-title").textContent = `${DECISION_LABELS[decision]} this submission`;
  $("decision-form").reset();
  $("decision-error").hidden = true;
  dlg.showModal();
}

function loadStream(url, onReady) {
  // url points at AdProof's own proxy, never at the provider.
  const player = $("player");
  if (player.dataset.src === url) {
    if (onReady) onReady();
    return;
  }
  player.dataset.src = url;
  if (window.Hls && window.Hls.isSupported()) {
    if (hls) hls.destroy();
    hls = new window.Hls();
    hls.loadSource(url);
    hls.attachMedia(player);
    if (onReady) hls.on(window.Hls.Events.MANIFEST_PARSED, onReady);
  } else if (player.canPlayType("application/vnd.apple.mpegurl")) {
    player.src = url;
    if (onReady) player.addEventListener("loadedmetadata", onReady, { once: true });
  } else {
    $("player-note").textContent =
      "This browser cannot play HLS streams; evidence playback is unavailable.";
  }
}

async function fetchPlayback(evidenceId) {
  const res = await api(`/api/evidence/${evidenceId}/playback`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    $("player-note").textContent =
      body.detail ?? "Playback is unavailable for this evidence item.";
    return null;
  }
  return res.json();
}

// Attach the stream as soon as the report opens, so the native play button
// works without first clicking an evidence timestamp.
async function preloadPlayer(evidenceId) {
  const data = await fetchPlayback(evidenceId);
  if (data) loadStream(data.playback_url);
}

async function playAt(evidenceId) {
  const data = await fetchPlayback(evidenceId);
  if (!data) return;
  const player = $("player");
  const seek = () => {
    player.currentTime = data.seek_to_seconds;
    player.play().catch(() => {});
  };
  loadStream(data.playback_url, seek);
  if (player.dataset.src === data.playback_url && player.readyState >= 1) seek();
}

async function loadReport() {
  if (!currentSubmissionId) return;
  const res = await api(`/api/submissions/${currentSubmissionId}/report`);
  if (!res.ok) return;
  const data = await res.json();

  // Swap the placeholder for the report. `report-panel` is the always-visible
  // container; the report itself lives in `report-body`.
  $("report-placeholder").hidden = true;
  $("report-body").hidden = false;
  $("report-title").textContent = `Submission — ${data.submission.creator_reference}`;
  const chip = $("submission-state");
  chip.textContent = data.submission.state;
  chip.className = `state-chip state-${data.submission.state}`;

  const errBox = $("submission-error");
  if (data.submission.error_summary) {
    errBox.textContent = data.submission.error_summary;
    errBox.hidden = false;
  } else {
    errBox.hidden = true;
  }

  renderStages(data.stages);

  const hasTerminalFailure = data.stages.some(
    (s) => s.state === "failed_terminal"
  );
  $("retry-btn").hidden = !hasTerminalFailure;

  if (data.media) {
    $("media-info").innerHTML = `
      <dl>
        <dt>VideoDB video</dt><dd><code>${esc(
          data.media.provider_video_id ?? "—"
        )}</code></dd>
        <dt>Collection</dt><dd><code>${esc(
          data.media.provider_collection_id ?? "—"
        )}</code></dd>
        <dt>Duration</dt><dd>${
          data.media.duration_seconds === null ||
          data.media.duration_seconds === undefined
            ? "not reported by provider"
            : data.media.duration_seconds.toFixed(2) + "s"
        }</dd>
        <dt>SDK</dt><dd>${esc(data.media.sdk_version ?? "—")}</dd>
      </dl>`;
    $("player-note").textContent =
      "Playback is proxied through AdProof with a short-lived token. The " +
      "provider URL is never sent to your browser.";
  } else {
    $("media-info").innerHTML =
      `<p class="empty">No media reference recorded yet. Ingestion has not completed.</p>`;
  }

  $("adjudication-slot").innerHTML = renderAdjudication(data);
  renderRules(data.rules);

  const firstEvidence = data.rules.flatMap((r) => r.evidence)[0];
  if (data.media && firstEvidence) preloadPlayer(firstEvidence.id);

  clearTimeout(pollTimer);
  if (!data.processing_complete) {
    pollTimer = setTimeout(() => {
      loadSubmissions();
      loadReport();
    }, 3000);
  }
}

$("retry-btn").addEventListener("click", async () => {
  await api(`/api/submissions/${currentSubmissionId}/retry`, { method: "POST" });
  loadReport();
});

/* ── views ──────────────────────────────────────────────────────────────── */

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    $("view-review").hidden = tab.dataset.view !== "review";
    $("view-campaigns").hidden = tab.dataset.view !== "campaigns";
    $("view-analytics").hidden = tab.dataset.view !== "analytics";
    if (tab.dataset.view === "analytics") loadAnalytics();
  })
);

$("disclosure-toggle").addEventListener("click", () => {
  const list = $("disclosure-list");
  list.hidden = !list.hidden;
});

/* ── campaigns ──────────────────────────────────────────────────────────── */

async function loadCampaigns() {
  campaigns = await (await api("/api/campaigns")).json();
  $("campaigns-empty").hidden = campaigns.length > 0;
  $("campaign-list").innerHTML = campaigns
    .map(
      (c) => `<li><div class="campaign-row">
        <span class="creator">${esc(c.name)}</span>
        <span class="campaign">rule set v${esc(c.rule_set_version ?? "—")}</span>
      </div></li>`
    )
    .join("");
  const select = document.querySelector('#submission-form select[name="campaign_id"]');
  select.innerHTML = campaigns
    .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`)
    .join("");
}

$("new-submission").addEventListener("click", () => {
  if (!campaigns.length) {
    alert("Create a campaign with requirements first.");
    return;
  }
  $("submission-dialog").showModal();
});
document.querySelectorAll("[data-close]").forEach((b) =>
  b.addEventListener("click", () => b.closest("dialog").close())
);

$("add-rule").addEventListener("click", addRuleRow);

$("new-campaign").addEventListener("click", () => {
  $("rule-rows").innerHTML = "";
  addRuleRow();
  $("campaign-error").hidden = true;
  $("campaign-dialog").showModal();
});

$("campaign-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const res = await api("/api/campaigns", {
    method: "POST",
    body: JSON.stringify({
      campaign_name: f.get("campaign_name"),
      brief_text: f.get("brief_text"),
      rules: collectRules(),
    }),
  });
  const err = $("campaign-error");
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    err.textContent =
      typeof d.detail === "string"
        ? d.detail
        : (d.detail?.[0]?.msg ?? "Could not create the campaign.");
    err.hidden = false;
    return;
  }
  err.hidden = true;
  $("campaign-dialog").close();
  e.target.reset();
  loadCampaigns();
});

$("submission-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    campaign_id: f.get("campaign_id"),
    creator_reference: f.get("creator_reference"),
    source_url: f.get("source_url"),
    idempotency_key: `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  };
  const res = await api("/api/submissions", { method: "POST", body: JSON.stringify(body) });
  const err = $("submission-error");
  if (!res.ok) {
    err.textContent = (await res.json()).detail ?? "Could not create the submission.";
    err.hidden = false;
    return;
  }
  err.hidden = true;
  $("submission-dialog").close();
  e.target.reset();
  loadSubmissions();
});

$("override-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const ok = await submitReview($("override-dialog").dataset.rule, "override", {
    human_state: f.get("human_state"),
    reason_category: f.get("reason_category"),
    reason_text: f.get("reason_text"),
  });
  if (ok) $("override-dialog").close();
});

$("decision-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const res = await api(`/api/submissions/${currentSubmissionId}/decision`, {
    method: "POST",
    body: JSON.stringify({
      decision: $("decision-dialog").dataset.decision,
      rationale: f.get("rationale"),
    }),
  });
  const err = $("decision-error");
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    err.textContent =
      typeof detail.detail === "string" ? detail.detail : "Could not record that decision.";
    err.hidden = false;
    return;
  }
  $("decision-dialog").close();
  loadReport();
  loadSubmissions();
});


/* ── rule editor ────────────────────────────────────────────────────────── */

const RULE_TYPES = [
  { v: "required_spoken_phrase",  label: "Must say a phrase",            fields: ["phrase","min_occurrences"] },
  { v: "forbidden_spoken_claim",  label: "Must NOT say (prohibited claims)", fields: ["forbidden_phrases"] },
  { v: "min_visual_duration",     label: "Must be visible for at least", fields: ["visual_concept","min_duration_seconds","score_threshold"] },
  { v: "max_visual_duration",     label: "Must appear no longer than",   fields: ["visual_concept","max_duration_seconds","score_threshold"] },
  { v: "required_visual_event",   label: "Must happen on screen",        fields: ["visual_concept","window_start_seconds","window_end_seconds"] },
  { v: "forbidden_visual_event",  label: "Must NOT appear on screen",    fields: ["visual_concept","score_threshold"] },
  { v: "disclosure_present",      label: "Advertising disclosure",       fields: ["modality_requirement","window_start_seconds","window_end_seconds"] },
  { v: "sequence",                label: "One thing before another",     fields: ["sequence_first","sequence_second","sequence_max_gap_seconds"] },
  { v: "subjective_human_review", label: "Human judgement only",         fields: ["reviewer_guidance"] },
];

const FIELD_DEFS = {
  phrase:                   { label: "Exact phrase", type: "text", ph: "AYUSH20" },
  min_occurrences:          { label: "Minimum times", type: "number", value: 1, min: 1 },
  forbidden_phrases:        { label: "Prohibited phrases (one per line)", type: "textarea", ph: "guaranteed weight loss\ncures fatigue" },
  visual_concept:           { label: "What must be seen", type: "text", ph: "PulseBar pack" },
  min_duration_seconds:     { label: "Minimum seconds", type: "number", value: 6, min: 0.5, step: 0.5 },
  max_duration_seconds:     { label: "Maximum seconds", type: "number", value: 2, min: 0.5, step: 0.5 },
  score_threshold:          { label: "Relevance cutoff (affects the measurement)", type: "number", value: 0.3, min: 0, max: 1, step: 0.05 },
  window_start_seconds:     { label: "Window starts at (s, optional)", type: "number", min: 0 },
  window_end_seconds:       { label: "Window ends at (s, optional)", type: "number", min: 0 },
  modality_requirement:     { label: "How it may be satisfied", type: "select",
                              options: [["either","Spoken or on screen"],["both","Both spoken and on screen"],
                                        ["spoken_only","Spoken only"],["visual_only","On screen only"]] },
  sequence_first:           { label: "This must come first", type: "text", ph: "demonstrating the product" },
  sequence_second:          { label: "Then this", type: "text", ph: "call to action" },
  sequence_max_gap_seconds: { label: "Largest gap allowed (s, optional)", type: "number", min: 1 },
  reviewer_guidance:        { label: "Guidance for the reviewer", type: "textarea", ph: "What should they look for?" },
};

let ruleSeq = 0;

function fieldHtml(name) {
  const f = FIELD_DEFS[name];
  const id = `f-${name}-${ruleSeq}`;
  if (f.type === "select") {
    return `<label>${esc(f.label)}<select data-field="${name}">${f.options
      .map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select></label>`;
  }
  if (f.type === "textarea") {
    return `<label>${esc(f.label)}<textarea data-field="${name}" rows="3" placeholder="${esc(f.ph || "")}"></textarea></label>`;
  }
  const attrs = ["min","max","step"].filter(a => f[a] !== undefined)
    .map(a => `${a}="${f[a]}"`).join(" ");
  return `<label>${esc(f.label)}<input data-field="${name}" type="${f.type}" ${attrs}
    ${f.value !== undefined ? `value="${f.value}"` : ""} placeholder="${esc(f.ph || "")}" /></label>`;
}

function renderRuleFields(row) {
  const type = row.querySelector("[data-field=rule_type]").value;
  const def = RULE_TYPES.find(r => r.v === type);
  row.querySelector(".rule-fields").innerHTML = def.fields.map(fieldHtml).join("");
  // Subjective rules never carry an absence policy: nothing is searched.
  row.querySelector(".policy-wrap").hidden = type === "subjective_human_review";
}

function addRuleRow() {
  ruleSeq += 1;
  const row = document.createElement("div");
  row.className = "rule-row";
  row.innerHTML = `
    <div class="rule-row-head">
      <select data-field="rule_type">${RULE_TYPES
        .map(r => `<option value="${r.v}">${esc(r.label)}</option>`).join("")}</select>
      <button type="button" class="btn btn-small remove-rule" aria-label="Remove requirement">Remove</button>
    </div>
    <label>Requirement, as a reviewer will read it
      <input data-field="requirement_text" required placeholder="Creator must state the code AYUSH20" /></label>
    <div class="rule-fields"></div>
    <div class="rule-row-foot">
      <label>Severity
        <select data-field="severity">
          <option value="required" selected>Required (asks for changes)</option>
          <option value="blocking">Blocking (recommends rejection)</option>
          <option value="optional">Optional (does not block)</option>
        </select></label>
      <label class="policy-wrap">If nothing is found
        <select data-field="absence_policy">
          <option value="uncertain" selected>Mark uncertain</option>
          <option value="require_human_review">Require human review</option>
          <option value="fail_when_coverage_complete">Fail (exact matches only)</option>
        </select></label>
    </div>`;
  row.querySelector("[data-field=rule_type]").addEventListener("change", () => renderRuleFields(row));
  row.querySelector(".remove-rule").addEventListener("click", () => {
    if (document.querySelectorAll(".rule-row").length > 1) row.remove();
  });
  $("rule-rows").appendChild(row);
  renderRuleFields(row);
}

function collectRules() {
  return [...document.querySelectorAll(".rule-row")].map((row) => {
    const rule = {};
    row.querySelectorAll("[data-field]").forEach((el) => {
      const name = el.dataset.field;
      let v = el.value;
      if (v === "" || v === null) return;
      if (name === "forbidden_phrases") {
        v = v.split("\n").map(x => x.trim()).filter(Boolean);
        if (!v.length) return;
      } else if (el.type === "number") {
        v = Number(v);
      }
      rule[name] = v;
    });
    return rule;
  });
}


/* ── analytics ──────────────────────────────────────────────────────────── */

const pct = (v) => (v === null || v === undefined ? "not available" : `${Math.round(v * 100)}%`);

async function loadAnalytics() {
  const sel = $("analytics-campaign");
  sel.innerHTML = campaigns
    .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`)
    .join("");
  if (!campaigns.length) {
    $("analytics-body").innerHTML =
      `<p class="empty">No campaigns yet. Analytics appear once submissions have been processed.</p>`;
    return;
  }
  renderAnalytics(sel.value || campaigns[0].id);
}

async function renderAnalytics(campaignId) {
  const res = await api(`/api/campaigns/${campaignId}/analytics`);
  if (!res.ok) return;
  const a = await res.json();
  const t = a.totals;

  const stat = (label, value, note) => `
    <div class="stat">
      <div class="stat-value">${esc(value)}</div>
      <div class="stat-label">${esc(label)}</div>
      ${note ? `<div class="stat-note">${esc(note)}</div>` : ""}
    </div>`;

  const patterns = a.failure_patterns.length
    ? a.failure_patterns.map((p) => `
        <li>
          <div class="pattern-head">
            <span>${esc(p.requirement_text)}</span>
            <span class="muted-inline">${p.machine_failures.count} machine failure(s)</span>
          </div>
          ${
            p.overridden_away.count
              ? `<div class="pattern-warn">Reviewers overturned ${p.overridden_away.count} of these.
                 ${Object.entries(p.override_reasons).map(([r, n]) => `${esc(r)} x${n}`).join(", ")}.
                 A cluster here usually means the rule or the retrieval is wrong, not the creator.</div>`
              : ""
          }
          <div class="muted-inline">submissions: ${p.machine_failures.submission_ids
            .map((id) => `<code>${esc(id.slice(0, 8))}</code>`).join(" ") || "none"}</div>
        </li>`).join("")
    : `<li class="empty">No failures recorded yet.</li>`;

  const trends = a.creator_trends.filter((c) => Object.keys(c.repeated_failures).length);
  $("analytics-body").innerHTML = `
    <div class="stat-row">
      ${stat("Submissions", t.submissions)}
      ${stat("Included", t.included.count, `${t.excluded_incomplete.count} excluded as incomplete`)}
      ${stat("Machine pass rate", pct(a.machine_pass_rate), "every requirement passed automatically")}
      ${stat("Final approval rate", pct(a.final_approval_rate), "of submissions that were decided")}
      ${stat("Override rate", pct(a.override_rate), "reviewed rules where a human disagreed")}
      ${stat("Unresolved", t.unresolved.count)}
    </div>
    <p class="note">
      Machine and human outcomes are reported separately on purpose. A gap
      between them is the signal, not an inconsistency.
    </p>

    <h3>Where submissions fail</h3>
    <ul class="pattern-list">${patterns}</ul>

    <h3>Creators with repeated failures</h3>
    ${
      trends.length
        ? `<ul class="pattern-list">${trends.map((c) => `
            <li><div class="pattern-head"><span>${esc(c.creator_reference)}</span>
              <span class="muted-inline">${c.submissions.count} submission(s)</span></div>
              <div class="muted-inline">${Object.entries(c.repeated_failures)
                .map(([r, n]) => `${esc(r)} failed ${n}x`).join(" · ")}</div></li>`).join("")}</ul>`
        : `<p class="empty">No creator has failed the same requirement twice yet.</p>`
    }

    ${
      Object.keys(a.unavailable).length
        ? `<h3>Not measurable yet</h3><ul class="unavailable-list">${Object.entries(a.unavailable)
            .map(([k, v]) => `<li><strong>${esc(k)}</strong>: ${esc(v)}</li>`).join("")}</ul>`
        : ""
    }`;
}

$("analytics-campaign").addEventListener("change", (e) => renderAnalytics(e.target.value));

/* ── request changes ────────────────────────────────────────────────────── */

async function openRevision() {
  const res = await api(`/api/submissions/${currentSubmissionId}/revision-draft`);
  if (!res.ok) {
    alert("Could not draft revision instructions.");
    return;
  }
  const d = await res.json();
  $("revision-form").querySelector("[name=message]").value = d.message;
  $("revision-excluded").innerHTML = d.excluded.length
    ? `<p class="note"><strong>Left out of the draft:</strong> ${d.excluded
        .map((x) => esc(x.reason)).join(" ")}</p>`
    : "";
  $("revision-error").hidden = true;
  $("revision-dialog").showModal();
}

$("revision-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = new FormData(e.target).get("message");
  const res = await api(`/api/submissions/${currentSubmissionId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision: "request_changes", rationale: message }),
  });
  const err = $("revision-error");
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    err.textContent = typeof d.detail === "string" ? d.detail : "Could not record that.";
    err.hidden = false;
    return;
  }
  $("revision-dialog").close();
  loadReport();
  loadSubmissions();
});

/* ── auth ───────────────────────────────────────────────────────────────── */

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: $("login-email").value,
      password: $("login-password").value,
    }),
  });
  if (!res.ok) {
    showLogin("Email or password is incorrect.");
    return;
  }
  $("login-password").value = "";
  boot();
});

$("logout").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  currentUser = null;
  showLogin();
});

async function boot() {
  let me;
  try {
    const res = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (!res.ok) {
      showLogin();
      return;
    }
    me = await res.json();
  } catch {
    showLogin();
    return;
  }
  currentUser = me.user;
  const ws = me.workspaces[0];
  $("who").textContent = ws
    ? `${me.user.email} · ${ws.name ?? ""} (${ws.role})`
    : me.user.email;
  showApp();
  loadIntegrity();
  loadCampaigns();
  loadSubmissions();
}

boot();
setInterval(() => {
  if (currentUser) loadSubmissions();
}, 10000);
