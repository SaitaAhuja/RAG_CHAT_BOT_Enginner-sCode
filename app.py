"""app.py - minimal chat screen. All logic is in chatbot.py. Run: venv/Scripts/streamlit.exe run app.py"""
import streamlit as st

from chatbot import answer
from prompts import BOT_NAME

st.set_page_config(page_title=BOT_NAME)
st.title(f"{BOT_NAME} - NED Prospectus 2025")

if "messages" not in st.session_state:
    st.session_state.messages = []   # list of (role, text, citations)

for role, text, cites in st.session_state.messages:
    with st.chat_message("user" if role == "User" else "assistant"):
        st.write(text)
        if cites:
            st.caption("Pages searched: " + ", ".join(cites))

question = st.chat_input("Ask about the prospectus (English / Roman Urdu / Urdu)")
if question:
    history = [(r, t) for r, t, _ in st.session_state.messages]
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Searching the prospectus..."):
            text, cites = answer(question, history)
        st.write(text)
        if cites:
            st.caption("Pages searched: " + ", ".join(cites))
    st.session_state.messages.append(("User", question, []))
    st.session_state.messages.append(("Bot", text, cites))
