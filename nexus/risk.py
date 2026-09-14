"""Pick a landing mode from the diff. Rules are registry data; first match wins."""

from __future__ import annotations

from fnmatch import fnmatch

SENSITIVE = ("package.json", "package-lock.json", "*.lock", "requirements*.txt", "pyproject.toml",
             ".github/*", "vercel.json", "wrangler.*", "Dockerfile")


def _hits(paths, globs):
    return any(fnmatch(p, g) or fnmatch(p.rsplit("/", 1)[-1], g) for p in paths for g in globs)


def classify(rules, labels, paths, lines, first_road=False):
    """direct | review | human. Unknown is not small."""
    rules, labels = rules or {}, {str(l).lower() for l in labels}
    if first_road or _hits(paths, rules.get("human_globs", ())) or labels & set(rules.get("human_labels", ())):
        return "human"
    if _hits(paths, rules.get("review_globs", ())) or _hits(paths, SENSITIVE) or lines > 300:
        return "review"
    direct = rules.get("direct_globs", ())
    if paths and all(_hits([p], direct) for p in paths):
        return "direct"
    if labels & {"hotfix", "small"} and lines <= 30:
        return "direct"
    return "review"
