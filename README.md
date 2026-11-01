# Evolution of Programming Languages

An interactive family tree of programming languages, from Fortran (1957) to Zig
(2016). Forty-three languages are placed by year and colour-coded by paradigm:
systems, object-oriented, functional, scripting and other. Lines show which
languages influenced which.

Hover over any language to light up its full ancestry and everything descended
from it, with its year, creator and what it contributed. Click it to open its
**Hello, World!** page.

## Hello, World!

Every language in the tree has its own page under `hello/`: the program, where
the language came from, what it went on to influence, and links to its neighbours
in time. Each page links back to the tree with that language's lineage lit up.

These are the core versions: Hello, World! as you would write it in each language
today. Historical versions, as each language was first written, may follow.

Where a page says a program runs as shown, `verify.py` executed it in the Docker
image named on the page.

## Layout

| Path | |
|---|---|
| `languages.json` | The tree: each language's year, parents, creator, note and source file |
| `hello/src/` | The Hello World programs, as real source files |
| `build.py` | Generates `hello/*.html` and the language data inside `index.html` |
| `verify.py` | Runs each program in Docker and records the results in `hello/verified.json` |

To change the tree or a program, edit `languages.json` or `hello/src/`, then:

```bash
python3 verify.py rust go
```

```bash
python3 build.py
```

`verify.py` with no arguments re-checks every program.

## Running it

Static files only; nothing needs building to view it. Open `index.html` in a
browser, or serve the folder:

```bash
python3 -m http.server
```

Fonts load from Google Fonts and syntax highlighting from Prism on cdnjs. Light
and dark themes follow your system setting, with a toggle in the corner.

## License

MIT. See [LICENSE](LICENSE).
