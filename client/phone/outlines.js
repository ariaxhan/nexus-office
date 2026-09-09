"use strict";
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const names = {superpowerai:"SuperPower AI", mommyai:"MommyAI"};
let outlines = [];

function badge(row) { return `<span class="badge ${row.approved ? "approved" : ""}">${esc(row.status)}</span>`; }
function render(markdown) {
  const out = [];
  let list = false;
  const close = () => { if (list) { out.push("</ul>"); list = false; } };
  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    const numbered = /^\d+\.\s+(.*)$/.exec(line);
    if (heading) { close(); out.push(`<h${heading[1].length + 1}>${esc(heading[2])}</h${heading[1].length + 1}>`); }
    else if (bullet || numbered) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${esc((bullet || numbered)[1])}</li>`); }
    else if (line) { close(); out.push(`<p>${esc(line)}</p>`); }
  }
  close();
  return out.join("");
}
function decisionLine(row) {
  if (!row.decision) return "";
  return `<span class="decision">${row.proposed ? '<em class="proposed">proposed</em> ' : ""}${esc(row.decision)}</span>`;
}
function valueLine(row) { return row.value ? `<span class="value">${esc(row.value)}</span>` : ""; }
function action(row, index) {
  return row.approved
    ? '<span class="queued">queued for the factory</span>'
    : `<button class="approve-row" data-index="${index}">Approve</button>`;
}
function card(row, index) {
  return `<div class="outline-card"><button class="open" data-index="${index}"><span class="lesson-id">${esc(row.lesson)}</span>${badge(row)}<span class="meta">${esc(names[row.product] || row.product)} · drafted ${esc(row.drafted || "unknown")}</span>${decisionLine(row)}${valueLine(row)}</button>${action(row, index)}<span class="feedback" role="status"></span></div>`;
}
function drawList() {
  document.getElementById("summary").textContent = `${outlines.length} ${outlines.length === 1 ? "outline" : "outlines"}`;
  document.getElementById("list").innerHTML = outlines.map(card).join("") || '<p class="empty">No outlines yet.</p>';
  document.getElementById("list").hidden = false;
  document.getElementById("reader").hidden = true;
}
function drawReader(index) {
  const row = outlines[index];
  if (!row) return drawList();
  document.getElementById("list").hidden = true;
  const reader = document.getElementById("reader");
  reader.hidden = false;
  reader.innerHTML = `<a class="back" href="#">Back to outlines</a><h1>${esc(names[row.product] || row.product)} ${esc(row.lesson)}</h1>
    <div class="reader-head">${badge(row)}<button id="approve" data-index="${index}" ${row.approved ? "disabled" : ""}>${row.approved ? "Approved" : "Approve outline"}</button><span class="feedback" role="status"></span></div>
    <p class="meta">${decisionLine(row)}</p>${row.value ? `<p class="value">${esc(row.value)}</p>` : ""}
    ${row.approved ? '<p class="queued">queued for the factory</p>' : ""}<small>template ${esc(row.template)} · lane ${esc(row.lane)} · ${esc(row.source)}</small>
    <div class="reader">${render(row.body)}</div>`;
  window.scrollTo(0, 0);
}
async function load() {
  const response = await fetch("/api/lesson-outlines");
  const data = await response.json();
  if (!response.ok || data.state !== "ok") throw new Error(data.error || "Outlines unavailable");
  outlines = data.outlines;
  const hash = /^#(\d+)$/.exec(location.hash);
  hash ? drawReader(Number(hash[1])) : drawList();
}
async function approve(button) {
  const index = Number(button.dataset.index);
  const row = outlines[index];
  const feedback = button.parentElement.querySelector(".feedback") || document.querySelector(".feedback");
  button.disabled = true;
  feedback.textContent = "Approving…";
  try {
    const response = await fetch("/api/lesson-outlines/approve", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product:row.product, lesson:row.lesson})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not approve");
    row.status = data.status; row.approved = true; row.proposed = false; row.queued = true;
    button.closest(".outline-card") ? drawList() : drawReader(index);
  } catch (error) { feedback.textContent = error.message; button.disabled = false; }
}
document.getElementById("list").addEventListener("click", event => {
  const approveButton = event.target.closest(".approve-row");
  if (approveButton) return approve(approveButton);
  const open = event.target.closest(".open");
  if (open) { location.hash = open.dataset.index; drawReader(Number(open.dataset.index)); }
});
document.getElementById("reader").addEventListener("click", event => {
  if (event.target.closest(".back")) { event.preventDefault(); history.replaceState(null, "", location.pathname); drawList(); }
  if (event.target.id === "approve") approve(event.target);
});
window.addEventListener("hashchange", () => { const hash = /^#(\d+)$/.exec(location.hash); hash ? drawReader(Number(hash[1])) : drawList(); });
load().catch(error => { document.getElementById("summary").textContent = "Could not load outlines"; document.getElementById("list").textContent = error.message; });
