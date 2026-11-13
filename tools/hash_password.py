"""生成 SHA256 密码哈希。用法：python tools/hash_password.py <明文密码>"""
import hashlib
import sys

if len(sys.argv) != 2:
    print("用法: python tools/hash_password.py <明文密码>", file=sys.stderr)
    sys.exit(1)
print(hashlib.sha256(sys.argv[1].encode("utf-8")).hexdigest())
