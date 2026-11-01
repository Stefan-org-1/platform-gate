import json
import os
import urllib.request

API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str = None):
        self.token = token or os.environ["GITHUB_TOKEN"]

    def request(self, method: str, path: str, body: dict = None):
        req = urllib.request.Request(
            path if path.startswith("http") else API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), resp.headers.get("Link", "")

    def get(self, path: str):
        return self.request("GET", path)[0]

    def paginate(self, path: str, key: str = None):
        url = path + ("&" if "?" in path else "?") + "per_page=100"
        while url:
            data, link = self.request("GET", url)
            yield from (data[key] if key else data)
            url = next((p.split(";")[0].strip(" <>") for p in link.split(",") if 'rel="next"' in p), None)

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)[0]
