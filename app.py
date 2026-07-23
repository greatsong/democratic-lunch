# 민주적 점심 — 교재 프롬프트로 생성한 코드를 단계별 페이지로 묶은 앱
# 1단계 = ① 샘플 프롬프트 결과, 2단계 = ③ 프롬프트 개선 결과
import streamlit as st

pg = st.navigation([
    st.Page("stage1_basic.py", title="1단계 · 기본판 (① 프롬프트)", default=True),
    st.Page("stage2_improved.py", title="2단계 · 개선판 (③ 개선)"),
    st.Page("stage3_full.py", title="3단계 · 풀버전 (완성형)"),
])
pg.run()
