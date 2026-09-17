# 배포된 셰이프 서버 스모크 테스트 — 애드온과 같은 계약(urllib, 프록시 인증 헤더, 202 폴링)으로 GLB 를 받는다
#
# 사용: python scripts/trellis3d/smoke_test.py --url https://…modal.run --token-file ~/.config/lp3d/shapegen_token.txt \
#         --views front.png back.png left.png right.png --out shape.glb [--octree 1024] [--steps 12]
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VIEW_KEYS = ("front", "back", "left", "right")


def headers(token: str) -> dict:
    token = token.strip()
    if not token:
        return {}
    if ":" in token:
        key, secret = token.split(":", 1)
        return {"Modal-Key": key.strip(), "Modal-Secret": secret.strip()}
    return {"Authorization": "Bearer " + token}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-file", default="")
    ap.add_argument("--views", nargs="+", required=True, help="front back left right 순서의 PNG 경로")
    ap.add_argument("--out", default="shape.glb")
    ap.add_argument("--octree", type=int, default=1024, help="1024 이상이면 캐스케이드, 그 외 512 단일 패스")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--guidance", type=float, default=7.5)
    ap.add_argument("--face-count", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--timeout", type=int, default=1500)
    a = ap.parse_args()

    token = open(a.token_file).read() if a.token_file else ""
    h = headers(token)
    base = a.url.rstrip("/")

    t0 = time.monotonic()
    req = urllib.request.Request(base + "/status", headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        status = json.loads(r.read())
    print(f"/status {status}  ({time.monotonic()-t0:.1f}s)", flush=True)

    body = {"octree_resolution": a.octree, "num_inference_steps": a.steps, "guidance_scale": a.guidance,
            "face_count": a.face_count, "seed": a.seed, "texture": False, "type": "glb"}
    for key, path in zip(VIEW_KEYS, a.views):
        body[key] = base64.b64encode(open(path, "rb").read()).decode("ascii")
    print(f"뷰 {len(a.views)}장 전송, octree={a.octree} steps={a.steps}", flush=True)

    t1 = time.monotonic()
    req = urllib.request.Request(base + "/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **h}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
        if r.status == 202:
            job = json.loads(data)
            print(f"202 job_id={job['job_id']} — 폴링 시작", flush=True)
            url = base + "/result/" + urllib.parse.quote(job["job_id"])
            deadline = time.monotonic() + a.timeout
            while True:
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as rr:
                        data = rr.read()
                        if rr.status == 200:
                            break
                except urllib.error.HTTPError as e:
                    print("서버 오류", e.code, e.read().decode("utf-8", "replace")[:500]); sys.exit(1)
                if time.monotonic() > deadline:
                    print("대기 시간 초과"); sys.exit(1)
                print(f"  … {time.monotonic()-t1:5.0f}s", flush=True)
                time.sleep(5)
    elapsed = time.monotonic() - t1
    if data[:4] != b"glTF":
        print("GLB 가 아닌 응답:", data[:200]); sys.exit(1)
    open(a.out, "wb").write(data)
    print(f"완료: {a.out} ({len(data)/2**20:.1f} MB) 생성 {elapsed:.0f}s", flush=True)


if __name__ == "__main__":
    main()
