import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ── 구글 시트 접수창구 주소 (비밀 금고에서 불러오기) ──────────
SHEET_URL = st.secrets["SHEET_URL"]

st.set_page_config(page_title="민주적 점심", page_icon="🍚")

# ── 개표 방송 분위기: 크림 바탕 + 주황 포인트 ──────────────
st.markdown("""
<style>
.stApp { background-color: #FFF9F2; }
h1, h2, h3 { color: #2B1D16; }
div.stButton > button {
    background-color: #FF5A36; color: white;
    border: none; border-radius: 10px; font-weight: 700;
}
</style>
""", unsafe_allow_html=True)

st.title("민주적 점심 개표 방송")
st.caption("점심의 민심을 개표합니다. 소중한 한 표를 행사해 주세요!")

메뉴목록 = ["김치찌개", "된장찌개", "돈까스", "비빔밥", "냉면", "샐러드"]
최소투표인원 = 3   # 이 인원이 모이기 전에는 당선을 발표하지 않아요

# ── 투표하기 ───────────────────────────────────
이름 = st.text_input("이름(또는 별명)")
메뉴 = st.selectbox("오늘 먹고 싶은 메뉴는?", 메뉴목록)

if st.button("이 메뉴에 한 표"):
    if not 이름.strip():
        st.warning("이름을 먼저 알려주세요!")
    else:
        requests.get(SHEET_URL, params={"member": 이름.strip(), "menu": 메뉴, "type": "먹고싶다"})
        st.balloons()   # 투표가 접수되면 풍선 축하 연출 한 번
        st.success(f"{이름}님의 한 표, 소중히 접수했습니다!")

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

# ── 오늘의 개표 ────────────────────────────────
오늘표 = df[(df["구분"] == "먹고싶다") & (df["날짜"] == 오늘)]

if 오늘표.empty:
    st.info("오늘은 아직 투표함이 비어 있어요. 첫 표를 기다립니다!")
else:
    # 같은 사람이 여러 번 냈으면 마지막 표만 세요
    최종표 = 오늘표.sort_values("시각").groupby("팀원").tail(1)
    집계 = 최종표["메뉴"].value_counts()
    인원 = len(최종표)

    if 인원 < 최소투표인원:
        # 표가 모이기 전에는 당선을 발표하지 않아요 (성급한 당선 방지)
        st.info(f"개표가 진행 중입니다 · 현재 {인원}명 투표 · {최소투표인원}명이 모이면 당선을 발표합니다")
    else:
        후보 = 집계[집계 == 집계.max()].index.tolist()

        st.markdown("<p style='text-align:center;color:#FF5A36;letter-spacing:4px'>개표 결과를 발표합니다</p>",
                    unsafe_allow_html=True)

        if len(후보) > 1:
            # 동점 = 당선을 미루고 결선 투표 안내 (재투표는 동점일 때만!)
            st.warning("동점입니다 ⚖️ 후보: " + ", ".join(후보)
                       + " — 결선 투표로 정해 주세요. 다시 투표하면 마지막 표만 인정됩니다.")
        else:
            당선 = 후보[0]
            st.markdown(f"<h1 style='text-align:center;font-size:60px;margin:0'>당선 · {당선}</h1>",
                        unsafe_allow_html=True)
            st.markdown(f"<p style='text-align:center;color:#8A7A6E'>{당선} 당선입니다. ({집계[당선]}표) 🎉</p>",
                        unsafe_allow_html=True)

            # 최근 7일 안에 먹었던 메뉴면 말풍선으로 능청스럽게 한마디
            이력 = 먹은기록[먹은기록["메뉴"] == 당선]
            if not 이력.empty:
                지난일 = (오늘 - 이력["날짜"].max()).days
                if 지난일 == 0:
                    잔소리 = f"{당선}, 오늘 벌써 드시지 않았나요? 또 드시게요? 😏"
                elif 지난일 <= 7:
                    잔소리 = f"{당선}요? {지난일}일 전에도 드셨는데 괜찮으시겠어요? 😏"
                else:
                    잔소리 = ""
                if 잔소리:
                    st.markdown(f"""<div style='border-left:4px solid #FF5A36;background:#FFE8DD;
                        padding:12px 16px;border-radius:10px;margin:8px 0'>{잔소리}</div>""",
                        unsafe_allow_html=True)

            if st.button("오늘 이거 먹었다"):
                # 이름을 안 적었어도 점심은 팀 전체의 기록이라 '전체'로 남겨요
                requests.get(SHEET_URL, params={"member": 이름.strip() or "전체",
                                                "menu": 당선, "type": "먹었다"})
                st.success("기록 완료! 다음에 또 나오면 저희가 다 기억하고 있을게요.")

    # 득표 현황 (가로 막대, 표 많은 메뉴가 위로) — 진행 중·결선 때도 보여요
    st.subheader("현재 개표 현황")
    득표 = 집계.rename_axis("메뉴").reset_index(name="득표수").sort_values("득표수")
    fig = px.bar(득표, x="득표수", y="메뉴", orientation="h",
                 color_discrete_sequence=["#FF5A36"])
    st.plotly_chart(fig, width="stretch")

st.divider()

# ── 이번 주 점심 리포트 ─────────────────────────
st.subheader("이번 주 점심 리포트")
최근먹은 = 먹은기록[먹은기록["날짜"] >= 일주일전]
if 최근먹은.empty:
    st.info("최근 7일 동안 확정된 점심 기록이 없어요.")
else:
    주간 = 최근먹은["메뉴"].value_counts().rename_axis("메뉴").reset_index(name="횟수")
    fig2 = px.bar(주간, x="메뉴", y="횟수", color_discrete_sequence=["#FF5A36"])
    st.plotly_chart(fig2, width="stretch")