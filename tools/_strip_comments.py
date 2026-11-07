import io, os, re, sys, tokenize, ast

ROOT = r"D:\project\PCL2-Qie\app\i18ntool"

CS = {".cs"}
JS = {".js"}
CSS = {".css"}
HTML = {".html"}
PY = {".py"}
SH = {".sh"}
PS = {".ps1"}
YML = {".yml", ".yaml"}
XML = {".csproj", ".svg"}

def newlines(n):
    return "\n" * n

def scan_cs_string(s, i):
    n = len(s)
    verbatim = False
    interp = False
    while i < n and s[i] in "@$":
        if s[i] == "@":
            verbatim = True
        else:
            interp = True
        i += 1
    if i >= n or s[i] != '"':
        return i
    i += 1
    while i < n:
        c = s[i]
        if interp and c == "{":
            if i + 1 < n and s[i + 1] == "{":
                i += 2
                continue
            depth = 1
            i += 1
            while i < n and depth > 0:
                ch = s[i]
                if ch == '"' or (ch in "@$" and '"' in s[i:i+3]):
                    i = scan_cs_string(s, i)
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                i += 1
            continue
        if verbatim:
            if c == '"':
                if i + 1 < n and s[i + 1] == '"':
                    i += 2
                    continue
                return i + 1
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        i += 1
    return i

def strip_csharp(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            end = n if j < 0 else j + 2
            cnt = s.count("\n", i, end)
            out.append(newlines(cnt) if cnt else " ")
            i = end
            continue
        if c == '"' or (c in "@$" and '"' in s[i:i+3]):
            j = scan_cs_string(s, i)
            out.append(s[i:j])
            i = j
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == "'":
                    j += 1
                    break
                j += 1
            out.append(s[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)

REGEX_PREV = set("=(,:;[!&|?{}])~^<>+-*%")
REGEX_KW = {"return","typeof","case","in","of","new","delete","void","instanceof","do","else","yield","await"}

def is_regex_start(s, i):
    k = i - 1
    while k >= 0 and s[k] in " \t\r\n":
        k -= 1
    if k < 0:
        return True
    prev = s[k]
    if prev in REGEX_PREV:
        return True
    if prev.isalnum() or prev in "_$":
        j = k
        while j >= 0 and (s[j].isalnum() or s[j] in "_$"):
            j -= 1
        word = s[j+1:k+1]
        return word in REGEX_KW
    return False

def strip_js(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            end = n if j < 0 else j + 2
            cnt = s.count("\n", i, end)
            out.append(newlines(cnt) if cnt else " ")
            i = end
            continue
        if c in "'\"":
            q = c
            j = i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == q:
                    j += 1
                    break
                j += 1
            out.append(s[i:j])
            i = j
            continue
        if c == "`":
            j = i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == "`":
                    j += 1
                    break
                j += 1
            out.append(s[i:j])
            i = j
            continue
        if c == "/" and is_regex_start(s, i):
            j = i + 1
            in_class = False
            while j < n:
                ch = s[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    j += 1
                    while j < n and s[j].isalpha():
                        j += 1
                    break
                elif ch == "\n":
                    break
                j += 1
            out.append(s[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)

def strip_block_only(s, opener, closer):
    out = []
    i = 0
    n = len(s)
    while i < n:
        k = s.find(opener, i)
        if k < 0:
            out.append(s[i:])
            break
        out.append(s[i:k])
        j = s.find(closer, k + len(opener))
        end = n if j < 0 else j + len(closer)
        cnt = s.count("\n", k, end)
        out.append(newlines(cnt) if cnt else " ")
        i = end
    return "".join(out)

def strip_hash_comments(s, shell_like=False, ps_like=False):
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if ps_like and c == "<" and s.startswith("<#", i):
            j = s.find("#>", i + 2)
            end = n if j < 0 else j + 2
            cnt = s.count("\n", i, end)
            out.append(newlines(cnt) if cnt else " ")
            i = end
            continue
        if c in "'\"":
            q = c
            j = i + 1
            while j < n:
                if q == "'" and shell_like:
                    if s[j] == "'":
                        j += 1
                        break
                    j += 1
                    continue
                if q == "'" and ps_like:
                    if s[j] == "'":
                        if j + 1 < n and s[j+1] == "'":
                            j += 2
                            continue
                        j += 1
                        break
                    j += 1
                    continue
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == q:
                    j += 1
                    break
                j += 1
            out.append(s[i:j])
            i = j
            continue
        if c == "#":
            prevch = s[i-1] if i > 0 else "\n"
            if prevch in " \t\n;":
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
        out.append(c)
        i += 1
    return "".join(out)

def strip_python(src):
    lines = src.splitlines(keepends=True)
    comments = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comments.append((tok.start, tok.end))
    except Exception:
        comments = []
    arr = [list(l) for l in lines]
    for (sr, sc), (er, ec) in reversed(comments):
        if sr - 1 < len(arr) and sr == er:
            line = "".join(arr[sr-1])
            arr[sr-1] = list(line[:sc] + line[ec:])
    src2 = "".join("".join(l) for l in arr)
    try:
        tree = ast.parse(src2)
    except Exception:
        return src2
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                d = body[0]
                spans.append((d.lineno, d.col_offset, d.end_lineno, d.end_col_offset))
    if not spans:
        return src2
    lines2 = src2.splitlines(keepends=True)
    arr2 = [list(l) for l in lines2]
    for (sl, sc, el, ec) in sorted(spans, reverse=True):
        if sl != el:
            arr2[sl-1] = list("".join(arr2[sl-1])[:sc] + "pass\n")
            for r in range(sl, el):
                arr2[r] = list("\n")
        else:
            line = "".join(arr2[sl-1])
            indent = len(line) - len(line.lstrip())
            arr2[sl-1] = list(line[:indent] + "pass\n")
    return "".join("".join(l) for l in arr2)

def tidy(text):
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text.endswith("\n"):
        text += "\n"
    return text

def strip_file(path):
    ext = os.path.splitext(path)[1].lower()
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    src = raw.decode("utf-8-sig")
    if ext in CS:
        out = strip_csharp(src)
    elif ext in JS:
        out = strip_js(src)
    elif ext in CSS or ext in HTML or ext in XML:
        out = strip_block_only(src, "<!--", "-->") if ext in HTML or ext in XML else strip_block_only(src, "/*", "*/")
    elif ext in PS:
        out = strip_hash_comments(src, ps_like=True)
    elif ext in SH:
        out = strip_hash_comments(src, shell_like=True)
    elif ext in YML:
        out = strip_hash_comments(src)
    elif ext in PY:
        out = strip_python(src)
    else:
        return None
    out = tidy(out)
    data = out.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    open(path, "wb").write(data)
    return (len(src), len(out))

def main():
    results = []
    for f in sys.argv[1:]:
        try:
            r = strip_file(f)
            if r:
                results.append((f, r[0], r[1]))
        except Exception as e:
            print("ERR " + f + " " + str(e), file=sys.stderr)
    for name, a, b in results:
        print("%7d -> %7d  %s" % (a, b, name.replace(ROOT + "\\", "")))
    print("files:", len(results))

main()