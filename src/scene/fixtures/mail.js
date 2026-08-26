import * as THREE from "three";
import { toon, tagSprite } from "../kit.js";

/**
 * the mailroom
 *
 * Intake is the mouth of the pipeline and the only stage the room could not see:
 * things get captured, cached, and never filed, and nothing anywhere said so.
 * This is a pigeonhole per feed, stacked with what is sitting in it.
 *
 * The one visual decision that matters: WHEN THE SUMMARY IS STALE, THE ROOM
 * SHOWS THE STALENESS WHERE THE COUNT WOULD HAVE GONE. A stale count rendered as
 * a current one is a lie you cannot see, which is worse than no mailroom at all.
 * So a stale cabinet loses its numbers entirely, its paper goes amber, and a red
 * band runs across it saying why.
 *
 * Three appearances that must never converge:
 *   a number    the last run covered this feed and this much is waiting
 *   "clear"     genuinely nothing waiting, in green, and it means it
 *   "unknown"   nobody could count it (email is a live mailbox) or the source
 *               itself is broken. Hatched dark, never an empty shelf
 */

export const id = "mail";
export const title = "the mailroom";
export const wall = true;

const INK = "#4a3b33";
const CREAM = "#fdf6e8";
const ALERT = "#d1495b";
const AMBER = "#c07c2c";
const GO = "#3f9e6a";

const WOOD = "#e0b183";
const VOID = "#7d6047";

/** Above this the stack stops growing and the tag carries the real number. A
 *  pigeonhole with 50 notes in it should look worse than one with 4 without
 *  becoming a chimney that leaves the room. */
const MAX_SHEETS = 9;

const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** Paper. Its own material per cabinet, never the shared cache, because stale
 *  paper is translucent and mutating a cached material would tint the office. */
function paperMaterial(stale) {
  return new THREE.MeshToonMaterial({
    color: stale ? AMBER : CREAM,
    transparent: stale,
    opacity: stale ? 0.55 : 1,
  });
}

export function build(ctx) {
  const slot = ctx.room?.wall;
  if (!slot) return null;

  const section = ctx.section || null;
  const ok = section?.state === "ok";
  // A source that is unconfigured, timed out or broken still gets a cabinet.
  // A mailroom that vanishes when intake is unreachable teaches you that no
  // mailroom means no mail, which is the exact thing this fixture is here for.
  const stale = ok ? !!section.stale : true;
  const banner = ok
    ? (stale ? clip(section.stale_reason || "the summary is not current", 62) : "")
    : `${section?.state || "no data"}: ${clip(section?.detail || "intake was never read", 46)}`;

  const holes = ok && section.pigeonholes?.length
    ? section.pigeonholes
    : [
        { key: "granola", label: "granola", waiting: null },
        { key: "capture", label: "mobile capture", waiting: null },
        { key: "email", label: "email", waiting: null },
      ];

  const W = slot.w;
  const H = slot.h;
  const BASE = 0.25;

  const group = new THREE.Group();
  group.position.set(slot.x, BASE, slot.z);

  const carcass = new THREE.Mesh(new THREE.BoxGeometry(W, H, 0.22), toon(WOOD));
  carcass.position.set(0, H / 2, 0.11);
  carcass.userData.fixture = { id, payload: { focus: null } };
  group.add(carcass);

  // The headline, hung above the cabinet at pod-sign scale so it is the thing
  // you read from the far end of the room rather than something you must walk to.
  const head = tagSprite(stale ? "MAILROOM · STALE" : "the mailroom", {
    bg: stale ? ALERT : INK,
    fg: CREAM,
    scale: 1.35,
  });
  head.position.set(0, H + 0.62, 0.3);
  group.add(head);

  // The band. Red, full width, and it sits ON the cabinet face where a person
  // looking for a number would look first.
  if (banner) {
    const band = new THREE.Mesh(
      new THREE.BoxGeometry(W - 0.16, 0.34, 0.06),
      new THREE.MeshBasicMaterial({ color: ALERT })
    );
    band.position.set(0, H - 0.26, 0.25);
    group.add(band);
    const why = tagSprite(banner, { bg: ALERT, fg: "#fff", scale: 0.82 });
    why.position.set(0, H - 0.26, 0.34);
    group.add(why);
  }

  const PAD = 0.12;
  const top = H - (banner ? 0.5 : 0.16);
  const cellW = (W - PAD * (holes.length + 1)) / holes.length;
  const cellH = top - 0.46;
  const paper = paperMaterial(stale);

  holes.forEach((hole, i) => {
    const cx = -W / 2 + PAD + cellW / 2 + i * (cellW + PAD);
    const floor = 0.46;

    const shelf = new THREE.Mesh(new THREE.BoxGeometry(cellW, cellH, 0.02), toon(VOID));
    shelf.position.set(cx, floor + cellH / 2, 0.23);
    group.add(shelf);

    const unknown = hole.waiting == null;
    const waiting = unknown ? 0 : hole.waiting;

    if (unknown) {
      // Hatched, not empty. Diagonal slats say "we could not look", where a bare
      // shelf would say "we looked and there was nothing".
      for (let s = 0; s < 5; s++) {
        const slat = new THREE.Mesh(
          new THREE.BoxGeometry(cellW * 0.66, 0.045, 0.02),
          new THREE.MeshBasicMaterial({ color: "#5c4632" })
        );
        slat.position.set(cx, floor + cellH * (0.2 + s * 0.14), 0.245);
        slat.rotation.z = -0.5;
        group.add(slat);
      }
    } else {
      const sheets = Math.min(waiting, MAX_SHEETS);
      for (let s = 0; s < sheets; s++) {
        const sheet = new THREE.Mesh(new THREE.BoxGeometry(cellW * 0.76, 0.05, 0.3), paper);
        // A hand-stacked pile, not a printer tray. Tiny jitter reads as paper.
        sheet.position.set(cx + (s % 2 ? 0.02 : -0.02), floor + 0.05 + s * 0.075, 0.3);
        sheet.rotation.y = (s % 3) * 0.02 - 0.02;
        group.add(sheet);
      }
    }

    // Where the count goes. Stale takes this slot; that is the whole point.
    const value = stale ? "stale" : unknown ? "unknown" : waiting ? String(waiting) : "clear";
    const bg = stale || (!ok && unknown) ? ALERT : unknown ? INK : waiting ? AMBER : GO;
    const tag = tagSprite(value, { bg, fg: "#fff", scale: 0.9 });
    tag.position.set(cx, floor + cellH - 0.24, 0.42);
    group.add(tag);

    const name = tagSprite(hole.label || hole.key, { bg: CREAM, fg: INK, scale: 0.78 });
    name.position.set(cx, 0.24, 0.42);
    group.add(name);

    // The click target is the whole pigeonhole, deep enough to catch a ray from
    // the room camera without needing a steady hand at desk scale.
    const pad = new THREE.Mesh(
      new THREE.BoxGeometry(cellW, cellH + 0.4, 0.6),
      new THREE.MeshBasicMaterial({ visible: false })
    );
    pad.position.set(cx, floor + cellH / 2 - 0.1, 0.5);
    pad.userData.fixture = { id, payload: { focus: hole.key } };
    group.add(pad);
  });

  return group;
}

/* ------------------------------------------------------------------ panel -- */

function line(api, label, value, cls) {
  const p = api.el("p", "log");
  p.append(api.el("b", null, label), " ");
  p.append(api.el("span", cls || null, String(value)));
  return p;
}

export function panel(payload, world, api) {
  const s = world?.sections?.mail;
  const wrap = api.el("div");

  if (!s || s.state !== "ok") {
    const why = {
      unconfigured: "No vault root is configured for this office, so intake was never asked.",
      missing: "There is no intake service at the configured vault root.",
      timeout: "Intake did not answer in time. This is NOT an empty mailroom: nobody could say what is in it.",
      unreadable: "Intake answered with something that was not its summary.",
      error: "Intake failed when asked for its summary.",
      unbuilt: "This source has not been built yet.",
    }[s?.state] || "There is no intake data in this snapshot.";
    const box = api.el("div", "issue hot");
    box.append(api.el("div", "issue-title", why));
    if (s?.detail) box.append(api.el("p", "p-detail", s.detail));
    wrap.append(box);
    return wrap;
  }

  // Staleness first, always, before a single count. Anything underneath it
  // describes a moment that has already passed.
  if (s.stale) {
    const box = api.el("div", "issue hot");
    box.append(api.el("div", "issue-title", "This summary is not current."));
    box.append(api.el("p", "p-detail", s.stale_reason || "intake did not say why"));
    box.append(api.el("p", "p-detail",
      "Every number below is what the LAST run decided, not what is true now. " +
      "Run a dry run to make them current."));
    wrap.append(box);
  } else {
    wrap.append(line(api, "current.", "the last run covers everything on disk", "ok"));
  }

  wrap.append(line(api, "last run", s.last_run || "never", s.last_run ? null : "bad"));
  if (s.dry_run != null) {
    wrap.append(line(api, s.dry_run ? "dry run" : "filing run",
      s.dry_run ? "decided everything, filed nothing" : "this run created issues"));
  }
  if (s.watermark) wrap.append(line(api, "watermark", s.watermark));

  wrap.append(api.el("h3", null, "pigeonholes"));
  for (const h of s.pigeonholes || []) {
    const unknown = h.waiting == null;
    const hot = s.stale || unknown || h.waiting > 0;
    const card = api.el("div", `issue${hot ? " hot" : ""}`);
    if (payload?.focus === h.key) card.style.borderColor = "rgba(74,59,51,0.55)";

    const top = api.el("div", "issue-top");
    top.append(api.el("span", "issue-num",
      s.stale ? "stale" : unknown ? "unknown" : String(h.waiting)));
    top.append(api.el("span", "issue-title", h.label || h.key));
    card.append(top);

    card.append(api.el("p", "p-detail", h.why || ""));
    const tags = api.el("div", "tags");
    if (h.on_disk != null) tags.append(api.el("span", "tag", `${h.on_disk} on disk`));
    tags.append(api.el("span", "tag", `${h.covered ?? 0} covered by the last run`));
    tags.append(api.el("span", "tag", `${h.filed ?? 0} filed as issues`));
    tags.append(api.el("span", `tag${h.in_last_run ? "" : " hot"}`,
      h.in_last_run ? "in the last run" : "not in the last run"));
    card.append(tags);
    wrap.append(card);
  }

  const c = s.counts || {};
  wrap.append(api.el("h3", null, "what the last run decided"));
  wrap.append(line(api, `${c.items ?? 0}`, "items decided"));
  wrap.append(line(api, `${c.would_file ?? 0}`, "a dry run says would be filed next"));
  wrap.append(line(api, `${c.filed ?? 0}`, "actually became issues"));
  wrap.append(line(api, `${c.cached ?? 0}`, "extractions cached on disk"));

  // Three different things. A single "not filed" number would hide the only
  // distinction that matters: one is a decision, one is a gate, one is a
  // FAILURE that looks exactly like an absence.
  const held = s.held || {};
  wrap.append(api.el("h3", null, "not filed, and not the same thing"));
  wrap.append(line(api, `${held.declined ?? 0}`, "declined: looked at, judged not actionable"));
  wrap.append(line(api, `${held.rate_limited ?? 0}`,
    "rate limited: Granola answered 429 and the retries ran out. NOT an absence",
    held.rate_limited ? "bad" : null));
  wrap.append(line(api, `${held.no_transcript ?? 0}`,
    "no transcript on disk yet", held.no_transcript ? "wait" : null));
  for (const [reason, n] of Object.entries(held.blocked || {})) {
    wrap.append(line(api, `${n}`, `blocked: ${reason}`, n ? "bad" : null));
  }
  if (!Object.keys(held.blocked || {}).length) {
    wrap.append(line(api, "0", "blocked on anything else", "ok"));
  }

  // Deliberate exclusions are data. Named, so a skipped item can never be
  // mistaken for one that quietly fell out of the pipeline.
  const ex = s.excluded || [];
  wrap.append(api.el("h3", null, `deliberately excluded (${ex.length})`));
  if (!ex.length) {
    wrap.append(api.el("p", "empty", "Nothing is excluded. Everything on disk is eligible."));
  } else {
    wrap.append(api.el("p", "p-detail",
      "Chosen by hand, not by a classifier. These were seen and kept out on purpose."));
    for (const item of ex) {
      const card = api.el("div", "issue");
      card.append(api.el("div", "issue-title", item.title || item.id || "(unnamed)"));
      if (item.id) card.append(api.el("div", "issue-repo", item.id));
      wrap.append(card);
    }
  }
  return wrap;
}

/**
 * The ugly cases on purpose: a stale summary, a feed nobody can count, a feed
 * that was not in the last run, a live rate limit, and a real exclusion list.
 * `?demo=1` that only ever shows the happy path is a demo that hides the bugs.
 */
export function demo() {
  return {
    state: "ok",
    stale: true,
    stale_reason: "granola: 12 of 50 on disk; capture was not in the last run",
    last_run: "2026-08-25T18:30:44.256629+00:00",
    watermark: "2026-08-25",
    dry_run: true,
    pigeonholes: [
      {
        key: "granola", label: "granola", in_last_run: true, on_disk: 50,
        covered: 12, filed: 3, waiting: 38,
        why: "38 of 50 on disk are not in the last run's decisions",
      },
      {
        key: "capture", label: "mobile capture", in_last_run: false, on_disk: 2,
        covered: 0, filed: 0, waiting: 2,
        why: "this feed was not in the last run, so none of it has been looked at",
      },
      {
        key: "email", label: "email", in_last_run: true, on_disk: null,
        covered: 9, filed: 1, waiting: null,
        why: "email is a live mailbox, so nothing about it can be counted from disk. Unknown, not zero.",
      },
    ],
    counts: { items: 21, would_file: 6, filed: 3, cached: 72 },
    held: {
      declined: 11,
      blocked: { blocked_on_identity: 2, not_opted_in: 1 },
      rate_limited: 4,
      no_transcript: 2,
    },
    excluded: [
      { id: "1dc01aab-ec47-4a2f-bef3-d8ccdc452a3d", title: "Career options and self-worth with mentor" },
      { id: "1e8a370a-3070-4d09-9307-f589021a5142", title: "Discuss Principles + Values" },
      { id: "", title: "any title matching /standup/" },
    ],
  };
}
