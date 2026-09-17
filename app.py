import os
import io
import pandas as pd
import streamlit as st
from PIL import Image
from google import genai
from google.genai import types

st.set_page_config(
    page_title="DSB GradeScript",
    page_icon="🎓",
    layout="wide"
)

# Daniels School of Business Visual Styling
st.markdown("""
    <style>
    :root {
        --purdue-gold: #C28E0E;
        --purdue-black: #000000;
    }
    .main-header {
        border-bottom: 3px solid #C28E0E;
        padding-bottom: 8px;
        margin-bottom: 20px;
    }
    .stButton>button {
        background-color: #000000;
        color: #C28E0E;
        border: 2px solid #C28E0E;
        font-weight: 600;
        border-radius: 4px;
        padding: 0.5rem 1rem;
    }
    .stButton>button:hover {
        background-color: #C28E0E;
        color: #000000;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------- ACCESS CONTROL GATE -----------------
def check_password():
    expected_password = st.secrets.get("APP_PASSWORD", "")
    if not expected_password:
        return True

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.markdown("<h2 class='main-header'>Daniels School of Business | DSB GradeScript</h2>", unsafe_allow_html=True)
    st.info("🔒 Restricted to Daniels School of Business faculty and authorized graders.")
    
    pwd_input = st.text_input("Enter Access Password", type="password")
    if st.button("Log In"):
        if pwd_input == expected_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False

if not check_password():
    st.stop()
# --------------------------------------------------------

# Initialize Session Logs & Answer Key Storage
if "grading_log" not in st.session_state:
    st.session_state.grading_log = []
if "pinned_key_data" not in st.session_state:
    st.session_state.pinned_key_data = None
if "pinned_key_name" not in st.session_state:
    st.session_state.pinned_key_name = ""
if "pinned_key_type" not in st.session_state:
    st.session_state.pinned_key_type = ""

# Header
st.markdown("<h1 class='main-header'>DSB GradeScript</h1>", unsafe_allow_html=True)
st.caption("Daniels School of Business | Multi-Page Handwritten Exam Evaluation Engine")

MODEL_NAME = "gemini-3.6-flash"
api_key = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", ""))

if not api_key:
    st.error("Configuration Error: GEMINI_API_KEY is not defined in Streamlit Secrets.")
    st.stop()

# Sidebar: Controls, Flags & Audit Log Export
with st.sidebar:
    st.subheader("Grading Controls")
    strictness = st.selectbox(
        "Scoring Strictness",
        [
            "Strict (Penalize missing account/tax labels & omitted units)",
            "Standard (Deduct for numerical error; minor deduction for label format)",
            "Lenient (Focus primarily on mathematical accuracy)"
        ]
    )
    
    st.divider()
    pin_prompts = st.checkbox(
        "📌 Pin Question, Rubric & Key", 
        value=True, 
        help="Retains the text rubric, prompt, and uploaded answer key across multiple student evaluations."
    )
    flag_for_review = st.checkbox(
        "🚩 Flag for Faculty Review", 
        value=False, 
        help="Marks this submission in the session audit log for ambiguous handwriting or edge cases."
    )
    
    st.divider()
    st.subheader("Session Audit Log")
    if st.session_state.grading_log:
        df_log = pd.DataFrame(st.session_state.grading_log)
        st.dataframe(df_log[["Student ID", "Final Score", "Flagged"]], hide_index=True)
        csv_data = df_log.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Session CSV",
            data=csv_data,
            file_name="dsb_gradescript_session_log.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.caption("No submissions recorded in this session yet.")

    st.divider()
    if st.button("Log Out"):
        st.session_state.authenticated = False
        st.rerun()

# Layout: Rubric/Answer Key vs. Student Submission
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. Problem, Rubric & Master Key")
    
    default_prompt = st.session_state.get("saved_prompt", "") if pin_prompts else ""
    default_rubric = st.session_state.get("saved_rubric", "") if pin_prompts else ""

    exam_prompt = st.text_area(
        "Exam Problem Statement",
        value=default_prompt,
        height=110,
        placeholder="Enter problem context, given facts, or starting trial balance..."
    )
    rubric_text = st.text_area(
        "Itemized Rubric / Deduction Rules",
        value=default_rubric,
        height=180,
        placeholder="""Define point allocation and deduction rules:
- Part A (4 pts): 2 pts for depreciation base, 2 pts for expense calculation.
- Part B (4 pts): Correct journal entry accounts & labels (Debit/Credit).
- Part C (2 pts): Net tax impact.
- Deduction: -1 pt for missing labels/units even if numbers match."""
    )

    if pin_prompts:
        st.session_state["saved_prompt"] = exam_prompt
        st.session_state["saved_rubric"] = rubric_text

    st.markdown("##### Upload Official Answer Key (Optional)")
    
    # Check if a key is already pinned in the session
    if st.session_state.pinned_key_data is not None:
        st.success(f"📌 **Pinned Key Active:** `{st.session_state.pinned_key_name}`")
        if st.button("Clear Pinned Answer Key"):
            st.session_state.pinned_key_data = None
            st.session_state.pinned_key_name = ""
            st.session_state.pinned_key_type = ""
            st.rerun()
    else:
        key_file = st.file_uploader(
            "Upload Master Key (PDF or Image)", 
            type=["pdf", "png", "jpg", "jpeg"],
            key="key_uploader"
        )
        if key_file:
            key_bytes = key_file.read()
            st.session_state.pinned_key_data = key_bytes
            st.session_state.pinned_key_name = key_file.name
            st.session_state.pinned_key_type = "pdf" if key_file.name.lower().endswith(".pdf") else "image"
            st.success(f"Loaded: {key_file.name}")
            st.rerun()

with col2:
    st.subheader("2. Multi-Page Student Submission")
    student_id = st.text_input("Student Identifier", placeholder="e.g., Student 104 or Purdue Username")
    upload_type = st.radio("Submission Format", ["Multiple Image Files (JPG/PNG)", "Single Multi-Page PDF"], horizontal=True)
    
    uploaded_pages = []
    
    if upload_type == "Multiple Image Files (JPG/PNG)":
        files = st.file_uploader(
            "Upload pages in chronological order",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True
        )
        if files:
            for file in files:
                img = Image.open(file)
                uploaded_pages.append(img)
            st.info(f"{len(uploaded_pages)} page(s) loaded.")
    else:
        pdf_file = st.file_uploader("Upload Student PDF", type=["pdf"])
        if pdf_file:
            pdf_bytes = pdf_file.read()
            uploaded_pages = [
                types.Part.from_bytes(
                    data=pdf_bytes,
                    mime_type="application/pdf"
                )
            ]
            st.info("Multi-page PDF loaded and prepared for vision evaluation.")

    if uploaded_pages and upload_type == "Multiple Image Files (JPG/PNG)":
        with st.expander("Preview Uploaded Student Pages", expanded=False):
            cols = st.columns(min(len(uploaded_pages), 3))
            for i, img in enumerate(uploaded_pages):
                cols[i % 3].image(img, caption=f"Page {i+1}", use_container_width=True)

st.divider()

# Evaluation Run
if st.button("Evaluate Multi-Page Submission", use_container_width=True):
    if not rubric_text and not st.session_state.pinned_key_data:
        st.warning("Please provide either an itemized rubric or an uploaded answer key.")
    elif not uploaded_pages:
        st.warning("Please upload student work (PDF or page images).")
    else:
        with st.spinner("Analyzing multi-page handwriting, comparing against master key, and tallying points..."):
            client = genai.Client(api_key=api_key)

            system_instruction = f"""
            You are DSB GradeScript, an objective, rigorous exam evaluator for the Daniels School of Business.
            You are evaluating a multi-page handwritten student submission.

            Grading Policy:
            - Strictness Level: {strictness}
            - Student ID: {student_id if student_id else 'Unspecified'}
            
            Key Directives:
            1. Track work across multiple pages sequentially. Work may begin on Page 1 and conclude on subsequent pages.
            2. Fully transcribe the student's handwritten calculations, steps, and schedules.
            3. Compare against the provided itemized rubric AND any uploaded Master Answer Key document.
            4. Check both numerical accuracy AND required formatting (e.g., account titles, debit/credit placements, tax schedules).
            5. If labels or units are absent where required by the rubric or key, apply deductions strictly.
            6. Provide an itemized breakdown followed by a clean summary formatted for quick LMS pasting.
            """

            content_payload = []

            # 1. Master Answer Key (if uploaded/pinned)
            if st.session_state.pinned_key_data is not None:
                content_payload.append("=== OFFICIAL MASTER ANSWER KEY DOCUMENT ===")
                if st.session_state.pinned_key_type == "pdf":
                    content_payload.append(
                        types.Part.from_bytes(
                            data=st.session_state.pinned_key_data,
                            mime_type="application/pdf"
                        )
                    )
                else:
                    key_img = Image.open(io.BytesIO(st.session_state.pinned_key_data))
                    content_payload.append(key_img)

            # 2. Student Submission
            content_payload.append("=== STUDENT SUBMISSION ===")
            for page in uploaded_pages:
                content_payload.append(page)

            # 3. Problem Prompt & Rubric Instructions
            content_payload.append(f"""
            === PROBLEM STATEMENT ===
            {exam_prompt}

            === ITEMIZED RUBRIC & CRITERIA ===
            {rubric_text}

            Please output your evaluation strictly using this layout:
            ### TRANSCRIPTION
            [Brief page-by-page extraction of student work]

            ### SCORECARD
            | Component | Points Possible | Points Earned | Deduction Details |
            |---|---|---|---|
            [Populate rows]

            ### SUMMARY FEEDBACK
            **Total Score:** [Earned] / [Possible]
            **Feedback:** [Concise, direct feedback highlighting arithmetic slips vs. terminology/label omissions]
            """)

            try:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=content_payload,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.1
                    )
                )

                eval_text = response.text
                st.session_state["latest_eval"] = eval_text
                st.session_state["current_student"] = student_id if student_id else "Unspecified"

            except Exception as e:
                st.error(f"Evaluation error: {str(e)}")

# Display Evaluation & Review Actions
if "latest_eval" in st.session_state:
    st.subheader(f"Evaluation Report: {st.session_state.get('current_student', 'Student')}")
    st.markdown(st.session_state["latest_eval"])
    
    st.divider()
    st.subheader("Grader Actions & LMS Feedback Bundle")
    
    feedback_bundle = st.text_area(
        "Copyable LMS Feedback Block",
        value=st.session_state["latest_eval"],
        height=180
    )
    
    action_col1, action_col2 = st.columns([1, 1])
    with action_col1:
        final_score_input = st.text_input("Confirm Final Score (e.g., 8.5/10)", key="final_score_record")
    with action_col2:
        st.write("")
        st.write("")
        if st.button("Log to Session Audit"):
            st.session_state.grading_log.append({
                "Student ID": st.session_state.get("current_student", "Unspecified"),
                "Final Score": final_score_input,
                "Flagged": "YES" if flag_for_review else "NO",
                "Feedback": feedback_bundle
            })
            st.success(f"Recorded evaluation for {st.session_state.get('current_student', 'Student')}!")
            st.rerun()
