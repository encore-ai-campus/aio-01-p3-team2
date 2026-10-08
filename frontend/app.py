import os

import streamlit as st


st.set_page_config(page_title="Multi-Agent AutoHealing", page_icon="🛠️")
st.title("Multi-Agent AutoHealing")
st.success("Frontend deployment is running.")
st.write("This page was deployed automatically from the `yejin` branch.")

backend_url = os.getenv("BACKEND_URL", "")
if backend_url:
    st.info(f"Backend URL: {backend_url}")
else:
    st.caption("BACKEND_URL is not configured yet.")
