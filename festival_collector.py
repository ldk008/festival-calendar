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

# ── 한국관광공사 Tour API ─────────────────────────────────
API_URL = "https://apis.data.go.kr/B551011/KorService1/searchFestival2"

def fetch_festivals(page=1, rows=100):
    """한국관광공사 행사/축제 목록 조회"""
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "serviceKey":     TOUR_API_KEY,
        "MobileOS":       "ETC",
        "MobileApp":      "FestivalCalendar",
        "_type":          "json",
        "eventStartDate": today,
        "arrange":        "A",
        "numOfRows":      rows,
        "pageNo":         page,
    }

    resp = requests.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("response", {}).get("body", {}).get("items", {})
    if not items:
        return [], 0

    item_list = items.get("item", [])
    if isinstance(item_list, dict):
        item_list = [item_list]

    total = int(data["response"]["body"]["totalCount"])
    return item_list, total


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
        # 관광공사 Tour API 필드명
        festival_id = str(item.get("contentid", ""))
        if not festival_id:
            continue

        addr  = item.get("addr1", "")
        area  = addr.split(" ")[0] if addr else ""

        row = {
            "id":          festival_id,
            "title":       item.get("title", "").strip(),
            "start_date":  item.get("eventstartdate", ""),
            "end_date":    item.get("eventenddate", ""),
            "area":        area,
            "sigungu":     addr,
            "place":       addr,
            "image_url":   item.get("firstimage", "") or item.get("firstimage2", ""),
            "detail_url":  f"https://korean.visitkorea.or.kr/detail/ms_detail.do?cotid={festival_id}",
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
