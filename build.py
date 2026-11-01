#!/usr/bin/env python3
"""Build the site from languages.json and hello/src/.

    python3 build.py

Writes one page per language to hello/<id>.html, and rewrites the generated
regions of index.html (the graph's language data and the list of Hello World
pages). Run verify.py first if you want the pages to show fresh run results.
"""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
HELLO = ROOT / "hello"

CATEGORY = {
    "systems": "Systems",
    "oop": "Object-Oriented",
    "functional": "Functional",
    "scripting": "Scripting",
    "other": "Other",
}

PRISM = "https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0"
FONTS = ("https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;0,700;"
         "1,300;1,400&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400"
         "&family=JetBrains+Mono:wght@400;500&display=swap")

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hello, World! in {name}</title>
  <meta name="description" content="Hello, World! in {name} ({year}), with its place in the family tree of programming languages.">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="{fonts}" rel="stylesheet">
  <link rel="stylesheet" href="../styles.css">
  <link rel="stylesheet" href="hello.css">
  <script>
    const saved = localStorage.getItem('dp-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
    else if (window.matchMedia('(prefers-color-scheme: dark)').matches)
      document.documentElement.setAttribute('data-theme', 'dark');
  </script>
</head>
<body>
  <nav class="site-nav">
    <div class="nav-inner">
      <a href="../index.html" class="nav-brand">Evolution of Programming</a>
      <div class="nav-right">
        <a href="../index.html#{id}" class="nav-link">Family tree</a>
        <button class="theme-toggle" id="themeToggle" aria-label="Toggle dark mode"></button>
      </div>
    </div>
  </nav>

  <header class="title-page hw-title">
    <p class="edition">Hello, World!</p>
    <h1>{name}</h1>
    <p class="subtitle">{creator}</p>
    <span class="language-badge hw-cat-{cat}">{year} &middot; {category}</span>
  </header>

  <main class="container">
    <p class="hw-note">{note}</p>

    <pre><code class="language-{prism}">{code}</code></pre>
    <p class="hw-run">{run}</p>
{aside}
    <p class="hw-edition">This is the core version: Hello, World! as you would write it in {name}
      today. A historical version, as it was first written, may follow.</p>

    <section class="hw-lineage" aria-label="Lineage">
      <div>
        <h2>Descended from</h2>
        {parents}
      </div>
      <div>
        <h2>Influenced</h2>
        {children}
      </div>
    </section>

    <nav class="hw-pager" aria-label="Chronological">
      {prev}
      {next}
    </nav>

    <footer>
      <p><a href="../index.html#{id}">See {name} in the family tree</a></p>
    </footer>
  </main>

  <script src="{prism_cdn}/prism.min.js"></script>
  <script src="{prism_cdn}/plugins/autoloader/prism-autoloader.min.js"></script>
  <script>
  document.getElementById('themeToggle').addEventListener('click', function() {{
    var html = document.documentElement;
    var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('dp-theme', next);
  }});
  </script>
</body>
</html>
"""


def esc(s):
    return html.escape(s, quote=True)


def chips(langs, empty):
    if not langs:
        return f'<p class="hw-none">{empty}</p>'
    links = "".join(
        f'<a class="hw-chip hw-cat-{l["cat"]}" href="{l["id"]}.html">{esc(l["name"])} <span>{l["year"]}</span></a>'
        for l in langs)
    return f'<div class="hw-chips">{links}</div>'


def run_line(v):
    if v and v.get("ok") is True:
        return (f'Runs as shown: checked {esc(v["checked"])} with <code>{esc(v["command"])}</code> '
                f'in <code>{esc(v["image"])}</code>.')
    if v and v.get("ok") is None and v.get("reason"):
        return f'Not machine-checked: {esc(v["reason"])}'
    return "Not yet machine-checked."


def pager(l, rel, label):
    if not l:
        return '<span></span>'
    return (f'<a class="hw-{rel}" rel="{rel}" href="{l["id"]}.html">'
            f'<small>{label}</small>{esc(l["name"])} <span>{l["year"]}</span></a>')


def replace_region(text, name, body):
    pattern = re.compile(rf"(/\* {name}:BEGIN[^*]*\*/|<!-- {name}:BEGIN[^>]*-->)(.*?)(/\* {name}:END \*/|<!-- {name}:END -->)", re.S)
    new, n = pattern.subn(lambda m: m.group(1) + body + m.group(3), text)
    if n != 1:
        raise SystemExit(f"index.html: expected one {name} region, found {n}")
    return new


def graph_data(langs):
    def js(s):
        return json.dumps(s, ensure_ascii=False)[1:-1].replace("'", "\\'")
    rows = []
    for l in langs:
        by = ",".join(f"'{p}'" for p in l["by"])
        rows.append(f"      {{ id:'{l['id']}', name:'{js(l['name'])}', year:{l['year']}, x:{l['x']}, "
                    f"cat:'{l['cat']}', by:[{by}], creator:'{js(l['creator'])}', note:'{js(l['note'])}' }},")
    return "\n" + "\n".join(rows) + "\n    "


def page_list(langs):
    items = "\n".join(
        f'        <li><a class="hw-cat-{l["cat"]}" href="hello/{l["id"]}.html">{esc(l["name"])} <span>{l["year"]}</span></a></li>'
        for l in langs)
    return f'\n      <ul class="hello-list">\n{items}\n      </ul>\n      '


def main():
    langs = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))
    langs.sort(key=lambda l: (l["year"], l["x"]))
    by_id = {l["id"]: l for l in langs}
    verified_path = HELLO / "verified.json"
    verified = json.loads(verified_path.read_text(encoding="utf-8")) if verified_path.exists() else {}

    for i, l in enumerate(langs):
        parents = [by_id[p] for p in l["by"]]
        children = [c for c in langs if l["id"] in c["by"]]
        code = (HELLO / "src" / l["file"]).read_text(encoding="utf-8").rstrip("\n")
        aside = f'    <p class="hw-aside">{l["aside"]}</p>\n' if l.get("aside") else ""
        page = PAGE.format(
            id=l["id"], name=esc(l["name"]), year=l["year"], creator=esc(l["creator"]),
            note=esc(l["note"]), cat=l["cat"], category=CATEGORY[l["cat"]], prism=l["prism"],
            code=esc(code), run=run_line(verified.get(l["id"])), aside=aside,
            parents=chips(parents, "Nothing in this tree: it starts a line of its own."),
            children=chips(children, "Nothing in this tree, yet."),
            prev=pager(langs[i - 1] if i else None, "prev", "Earlier"),
            next=pager(langs[i + 1] if i + 1 < len(langs) else None, "next", "Later"),
            fonts=FONTS, prism_cdn=PRISM,
        )
        (HELLO / f"{l['id']}.html").write_text(page, encoding="utf-8")

    index = ROOT / "index.html"
    text = index.read_text(encoding="utf-8")
    text = replace_region(text, "LANGS", graph_data(langs))
    text = replace_region(text, "HELLO-LIST", page_list(langs))
    index.write_text(text, encoding="utf-8")
    print(f"built {len(langs)} pages; {sum(1 for v in verified.values() if v.get('ok') is True)} verified")


if __name__ == "__main__":
    main()
