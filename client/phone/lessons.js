"use strict";

const esc = value => String(value || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const short = value => value ? String(value).slice(0, 10) : "missing";
const link = (url, label) => url ? `<a href="${esc(url)}" rel="noreferrer">${esc(label)}</a>` : `<span class="meta">not exposed</span>`;

function surface(title, value, candidate) {
  return `<section class="surface"><h2>${title}</h2><p>${link(value.url, "open")}</p>` +
    `<p><span class="label">source</span> ${esc(short(value.source_sha))}</p>` +
    `<p><span class="label">deployment</span> ${esc(value.deployment_id || "missing")}</p>` +
    (candidate ? `<p><span class="label">QA</span> ${esc(value.qa)}</p>` : "") +
    `<p><span class="label">checked</span> ${esc(value.checked_at || "missing")}</p></section>`;
}

function draw(data) {
  const summary = document.getElementById("summary");
  const out = document.getElementById("lessons");
  if (data.state !== "ok") { summary.textContent = data.state; out.innerHTML = `<p class="empty">${esc(data.detail)}</p>`; return; }
  summary.textContent = `${data.counts.total} lessons · ${data.counts.failed} need attention`;
  out.innerHTML = data.lessons.map(row => {
    const cls = row.candidate_newer_than_production ? "lesson newer" : `lesson ${row.status}`;
    const problems = row.problems.length ? `<ul class="problems">${row.problems.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
    return `<article class="${cls}"><div class="identity"><strong>${esc(row.product)}</strong><span>${esc(row.lesson)}</span></div>${surface("candidate", row.candidate, true)}${surface("production", row.production, false)}${problems}</article>`;
  }).join("") || `<p class="empty">No canonical preview receipts.</p>`;
}

fetch("/api/lesson-previews").then(r => r.json()).then(draw).catch(error => draw({state:"error", detail:error.message}));
