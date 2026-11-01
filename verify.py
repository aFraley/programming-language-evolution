#!/usr/bin/env python3
"""Verify every Hello World in hello/src by actually running it in Docker.

    python3 verify.py              # all languages in languages.json
    python3 verify.py rust go      # just these ids

Each language runs in a pinned image. hello/src is mounted read-only at /src
and copied into /tmp/w inside the container (renamed when the toolchain cares
about the filename). A run passes when it exits 0 and its combined
stdout+stderr contains "Hello, World!" as a line of its own.

Results are merged into hello/verified.json; ids not re-run are kept as-is.
Plain Python 3 stdlib; needs only a working `docker` CLI.
"""

import concurrent.futures
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "hello" / "src"
OUT = ROOT / "hello" / "verified.json"
GREETING = "Hello, World!"
TIMEOUT = 300  # seconds per language
WORKERS = 4

# Images that have to be assembled from a base image plus packages or a
# toolchain download. Key -> (label shown in verified.json, Dockerfile).
CUSTOM_IMAGES = {
    "debian-tools": (
        "debian:bookworm + apt",
        """FROM debian:bookworm
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
      gnucobol bwbasic fp-compiler sbcl guile-3.0 gnat sqlite3 lua5.4 polyml \\
      gobjc libgnustep-base-dev gnustep-make make clang ldc gcc libc6-dev \\
 && rm -rf /var/lib/apt/lists/*
""",
    ),
    "smalltalk": (
        "debian:bullseye + apt gnu-smalltalk",  # dropped from bookworm
        # The bullseye-security arm64 pool currently 404s on a dependency, so use main only.
        """FROM debian:bullseye
RUN sed -i '/security/d' /etc/apt/sources.list \\
 && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gnu-smalltalk \\
 && rm -rf /var/lib/apt/lists/*
""",
    ),
    "marst": (
        "debian:bookworm + GNU MARST 2.7 (built from source)",
        """FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates \\
 && curl -fsSL https://ftp.gnu.org/gnu/marst/marst-2.7.tar.gz | tar xz -C /opt \\
 && cd /opt/marst-2.7 && ./configure && make && make install && ldconfig \\
 && rm -rf /var/lib/apt/lists/*
""",
    ),
    "cim": (
        "debian:bookworm + GNU Cim 5.1 (built from source)",
        # Cim 5.1 predates aarch64 and GCC 10+: refresh config.guess, allow
        # common symbols / K&R C, and symlink so its '../../lib/cim.h' include resolves.
        """FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates autotools-dev \\
 && mkdir -p /opt/b && curl -fsSL https://ftp.gnu.org/gnu/cim/cim-5.1.tar.gz | tar xz -C /opt/b \\
 && ln -s /opt/b/cim-5.1/lib /opt/lib && cd /opt/b/cim-5.1 \\
 && cp /usr/share/misc/config.guess /usr/share/misc/config.sub . \\
 && CFLAGS="-O2 -fcommon -std=gnu89 -Wno-implicit-function-declaration -Wno-int-conversion" ./configure \\
 && make && make install && ldconfig \\
 && rm -rf /var/lib/apt/lists/*
""",
    ),
    "ocaml": (
        "ocaml/opam:debian-12-ocaml-5.2",
        # the image only puts the compiler on PATH via `opam exec`; do it statically
        """FROM ocaml/opam:debian-12-ocaml-5.2
ENV PATH=/home/opam/.opam/5.2/bin:$PATH
""",
    ),
    "kotlin": (
        "eclipse-temurin:21 + kotlinc 2.1.0",
        """FROM eclipse-temurin:21
RUN apt-get update && apt-get install -y --no-install-recommends unzip curl \\
 && curl -fsSL -o /tmp/k.zip https://github.com/JetBrains/kotlin/releases/download/v2.1.0/kotlin-compiler-2.1.0.zip \\
 && unzip -q /tmp/k.zip -d /opt && rm /tmp/k.zip && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/kotlinc/bin:$PATH
""",
    ),
    "zig": (
        "debian:bookworm + zig 0.13.0 (ziglang.org tarball)",
        """FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xz-utils \\
 && curl -fsSL https://ziglang.org/download/0.13.0/zig-linux-$(uname -m)-0.13.0.tar.xz | tar xJ -C /opt \\
 && ln -s /opt/zig-linux-$(uname -m)-0.13.0/zig /usr/local/bin/zig && rm -rf /var/lib/apt/lists/*
""",
    ),
}

DOTNET = "mcr.microsoft.com/dotnet/sdk:10.0"
# GCC cannot compile @autoreleasepool, so use clang against Debian's GCC-runtime GNUstep
# (on macOS this is just: clang -framework Foundation objc.m -o objc).
GNUSTEP = "-fobjc-runtime=gcc -I$(gcc -print-file-name=include) $(gnustep-config --objc-flags) $(gnustep-config --base-libs)"

# id -> (image or CUSTOM_IMAGES key, command run in the work dir, {src file: name to copy it as})
# The command is exactly what a reader would type locally with the toolchain installed.
LANGS = {
    "fortran":    ("gcc:14", "gfortran fortran.f90 -o fortran && ./fortran"),
    "lisp":       ("debian-tools", "sbcl --script lisp.lisp"),
    "cobol":      ("debian-tools", "cobc -x cobol.cob && ./cobol"),
    "algol":      ("marst", "marst algol.a60 -o algol.c && gcc algol.c -lalgol -lm -o algol && ./algol"),
    "basic":      ("debian-tools", "bwbasic basic.bas"),
    "simula":     ("cim", "cim simula.sim && ./simula"),
    "pascal":     ("debian-tools", "fpc pascal.pas && ./pascal"),
    "c":          ("gcc:14", "gcc c.c -o c && ./c"),
    "smalltalk":  ("smalltalk", "gst smalltalk.st"),
    "prolog":     ("swipl:9.2.9", "swipl -g halt prolog.pro"),
    "ml":         ("debian-tools", "poly --script ml.sml"),
    "sql":        ("debian-tools", "sqlite3 :memory: < sql.sql"),
    "scheme":     ("debian-tools", "guile scheme.scm"),
    "ada":        ("debian-tools", "gnatmake hello.adb && ./hello", {"ada.adb": "hello.adb"}),
    "objc":       ("debian-tools", f"clang objc.m {GNUSTEP} -o objc && ./objc"),
    "cpp":        ("gcc:14", "g++ cpp.cpp -o cpp && ./cpp"),
    "erlang":     ("erlang:27", "erlc hello.erl && erl -noshell -s hello world -s init stop",
                   {"erlang.erl": "hello.erl"}),
    "perl":       ("perl:5.40", "perl perl.pl"),
    "haskell":    ("haskell:9.10", "ghc haskell.hs -o haskell && ./haskell"),
    "python":     ("python:3.13-slim", "python3 python.py"),
    "vb":         (DOTNET, "dotnet new console -lang VB -o app && cp vb.vb app/Program.vb && dotnet run --project app"),
    "lua":        ("debian-tools", "lua5.4 lua.lua"),
    "r":          ("r-base:4.4.2", "Rscript r.R"),
    "java":       ("eclipse-temurin:21", "javac Hello.java && java Hello", {"java.java": "Hello.java"}),
    "ruby":       ("ruby:3.3-slim", "ruby ruby.rb"),
    "javascript": ("node:22", "node javascript.js"),
    "php":        ("php:8.3-cli", "php php.php"),
    "ocaml":      ("ocaml", "ocaml ocaml.ml"),
    "csharp":     (DOTNET, "dotnet run csharp.cs"),
    "d":          ("debian-tools", "ldc2 d.d && ./d"),
    "groovy":     ("groovy:4.0-jdk21", "groovy groovy.groovy"),
    "scala":      ("virtuslab/scala-cli:1.5.4", "scala-cli run --server=false scala.scala"),
    "fsharp":     (DOTNET, "dotnet fsi fsharp.fsx"),
    "clojure":    ("clojure:temurin-21-tools-deps", "clojure -M clojure.clj"),
    "go":         ("golang:1.23", "go run go.go"),
    "rust":       ("rust:1.83", "rustc rust.rs && ./rust"),
    "kotlin":     ("kotlin", "kotlinc kotlin.kt -include-runtime -d kotlin.jar && java -jar kotlin.jar"),
    "dart":       ("dart:3.5", "dart run dart.dart"),
    "elixir":     ("elixir:1.17", "elixir elixir.exs"),
    "typescript": ("node:22", "npx -y tsx typescript.ts"),
    "julia":      ("julia:1.11", "julia julia.jl"),
    "swift":      ("swift:6.0", "swiftc swift.swift -o swift && ./swift"),
    "zig":        ("zig", "zig run zig.zig"),
}

# Languages that cannot be run at all: id -> one-sentence reason.
UNRUNNABLE = {}

ENV = ["DOTNET_NOLOGO=1", "DOTNET_CLI_TELEMETRY_OPTOUT=1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1"]

# NSLog prefixes each line with "2026-10-31 12:00:00.123 objc[42:42] ".
NSLOG_PREFIX = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ \S+\[[\d:]+\] ")


def docker_tag(image):
    return f"hello-verify-{image}:local" if image in CUSTOM_IMAGES else image


def image_label(image):
    return CUSTOM_IMAGES[image][0] if image in CUSTOM_IMAGES else image


def prepare_image(image):
    """Build a custom image or pull a stock one, so pull noise never lands in a run's output."""
    tag = docker_tag(image)
    if image in CUSTOM_IMAGES:
        print(f"  building {tag}", flush=True)
        subprocess.run(["docker", "build", "-q", "-t", tag, "-"], input=CUSTOM_IMAGES[image][1],
                       text=True, check=True, stdout=subprocess.DEVNULL)
    elif subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0:
        print(f"  pulling {tag}", flush=True)
        subprocess.run(["docker", "pull", "-q", tag], check=True, stdout=subprocess.DEVNULL)


def greeted(output):
    lines = (NSLOG_PREFIX.sub("", line.strip()) for line in output.splitlines())
    return GREETING in lines


def run(lang_id):
    image, command, *rest = LANGS[lang_id]
    renames = rest[0] if rest else {}
    copies = "".join(f" && cp /src/{src} {dst}" for src, dst in renames.items())
    script = f"set -e; mkdir -p /tmp/w && cp /src/* /tmp/w && cd /tmp/w{copies}; {command}"
    argv = ["docker", "run", "--rm", "--entrypoint", "", "-v", f"{SRC}:/src:ro"]
    for e in ENV:
        argv += ["-e", e]
    argv += [docker_tag(image), "sh", "-c", script]
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        output, code = p.stdout.strip(), p.returncode
    except subprocess.TimeoutExpired:
        output, code = f"timed out after {TIMEOUT}s", None
    return {
        "ok": code == 0 and greeted(output),
        "image": image_label(image),
        "command": command,
        "output": output[:200],
        "checked": datetime.date.today().isoformat(),
    }


def main(argv):
    ids = [entry["id"] for entry in json.loads((ROOT / "languages.json").read_text())]
    wanted = argv or ids
    unknown = [i for i in wanted if i not in LANGS and i not in UNRUNNABLE]
    if unknown:
        sys.exit(f"no recipe for: {', '.join(unknown)}")

    results = json.loads(OUT.read_text()) if OUT.exists() else {}
    for i in wanted:
        if i in UNRUNNABLE:
            results[i] = {"ok": None, "reason": UNRUNNABLE[i]}
    runnable = [i for i in wanted if i in LANGS]

    print("preparing images", flush=True)
    for image in sorted({LANGS[i][0] for i in runnable}):
        try:
            prepare_image(image)
        except subprocess.CalledProcessError:
            print(f"  could not prepare {image}; its languages will fail", flush=True)

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for lang_id, result in zip(runnable, pool.map(run, runnable)):
            results[lang_id] = result
            mark = "ok  " if result["ok"] else "FAIL"
            print(f"{mark} {lang_id:11} {result['output'][:70]!r}", flush=True)

    ordered = {i: results[i] for i in ids if i in results}
    ordered.update({k: v for k, v in results.items() if k not in ordered})
    OUT.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n")
    failed = [i for i in wanted if results[i]["ok"] is False]
    print(f"\n{len(wanted) - len(failed)}/{len(wanted)} passed or skipped; failed: {failed or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
