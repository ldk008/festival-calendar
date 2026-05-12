"""
festival_collector.py
- 전국문화축제표준데이터 API → Supabase 저장
- 신규 축제 발견 시 Pinterest 자동 핀 생성
- GitHub Actions에서 매일 자동 실행
"""

import os
import requests
from supabase import create_client
from datetime import datetime

# ── 환경변수 ──────────────────────────────────────────────
TOUR_API_KEY     = os.environ["TOUR_API_KEY"]
SUPABASE_URL     = os.environ["SUPABASE_URL"]
SUPABASE_KEY     = os.environ["SUPABASE_KEY"]
PINTEREST_TOKEN  = os.environ.get("PINTEREST_TOKEN", "")
PINTEREST_BOARD  = os.environ.get("PINTEREST_BOARD_ID", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── 전국문화축제표준데이터 API ────────────────────────────
API_URL = "https://api.data.go.kr/openapi/tn_pubr_public_cltur_fstvl_api"

def fetch_festivals(page=1, rows=100):
    """전국문화축제표준데이터 조회"""
    params = {
        "serviceKey": TOUR_API_KEY,
        "pageNo":     page,
        "numOfRows":  rows,
        "type":       "json",
    }

    resp = requests.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("response", {}).get("body", {}).get("items", [])
    total = int(data.get("response", {}).get("body", {}).get("totalCount", 0))

    if isinstance(items, dict):
        items = [items]

    return items or [], total


def fetch_all_festivals():
    """전체 페이지 순회해서 모든 축제 수집"""
    all_items = []
    page = 1
    rows = 100

    while True:
        items, total = fetch_festivals(page=page, rows=rows)
        if not items:
            break
        all_items.extend(items)
        print(f"  페이지 {page} — {len(items)}건 수집 (총 {total}건)")
        if len(all_items) >= total:
            break
        page += 1

    return all_items


# ── Supabase 저장 ─────────────────────────────────────────
def upsert_festivals(items):
    """신규/변경 축제 저장"""
    new_festivals = []

    for item in items:
        # 전국문화축제표준데이터 필드명
        festival_id = str(item.get("fstvlNm", "") + item.get("fstvlStartDate", "")).replace(" ", "_")
        if not festival_id:
            continue

        # 날짜 형식 변환: 2025-05-01 → 20250501
        start = item.get("fstvlStartDate", "").replace("-", "")
        end   = item.get("fstvlEndDate",   "").replace("-", "")
        addr  = item.get("rdnmadr", "") or item.get("lnmadr", "")
        area  = addr.split(" ")[0] if addr else ""

        row = {
            "id":          festival_id[:200],
            "title":       item.get("fstvlNm", "").strip(),
            "start_date":  start,
            "end_date":    end,
            "area":        area,
            "sigungu":     addr,
            "place":       item.get("fstvlPlace", addr),
            "image_url":   item.get("imageUrl", ""),
            "detail_url":  item.get("homepageUrl", "") or f"https://www.google.com/search?q={item.get('fstvlNm','')}",
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
        print("  Pinterest 토큰/보드 미설정 — 스킵")
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
            "url": festival["image_url"] if festival.get("image_url") else "https://via.placeholder.com/800x1200?text=Festival"
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
        print(f"  ✅ Pinterest 핀 생성: {festival['title']}")
        supabase.table("festivals") \
            .update({"pinned_to_pinterest": True}) \
            .eq("id", festival["id"]) \
            .execute()
        return True
    else:
        print(f"  ❌ Pinterest 실패: {resp.status_code} — {resp.text}")
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
    print(f"  → 신규 축제 {len(new_festivals)}건 발견")

    if new_festivals:
        print("\n[3] Pinterest 핀 생성 중...")
        pinned = 0
        for f in new_festivals:
            if f.get("image_url"):
                success = create_pinterest_pin(f)
                if success:
                    pinned += 1
        print(f"  → {pinned}건 핀 생성 완료")
    else:
        print("\n[3] 신규 축제 없음 — Pinterest 스킵")

    print(f"\n{'='*50}")
    print(f"  완료: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
