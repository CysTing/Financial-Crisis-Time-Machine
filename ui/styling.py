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
    [data-testid='stMetricValue'] { font-family:'Space Grotesk',sans-serif; font-size:32px; }
    button[data-testid='stBaseButton-primary'],button[data-testid='stBaseButton-primary'] p { color:#072321; font-weight:600; }
    div[data-testid='stVerticalBlockBorderWrapper'] { border-color:#26364b; }
    /* Offer labeled app exports instead of the grid's raw, unlabeled shortcut. */
    button[data-testid='stBaseButton-elementToolbar'][aria-label='Download as CSV'] { display:none; }
    .eyebrow { color:#5de4d3; font-size:11px; letter-spacing:.2em; font-weight:700; margin-bottom:14px; }
    .hero { padding:24px 0 28px; border-bottom:1px solid #26364b; margin-bottom:24px; }
    .hero h1 { font-size:clamp(38px,5vw,72px); line-height:1.03; max-width:950px; margin:12px 0 20px; }
    .hero p { color:#a3b4c8; font-size:17px; max-width:700px; line-height:1.7; }
    .hero-grid { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(300px,1fr); align-items:center; gap:48px; }
    .hero-grid h1 { font-size:clamp(38px,4.4vw,64px); }
    .hero-break { display:none; }
    .engine-trace { background:linear-gradient(145deg,#132738,#101b2d); border:1px solid #315361; border-radius:14px; padding:26px; }
    .engine-trace .eyebrow { font-size:10px; letter-spacing:.15em; }
    .trace-row { position:relative; display:flex; align-items:flex-start; gap:16px; padding:16px 0; }
    .trace-row:not(:nth-last-child(2))::after { content:''; width:1px; background:#39616a; position:absolute; left:17px; top:49px; bottom:-14px; }
    .trace-node { flex:0 0 35px; height:35px; display:flex; align-items:center; justify-content:center; border:1px solid #4b8585; border-radius:50%; font:11px monospace; color:#79e2d3; background:#142e3b; }
    .trace-row b { font-size:14px; font-weight:600; }
    .engine-trace .trace-row p { margin:5px 0 0; font-size:12px; line-height:1.6; color:#a2b8ca; }
    .trace-footer { border-top:1px solid #2d485a; padding-top:16px; margin-top:8px; font-size:10px; line-height:1.8; color:#92acbf; letter-spacing:.03em; }
    .st-key-showcase_entry { border-color:#39595f; background:#101f2c; }
    .accent { color:#5de4d3; }
    .brand { font-family:'Space Grotesk',sans-serif; font-size:20px; font-weight:700; line-height:1.3; display:block; padding:0; margin:0 0 16px; }
    .smallcaps { display:block; font-size:10px; line-height:1.6; letter-spacing:.17em; color:#8296af; margin:0 0 25px; }
    .event { padding:15px 0; border-bottom:1px solid #26364b; }
    .event time { font-family:monospace; font-size:11px; color:#5de4d3; }
    .event h4 { margin:6px 0; font-size:15px; }
    .event p { margin:0; color:#a3b4c8; font-size:13px; }
    .stamp { display:inline-block; padding:5px 9px; border:1px solid #34475e; color:#afbed0; border-radius:4px; font-size:10px; letter-spacing:.12em; }
    .mission-anchor { display:inline-block; margin-top:18px; padding:10px 16px; border:1px solid #447a7b; border-radius:6px; color:#79e2d3; font-size:13px; text-decoration:none; }
    .mission-anchor:hover { background:#152c38; color:#b3fff2; }
    .stApp a.mission-anchor { color:#79e2d3; text-decoration:none; }
    .footnote { color:#8194ad; font-size:12px; margin-top:30px; }
    @media(max-width:1100px){
      .hero-grid { gap:24px; grid-template-columns:minmax(0,1.2fr) minmax(270px,1fr); }
      .hero-break { display:block; }
      .engine-trace { padding:20px; }
    }
    @media(max-width:760px){
      .hero-grid { grid-template-columns:1fr; gap:24px; }
      .hero-break { display:none; }
      .engine-trace { display:none; }
    }
    @media(max-width:640px){
      [data-testid='stMetric'] { min-height:108px; padding:12px; }
      [data-testid='stMetricValue'] { font-size:clamp(20px,5.5vw,28px); }
      [data-testid='stMetricLabel'] { font-size:12px; }
      [data-testid='stHorizontalBlock']:has([data-testid='stMetric']) { flex-wrap:wrap; gap:.5rem; }
      [data-testid='stColumn']:has([data-testid='stMetric']) { min-width:calc(50% - .5rem)!important; flex:1 1 calc(50% - .5rem)!important; }
      .hero { padding-top:8px; }
      .hero h1 { font-size:42px; }
      .hero-grid { gap:20px; }
      .engine-trace { padding:20px; }
    }
    </style>""")
