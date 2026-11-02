"""Small, stable presentation layer over native Streamlit components."""

import streamlit as st


def apply_theme() -> None:
    st.html("""<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');
    :root { --cyan:#5de4d3; --muted:#91a3b8; }
    .stApp { background: #090f1b; }
    h1,h2,h3 { font-family:'Space Grotesk',sans-serif; letter-spacing:-.035em; }
    html,body,.stApp { font-family:'DM Sans',sans-serif; }
    .block-container { padding-top:4.5rem; padding-bottom:3rem; max-width:1500px; }
    [data-testid='stSidebar'] { background:#0e1727; border-right:1px solid #213047; }
    [data-testid='stMetric'] { background:#111d2e; padding:18px; border:1px solid #25354b; border-radius:10px; min-height:140px; }
    [data-testid='stMetricLabel'] { color:#9caec2; }
    [data-testid='stMetricValue'] { font-family:'Space Grotesk',sans-serif; }
    div[data-testid='stVerticalBlockBorderWrapper'] { border-color:#26364b; }
    .eyebrow { color:#5de4d3; font-size:11px; letter-spacing:.2em; font-weight:700; margin-bottom:14px; }
    .hero { padding:24px 0 28px; border-bottom:1px solid #26364b; margin-bottom:24px; }
    .hero h1 { font-size:clamp(38px,5vw,72px); line-height:1.03; max-width:950px; margin:12px 0 20px; }
    .hero p { color:#a3b4c8; font-size:17px; max-width:700px; line-height:1.7; }
    .accent { color:#5de4d3; }
    .brand { font-family:'Space Grotesk',sans-serif; font-size:20px; font-weight:700; line-height:1.15; }
    .smallcaps { font-size:10px; letter-spacing:.17em; color:#8296af; margin:10px 0 25px; }
    .event { padding:15px 0; border-bottom:1px solid #26364b; }
    .event time { font-family:monospace; font-size:11px; color:#5de4d3; }
    .event h4 { margin:6px 0; font-size:15px; }
    .event p { margin:0; color:#a3b4c8; font-size:13px; }
    .stamp { display:inline-block; padding:5px 9px; border:1px solid #34475e; color:#afbed0; border-radius:4px; font-size:10px; letter-spacing:.12em; }
    .footnote { color:#8194ad; font-size:12px; margin-top:30px; }
    </style>""")
