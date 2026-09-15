import assert from "node:assert/strict";
import test from "node:test";

// The smallest DOM the renderer uses: elements, text nodes, attributes, serialized for comparison.
class Text { constructor(value) { this.value = value; } html() { return this.value.replace(/&/g, "&amp;").replace(/</g, "&lt;"); } }
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; }
  set className(value) { this.attrs.class = value; }
  set textContent(value) { this.children = [new Text(String(value))]; }
  setAttribute(name, value) { this.attrs[name] = value; }
  append(...nodes) { this.children.push(...nodes); }
  html() {
    const attrs = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${v}"`).join("");
    return `<${this.tag}${attrs}>${this.children.map(child => child.html()).join("")}</${this.tag}>`;
  }
}
globalThis.document = { createElement: tag => new Element(tag), createTextNode: value => new Text(value) };
const { markdownView, tokenText } = await import("../client/phone/office-markdown.js");
const render = (raw, options) => markdownView(raw, options).children.map(child => child.html()).join("");

test("renders inline and block markdown", () => {
  assert.equal(render("**bold** *it* `code` [site](https://x.dev)"),
    '<p><strong>bold</strong> <em>it</em> <code>code</code> <a href="https://x.dev" target="_blank" rel="noopener noreferrer">site</a></p>');
  assert.equal(render("## Head\n- a\n- b\n\n1. one\n2. two"), "<h3>Head</h3><ul><li>a</li><li>b</li></ul><ol><li>one</li><li>two</li></ol>");
  assert.equal(render("> quoted **x**"), "<blockquote><p>quoted <strong>x</strong></p></blockquote>");
  assert.equal(render("```\n**not bold**\n```"), "<pre>**not bold**</pre>");
  assert.equal(render("| a | b |\n|---|---|\n| 1 | 2 |"),
    '<div class="mdtable"><table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table></div>');
});

test("raw HTML and unsafe links stay inert", () => {
  assert.equal(render('<img src=x onerror="alert(1)">'), '<p>&lt;img src=x onerror="alert(1)"></p>');
  assert.equal(render("[click](javascript:alert(1))"), "<p>click)</p>");
  assert.equal(render("[click](javascript:void)"), "<p>click</p>");
});

test("tokens link in plain text and emphasis, never in code", () => {
  const text = tokenText([{ text: "tbs#12" }, { text: "abc1234" }], token => {
    const node = new Element("button"); node.textContent = token.text; return node;
  });
  assert.equal(render("see **tbs#12** and abc1234 but `tbs#12`", { text }),
    "<p>see <strong><button>tbs#12</button></strong> and <button>abc1234</button> but <code>tbs#12</code></p>");
});
