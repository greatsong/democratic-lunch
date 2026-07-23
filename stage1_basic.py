import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ── 구글 시트 접수창구 주소 (비밀 금고에서 불러오기) ──────────
SHEET_URL = st.secrets["SHEET_URL"]

st.set_page_config(page_title="민주적 점심", page_icon="🍚")
st.title("민주적 점심")
st.write("오늘 점심, 투표로 정해요. 한 표 부탁드립니다!")

메뉴목록 = ["김치찌개", "된장찌개", "돈까스", "비빔밥", "냉면", "샐러드"]

# ── 투표하기 ───────────────────────────────────
이름 = st.text_input("이름(또는 별명)")
메뉴 = st.selectbox("오늘 먹고 싶은 메뉴는?", 메뉴목록)

if st.button("이 메뉴에 한 표"):
    if not 이름.strip():
        st.warning("이름을 먼저 알려주세요!")
    else:
        # 파라미터로 넘기면 한글 인코딩은 requests가 알아서 처리해요
        requests.get(SHEET_URL, params={"member": 이름.strip(), "menu": 메뉴, "type": "먹고싶다"})
        st.success(f"{이름}님의 한 표, 잘 접수했어요!")

st.divider()

# ── 전체 기록 불러오기 ──────────────────────────
rows = requests.get(SHEET_URL).json()   # [[머리글], [기록1], [기록2], ...]

if len(rows) <= 1:
    st.info("아직 기록이 없어요. 첫 표의 주인공이 되어 보세요!")
    st.stop()

df = pd.DataFrame(rows[1:], columns=rows[0])        # 첫 줄은 머리글
df["날짜"] = pd.to_datetime(df["시각"]).dt.date      # 이미 한국 시간이라 그대로 읽어요

오늘 = datetime.now(ZoneInfo("Asia/Seoul")).date()   # '오늘'도 한국 시간 기준
일주일전 = 오늘 - timedelta(days=7)
먹은기록 = df[df["구분"] == "먹었다"]

# ── 오늘의 당선 발표 ─────────────────────────────
오늘표 = df[(df["구분"] == "먹고싶다") & (df["날짜"] == 오늘)]

if 오늘표.empty:
    st.info("오늘은 아직 아무도 투표하지 않았어요.")
else:
    # 같은 사람이 여러 번 냈으면 마지막 표만 세요
    최종표 = 오늘표.sort_values("시각").groupby("팀원").tail(1)
    집계 = 최종표["메뉴"].value_counts()
    당선 = 집계.index[0]

    st.subheader("오늘의 당선")
    st.markdown(f"## {당선} ({집계[당선]}표)")

    # 최근 7일 안에 먹었던 메뉴면 능청스럽게 한마디
    이력 = 먹은기록[먹은기록["메뉴"] == 당선]
    if not 이력.empty:
        지난일 = (오늘 - 이력["날짜"].max()).days
        if 지난일 == 0:
            st.error(f"{당선}, 오늘 벌써 드시지 않았나요? 또 드시게요?")
        elif 지난일 <= 7:
            st.error(f"{당선}요? {지난일}일 전에도 드셨는데 괜찮으시겠어요?")

    if st.button("오늘 이거 먹었다"):
        # 이름을 안 적었어도 점심은 팀 전체의 기록이라 '전체'로 남겨요
        requests.get(SHEET_URL, params={"member": 이름.strip() or "전체",
                                        "menu": 당선, "type": "먹었다"})
        st.success("기록 완료! 다음에 또 나오면 저희가 다 기억하고 있을게요.")

    st.write("오늘의 득표 현황")
    st.dataframe(집계.rename("득표수"), width="stretch")

st.divider()

# ── 최근 7일 동안 먹은 기록 ─────────────────────
st.write("최근 7일 동안 먹은 기록")
최근먹은 = 먹은기록[먹은기록["날짜"] >= 일주일전]
if 최근먹은.empty:
    st.info("최근 7일 동안 확정된 점심 기록이 없어요.")
else:
    st.dataframe(최근먹은[["날짜", "메뉴"]].sort_values("날짜", ascending=False),
                 width="stretch", hide_index=True)