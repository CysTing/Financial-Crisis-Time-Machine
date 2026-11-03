"""Present computed replay evidence without accessing internal market data."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st


def _usd(value) -> str:
    return "N/A" if value is None else f"USD {value:,.2f}"


def render_integrity(evidence: dict, passport: str, *, key: str, challenge=None,
                     on_challenge=None, challenge_args=()) -> None:
    """Keep the financial task prominent and make its evidence one click away."""
    status=evidence.get("status","unavailable")
    payload=json.loads(passport)
    source="SYNTHETIC DEMO" if "synthetic" in str(payload.get("context",{}).get("source","")).lower() else "HISTORICAL DATA"
    summary=f"{evidence.get('passed',0)}/{evidence.get('total',0)} checks passed" if status=="pass" else "review required" if status=="fail" else "not available"
    with st.expander(f"Replay integrity · {summary}",icon=":material/fact_check:"):
        st.caption(f"LIVE SNAPSHOT CHECKS · {source} · AS OF {str(payload.get('as_of','N/A'))[:10]}")
        if status=="pass":
            st.success("Recorded balances, timing, and data boundaries agree within the stated checks.")
        elif status=="fail":
            st.error("A snapshot check failed. Inspect the details before relying on this result.")
        else:
            st.info("This snapshot does not contain enough valid evidence to run the checks.")
        proof=evidence.get("proof",{})
        st.markdown("**Follow the money · latest recorded close**")
        st.write(f"{_usd(proof.get('cash'))} cash + {_usd(proof.get('positions_value'))} positions = {_usd(proof.get('equity'))} portfolio value")
        if proof.get("previous_equity") is not None:
            st.write(f"{_usd(proof.get('asset_pnl'))} asset P&L − {_usd(proof.get('costs'))} costs = {_usd(proof.get('equity_change'))} equity change")
            residual=proof.get("residual")
            tolerance=proof.get("tolerance")
            if residual is not None and tolerance is not None:
                st.caption(f"P&L residual: USD {residual:.10f} · Allowed rounding tolerance: USD {tolerance:.10f}. Old holdings earn the move into this close; newly executed holdings earn subsequent moves.")
        elif any(row["id"]=="pnl" and row["status"]=="not_applicable" for row in evidence.get("checks",[])):
            st.caption("Initial cash baseline: no elapsed-session P&L to reconcile yet.")
        else:
            st.caption("P&L proof is unavailable. Inspect the check details below.")
        checks=evidence.get("checks",[])
        if checks:
            # Evidence is prose: allow it to wrap rather than truncating it in
            # a fixed-height grid cell, especially on narrow screens.
            columns=st.columns(2)
            for index,row in enumerate(checks):
                color={"pass":"green","fail":"red","not_applicable":"gray"}.get(row["status"],"gray")
                label={"pass":"PASS","fail":"FAIL","not_applicable":"N/A"}.get(row["status"],"N/A")
                with columns[index%2],st.container(border=True):
                    st.markdown(f":{color}-badge[{label}] **{row['label']}**")
                    st.caption(row["detail"])
        st.caption("Checks inspect the supplied snapshot. They do not certify provider accuracy, model suitability, security, or regulatory compliance.")
        if on_challenge is not None:
            st.markdown("**Challenge the checks**")
            st.caption("Introduce a balance mismatch, a future account row, and a P&L mismatch into separate copies. See whether the diagnostics detect them. Your replay stays intact.")
            st.button("Run corruption drill",key=f"{key}_drill",on_click=on_challenge,args=challenge_args,icon=":material/science:")
            if challenge:
                if challenge.get("passed") and challenge.get("original_unchanged"):
                    st.success("All applicable corruptions detected. Original snapshot unchanged.")
                else:
                    st.error("The drill needs review. Inspect the individual outcomes below.")
                st.dataframe(pd.DataFrame([{"Injected fault":row["name"],"Result":"DETECTED" if row["detected"] else "N/A" if row["detected"] is None else "MISSED"} for row in challenge["cases"]]),hide_index=True,width="stretch")
                st.caption("An isolated snapshot-diagnostics exercise, not a security or penetration test.")
        st.markdown("**Run passport · reproducible evidence**")
        st.caption("Source and configuration, as-of boundary, and SHA-256 fingerprints of the revealed datasets. Compare passports from the same run to detect changed inputs or outputs. Hashes are identifiers, not digital signatures or proof of authenticity.")
        st.code(payload.get("payload_fingerprint","Unavailable"),language=None,wrap_lines=True)
        st.download_button("Download run passport JSON",passport,"financial-time-machine-passport.json","application/json",key=key,width="stretch")
