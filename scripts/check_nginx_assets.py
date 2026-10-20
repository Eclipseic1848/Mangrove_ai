"""只读检查哈希资源压缩及缓存，不缓存接口、入口页或错误响应。"""
import gzip
import hashlib
import json
import re
import sys
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def main() -> None:
    origin = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1/"

    def read(path: str, encoding: str = "identity"):
        request = Request(urljoin(origin, path), headers={"Accept-Encoding": encoding})
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    status, headers, index = read("/")
    assert status == 200 and "31536000" not in headers.get("Cache-Control", "")
    entry = re.search(r'src="(/assets/[^"/]+-[A-Za-z0-9_-]{8}\.js)"', index.decode("utf-8"))
    assert entry is not None, "入口页未找到带摘要的 JavaScript"
    resource = entry.group(1)
    plain_status, _, plain = read(resource)
    status, headers, compressed = read(resource, "gzip")
    assert status == plain_status == 200
    assert headers.get("Content-Encoding") == "gzip", "JavaScript 未压缩"
    assert "max-age=31536000" in headers.get("Cache-Control", ""), "哈希资源未缓存"
    assert "Accept-Encoding" in headers.get("Vary", "")
    assert gzip.decompress(compressed) == plain and len(compressed) < len(plain)
    status, headers, _ = read("/api/health", "gzip")
    assert status == 200 and "31536000" not in headers.get("Cache-Control", "")
    status, headers, _ = read("/assets/mangrove-missing-1234abcd.js", "gzip")
    assert status == 404 and "31536000" not in headers.get("Cache-Control", "")
    print(json.dumps({"resource": resource, "identity_bytes": len(plain),
                      "gzip_bytes": len(compressed), "saved_percent": round(100 * (1 - len(compressed) / len(plain)), 1),
                      "decoded_sha256": hashlib.sha256(plain).hexdigest(),
                      "index_api_and_missing_resource_not_cached": True}))


if __name__ == "__main__":
    main()
