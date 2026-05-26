"""
festival_collector.py
- 전국문화축제표준데이터 API → Supabase 저장
- GitHub Actions에서 매일 자동 실행
"""

import os
import requests
from supabase import create_client
from datetime import datetime

# ── 환경변수 ──────────────────────────────────────────────
TOUR_API_KEY    = os.environ["TOUR_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
PINTEREST_TOKEN = os.environ.get("PINTEREST_TOKEN", "")
PINTEREST_BOARD = os.environ.get("PINTEREST_BOARD_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── 전국문화축제표준데이터 API ────────────────────────────
API_URL = "https://api.data.go.kr/openapi/tn_pubr_public_cltur_fstvl_api"

def fetch_festivals(page=1, rows=100):
    params = {
        "serviceKey": TOUR_API_KEY,
        "pageNo":     page,
        "numOfRows":  rows,
        "type":       "json",
    }
    resp = requests.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    body  = data.get("response", {}).get("body", {})
    items = body.get("items", [])
    total = int(body.get("totalCount", 0))

    if isinstance(items, dict):
        items = [items]

    return items or [], total


def fetch_all_festivals():
    all_items = []
    page = 1
    rows = 100
    while True:
        items, total = fetch_festivals(page=page, rows=rows)
        if not items:
            break
        all_items.extend(items)
        print(f"  페이지 {page} — {len(items)}건 (총 {total}건)")
        if len(all_items) >= total:
            break
        page += 1
    return all_items


# ── Supabase 저장 ─────────────────────────────────────────
def upsert_festivals(items):
    new_festivals = []
    for item in items:
        # 축제명+시작일로 고유 ID 생성
        name  = item.get("fstvlNm", "").strip()
        start = item.get("fstvlStartDate", "").replace("-", "")
        end   = item.get("fstvlEndDate",   "").replace("-", "")

        if not name:
            continue

        festival_id = (name + start)[:200].replace(" ", "_")
        addr  = item.get("rdnmadr", "") or item.get("lnmadr", "")
        area  = addr.split(" ")[0] if addr else ""
        url   = item.get("homepageUrl", "") or ""
        if url and not url.startswith("http"):
            url = "https://" + url

        row = {
            "id":          festival_id,
            "title":       name,
            "start_date":  start,
            "end_date":    end,
            "area":        area,
            "sigungu":     addr,
            "place":       item.get("opar", addr),
            "image_url":   "",
            "detail_url":  url or f"https://www.google.com/search?q={name}+축제",
            "pinned_to_pinterest": False,
        }

        result = supabase.table("festivals").upsert(
            row, on_conflict="id", ignore_duplicates=True
        ).execute()

        if result.data:
            new_festivals.append(row)

    return new_festivals


# ── Pinterest 자동 핀 ─────────────────────────────────────
def create_pinterest_pin(festival):
    if not PINTEREST_TOKEN or not PINTEREST_BOARD:
        return False

    def fmt(d):
        return f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d) == 8 else d

    description = (
        f"🎉 {festival['title']}\n"
        f"📅 {fmt(festival['start_date'])} ~ {fmt(festival['end_date'])}\n"
        f"📍 {festival['sigungu']}\n\n"
        f"전국 축제 일정은 링크에서 확인하세요!"
    )
    payload = {
        "board_id":    PINTEREST_BOARD,
        "title":       festival["title"][:100],
        "description": description[:500],
        "link":        festival["detail_url"],
        "media_source": {
            "source_type": "image_url",
            "url": "https://via.placeholder.com/800x1200?text=Festival"
        }
    }
    headers = {
        "Authorization": f"Bearer {PINTEREST_TOKEN}",
        "Content-Type":  "application/json"
    }
    resp = requests.post(
        "https://api.pinterest.com/v5/pins",
        json=payload, headers=headers, timeout=15
    )
    if resp.status_code == 201:
        print(f"  ✅ Pinterest 핀: {festival['title']}")
        supabase.table("festivals").update({"pinned_to_pinterest": True}).eq("id", festival["id"]).execute()
        return True
    return False


# ── 메인 ─────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"  축제 수집 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    print("\n[1] API 수집 중...")
    items = fetch_all_festivals()
    print(f"  → 총 {len(items)}건 수집 완료")

    print("\n[2] Supabase 저장 중...")
    new_festivals = upsert_festivals(items)
    print(f"  → 신규 {len(new_festivals)}건 저장")

    if new_festivals and PINTEREST_TOKEN:
        print("\n[3] Pinterest 핀 생성 중...")
        pinned = sum(1 for f in new_festivals if create_pinterest_pin(f))
        print(f"  → {pinned}건 핀 생성")
    else:
        print("\n[3] Pinterest 스킵")

    print(f"\n{'='*50}")
    print(f"  완료: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
