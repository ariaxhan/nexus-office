import {el} from './office-ui.js';
export function markdownView(raw) {
  const view = el("div", "markdown");
  const lines = String(raw || "").replace(/\r\n/g, "\n").split("\n");
  let code = null;
  let paragraph = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    const node = el("p");
    inlineMarkdown(node, paragraph.join(" "));
    view.appendChild(node);
    paragraph = [];
  }

  for (let at = 0; at < lines.length; at++) {
    const lineText = lines[at];
    if (/^```/.test(lineText)) {
      flushParagraph();
      if (code) {
        view.appendChild(code);
        code = null;
      } else {
        code = el("pre");
      }
      continue;
    }
    if (code) {
      code.textContent += (code.textContent ? "\n" : "") + lineText;
      continue;
    }
    if (!lineText.trim()) {
      flushParagraph();
      continue;
    }
    const heading = lineText.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      const title = el("h" + Math.min(heading[1].length + 1, 6));
      inlineMarkdown(title, heading[2]);
      view.appendChild(title);
      continue;
    }
    const item = lineText.match(/^\s*(?:[-*+]|\d+\.)\s+(.*)$/);
    if (item) {
      flushParagraph();
      const bullet = el("p", "mditem");
      bullet.appendChild(document.createTextNode("• "));
      inlineMarkdown(bullet, item[1]);
      view.appendChild(bullet);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(lineText)) {
      flushParagraph();
      const table = el("pre", "mdtable");
      const rows = [];
      while (at < lines.length && /^\s*\|.*\|\s*$/.test(lines[at])) {
        rows.push(lines[at].trim());
        at++;
      }
      at--;
      table.textContent = rows.join("\n");
      view.appendChild(table);
      continue;
    }
    paragraph.push(lineText.trim());
  }
  flushParagraph();
  if (code) view.appendChild(code);
  return view;
}

function inlineMarkdown(node, raw) {
  const text = String(raw || "");
  const token = /(\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)/g;
  let from = 0;
  let match = null;
  while ((match = token.exec(text)) !== null) {
    if (match.index > from) node.appendChild(document.createTextNode(text.slice(from, match.index)));
    if (match[2]) {
      const href = String(match[3] || "");
      const link = el("a", null, match[2]);
      if (/^(?:https?:\/\/|#)/i.test(href)) {
        link.href = href;
        if (href.charAt(0) !== "#") {
          link.target = "_blank";
          link.rel = "noreferrer";
        }
      }
      node.appendChild(link);
    } else if (match[4]) {
      node.appendChild(el("strong", null, match[4]));
    } else if (match[5]) {
      node.appendChild(el("code", null, match[5]));
    } else {
      node.appendChild(el("em", null, match[6]));
    }
    from = token.lastIndex;
  }
  if (from < text.length) node.appendChild(document.createTextNode(text.slice(from)));
}
