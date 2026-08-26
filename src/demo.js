/**
 * A fake floor, for `?demo=1`.
 *
 * Two jobs. It lets anyone clone this repo and see what the room is before they
 * own a Cloudflare account or a pipeline, and it lets the room be worked on
 * without a live snapshot or a session, which is how the styling and the
 * filtering actually get verified.
 *
 * Every state in STATES appears at least once on purpose: a demo that only shows
 * the happy path is a demo that lets the ugly cases rot.
 */

import { FIXTURES } from "./scene/fixtures/all.js";

const now = Date.now();
const ago = (mins) => new Date(now - mins * 60000).toISOString().replace(/\.\d+Z$/, "Z");

const marker = "<!-- pipeline-bot -->";

const issue = (number, title, body, opts = {}) => ({
  number,
  title,
  body,
  labels: opts.labels || [],
  url: `https://github.com/${opts.repo || "acme/demo"}/issues/${number}`,
  updatedAt: ago(opts.age ?? 40),
  bot_last: !!opts.botLast,
  last_word: opts.botLast
    ? `Automated pass complete. Comment here to send this back through review.\n\n${marker}`
    : "",
});

const pr = (number, title, opts = {}) => ({
  number, title,
  head: opts.head || `pipeline/auto-issue-${opts.closes?.[0] ?? number}`,
  base: "main",
  url: `https://github.com/${opts.repo || "acme/demo"}/pull/${number}`,
  draft: !!opts.draft,
  mergeable: opts.mergeable || "MERGEABLE",
  state: opts.state || "CLEAN",
  closes: opts.closes || [],
  updatedAt: ago(opts.age ?? 20),
});

const station = (repo, outcome, detail, issues, runs, prs) => ({
  repo,
  identity: "demo",
  access: outcome !== "no-access",
  outcome: outcome === "no-access" ? "" : outcome,
  detail,
  at: ago(runs?.[0]?.mins ?? 30),
  runs: (runs || []).map((r) => ({
    at: ago(r.mins),
    outcome: r.outcome,
    issue: r.issue || "",
    detail: r.detail || "",
  })),
  issues: issues || [],
  issues_error: outcome === "no-access" ? "no account holds push here" : null,
  // Finished work waiting only on a merge. One of them conflicts, because a
  // demo where every button is live never shows what a blocked one looks like.
  prs: prs || [],
  prs_error: null,
});

/**
 * Sections belonging to a SOURCE rather than to a fixture.
 *
 * `sections` is otherwise built from the fixtures themselves, which works right
 * up until a fixture reads somebody else's data. The working light reads
 * `sections.pipeline`, which comes from `client/sources/pipeline.py` and has no
 * fixture module, so the demo floor could only ever render one of its three
 * states and the two that matter most had no picture in CI.
 *
 * `?demo=1&pipeline=idle` picks another. The default is a run in flight, to
 * match the rest of this floor: it fabricates a live gate and a running
 * commission too, and being coy about only this one would be inconsistent.
 */
const PIPELINES = {
  running: {
    state: "ok", running: true, running_for: "12m",
    doing: "acme/storefront #214 · writing the regression test", next_in: null,
  },
  idle: { state: "ok", running: false, doing: "", next_in: "18m" },
  off: {
    state: "off", running: false,
    detail: "the kill switch is on, so nothing will run at all",
  },
  // Deliberately absent, which is how a real office with no pipeline configured
  // looks, and the state the light has to shout about rather than swallow.
  unknown: null,
};

export function demoWorld() {
  const want = new URLSearchParams(location.search).get("pipeline") || "running";
  const pipeline = PIPELINES[want] !== undefined ? PIPELINES[want] : PIPELINES.running;

  return {
    at: ago(3),
    // Each fixture supplies its own fake section. Keeping the fake beside the
    // thing it feeds is what stops the demo floor drifting out of date.
    sections: {
      ...Object.fromEntries(
        FIXTURES.filter((f) => f.demo).map((f) => [f.id, f.demo()])
          .filter(([, v]) => v != null)
      ),
      ...(pipeline ? { pipeline } : {}),
    },
    // Three orders, one of each fate. The failed one matters most: a decision
    // that failed used to look exactly like one that worked, because its reason
    // was written to the queue and never shown to anybody.
    decisions: [
      { id: 31, at: ago(1), kind: "nudge", repo: "northwind/api", issue: null,
        payload: {}, status: "pending", applied_at: null, result: null },
      { id: 30, at: ago(9), kind: "chat", repo: "", issue: null,
        payload: { body: "What is actually blocking the storefront checkout fix?" },
        status: "failed", applied_at: ago(8),
        result: "the runtime did not take it: <urlopen error [Errno 61] Connection refused>" },
      { id: 29, at: ago(46), kind: "unblock", repo: "acme/billing", issue: "58",
        payload: { body: "Refunds follow the 30 day policy. Build against that." },
        status: "done", applied_at: ago(45),
        result: "as ariaxhan: issue comment; issue edit: label was not set" },
    ],
    generated: ago(3),
    heartbeat: ago(3),
    killed: false,
    // A gate is in the demo on purpose. It is the highest-value state in the room
    // and the easiest to leave untested, because it only shows up when something
    // is genuinely stuck.
    runtime: {
      url: "http://127.0.0.1:8787",
      root: "/demo/acme/storefront",
      gate: {
        state: "pending",
        id: "a1b2c3d4e5f60718",
        permission: "run_bash",
        target: "npx playwright install --with-deps chromium",
        detail: "The lane wants a browser it can drive before it reproduces the bug.",
        asked_at: (now - 47000) / 1000,
        waiting_s: 47,
      },
      board: {
        state: "up",
        root: "/demo/acme/storefront",
        runs: [{ id: "run-9f2", slug: "checkout-host-name", state: "running",
                 started: ago(8), step: "writing the test" }],
        active: [{ slug: "checkout-host-name", state: "active",
                   title: "Capture host_name on iOS", updated: ago(8) }],
        // Three of the twenty-two, and one of them never wrote a chronicle. The
        // done column was empty here until the board fixture pointed out that a
        // demo floor which cannot show a missing chronicle cannot photograph the
        // one thing that issue is about.
        complete: [
          { slug: "gate-answer-by-id", title: "Answer a gate by its id, never its position",
            updated: ago(90), has_chronicle: true, has_transcript: true },
          { slug: "shot-harness", title: "Give the runner eyes",
            updated: ago(240), has_chronicle: true, has_transcript: false },
          { slug: "hotfix-poll-loop", title: "Stop the poll loop hammering a dead runtime",
            updated: ago(700), has_chronicle: false, has_transcript: false },
        ],
        archived_count: 14,
        metrics: {
          active: 1, complete: 22, archived: 14, runs: 61,
          total_cost: 18.42, average_cache_read_ratio: 0.71,
        },
      },
    },
    today: {
      survey: 41, deferred: 22, landed: 6, refused: 4,
      "caught-up": 9, "report-only": 3, parked: 2, "no-issues": 2,
    },
    stations: [
      station("acme/storefront", "landed", "pipeline/auto/push", [
        issue(214, "Checkout drops the host name on mobile Safari",
          "## What happens\n\nThe `host_name` field is captured on desktop and lost on iOS.\n\n- [x] reproduced on iOS 18\n- [ ] fix the serialiser\n- [ ] regression test\n\n| browser | captured |\n| --- | --- |\n| Chrome | yes |\n| Safari iOS | **no** |",
          { repo: "acme/storefront", age: 22 }),
        issue(211, "Add a cheap doctor command", "Prints what is configured and what is reachable, with a remedy beside anything broken.", { repo: "acme/storefront", age: 190 }),
      ], [
        { mins: 22, outcome: "landed", issue: "213", detail: "pipeline/auto/push" },
        { mins: 84, outcome: "survey", detail: "3 open: 2 to work, 1 waiting on a human" },
      ], [
        pr(215, "#213: stop the checkout serialiser dropping host_name",
           { repo: "acme/storefront", closes: [213], age: 21 }),
        pr(212, "#209: retry the webhook once before giving up",
           { repo: "acme/storefront", closes: [209], mergeable: "CONFLICTING", age: 320 }),
      ]),

      station("acme/billing", "refused", "no commit", [
        issue(58, "Decide the refund policy before this can be built",
          "This is a business decision, not an engineering one.\n\n> The runner correctly refused it.\n\nSomeone has to say what the policy *is*.",
          { repo: "acme/billing", botLast: true, labels: ["waiting on human"], age: 61 }),
      ], [
        { mins: 61, outcome: "refused", issue: "58", detail: "needs a human decision, not code" },
      ]),

      station("acme/website", "deferred", "4 issue(s) not reached this run", [
        issue(90, "Dark mode flashes white on first paint",
          "The theme is applied after hydration.\n\n```js\ndocument.documentElement.dataset.theme = stored;\n```\n\nMove it into the head.",
          { repo: "acme/website", age: 15 }),
        issue(88, "Compress the hero image", "It is 2.4MB.", { repo: "acme/website", age: 300 }),
        issue(84, "Broken link in the footer", "Points at the old docs domain.", { repo: "acme/website", age: 900 }),
      ], [
        { mins: 15, outcome: "deferred", detail: "4 issue(s) not reached this run" },
      ]),

      station("acme/docs", "caught-up", "nothing open", [], [
        { mins: 45, outcome: "caught-up", detail: "nothing open" },
      ]),

      station("acme/mobile", "landed", "pipeline/auto/push", [
        issue(31, "Crash on cold start when the cache is empty",
          "Stack trace attached. Happens once per install.", { repo: "acme/mobile", age: 8 }),
      ], [
        { mins: 8, outcome: "landed", issue: "30", detail: "pipeline/auto/push" },
      ], [
        pr(33, "#30: guard the cold-start cache read",
           { repo: "acme/mobile", closes: [30], age: 7 }),
      ]),

      station("acme/legacy-import", "parked",
        "PARKED. This is a duplicate clone of a client production repo.", [], [
        { mins: 120, outcome: "parked", detail: "PARKED, see the repo's own config" },
      ]),

      station("northwind/api", "survey", "9 open: 4 to work, 5 waiting on a human", [
        issue(402, "Rate limiting returns 500 instead of 429",
          "## Repro\n\n1. Hammer `/v1/search`\n2. Watch the status codes\n\nExpected `429`, got `500`.",
          { repo: "northwind/api", botLast: true, age: 33 }),
        issue(398, "Document the webhook retry schedule",
          "Support keeps getting asked and there is no answer written down.",
          { repo: "northwind/api", age: 500 }),
      ], [
        { mins: 33, outcome: "survey", detail: "9 open: 4 to work, 5 waiting on a human" },
        { mins: 400, outcome: "landed", issue: "395", detail: "pipeline/auto/push" },
      ]),

      station("northwind/warehouse", "deferred", "2 issue(s) not reached this run", [
        issue(77, "Stock counts drift after a partial return",
          "Off by the returned quantity, every time. Suspect the ledger is written twice.",
          { repo: "northwind/warehouse", age: 70 }),
      ], [
        { mins: 70, outcome: "deferred", detail: "2 issue(s) not reached this run" },
      ]),

      station("northwind/analytics", "no-issues", "issues are disabled on this repo", [], [
        { mins: 200, outcome: "no-issues", detail: "issues are disabled on this repo" },
      ]),

      station("northwind/internal-tools", "no-access",
        "no account we hold a token for can push here", [], []),

      station("tiny/scratch", "report-only", "issue_only", [
        issue(4, "Should this repo exist at all?",
          "It has three files and none of them are imported anywhere.",
          { repo: "tiny/scratch", botLast: true, age: 1400 }),
      ], [
        { mins: 1400, outcome: "report-only", issue: "4", detail: "issue_only" },
      ]),

      station("tiny/experiments", "caught-up", "nothing open", [], [
        { mins: 600, outcome: "caught-up", detail: "nothing open" },
      ]),
    ],
  };
}
