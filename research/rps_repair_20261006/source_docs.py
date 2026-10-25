"""Bounded reads of documentation endpoints linked by the official public site."""
import requests

from common import OUT, guard, now, save, sha


def main():
    guard()
    folder = OUT / "baostock_docs"
    folder.mkdir(exist_ok=False)
    for name in ["home.md", "stockKData.md", "stockBasic.md", "default.md"]:
        url = f"https://www.baostock.com/helpdocs/api/markdown/{name}"
        response = requests.post(url, json={}, timeout=20)
        response.raise_for_status()
        path = folder / name
        path.write_bytes(response.content)
        save(f"baostock_docs/{name}.receipt.json", dict(captured_at_utc=now(), url=url,
             method="POST", purpose="public documentation read, as used by the official site's viewer",
             request_body={}, status=response.status_code, sha256=sha(path)))
        text = response.content.decode("utf-8")
        print(f"=== {name} ===", flush=True)
        if name in ["home.md", "default.md"]:
            print(text, flush=True)
        else:
            for line in text.splitlines():
                if any(word in line for word in ["adjustflag", "isST", "tradestatus", "volume", "amount", "status", "outDate", "退市", "免费", "访问", "接口"]):
                    print(line, flush=True)


if __name__ == "__main__":
    main()
