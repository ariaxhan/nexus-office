"use strict";

const esc = value => String(value || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const short = value => value ? String(value).slice(0, 10) : "missing";
const link = (url, label) => url ? `<a href="${esc(url)}" rel="noreferrer">${esc(label)}</a>` : `<span class="meta">not exposed</span>`;

function surface(title, value, candidate) {
  return `<section class="surface"><h2>${title}</h2><p>${link(value.url, "open")}</p>` +
    `<p><span class="label">source</span> ${esc(short(value.source_sha))}</p>` +
    `<p><span class="label">deployment</span> ${esc(short(value.deployment_id))}</p>` +
    (candidate ? `<p><span class="label">QA</span> ${esc(value.qa)}</p>` : "") +
    `<p><span class="label">checked</span> ${esc(value.checked_at || "missing")}</p></section>`;
}

function card(row, previewReady = false) {
  const cls = previewReady ? "lesson candidate-ready" : row.candidate_newer_than_production ? "lesson newer" : `lesson ${row.status}`;
  const problems = row.problems.length ? `<ul class="problems">${row.problems.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  return `<article class="${cls}"><div class="identity"><strong>${esc(row.product)}</strong><span>${esc(row.lesson)}</span></div>${surface("candidate", row.candidate, true)}${surface("production", row.production, false)}${problems}</article>`;
}

function draw(data) {
  const summary = document.getElementById("summary");
  const previews = document.getElementById("previews");
  const coverage = document.getElementById("coverage");
  const all = document.getElementById("all-lessons");
  const toggle = document.getElementById("toggle-all");
  if (data.state !== "ok") { summary.textContent = data.state; previews.innerHTML = `<p class="empty">${esc(data.detail)}</p>`; return; }

  const verified = data.lessons.filter(row => row.candidate && row.candidate.url);
  const missing = data.lessons.length - verified.length;
  summary.textContent = `${verified.length} verified · ${data.counts.total} lessons tracked`;
  document.getElementById("preview-count").textContent = `${verified.length} available`;
  previews.innerHTML = verified.map(row => card(row, true)).join("") || `<p class="empty">No verified previews yet.</p>`;

  const products = new Map();
  data.lessons.forEach(row => {
    const value = products.get(row.product) || {total: 0, verified: 0};
    value.total += 1;
    if (row.candidate && row.candidate.url) value.verified += 1;
    products.set(row.product, value);
  });
  coverage.innerHTML = `<p class="coverage-note"><strong>${missing}</strong> lessons do not yet have verified preview evidence.</p><div class="coverage-grid">${[...products].map(([name, value]) => `<div><strong>${esc(name)}</strong><span>${value.verified} of ${value.total}</span></div>`).join("")}</div>`;
  toggle.hidden = false;
  toggle.textContent = `Show all ${data.counts.total} lessons`;
  toggle.addEventListener("click", () => {
    const opening = all.hidden;
    if (opening && !all.innerHTML) all.innerHTML = data.lessons.map(card).join("");
    all.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
    toggle.textContent = opening ? "Hide lesson inventory" : `Show all ${data.counts.total} lessons`;
  });
}

fetch("/api/lesson-previews").then(r => r.json()).then(draw).catch(error => draw({state:"error", detail:error.message}));
