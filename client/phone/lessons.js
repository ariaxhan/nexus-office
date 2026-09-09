"use strict";
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const link = (url, label) => url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(label)}</a>` : "";
const names = {superpowerai:"SuperPower AI", mommyai:"MommyAI"};
let snapshot;
const steeringDrafts = new Map();

function signal(title, value, detail) {
  const state = value.present === null ? "unknown" : value.present ? "present" : "missing";
  return `<div class="signal ${state}"><dt>${title}<span>${state}</span></dt><dd>${detail}</dd></div>`;
}
function finding(title, value) {
  if (value.state === "unknown") return `<p><strong>${title}</strong> <span class="meta">No ledger row</span></p>`;
  const count = (v, name) => `${v === null ? "unknown" : v} ${name}`;
  return `<p><strong>${title}</strong> ${esc(value.state)} · ${esc(count(value.defects, "defects"))} · ${esc(count(value.gaps, "gaps"))} ${link(value.report_url, "Open report")}<small>${esc(value.scope || "")} ${esc(value.checked_at)}</small></p>`;
}
function lesson(row) {
  const p = row.planned, d = row.drafted, v = row.previewed, m = row.on_main;
  const identity = `${row.product}-${row.lesson}`;
  return `<details class="lesson" id="${identity}"><summary><span class="lesson-id">${row.lesson}</span><span class="lesson-title">${esc(row.title || "Topic not recorded")}</span><span class="state">${esc(row.state)}</span>${!p.decision ? '<span class="no-decision">no decision</span>' : ""}</summary>
    <div class="lesson-body"><dl class="signals">
    ${signal("Planned", p, `<span class="${p.decision ? "" : "no-decision"}">${esc(p.decision || "no decision")}</span> ${link(p.report_url, "Open options")}<small>${esc(p.source)}</small>`)}
    ${signal("Drafted", d, esc(d.sources.join("\n") || "No bilingual draft or build directory"))}
    ${signal("Previewed", v, `${link(v.url, "View lesson")}<small>${esc(v.checked_at)} · ${esc(v.source)}</small>`)}
    ${signal("On main", m, `${esc(m.sha ? `origin/main ${m.sha}` : "Unknown revision")}<small>${esc(m.source)}<br>${esc(m.detail)}</small>`)}
    ${signal("Published", row.published, esc(row.published.detail))}
    ${signal("Media", row.media, `<small>${esc(row.media.source)}</small>`)}
    </dl><section class="findings" aria-label="Lesson checks">${finding("Continuity", row.continuity)}${finding("Playthrough", row.playthrough)}</section>
    <form data-product="${row.product}" data-lesson="${row.lesson}"><label for="steer-${identity}">Steer ${names[row.product]} ${row.lesson}</label><p class="meta">Appends to OPTIONS for the lesson lane to read.</p><textarea id="steer-${identity}" name="text" rows="3" maxlength="8000" required placeholder="What should change, stay, or be checked?">${esc(steeringDrafts.get(identity) || "")}</textarea><button type="submit">Save steering</button><p class="feedback" role="status"></p></form></div></details>`;
}
function draw() {
  const data = snapshot;
  document.getElementById("summary").textContent = `${data.lessons.length} lessons · checked ${new Date(data.checked_at).toLocaleTimeString()}`;
  document.getElementById("gaps").textContent = data.gaps.join(" · ");
  const query = document.getElementById("search").value.toLowerCase().trim();
  const product = document.getElementById("product").value;
  document.getElementById("lessons").innerHTML = Object.entries(names).filter(([key]) => !product || key === product).map(([key, name]) => {
    const rows = data.lessons.filter(row => row.product === key && `${row.lesson} ${row.title} ${row.planned.decision}`.toLowerCase().includes(query));
    return `<section class="product"><h2>${name} <span class="meta">${rows.length} ${rows.length === 1 ? "lesson" : "lessons"}</span></h2>${rows.map(lesson).join("") || '<p class="empty">No matching lessons.</p>'}</section>`;
  }).join("");
}
async function load() {
  const response = await fetch("/api/lesson-status");
  const data = await response.json();
  if (!response.ok || data.state !== "ok") throw new Error(data.error || data.detail || "Lesson data unavailable");
  snapshot = data;
  draw();
}
async function steer(event) {
  const form = event.target.closest("form");
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector("button"), feedback = form.querySelector(".feedback");
  button.disabled = true;
  feedback.textContent = "Saving…";
  try {
    const response = await fetch("/api/lesson-steering", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product:form.dataset.product, lesson:form.dataset.lesson, text:form.elements.text.value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not save steering");
    form.elements.text.value = "";
    steeringDrafts.delete(`${form.dataset.product}-${form.dataset.lesson}`);
    feedback.innerHTML = `Saved. ${link(data.report_url, "Read appended steering")}<small>${esc(data.line)}</small>`;
  } catch (error) { feedback.textContent = error.message; }
  finally { button.disabled = false; }
}
document.getElementById("lessons").addEventListener("submit", steer);
document.getElementById("lessons").addEventListener("input", event => {
  const form = event.target.closest("form");
  if (form) steeringDrafts.set(`${form.dataset.product}-${form.dataset.lesson}`, form.elements.text.value);
});
document.getElementById("product").addEventListener("change", draw);
document.getElementById("search").addEventListener("input", draw);
load().catch(error => { document.getElementById("summary").textContent = "Could not load lessons"; document.getElementById("gaps").textContent = error.message; });
