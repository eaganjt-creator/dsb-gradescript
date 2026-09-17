import os
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

# ----------------- ACCESS CONTROL / PASSWORD GATE -----------------
def check_password():
    """Returns True if the user has entered the correct password."""
    expected_password = st.secrets.get("APP_PASSWORD", "")
    
    # If no password is set in Secrets, bypass the gate
    if not expected_password:
        return True

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.markdown("<h2 class='main-header'>Daniels School of Business | DSB GradeScript</h2>", unsafe_allow_html=True)
    st.info("🔒 This tool is restricted to DSB faculty and authorized graders.")
    
    pwd_input = st.text_input("Enter Access Password", type="password")
    if st.button("Log In"):
        if pwd_input == expected_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password. Please contact the department administrator.")
    return False

if not check_password():
    st.stop()
# ------------------------------------------------------------------

# Header
st.markdown("<h1 class='main-header'>DSB GradeScript</h1>", unsafe_allow_html=True)
st.caption("Daniels School of Business | Multi-Page Handwritten Exam Evaluation Engine")

# Read Gemini API Key silently from Secrets
api_key = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", ""))

if not api_key:
    st.error("System configuration error: GEMINI_API_KEY is not set in Streamlit Secrets.")
    st.stop()

# Sidebar: Strictly for Grading Parameters
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
    student_id = st.text_input("Student Identifier (Optional)", placeholder="e.g., Student 104")
    
    st.divider()
    if st.button("Log Out"):
        st.session_state.authenticated = False
        st.rerun()

# Main Interface: Rubric & Multi-Page Ingestion
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. Problem & Grading Rubric")
    exam_prompt = st.text_area(
        "Exam Problem Statement",
        height=140,
        placeholder="Enter the problem statement, context, or starting numbers..."
    )
    rubric_text = st.text_area(
        "Itemized Rubric / Answer Key",
        height=280,
        placeholder="""Define point allocation and deduction rules:
- Part A (4 pts): 2 pts for base, 2 pts for calculation.
- Part B (4 pts): Correct account names & labels (Debit/Credit).
- Part C (2 pts): Net tax/income calculation.
- Deduction: -1 pt for omitted labels or improper terminology."""
    )

with col2:
    st.subheader("2. Multi-Page Student Submission")
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
        with st.expander("Preview Uploaded Pages", expanded=False):
            cols = st.columns(min(len(uploaded_pages), 3))
            for i, img in enumerate(uploaded_pages):
                cols[i % 3].image(img, caption=f"Page {i+1}", use_container_width=True)

st.divider()

# Grade Execution
if st.button("Evaluate Multi-Page Submission", use_container_width=True):
    if not rubric_text:
        st.warning("Please define the grading rubric and answer key.")
    elif not uploaded_pages:
        st.warning("Please upload at least one page or a PDF.")
    else:
        with st.spinner("Analyzing multi-page handwriting, verifying accounts/labels, and tallying points..."):
            client = genai.Client(api_key=api_key)

            system_instruction = f"""
            You are DSB GradeScript, an objective, rigorous exam evaluator for the Daniels School of Business.
            You are evaluating a multi-page handwritten student submission.

            Grading Policy:
            - Strictness Level: {strictness}
            - Student ID / Label: {student_id if student_id else 'Unspecified'}
            
            Key Directives:
            1. Track work across multiple pages sequentially. Work may begin on Page 1 and conclude on subsequent pages.
            2. Fully transcribe the student's handwritten calculations, steps, and schedules.
            3. Check both mathematical values AND structural formatting (e.g., proper account titles, debit/credit placement, tax classifications).
            4. If labels are absent where required by the rubric, apply deductions strictly.
            5. Provide clear, constructive feedback detailing exactly where points were gained or lost.
            """

            content_payload = list(uploaded_pages)
            content_payload.append(f"""
            PROBLEM STATEMENT:
            {exam_prompt}

            OFFICIAL RUBRIC & SOLUTION:
            {rubric_text}

            Please output your evaluation using the following structure:
            1. **Page-by-Page Transcription**: Brief extraction of student steps from each page.
            2. **Itemized Scorecard**: Table showing Component, Points Possible, Points Earned, and Deduction Reason.
            3. **Total Score**: Final Score (Earned / Total).
            4. **Instructor Notes & Feedback**: Specific feedback highlighting arithmetic slips vs. terminology/label omissions.
            """)

            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=content_payload,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.1
                    )
                )

                st.subheader("Evaluation Report")
                st.markdown(response.text)

            except Exception as e:
                st.error(f"Evaluation error: {str(e)}")
