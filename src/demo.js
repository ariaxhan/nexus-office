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

const station = (repo, outcome, detail, issues, runs) => ({
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
});

export function demoWorld() {
  return {
    at: ago(3),
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
        runs: [{ id: "run-9f2", slug: "checkout-host-name", state: "running" }],
        active: [{ slug: "checkout-host-name", state: "active", title: "Capture host_name on iOS" }],
        complete: [],
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
