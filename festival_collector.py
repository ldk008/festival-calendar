"""
festival_collector.py
- 한국관광공사 Tour API → Supabase 저장
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
PINTEREST_TOKEN  = os.environ.get("PINTEREST_TOKEN", "")   # 나중에 추가
PINTEREST_BOARD  = os.environ.get("PINTEREST_BOARD_ID", "") # 나중에 추가

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── 관광공사 API ──────────────────────────────────────────
TOUR_BASE = "http://apis.data.go.kr/B551011/KorService1"

def fetch_festivals(area_code="", page=1, rows=100):
    """관광공사 행사/축제 목록 조회"""
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "serviceKey": TOUR_API_KEY,
        "MobileOS":   "ETC",
        "MobileApp":  "FestivalCalendar",
        "_type":      "json",
        "eventStartDate": today,
        "arrange":    "A",          # 제목순
        "numOfRows":  rows,
        "pageNo":     page,
    }
    if area_code:
        params["areaCode"] = area_code

    resp = requests.get(f"{TOUR_BASE}/searchFestival1", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("response", {}).get("body", {}).get("items", {})
    if not items:
        return [], 0

    item_list = items.get("item", [])
    if isinstance(item_list, dict):   # 결과 1건이면 dict로 옴
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
    """신규/변경 축제 저장, 중복 방지 (id 기준)"""
    new_ids = []

    for item in items:
        festival_id = str(item.get("contentid", ""))
        if not festival_id:
            continue

        row = {
            "id":          festival_id,
            "title":       item.get("title", "").strip(),
            "start_date":  item.get("eventstartdate", ""),
            "end_date":    item.get("eventenddate", ""),
            "area":        item.get("addr1", "").split(" ")[0] if item.get("addr1") else "",
            "sigungu":     item.get("addr1", ""),
            "place":       item.get("addr1", ""),
            "image_url":   item.get("firstimage", "") or item.get("firstimage2", ""),
            "detail_url":  f"https://korean.visitkorea.or.kr/detail/ms_detail.do?cotid={festival_id}",
            "pinned_to_pinterest": False,
        }

        # upsert: 있으면 업데이트, 없으면 삽입
        result = supabase.table("festivals").upsert(
            row, on_conflict="id", ignore_duplicates=False
        ).execute()

        # 새로 삽입된 경우만 Pinterest 대상에 추가
        if result.data:
            existing = supabase.table("festivals") \
                .select("pinned_to_pinterest") \
                .eq("id", festival_id) \
                .single() \
                .execute()
            if existing.data and not existing.data["pinned_to_pinterest"]:
                new_ids.append(row)

    return new_ids


# ── Pinterest 자동 핀 ─────────────────────────────────────
def create_pinterest_pin(festival):
    """축제 1건 → Pinterest 핀 생성"""
    if not PINTEREST_TOKEN or not PINTEREST_BOARD:
        print("  Pinterest 토큰/보드 미설정 — 스킵")
        return False

    title    = festival["title"]
    start    = festival["start_date"]
    end      = festival["end_date"]
    place    = festival["sigungu"]
    link     = festival["detail_url"]
    image    = festival["image_url"]

    # 날짜 포맷: 20250501 → 2025.05.01
    def fmt(d):
        return f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d) == 8 else d

    description = (
        f"🎉 {title}\n"
        f"📅 {fmt(start)} ~ {fmt(end)}\n"
        f"📍 {place}\n\n"
        f"전국 축제 일정은 링크에서 확인하세요!"
    )

    payload = {
        "board_id":   PINTEREST_BOARD,
        "title":      title[:100],
        "description": description[:500],
        "link":       link,
        "media_source": {
            "source_type": "image_url",
            "url": image if image else "https://via.placeholder.com/800x1200?text=Festival"
        }
    }

    headers = {
        "Authorization": f"Bearer {PINTEREST_TOKEN}",
        "Content-Type":  "application/json"
    }

    resp = requests.post(
        "https://api.pinterest.com/v5/pins",
        json=payload,
        headers=headers,
        timeout=15
    )

    if resp.status_code == 201:
        print(f"  ✅ Pinterest 핀 생성: {title}")
        # DB에 pinned 표시
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

    # 1. 관광공사 API 전체 수집
    print("\n[1] 관광공사 API 수집 중...")
    items = fetch_all_festivals()
    print(f"  → 총 {len(items)}건 수집 완료")

    # 2. Supabase 저장
    print("\n[2] Supabase 저장 중...")
    new_festivals = upsert_festivals(items)
    print(f"  → 신규 축제 {len(new_festivals)}건 발견")

    # 3. Pinterest 자동 핀 (신규 축제만, 이미지 있는 것만)
    if new_festivals:
        print("\n[3] Pinterest 핀 생성 중...")
        pinned = 0
        for f in new_festivals:
            if f.get("image_url"):  # 이미지 없으면 스킵
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
