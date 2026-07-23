# -*- coding: utf-8 -*-
"""민주적 점심 — 풀버전 (점심 선거관리위원회)

한 화면 안에서 '점심 선거 방송' 한 편을 튼다.
투표 → 개표 방송 → 당선 발표 → (가끔) 잔소리 → 주간 결산.

저장소 계약(설계 02 문서 그대로):
  - 저장: GET + params(member, menu, type)   → Apps Script가 KST 문자열 시각을 붙여 한 줄 저장
  - 읽기: GET, 파라미터 없음                    → 2차원 JSON 배열(첫 줄 헤더 [시각, 팀원, 메뉴, 구분])
  - 시각은 이미 Asia/Seoul 문자열이므로 재변환하지 않는다.
  - 팀원 등록도 같은 log 시트에 구분="팀원등록"(메뉴 "-") 행으로 저장한다(탭을 늘리지 않는다).
"""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# 이름 기억용(브라우저 localStorage). 패키지가 없거나 JS를 못 쓰는 환경(AppTest 등)에서도
# 앱이 죽지 않도록 import 자체를 감싸고, 세션 상태로 폴백한다.
try:
    from streamlit_js_eval import streamlit_js_eval  # type: ignore
    _HAS_JS = True
except Exception:
    streamlit_js_eval = None
    _HAS_JS = False

# ─────────────────────────────────────────────────────────────
# 상수 (조정은 여기서)
# ─────────────────────────────────────────────────────────────
KST = ZoneInfo("Asia/Seoul")
HEADER = ["시각", "팀원", "메뉴", "구분"]
TIME_FMT = "%Y-%m-%d %H:%M:%S"

LS_NAME_KEY = "lunch_voter_name"  # 브라우저 localStorage 키(이름 기억)
# URL 쿼리 mode 값 ↔ 화면 모드
MODE_BY_PARAM = {"live": "실시간 공개", "ceremony": "개표식"}
PARAM_BY_MODE = {v: k for k, v in MODE_BY_PARAM.items()}

VOTE = "먹고싶다"      # 구분: 투표
EAT = "먹었다"        # 구분: 확정 섭취
REGISTER = "팀원등록"  # 구분: 팀원 명단 등록(메뉴는 "-")
SENTINEL_MEMBER = "전체"  # 먹었다 확정 행의 기본 작성자(명단에서 제외)

# ── 민주적 선정 가중 상수 (수치는 전부 여기서 조정) ──
NAG_DAYS = 7                # 잔소리 발동 기준일 (00 종합: 3일↔7일 충돌 → 7일로 통일)
WISH_WEIGHT_BASE = 2.0      # 소외 가중치 = WISH_WEIGHT_BASE − 소원성취율 (이력 없으면 1.0)
PENALTY_RECENT_DAYS = 7     # 최근 섭취 페널티 집계 기간(일)
PENALTY_PER_EAT = 0.6       # 최근 섭취 1회당 감점
STREAK_LOOKBACK_DAYS = 5    # 연승 판단 기간(일)
STREAK_PENALTY = 0.7        # 연속 우승 1회당 감점(2연승부터)
CLOSE_MARGIN = 1.0          # 접전 판정: 1·2위 최종점수 차이가 이 값 이하면 접전
TIE_EPS = 1e-6              # 동점 판정: 1위와 최종점수 차이가 이 값 이하면 동점(가중치 소수점 포함)
LONELY_RATE = 0.5           # 소원성취율이 이 값 미만인 당선자 투표자 → "소원 성취" 멘트
NEGLECT_RATE = 0.4          # 이 값 미만 팀원이 있으면 "좀 더 챙겨야" 알림

# 개표 연출 속도(초). 테스트에서도 그대로 도니 과하지 않게.
COUNT_STEP_SEC = 0.03
COUNT_PAUSE_SEC = 0.4

# ── 주제 프리셋 (라벨·목록만 갈아끼운다. 데이터 계약은 불변) ──
LUNCH_MENUS = ["김치찌개", "된장찌개", "돈까스", "비빔밥", "마라탕", "제육볶음", "순대국밥", "냉면"]
CAFE_MENUS = ["스타벅스", "메가커피", "컴포즈커피", "투썸플레이스", "빽다방", "이디야", "폴바셋", "동네카페"]

TOPICS = {
    "lunch": {
        "title": "민주적 점심",
        "subtitle": "점심의 민심을 개표합니다",
        "menus": LUNCH_MENUS,
        "item": "메뉴",
        "booth_q": "오늘 먹고 싶은 메뉴는?",
        "eat_verb_past": "드셨",
                "double_nag": "어… {winner}로 점심을 두 번 드시려고요? 선관위가 눈을 의심하고 있습니다.",
        "record_btn": "먹었다고 기록하기",
        "nag_accept": "그래도 먹겠다",
        "weekly_title": "이번 주 점심 개표 결산",
        "meals_metric": "이번 주 먹은 끼니",
        "top_metric": "최다 당선 메뉴",
    },
    "cafe": {
        "title": "카페 어디로 갈까",
        "subtitle": "오늘의 카페를 개표로 정합니다",
        "menus": CAFE_MENUS,
        "item": "카페",
        "booth_q": "오늘 가고 싶은 카페는?",
        "eat_verb_past": "가셨",
                "double_nag": "어… {winner}를 두 번 가시려고요? 선관위가 눈을 의심하고 있습니다.",
        "record_btn": "다녀왔다고 기록하기",
        "nag_accept": "그래도 가겠다",
        "weekly_title": "이번 주 카페 개표 결산",
        "meals_metric": "이번 주 방문 횟수",
        "top_metric": "최다 당선 카페",
    },
}


def current_topic_key() -> str:
    try:
        key = st.query_params.get("topic")
    except Exception:
        key = None
    return key if key in TOPICS else "lunch"


def topic_cfg() -> dict:
    return TOPICS[current_topic_key()]

# ── 팔레트(v2 식욕 팔레트) ──
CREAM = "#FFF9F2"   # 배경 크림
INK = "#2B1D16"     # 본문 딥브라운
CORAL = "#FF5A36"   # 주역 코랄/토마토
WARM = "#8A7A6E"    # 보조 웜그레이
SALMON = "#FFE8DD"  # 카드·말풍선 라이트살몬
GREEN = "#2E9E6B"   # 성공 그린(소량)


# ─────────────────────────────────────────────────────────────
# 시간 유틸 (앱의 '오늘'은 Asia/Seoul 기준)
# ─────────────────────────────────────────────────────────────
def now_kst_str() -> str:
    return datetime.now(KST).strftime(TIME_FMT)


def today():
    return datetime.now(KST).date()


# ─────────────────────────────────────────────────────────────
# 저장소 계약 (데모 모드 / 실 시트 모드)
# ─────────────────────────────────────────────────────────────
def get_sheet_url() -> str:
    """secrets에 SHEET_URL이 없으면 빈 문자열(=데모 모드)."""
    try:
        return str(st.secrets["SHEET_URL"]).strip()
    except Exception:
        return ""


def is_demo() -> bool:
    return get_sheet_url() == ""


def ensure_demo_seed():
    if "demo_rows" not in st.session_state:
        st.session_state["demo_rows"] = build_demo_rows(current_topic_key())


def load_rows():
    """2차원 배열(첫 줄 헤더)을 반환. 실 시트는 파라미터 없이 GET."""
    url = get_sheet_url()
    if not url:
        ensure_demo_seed()
        return st.session_state["demo_rows"]
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if not isinstance(data, list) or not data:
            return [HEADER]
        return data
    except Exception as e:
        st.error(f"시트를 읽지 못했어요: {e}")
        return [HEADER]


def save_row(member: str, menu: str, type_: str) -> bool:
    """저장 계약: GET params(member, menu, type). 데모 모드는 세션에만 쌓인다."""
    url = get_sheet_url()
    if not url:
        ensure_demo_seed()
        st.session_state["demo_rows"].append([now_kst_str(), member, menu, type_])
        return True
    try:
        requests.get(url, params={"member": member, "menu": menu, "type": type_}, timeout=10)
        return True
    except Exception as e:
        st.error(f"저장하지 못했어요: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 이름 기억 (localStorage, JS 없으면 세션 폴백) · URL
# ─────────────────────────────────────────────────────────────
def load_remembered_name():
    """직전에 쓴 이름을 불러온다. JS가 되면 localStorage, 아니면 세션 상태.
    이름은 URL에 넣지 않는다(공유 링크에 남의 이름이 따라가지 않게)."""
    if "remembered_name" in st.session_state:
        return st.session_state["remembered_name"]
    name = None
    if _HAS_JS:
        try:
            name = streamlit_js_eval(
                js_expressions=f"localStorage.getItem('{LS_NAME_KEY}')",
                key="ls_get_name", want_output=True,
            )
        except Exception:
            name = None
    if isinstance(name, str):
        name = name.strip() or None
    st.session_state["remembered_name"] = name
    return name


def remember_name(name: str):
    """이름을 기억해 둔다(다음 접속 때 자동 선택). JS 실패해도 세션엔 남는다."""
    st.session_state["remembered_name"] = name
    if _HAS_JS:
        try:
            streamlit_js_eval(
                js_expressions=f"localStorage.setItem('{LS_NAME_KEY}', {json.dumps(name)})",
                key="ls_set_name",
            )
        except Exception:
            pass


def base_url() -> str:
    """공유 링크의 기준 주소(쿼리 제거). 알 수 없으면 빈 문자열."""
    try:
        u = st.context.url
    except Exception:
        u = None
    if not u:
        return ""
    return u.split("?")[0]


# ─────────────────────────────────────────────────────────────
# 데모 데이터 (최근 2주치 그럴듯한 기록, 결정론적)
# ─────────────────────────────────────────────────────────────
def build_demo_rows(topic: str = "lunch"):
    """최근 2주치 투표·식사 + 오늘 표 몇 개. 재민을 은근히 소외시켜 민주 지수가 살아있게 짠다.

    아래 시나리오는 점심 메뉴 이름으로 짜여 있고, 다른 주제(카페 등)면 마지막에 목록 순서대로
    이름만 갈아끼운다. 크래프팅된 구조(재민 소외·최근 우승 잔소리)는 주제와 무관하게 유지된다.
    """
    members = ["은지", "도현", "수아", "재민"]
    rows = [list(HEADER)]

    # 팀원 등록 행(같은 log 시트, 구분=팀원등록, 메뉴 "-")
    for m in members:
        rows.append([f"2026-07-01 09:00:00", m, "-", REGISTER])

    base = today()
    # 지난 14일 시나리오: (그날 투표 메뉴 dict, 확정 메뉴)
    plan = [
        ({"은지": "김치찌개", "도현": "김치찌개", "수아": "돈까스", "재민": "마라탕"}, "김치찌개"),
        ({"은지": "된장찌개", "도현": "비빔밥", "수아": "된장찌개", "재민": "냉면"}, "된장찌개"),
        ({"은지": "돈까스", "도현": "돈까스", "수아": "제육볶음", "재민": "마라탕"}, "돈까스"),
        ({"은지": "김치찌개", "도현": "순대국밥", "수아": "김치찌개", "재민": "비빔밥"}, "김치찌개"),
        ({"은지": "비빔밥", "도현": "제육볶음", "수아": "비빔밥", "재민": "마라탕"}, "비빔밥"),
        ({"은지": "냉면", "도현": "냉면", "수아": "돈까스", "재민": "순대국밥"}, "냉면"),
        ({"은지": "제육볶음", "도현": "제육볶음", "수아": "김치찌개", "재민": "마라탕"}, "제육볶음"),
        ({"은지": "김치찌개", "도현": "김치찌개", "수아": "된장찌개", "재민": "비빔밥"}, "김치찌개"),
        ({"은지": "돈까스", "도현": "비빔밥", "수아": "돈까스", "재민": "마라탕"}, "돈까스"),
        ({"은지": "된장찌개", "도현": "된장찌개", "수아": "순대국밥", "재민": "냉면"}, "된장찌개"),
        ({"은지": "비빔밥", "도현": "비빔밥", "수아": "제육볶음", "재민": "마라탕"}, "비빔밥"),
        ({"은지": "제육볶음", "도현": "돈까스", "수아": "제육볶음", "재민": "순대국밥"}, "제육볶음"),
    ]
    for i, (votes, eaten) in enumerate(plan):
        # 최근이 리스트 앞이 되도록 과거→현재 순서로 날짜 배치
        day = base - timedelta(days=len(plan) + 1 - i)
        for j, (m, menu) in enumerate(votes.items()):
            t = day.strftime("%Y-%m-%d") + f" 11:{20 + j:02d}:00"
            rows.append([t, m, menu, VOTE])
        rows.append([day.strftime("%Y-%m-%d") + " 12:40:00", SENTINEL_MEMBER, eaten, EAT])

    # 오늘 표 몇 개(개표가 바로 돌아가게) — 김치찌개는 이틀 전에 먹어 잔소리가 붙는다.
    # 시각은 새벽(00:0x)으로 박아, 실제 사용자가 오늘 언제 투표하든 그 표가 '더 늦은 시각'이 되어
    # 사람별 마지막 표 규칙에서 시연 데이터에 밀리지 않게 한다(v3 버그 수정의 핵심).
    two_days_ago = (base - timedelta(days=2)).strftime("%Y-%m-%d") + " 12:40:00"
    rows.append([two_days_ago, SENTINEL_MEMBER, "김치찌개", EAT])
    tstr = base.strftime("%Y-%m-%d")
    rows.append([tstr + " 00:01:00", "은지", "김치찌개", VOTE])
    rows.append([tstr + " 00:02:00", "도현", "김치찌개", VOTE])
    rows.append([tstr + " 00:03:00", "수아", "돈까스", VOTE])

    # 주제가 점심이 아니면 메뉴 이름만 목록 순서대로 치환(구조는 그대로)
    menus = TOPICS.get(topic, TOPICS["lunch"])["menus"]
    if menus is not LUNCH_MENUS:
        remap = dict(zip(LUNCH_MENUS, menus))
        for r in rows[1:]:
            if r[3] in (VOTE, EAT):
                r[2] = remap.get(r[2], r[2])
    return rows


# ─────────────────────────────────────────────────────────────
# 데이터프레임 · 집계
# ─────────────────────────────────────────────────────────────
def rows_to_df(rows) -> pd.DataFrame:
    """2차원 배열 → DataFrame. 첫 줄은 헤더, 열 순서는 [시각,팀원,메뉴,구분] 고정."""
    if not rows or len(rows) < 2:
        df = pd.DataFrame(columns=HEADER)
        df["dt"] = pd.to_datetime([])
        return df
    body = rows[1:]
    df = pd.DataFrame(body, columns=HEADER)
    for c in HEADER:
        df[c] = df[c].astype(str)
    df["메뉴"] = df["메뉴"].str.strip()
    df["팀원"] = df["팀원"].str.strip()
    # 시각은 이미 KST 문자열 → 그대로 파싱, UTC 재변환 금지
    df["dt"] = pd.to_datetime(df["시각"], format=TIME_FMT, errors="coerce")
    return df


def is_empty_log(df: pd.DataFrame) -> bool:
    """실제 투표·식사 기록이 하나도 없으면 True(팀원등록만 있어도 빈 것으로 본다)."""
    return df[df["구분"].isin([VOTE, EAT])].empty


def all_members(df: pd.DataFrame):
    """전체 명단 = 팀원등록 행 + 투표·먹었다 등장 인물의 합집합(등장 순서 유지)."""
    names = []
    ordered = df[df["구분"] == REGISTER]["팀원"].tolist()
    ordered += df[df["구분"].isin([VOTE, EAT])]["팀원"].tolist()
    for n in ordered:
        n = str(n).strip()
        if n and n != SENTINEL_MEMBER and n not in names:
            names.append(n)
    return names


def todays_votes(df: pd.DataFrame) -> pd.DataFrame:
    """오늘 들어온 투표 중 사람마다 '마지막 제출'만 남긴다."""
    v = df[(df["구분"] == VOTE) & (df["dt"].dt.date == today())].copy()
    if v.empty:
        return v
    v = v.sort_values("dt")
    v = v.groupby("팀원", as_index=False).last()
    return v


def eat_history(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["구분"] == EAT) & df["dt"].notna()].copy()


def recent_eat_counts(df: pd.DataFrame, days: int) -> dict:
    """최근 days일 안에 '먹었다'로 기록된 메뉴별 횟수."""
    e = eat_history(df)
    if e.empty:
        return {}
    cutoff = today() - timedelta(days=days)
    e = e[e["dt"].dt.date > cutoff]
    return e["메뉴"].value_counts().to_dict()


def last_eaten_map(df: pd.DataFrame) -> dict:
    """메뉴별 마지막으로 먹은 날짜(전체 이력 대상)."""
    e = eat_history(df)
    if e.empty:
        return {}
    return e.groupby("메뉴")["dt"].max().apply(lambda x: x.date()).to_dict()


def eat_by_day(df: pd.DataFrame) -> dict:
    """날짜별 확정 메뉴(하루 1회 전제, 여러 개면 마지막 것)."""
    e = eat_history(df)
    if e.empty:
        return {}
    e = e.sort_values("dt")
    return e.groupby(e["dt"].dt.date)["메뉴"].last().to_dict()


def eaten_today(df: pd.DataFrame) -> bool:
    e = eat_history(df)
    if e.empty:
        return False
    return (e["dt"].dt.date == today()).any()


def achievement_rates(df: pd.DataFrame) -> dict:
    """팀원별 소원 성취율 = (그날 마지막 제출이 그날 확정 메뉴와 일치한 날) ÷ (투표 참여한 날).

    이력 없는(투표 안 한) 팀원은 결과에 넣지 않는다 → 가중치 계산에서 기본값 1.0으로 처리.
    """
    v = df[(df["구분"] == VOTE) & df["dt"].notna()].copy()
    if v.empty:
        return {}
    v["date"] = v["dt"].dt.date
    ebd = eat_by_day(df)
    rates = {}
    for member, g in v.groupby("팀원"):
        member = str(member).strip()
        if not member or member == SENTINEL_MEMBER:
            continue
        days = sorted(g["date"].unique())
        if not days:
            continue
        hits = 0
        for d in days:
            last_menu = g[g["date"] == d].sort_values("dt")["메뉴"].iloc[-1]
            if ebd.get(d) == last_menu:
                hits += 1
        rates[member] = hits / len(days)
    return rates


def member_weight(member: str, rates: dict) -> float:
    """소외 가중치: 이력 있으면 2−성취율, 없으면 1.0."""
    if member in rates:
        return WISH_WEIGHT_BASE - rates[member]
    return 1.0


def winning_streaks(df: pd.DataFrame) -> dict:
    """가장 최근부터 연속으로 확정된 메뉴의 연승 수(2연승부터 감점 대상)."""
    ebd = eat_by_day(df)
    if not ebd:
        return {}
    days = sorted(ebd.keys(), reverse=True)
    days = [d for d in days if d >= today() - timedelta(days=STREAK_LOOKBACK_DAYS)]
    if not days:
        return {}
    top = ebd[days[0]]
    cnt = 0
    for d in days:
        if ebd[d] == top:
            cnt += 1
        else:
            break
    if cnt >= 2:
        return {top: cnt - 1}  # 2연승→1, 3연승→2 …
    return {}


# ─────────────────────────────────────────────────────────────
# 민주적 선정
# ─────────────────────────────────────────────────────────────
def select_winner(df: pd.DataFrame, official: bool = False) -> dict:
    """가중 득표 + 최근섭취 페널티 + 연승 견제로 당선 메뉴를 뽑는다.

    최종점수 1위가 여럿이면(가중치 소수점 포함) 당선을 발표하지 않고 '동점'으로 돌려준다.
    official=True면 선관위 직권 결정 — 동점 후보 중 '가장 오랜만인 메뉴'를 당선으로 확정한다.
    """
    v = todays_votes(df)
    if v.empty:
        return {"empty": True}

    rates = achievement_rates(df)
    recent = recent_eat_counts(df, PENALTY_RECENT_DAYS)
    streaks = winning_streaks(df)

    raw = {}          # 메뉴별 원표 수
    scores = {}       # 메뉴별 가중 점수
    first_time = {}   # 메뉴별 오늘 첫 제출 시각
    voters = {}       # 메뉴별 투표자
    for _, r in v.iterrows():
        menu = str(r["메뉴"]).strip()
        member = str(r["팀원"]).strip()
        raw[menu] = raw.get(menu, 0) + 1
        scores[menu] = scores.get(menu, 0.0) + member_weight(member, rates)
        voters.setdefault(menu, []).append(member)
        ft = r["dt"]
        if menu not in first_time or ft < first_time[menu]:
            first_time[menu] = ft

    # 페널티 적용
    for menu in scores:
        scores[menu] -= PENALTY_PER_EAT * recent.get(menu, 0)
        scores[menu] -= STREAK_PENALTY * streaks.get(menu, 0)

    # 순위: 점수 내림차순 → 동률이면 최근섭취 적은 순(더 오랜만) → 먼저 제출된 순.
    # 이 2·3순위 키는 '직권 결정' 때 가장 오랜만인 메뉴를 고르는 데만 쓴다(자동 당선엔 안 씀).
    def sort_key(menu):
        return (-scores[menu], recent.get(menu, 0), first_time[menu])

    ranked = sorted(scores.keys(), key=sort_key)

    # 동점 판정: 최종점수 1위가 여럿인가(소수점 포함)
    top_score = scores[ranked[0]]
    tied = [m for m in ranked if abs(scores[m] - top_score) <= TIE_EPS]
    is_tie = len(tied) >= 2

    fallback_winner = ranked[0]  # 직권 결정 시 당선(동점 후보 중 가장 오랜만)
    if is_tie and not official:
        winner = None            # 동점이면 당선을 발표하지 않는다
    else:
        winner = fallback_winner

    # 접전 / 만장일치 (동점이 아닐 때만 의미)
    n_menus = len(scores)
    gap = (scores[ranked[0]] - scores[ranked[1]]) if n_menus >= 2 else None
    unanimous = (n_menus == 1)
    close = (not is_tie and n_menus >= 2 and gap is not None and gap <= CLOSE_MARGIN)

    wish_member = None
    new_menu = False
    streak = 0
    if winner is not None:
        # 소원 성취 멘트 대상: 당선 메뉴 투표자 중 성취율 최저(0.5 미만)
        cand = [(rates.get(m, None), m) for m in voters[winner] if m in rates]
        cand = [(rt, m) for rt, m in cand if rt is not None and rt < LONELY_RATE]
        if cand:
            cand.sort()
            wish_member = cand[0][1]
        # 새 메뉴 여부: 과거 투표·식사 이력에 한 번도 없던 메뉴
        past = df[(df["구분"].isin([VOTE, EAT])) & (df["dt"].dt.date < today())]["메뉴"]
        new_menu = winner not in set(past.tolist())
        streak = streaks.get(winner, 0)

    return {
        "empty": False,
        "winner": winner,
        "tie": is_tie and not official,
        "tied": tied,
        "fallback_winner": fallback_winner,
        "by_official": bool(is_tie and official),
        "raw": raw,
        "scores": scores,
        "ranked": ranked,
        "total_votes": int(sum(raw.values())),
        "n_menus": n_menus,
        "unanimous": unanimous,
        "close": close,
        "gap": gap,
        "wish_member": wish_member,
        "new_menu": new_menu,
        "streak": streak,
    }


def democracy_index(rates: dict):
    """민주 지수 = 형평성 점수. 성취율 격차가 클수록 낮아진다(0~100)."""
    vals = list(rates.values())
    if not vals:
        return 100, None
    spread = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
    idx = round(100 * (1 - spread))
    neglected = None
    lo = min(vals)
    if lo < NEGLECT_RATE and (max(vals) - lo) >= 0.25:
        neglected = sorted([(v, k) for k, v in rates.items()])[0][1]
    return idx, neglected


# ─────────────────────────────────────────────────────────────
# 멘트 (잔소리·안내 — 설계 01 §4 / 03 B-6)
# ─────────────────────────────────────────────────────────────
def winner_caption(res: dict) -> str:
    winner = res["winner"]
    votes = res["raw"].get(winner, 0)
    if res.get("by_official"):
        item = topic_cfg()["item"]
        return f"선관위 직권 결정으로 {winner} 당선입니다. 후보 중 가장 오랜만인 {item}예요. ({votes}표)"
    if res["unanimous"]:
        return f"{winner} 당선입니다. ({votes}표 만장일치) 🎉"
    return f"{winner} 당선입니다. ({votes}표) 🎉"


def nag_bubble(res: dict, df: pd.DataFrame):
    """당선 후보에 대한 선관위 한마디. (텍스트, 잔소리여부) 반환. 카피는 주제 프리셋을 따른다."""
    cfg = topic_cfg()
    item = cfg["item"]
    winner = res["winner"]
    last = last_eaten_map(df)
    week = recent_eat_counts(df, 7)

    # 오늘 이미 먹었다/다녀왔다
    e = eat_history(df)
    if not e.empty:
        eaten_menus_today = set(e[e["dt"].dt.date == today()]["메뉴"].tolist())
        if winner in eaten_menus_today:
            return (cfg["double_nag"].format(winner=winner), True)

    # 이번 주 3회 이상
    if week.get(winner, 0) >= 3:
        return (f"이번 주 벌써 세 번째 {winner}입니다. 이쯤 되면 애정 아닌가요?", True)

    # 최근 NAG_DAYS일 이내 → 실제 경과일을 넣는다
    if winner in last:
        days = (today() - last[winner]).days
        if 0 <= days <= NAG_DAYS:
            if days == 0:
                return (f"{winner}요? 오늘도 또 가시게요? 취향 한번 확고하시네요.", True)
            return (f"{winner}요? {days}일 전에도 {cfg['eat_verb_past']}는데 괜찮으시겠어요? 😏", True)

    # 새 후보 / 오랜만 → 칭찬으로 변주(잔소리만 있는 앱이 되지 않게)
    if res.get("new_menu"):
        return (f"오, 처음 보는 {item}네요. 이 팀의 새로운 역사가 시작되는 순간이에요. 오늘 용기 낸 한 표, 응원할게요.", False)
    return ("오랜만이네요! 이 조합, 선관위도 환영합니다.", False)


# ─────────────────────────────────────────────────────────────
# 스타일
# ─────────────────────────────────────────────────────────────
def inject_css():
    st.markdown(
        f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Black+Han+Sans&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap');

.stApp {{ background-color: {CREAM}; }}
html, body, .stApp, [class*="css"] {{
  font-family: 'IBM Plex Sans KR', sans-serif; color: {INK};
}}
.block-container {{ padding-top: 2.4rem; max-width: 1060px; }}

/* 헤더: 크림 배경 위 큰 타이포 + 가는 코랄 밑줄 */
.head-wrap {{ margin: 0 0 26px; }}
.head-title {{
  font-family: 'Black Han Sans', sans-serif;
  font-size: 46px; line-height: 1.05; color: {INK};
  display: inline-block; padding-bottom: 8px;
  border-bottom: 3px solid {CORAL};
}}
.head-sub {{ color: {WARM}; font-size: 15px; margin-top: 10px; }}
.demo-badge {{
  display: inline-block; background: {SALMON}; color: {CORAL};
  font-weight: 700; padding: 3px 12px; border-radius: 9999px;
  font-size: 12px; margin-left: 10px; vertical-align: middle;
}}

.section-title {{
  color: {INK}; font-weight: 700; font-size: 18px;
  margin: 8px 0 14px; letter-spacing: -0.2px;
}}

/* 개표판 바 */
.tally-row {{ display: flex; align-items: center; gap: 12px; margin: 12px 0; }}
.rank-badge {{
  flex: 0 0 auto; width: 26px; height: 26px; border-radius: 9999px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 14px; color: #fff;
}}
.tally-body {{ flex: 1 1 auto; }}
.tally-label {{ font-weight: 600; color: {INK}; font-size: 14px; margin-bottom: 5px; }}
.tally-label .cnt {{ color: {WARM}; font-weight: 500; }}
.tally-bar-bg {{ background: #F1E7DE; border-radius: 9999px; height: 12px; width: 100%; overflow: hidden; }}
.tally-bar {{ height: 12px; border-radius: 9999px; }}

/* 당선 무대 */
.winner-card {{
  background: {SALMON}; border-radius: 18px;
  padding: 30px 24px; text-align: center; margin: 8px 0 6px;
  box-shadow: 0 8px 30px rgba(255,90,54,0.10);
}}
.winner-label {{
  color: {CORAL}; font-weight: 700; font-size: 14px;
  letter-spacing: 3px; margin-bottom: 6px;
}}
.winner {{
  font-family: 'Black Han Sans', sans-serif;
  color: {INK}; font-size: 72px; line-height: 1.0; margin: 2px 0;
}}
.winner-tie {{
  font-family: 'Black Han Sans', sans-serif;
  color: {CORAL}; font-size: 48px; line-height: 1.05; margin: 2px 0;
}}
.winner-caption {{ color: {WARM}; font-size: 16px; margin-top: 12px; }}

/* 말풍선 공통 */
.bubble {{
  border-radius: 16px; padding: 16px 20px 16px 22px;
  margin-top: 16px; position: relative; font-size: 15px; line-height: 1.5;
}}
.nag-bubble {{ background: {SALMON}; border-left: 4px solid {CORAL}; color: {INK}; }}
.praise-bubble {{ background: #EAF6F0; border-left: 4px solid {GREEN}; color: {INK}; }}
.bubble .quote {{
  font-family: 'Black Han Sans', sans-serif; color: {CORAL};
  font-size: 30px; line-height: 0; margin-right: 6px; vertical-align: -6px;
}}
.bubble .who {{ display: block; color: {WARM}; font-size: 12px; margin-bottom: 6px; }}

/* 버튼 */
.stButton>button {{
  background: {CORAL}; color: #fff; font-weight: 600;
  border: none; border-radius: 12px; padding: 10px 22px;
  box-shadow: 0 4px 14px rgba(255,90,54,0.18);
}}
.stButton>button:hover {{ background: #ec4a29; color: #fff; }}
.stButton>button:disabled {{ background: #E7D8CE; color: #fff; box-shadow: none; }}

/* 메뉴 칩(pills) */
div[data-testid="stButtonGroup"] button {{
  border-radius: 9999px !important; border: 1px solid #E4D4C8 !important;
  background: #fff !important; color: {INK} !important; font-weight: 500 !important;
  box-shadow: none !important; padding: 6px 16px !important;
}}
div[data-testid="stButtonGroup"] button[aria-checked="true"],
div[data-testid="stButtonGroup"] button[aria-pressed="true"],
div[data-testid="stButtonGroup"] button[kind="pillsActive"] {{
  background: {CORAL} !important; color: #fff !important; border-color: {CORAL} !important;
}}

/* 기본 위젯 노출감 줄이기 */
[data-testid="stMetric"] {{
  background: #fff; border: 1px solid #F0E4DA; border-radius: 14px; padding: 14px 16px;
}}
[data-testid="stMetricValue"] {{ color: {CORAL}; }}
div[data-testid="stExpander"] details {{
  border: 1px solid #F0E4DA !important; border-radius: 16px !important; background: #fff;
}}
hr {{ border-color: #F0E4DA; }}

/* 모드 선택 카드 */
.mode-card {{
  background: #fff; border: 1px solid #F0E4DA; border-radius: 16px;
  padding: 18px 20px; margin-bottom: 10px; min-height: 118px;
}}
.mode-card .mode-name {{
  font-family: 'Black Han Sans', sans-serif; font-size: 24px; color: {CORAL}; margin-bottom: 6px;
}}
.mode-card .mode-desc {{ color: {WARM}; font-size: 14px; line-height: 1.5; }}

/* 개표식 대기 카드 */
.wait-card {{
  background: {SALMON}; border-radius: 18px; padding: 26px 22px; text-align: center;
}}
.wait-card .num {{
  font-family: 'Black Han Sans', sans-serif; font-size: 40px; color: {CORAL}; line-height: 1.1;
}}
.wait-card .cap {{ color: {WARM}; font-size: 14px; margin-top: 8px; }}
</style>
""",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# 화면 조각
# ─────────────────────────────────────────────────────────────
def render_header():
    cfg = topic_cfg()
    d = today()
    yoil = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
    badge = '<span class="demo-badge">데모 모드</span>' if is_demo() else ""
    st.markdown(
        f"""
<div class="head-wrap">
  <div class="head-title">{cfg["title"]} {badge}</div>
  <div class="head-sub">{cfg["subtitle"]} · {d.year}년 {d.month}월 {d.day}일 {yoil}요일</div>
</div>
""",
        unsafe_allow_html=True,
    )
    if is_demo():
        st.caption("실제 구글 시트 대신 내장 가짜 데이터로 돌아갑니다. 이 세션에서 넣은 표는 세션 안에서만 쌓여요.")


def render_voting_booth(members):
    st.markdown('<div class="section-title">투표소</div>', unsafe_allow_html=True)
    name_opts = members + ["직접 입력"]
    # 기억해 둔 이름이 명단에 있으면, 위젯이 처음 그려지기 전에 기본 선택으로 심는다.
    remembered = st.session_state.get("remembered_name")
    if remembered and remembered in name_opts and "voter_name" not in st.session_state:
        st.session_state["voter_name"] = remembered

    cfg = topic_cfg()
    menus = cfg["menus"]
    item = cfg["item"]

    name = st.selectbox("이름을 고르세요", name_opts, key="voter_name")
    if name == "직접 입력":
        name = st.text_input("이름 직접 입력", key="voter_name_custom").strip()

    # 칩(pills). default 파라미터와 key를 함께 쓰면 재실행 때 선택이 기본값으로
    # 되돌아갈 수 있어, 세션 상태를 유일한 기준으로 두고 default는 쓰지 않는다.
    st.session_state.setdefault("vote_menu", menus[0])
    menu = st.pills(cfg["booth_q"], menus, selection_mode="single", key="vote_menu")
    new_menu = st.text_input(f"또는 새 {item} 직접 적기", key="new_menu").strip()

    if st.button(f"이 {item}에 한 표", key="vote_btn"):
        chosen = new_menu if new_menu else menu
        if not name:
            st.warning("이름을 먼저 골라주세요.")
        elif not chosen:
            st.warning(f"{item}를 하나 골라주세요.")
        else:
            save_row(name, chosen, VOTE)
            remember_name(name)
            # 새 표가 들어오면 동점이 자연히 갈릴 수 있으니 직권 결정 상태를 초기화
            st.session_state.pop("tie_override", None)
            st.session_state["_flash"] = f"'{chosen}'에 한 표, 정상 접수했습니다 🗳️"
            st.rerun()


def render_live_board(df):
    st.markdown('<div class="section-title">실시간 개표판</div>', unsafe_allow_html=True)
    v = todays_votes(df)
    if v.empty:
        st.info("아직 접수된 표가 없어요. 오늘 첫 표의 주인공이 되어 주세요.")
        return
    counts = v["메뉴"].value_counts()
    total = int(counts.sum())
    st.caption(f"현재 총 {total}표 접수 · 사람마다 마지막 표 한 장만 셉니다")
    top = counts.max()
    for i, (menu, c) in enumerate(counts.items()):
        color = CORAL if i == 0 else WARM
        badge_bg = CORAL if i == 0 else WARM
        pct = int(c / top * 100) if top else 0
        st.markdown(
            f"""
<div class="tally-row">
  <div class="rank-badge" style="background:{badge_bg};">{i + 1}</div>
  <div class="tally-body">
    <div class="tally-label">{menu} <span class="cnt">· {int(c)}표</span></div>
    <div class="tally-bar-bg"><div class="tally-bar" style="width:{pct}%;background:{color};"></div></div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )


def animate_counting(res):
    """개표 방송 연출: 진행 막대 + 문구 교체 → 90%에서 멈칫 → 발표(또는 동점 선언)."""
    is_tie = res.get("tie")
    leader = res.get("winner") or (res.get("tied") or ["선두"])[0]
    text_slot = st.empty()
    bar = st.progress(0)
    steps = 20
    for i in range(steps + 1):
        pct = int(i / steps * 100)
        if pct < 90:
            text_slot.markdown(f"개표 중입니다… 현재 개표율 {pct}%… **{leader}** 선두…")
        elif pct < 100:
            text_slot.markdown("개표율 90%. 마지막 표를 확인하고 있습니다 🥁")
        elif is_tie:
            text_slot.markdown("개표 결과, 동점입니다 ⚖️")
        else:
            text_slot.markdown("개표 결과를 발표하겠습니다.")
        bar.progress(pct)
        time.sleep(COUNT_STEP_SEC)
        if pct == 90:
            time.sleep(COUNT_PAUSE_SEC)
    text_slot.empty()
    bar.empty()
    if is_tie or res["close"]:
        st.snow()
    else:
        st.balloons()


def render_winner_stage(res, df):
    winner = res["winner"]
    st.markdown(
        f"""
<div class="winner-card">
  <div class="winner-label">오늘의 당선</div>
  <div class="winner">{winner}</div>
  <div class="winner-caption">{winner_caption(res)}</div>
</div>
""",
        unsafe_allow_html=True,
    )

    if res.get("wish_member"):
        st.markdown(
            f'<div class="bubble praise-bubble"><span class="who">선관위 배려</span>'
            f'이번엔 {res["wish_member"]} 님이 찍은 메뉴가 1위예요. '
            f'그동안 계속 밀리셨는데, 오늘은 좀 몰아드렸습니다. 이런 게 팀워크죠.</div>',
            unsafe_allow_html=True,
        )
    if res.get("streak", 0) >= 1:
        st.markdown(
            f'<div class="bubble praise-bubble"><span class="who">선관위 견제</span>'
            f'{winner}, 최근 연이어 우승 중이라 견제 감점을 얹었는데도 이겼네요. '
            f'다음엔 다른 메뉴에게도 기회를 줘볼까요?</div>',
            unsafe_allow_html=True,
        )

    # 잔소리 말풍선
    text, is_nag = nag_bubble(res, df)
    if is_nag:
        st.markdown(
            f'<div class="bubble nag-bubble"><span class="who">선관위 한마디</span>'
            f'<span class="quote">&ldquo;</span>{text}</div>',
            unsafe_allow_html=True,
        )
        c1, _ = st.columns([1, 3])
        with c1:
            if st.button(topic_cfg()["nag_accept"], key="nag_eat"):
                st.session_state["_flash"] = "알겠습니다. 선관위는 말릴 뿐, 막지는 않습니다."
        # 당선 번복은 없다 — 능청스럽게 승복 카피로 마무리
        st.caption("당선은 당선입니다. 민주주의는 승복이에요.")
    else:
        st.markdown(
            f'<div class="bubble praise-bubble"><span class="who">선관위 한마디</span>'
            f'<span class="quote">&ldquo;</span>{text}</div>',
            unsafe_allow_html=True,
        )

    # 먹었다 확정 기록 (하루 1회)
    already = eaten_today(df)
    if st.button(topic_cfg()["record_btn"], key="eat_btn", disabled=already):
        save_row(SENTINEL_MEMBER, winner, EAT)
        st.session_state["_flash"] = "기록 완료! 다음에 또 나오면 저희가 다 기억하고 있을게요."
        st.rerun()
    if already:
        st.caption("오늘은 이미 확정 기록이 있어요. (소원 성취율 계산을 위해 하루 한 번만 기록합니다)")


def render_tie(res, df):
    """동점: 당선을 발표하지 않고 결선 투표를 안내한다. 직권 결정 경로를 하나 둔다."""
    tied = res.get("tied", [])
    st.markdown(
        f"""
<div class="winner-card">
  <div class="winner-label">동점</div>
  <div class="winner-tie">동점입니다 ⚖️</div>
  <div class="winner-caption">후보: {" · ".join(tied)} — 결선 투표로 정해 주세요.</div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.info("표는 사람마다 마지막 한 장만 셉니다. 후보 중 하나에 다시 투표하면 자연히 판가름 나요.")
    if st.button("선관위 직권 결정 (가장 오랜만인 메뉴로 확정)", key="official_btn"):
        st.session_state["tie_override"] = True
        if "counting_result" in st.session_state:
            st.session_state["counting_result"] = select_winner(df, official=True)
        st.rerun()


def render_result_area(res, df):
    """동점이면 결선 안내, 아니면 당선 무대."""
    if res.get("tie"):
        render_tie(res, df)
    else:
        render_winner_stage(res, df)


def render_participation(df):
    """개표식 모드: 개표 전에는 득표·당선을 숨기고 참여 현황만 보여준다."""
    st.markdown('<div class="section-title">개표식 대기 중</div>', unsafe_allow_html=True)
    v = todays_votes(df)
    n = int(len(v))
    st.markdown(
        f"""
<div class="wait-card">
  <div class="num">{n}명</div>
  <div>투표 완료</div>
  <div class="cap">득표 현황과 당선은 개표식에서 공개됩니다 🤫</div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_realtime_result(df):
    """실시간 공개 모드: 개표 버튼 없이 결과를 바로 갱신(연출 없음). 동점이면 결선 안내."""
    official = bool(st.session_state.get("tie_override"))
    res = select_winner(df, official=official)
    if res.get("empty"):
        return
    st.markdown("---")
    render_result_area(res, df)


def render_count_section(df):
    v = todays_votes(df)
    st.markdown("---")
    disabled = v.empty
    if st.button("개표를 시작합니다", key="count_btn", disabled=disabled):
        official = bool(st.session_state.get("tie_override"))
        st.session_state["counting_result"] = select_winner(df, official=official)
        st.session_state["just_counted"] = True

    res = st.session_state.get("counting_result")
    if not res or res.get("empty"):
        if disabled:
            st.caption("표가 한 장이라도 들어오면 개표를 시작할 수 있어요.")
        return

    # 연출은 방금 개표한 그 순간에만 1회 (재실행 시 반복 금지)
    if st.session_state.get("just_counted"):
        animate_counting(res)
        st.session_state["just_counted"] = False

    render_result_area(res, df)


def render_weekly_report(df):
    cfg = topic_cfg()
    with st.expander(cfg["weekly_title"]):
        e = eat_history(df)
        cutoff = today() - timedelta(days=7)
        week = e[e["dt"].dt.date > cutoff] if not e.empty else e

        n_meals = int(len(week))
        if n_meals == 0:
            st.info("이번 주엔 아직 확정된 끼니가 없어요.")
        top_menu, top_cnt = "-", 0
        if n_meals:
            vc = week["메뉴"].value_counts()
            top_menu, top_cnt = vc.index[0], int(vc.iloc[0])

        # 새로 도전한 메뉴: 이번 주에 처음 등장한 메뉴 수
        past_menus = set(
            df[(df["구분"].isin([VOTE, EAT])) & (df["dt"].dt.date <= cutoff)]["메뉴"].tolist()
        )
        new_menus = set(week["메뉴"].tolist()) - past_menus if n_meals else set()

        c1, c2, c3 = st.columns(3)
        c1.metric(cfg["meals_metric"], f"{n_meals}회")
        c2.metric(cfg["top_metric"], f"{top_menu}" + (f" ({top_cnt}회)" if top_cnt else ""))
        c3.metric(f"새로 도전한 {cfg['item']}", f"{len(new_menus)}개")

        # 요일별 타임라인 (월~금)
        st.markdown("**요일별 밥상**")
        ebd = eat_by_day(df)
        cols = st.columns(5)
        monday = today() - timedelta(days=today().weekday())
        for i, label in enumerate(["월", "화", "수", "목", "금"]):
            d = monday + timedelta(days=i)
            menu = ebd.get(d, "—")
            cols[i].markdown(f"**{label}**\n\n{menu}")

        # 빈도 막대그래프 (plotly)
        if n_meals:
            vc = week["메뉴"].value_counts()
            fig_df = pd.DataFrame({"메뉴": vc.index, "횟수": vc.values})
            colors = [CORAL if i == 0 else WARM for i in range(len(fig_df))]
            fig = px.bar(fig_df, x="메뉴", y="횟수", text="횟수")
            fig.update_traces(marker_color=colors)
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=10, b=10), height=280, showlegend=False,
                font=dict(color=INK),
            )
            fig.update_yaxes(dtick=1)
            st.plotly_chart(fig, width="stretch")

        # 민주 지수 + 선관위 총평
        rates = achievement_rates(df)
        idx, neglected = democracy_index(rates)
        st.markdown(f"**민주 지수(형평성): {idx}점** — 팀원들에게 얼마나 골고루 돌아갔는지를 봅니다.")
        if neglected:
            st.warning(f"이번 주엔 {neglected} 님을 좀 더 챙겨줄 필요가 있어요. 소원 성취율이 유독 낮습니다.")
        if n_meals:
            st.markdown(
                f"선관위 총평: 이번 주 민심은 **{top_menu}** 쪽으로 기울었습니다. "
                f"다음 주 공약이 궁금합니다."
            )


def render_landing():
    """mode 파라미터가 없을 때: 두 모드를 고르는 안내 화면 + 공유 링크."""
    st.markdown('<div class="section-title">어떤 방식으로 진행할까요?</div>', unsafe_allow_html=True)
    st.write("모드는 접속 링크로 정해집니다. 아래 링크를 팀 채팅방에 올리면 모두 같은 모드로 들어와요.")

    base = base_url()
    topic_key = current_topic_key()
    topic_suffix = "" if topic_key == "lunch" else f"&topic={topic_key}"
    live_link = f"{base}?mode=live{topic_suffix}"
    ceremony_link = f"{base}?mode=ceremony{topic_suffix}"

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            '<div class="mode-card"><div class="mode-name">실시간 공개</div>'
            '<div class="mode-desc">표가 들어올 때마다 개표판과 당선이 바로 갱신됩니다. '
            '가볍게 정할 때.</div></div>',
            unsafe_allow_html=True,
        )
        st.caption("공유용 링크")
        st.code(live_link, language=None)
        if st.button("실시간 공개로 열기", key="enter_live"):
            st.query_params["mode"] = "live"
            if topic_key != "lunch":
                st.query_params["topic"] = topic_key
            st.rerun()
    with c2:
        st.markdown(
            '<div class="mode-card"><div class="mode-name">개표식</div>'
            '<div class="mode-desc">개표 전까지 결과를 가리고, 다 같이 개표 버튼으로 공개합니다. '
            '이벤트처럼 즐길 때.</div></div>',
            unsafe_allow_html=True,
        )
        st.caption("공유용 링크")
        st.code(ceremony_link, language=None)
        if st.button("개표식으로 열기", key="enter_ceremony"):
            st.query_params["mode"] = "ceremony"
            if topic_key != "lunch":
                st.query_params["topic"] = topic_key
            st.rerun()

    st.info("이 링크를 팀 채팅방에 올리면 모두 같은 모드로 들어옵니다. 개인이 임의로 바꾸지 못해요.")


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title=topic_cfg()["title"], page_icon="🗳️", layout="wide")
    inject_css()
    load_remembered_name()  # 이름 기억 불러오기(JS 없으면 세션 폴백)

    rows = load_rows()
    df = rows_to_df(rows)

    render_header()

    if st.session_state.get("_flash"):
        st.success(st.session_state.pop("_flash"))

    # 공개 방식은 URL 쿼리(mode)로 고정한다. 없으면 안내 화면.
    mode = MODE_BY_PARAM.get(st.query_params.get("mode"))
    if mode is None:
        render_landing()
        return

    st.caption(
        f"현재 방식: {mode} · 모드는 접속 링크로 정해져 개인이 바꾸지 않습니다."
    )

    members = all_members(df)

    left, right = st.columns([1, 1])
    with left:
        render_voting_booth(members)
    with right:
        if mode == "실시간 공개":
            render_live_board(df)
        else:
            render_participation(df)

    if mode == "실시간 공개":
        render_realtime_result(df)
    else:
        render_count_section(df)

    render_weekly_report(df)


if __name__ == "__main__":
    main()
