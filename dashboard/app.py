"""Streamlit dashboard. Run: streamlit run dashboard/app.py"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.config import Settings
from app.db import Database

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AI Code Review Bot", page_icon="🤖", layout="wide")
st.title("🤖 AI Code Review Bot")


@st.cache_resource
def get_db() -> Database:
    return Database(Settings.from_env().database_url)


db = get_db()
stats = db.stats()
runs = pd.DataFrame(db.all_runs())
findings = pd.DataFrame(db.all_findings())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Reviews", stats["runs"])
c2.metric("Findings", stats["findings"])
c3.metric("Comments posted", stats["posted"])
rate = stats["helpful_rate"]
c4.metric("Helpful rate (👍 / votes)", "n/a" if rate is None else f"{rate:.0%}",
          help=f"{stats['thumbs_up']} 👍 · {stats['thumbs_down']} 👎")

if findings.empty:
    st.info("No reviews yet. Run `python -m app.cli review owner/repo 1` or open a PR.")
    st.stop()

left, right = st.columns(2)
with left:
    st.subheader("Findings by severity")
    st.bar_chart(findings["severity"].value_counts())
with right:
    st.subheader("Findings by category")
    st.bar_chart(findings["category"].value_counts())

st.subheader("Feedback per category")
fb = findings.groupby("category")[["thumbs_up", "thumbs_down"]].sum()
st.dataframe(fb, use_container_width=True)

st.subheader("Recent reviews")
st.dataframe(runs[["id", "repo", "pr_number", "head_sha", "status", "model", "prompt_version",
                   "files_reviewed", "created_at", "error"]], use_container_width=True)

st.subheader("All findings")
st.dataframe(findings[["repo", "pr_number", "file", "line", "severity", "category", "message",
                       "confidence", "source", "posted", "thumbs_up", "thumbs_down"]],
             use_container_width=True)
