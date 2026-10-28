"""Static check of app/*.py for constructs that break on MicroPython.

    python3 -I sim/mpy_check.py [app_dir]

Exits 1 if anything is found.
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

BANNED_MODULES = {
    "dataclasses", "typing", "enum", "functools", "itertools", "abc", "collections",
    "collections.abc", "typing_extensions", "__future__", "threading", "pathlib",
}
ALLOWED_MODULES = {"math", "random", "array", "struct", "os", "sys", "gc", "micropython",
                   "badgeware", "pairing", "json", "hashlib", "binascii", "vfs", "rp2",
                   "builtins", "time"}
LOCAL_PREFIX = "am_"
# CPython os functions MicroPython's os module doesn't have
OS_MISSING = {"path", "walk", "makedirs", "removedirs", "scandir", "replace", "getenv",
              "environ", "fsync", "chmod", "link", "symlink", "readlink", "lstat",
              "truncate", "fspath", "access", "getpid", "cpu_count"}
# str/bytes methods missing on MicroPython (flagged on any attribute call; rename
# your own methods if one of these collides)
STR_MISSING = {"ljust", "rjust", "zfill", "title", "capitalize", "casefold", "swapcase",
               "removeprefix", "removesuffix", "expandtabs", "format_map",
               "isidentifier", "isprintable", "isnumeric", "isdecimal", "hexdigest"}
# firmware names importable from badgeware (everything else there is a global)
BADGEWARE_NAMES = {"State", "link"}
COLLIDING = {"ui", "game", "world", "render", "app", "icons", "mona", "items", "monsters",
             "content", "save", "build"}


class Checker(ast.NodeVisitor):
    def __init__(self, path, src, local_modules):
        self.path = path
        self.src = src
        self.lines = src.splitlines()
        self.local = local_modules
        self.findings = []

    def add(self, node, msg):
        self.findings.append((self.path, getattr(node, "lineno", 0), msg))

    def _module(self, node, name):
        root = name.split(".")[0]
        if root in COLLIDING:
            self.add(node, "import of '%s' collides with other badge apps' modules" % name)
        elif name in BANNED_MODULES or root in BANNED_MODULES:
            self.add(node, "import of '%s' is not available / not allowed on the badge" % name)
        elif (root not in ALLOWED_MODULES and root not in self.local
              and not root.startswith(LOCAL_PREFIX)):
            self.add(node, "import of '%s' is outside the contract allowlist (%s, %s*)"
                     % (name, ", ".join(sorted(ALLOWED_MODULES)), LOCAL_PREFIX))
        elif root in self.local and not root.startswith(LOCAL_PREFIX):
            self.add(node, "local module '%s' should be named %s*" % (root, LOCAL_PREFIX))

    def visit_Import(self, node):
        for a in node.names:
            self._module(node, a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level:
            self.add(node, "relative import (from . import ...) - use plain module names")
        elif node.module:
            self._module(node, node.module)
            if node.module == "micropython":
                for a in node.names:
                    if a.name != "const":
                        self.add(node, "micropython.%s: only const is allowed" % a.name)
            if node.module == "badgeware":
                for a in node.names:
                    if a.name not in BADGEWARE_NAMES:
                        self.add(node, "from badgeware import %s: only %s are allowed "
                                 "(other names are firmware globals)"
                                 % (a.name, ", ".join(sorted(BADGEWARE_NAMES))))
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        self.add(node, "walrus operator ':='")
        self.generic_visit(node)

    def visit_Match(self, node):
        self.add(node, "match statement")
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        for stmt in node.body:
            targets = []
            if isinstance(stmt, ast.Assign):
                targets = stmt.targets
            elif isinstance(stmt, ast.AnnAssign):
                targets = [stmt.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "__slots__":
                    self.add(stmt, "__slots__ in class %s" % node.name)
        for d in node.decorator_list:
            self._decorator(d)
        self.generic_visit(node)

    def _decorator(self, d):
        name = d.func if isinstance(d, ast.Call) else d
        text = ast.unparse(name)
        if text in ("dataclass", "dataclasses.dataclass", "lru_cache", "functools.lru_cache",
                    "cache", "functools.cache", "functools.wraps", "wraps",
                    "abstractmethod", "abc.abstractmethod", "cached_property",
                    "functools.cached_property"):
            self.add(d, "decorator @%s is not available on MicroPython" % text)

    def _func(self, node):
        a = node.args
        if a.kwonlyargs:
            self.add(node, "keyword-only arguments in %s()" % node.name)
        if a.posonlyargs:
            self.add(node, "positional-only arguments ('/') in %s()" % node.name)
        for d in node.decorator_list:
            self._decorator(d)
        self.generic_visit(node)

    visit_FunctionDef = _func

    def visit_AsyncFunctionDef(self, node):
        self.add(node, "async def")
        self._func(node)

    def visit_JoinedStr(self, node):
        # f"{x=}" : after the expression, the next non-space char inside the braces is '='
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                e = v.value
                if getattr(e, "end_lineno", None) is None:
                    continue
                line = self.lines[e.end_lineno - 1] if e.end_lineno <= len(self.lines) else ""
                rest = line.encode("utf-8")[e.end_col_offset:].decode("utf-8", "replace").lstrip()
                if rest.startswith("=") and not rest.startswith("=="):
                    self.add(node, "f-string '=' debug specifier")
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name) and f.id in ("breakpoint", "exec", "eval", "compile"):
            self.add(node, "%s() is unavailable or a bad idea on the badge" % f.id)
        if isinstance(f, ast.Attribute):
            if f.attr in STR_MISSING:
                what = ("hashlib digests have no hexdigest(); use binascii.hexlify(h.digest())"
                        if f.attr == "hexdigest" else
                        "%s() is not available on MicroPython str/bytes" % f.attr)
                self.add(node, what)
            if (f.attr == "dumps" and isinstance(f.value, ast.Name) and f.value.id == "json"):
                for kw in node.keywords:
                    if kw.arg in ("indent", "sort_keys", "default", "ensure_ascii"):
                        self.add(node, "json.dumps(%s=...) is not supported on MicroPython"
                                 % kw.arg)
        if isinstance(f, ast.Name) and f.id == "print":
            for kw in node.keywords:
                if kw.arg == "flush":
                    self.add(node, "print(flush=...) is not supported on MicroPython")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        v = node.value
        if isinstance(v, ast.Name) and v.id == "os" and node.attr in OS_MISSING:
            self.add(node, "os.%s does not exist on MicroPython" % node.attr)
        self.generic_visit(node)


def check(app_dir):
    files = sorted(f for f in os.listdir(app_dir) if f.endswith(".py"))
    local = {f[:-3] for f in files if f != "__init__.py"}
    findings = []
    for f in files:
        path = os.path.join(app_dir, f)
        mod = f[:-3]
        if mod in COLLIDING:
            findings.append((path, 0, "module file name '%s' collides with other badge apps" % mod))
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            findings.append((path, e.lineno or 0, "syntax error: %s" % e.msg))
            continue
        c = Checker(path, src, local)
        c.visit(tree)
        findings.extend(c.findings)
    return files, findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    app_dir = os.path.abspath(argv[0] if argv else os.path.join(os.path.dirname(HERE), "app"))
    files, findings = check(app_dir)
    for path, line, msg in findings:
        rel = os.path.relpath(path)
        print("%s:%d: %s" % (path if rel.startswith("..") else rel, line, msg))
    print("mpy_check: %d file(s), %d finding(s)" % (len(files), len(findings)))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
