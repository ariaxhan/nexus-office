import { test } from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown as md } from "../src/ui/markdown.js";

/**
 * The safety tests are the point of this file.
 *
 * Issue bodies are written by anyone who can open an issue and are rendered into
 * a page holding a session token, so "does a heading become an h4" is a nicety
 * and "can a body execute script" is the whole reason this renderer is hand
 * written instead of pulled from a CDN.
 */

test("script tags cannot survive", () => {
  const out = md("<script>alert(1)</script>");
  assert.ok(!out.includes("<script"), out);
  assert.ok(out.includes("&lt;script&gt;"));
});

test("an img with an onerror handler cannot survive", () => {
  const out = md("<img src=x onerror=alert(1)>");
  assert.ok(!out.includes("<img"), out);
});

test("javascript: links stay text, they never become anchors", () => {
  const out = md("[click](javascript:alert(1))");
  assert.ok(!out.includes("<a "), out);
});

test("data: links stay text", () => {
  const out = md("[x](data:text/html,<script>alert(1)</script>)");
  assert.ok(!out.includes("<a "), out);
});

test("http links become anchors that cannot reach back through opener", () => {
  const out = md("[ok](https://example.com/a)");
  assert.ok(out.includes('href="https://example.com/a"'), out);
  assert.ok(out.includes("noopener"), out);
});

test("html inside a code fence is shown, not run", () => {
  const out = md("```\n<script>x</script>\n```");
  assert.ok(out.includes("<pre><code>"), out);
  assert.ok(!out.includes("<script>x"), out);
});

test("a bare number in prose is not eaten by the code-span placeholder", () => {
  // The first version of this used " N " as its marker, which silently ate any
  // digit surrounded by spaces. `code` here forces a span to exist alongside it.
  const out = md("wait `n` for 5 minutes then 12 more");
  assert.ok(out.includes("for 5 minutes"), out);
  assert.ok(out.includes("12 more"), out);
  assert.ok(out.includes("<code>n</code>"), out);
});

test("headings shift down two levels", () => {
  assert.ok(md("# Top").includes("<h3>Top</h3>"));
  assert.ok(md("## Second").includes("<h4>Second</h4>"));
});

test("task lists render as checkboxes and track their state", () => {
  const out = md("- [ ] todo\n- [x] done");
  assert.equal((out.match(/type="checkbox"/g) || []).length, 2);
  assert.equal((out.match(/checked/g) || []).length, 1);
  assert.ok(out.includes("md-done"), out);
});

test("tables render and are wrapped so they can scroll", () => {
  const out = md("| a | b |\n| --- | --- |\n| 1 | 2 |");
  assert.ok(out.includes("md-table-wrap"), out);
  assert.ok(out.includes("<th>a</th>"), out);
  assert.ok(out.includes("<td>2</td>"), out);
});

test("a pipe that is not a table stays a paragraph", () => {
  const out = md("this | that");
  assert.ok(out.startsWith("<p>"), out);
  assert.ok(!out.includes("<table"), out);
});

test("emphasis, strong and strikethrough", () => {
  const out = md("**b** and *i* and ~~s~~");
  assert.ok(out.includes("<strong>b</strong>"), out);
  assert.ok(out.includes("<em>i</em>"), out);
  assert.ok(out.includes("<del>s</del>"), out);
});

test("blockquotes nest their own markdown", () => {
  const out = md("> ## inside");
  assert.ok(out.includes("<blockquote>"), out);
  assert.ok(out.includes("<h4>inside</h4>"), out);
});

test("ordered and unordered lists are distinguished", () => {
  assert.ok(md("1. one\n2. two").includes("<ol>"));
  assert.ok(md("- one\n- two").includes("<ul>"));
});

test("an indented continuation joins the item above it", () => {
  const out = md("- first line\n  continued here\n- second");
  assert.ok(out.includes("first line continued here"), out);
  assert.equal((out.match(/<li>/g) || []).length, 2);
});

test("empty and nullish input render to nothing rather than throwing", () => {
  assert.equal(md(""), "");
  assert.equal(md(null), "");
  assert.equal(md(undefined), "");
});

test("an unterminated code fence does not run off the end", () => {
  const out = md("```\nstill open");
  assert.ok(out.includes("<pre><code>"), out);
  assert.ok(out.includes("still open"), out);
});

test("html comments are stripped, so machinery never leaks into prose", () => {
  const out = md("visible\n\n<!-- pipeline-bot -->");
  assert.ok(out.includes("visible"), out);
  assert.ok(!out.includes("pipeline-bot"), out);
  assert.ok(!out.includes("&lt;!--"), out);
});
