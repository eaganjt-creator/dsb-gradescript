import os
import io
import json
import re
import time
import base64
import random
import threading
from datetime import datetime
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
import qrcode
from google import genai
from google.genai import types

st.set_page_config(
    page_title="DSB GradeScript",
    page_icon="🎓",
    layout="wide"
)

# ----------------- CONTAINER KEEP-ALIVE DAEMON -----------------
@st.cache_resource
def start_keep_alive_daemon(app_url: str = "https://dsb-gradescript.streamlit.app"):
    """
    Spawns a persistent background daemon thread that pings the application endpoint
    every 12 minutes, preventing Streamlit Community Cloud from putting the container to sleep.
    """
    def ping_worker():
        while True:
            time.sleep(720)  # Ping every 12 minutes
            try:
                requests.head(app_url, timeout=10)
            except Exception:
                pass

    worker_thread = threading.Thread(target=ping_worker, daemon=True)
    worker_thread.start()
    return True

start_keep_alive_daemon()
# ---------------------------------------------------------------

# Active Browser WebSocket Keep-Alive (Prevents Idle Disconnects During Active Grading)
components.html(
    """
    <script>
    setInterval(function() {
        window.dispatchEvent(new Event('resize'));
    }, 45000);
    </script>
    """,
    height=0,
    width=0,
)

# Daniels School of Business Styling & Mobile Button Enlargement
st.markdown("""
    <style>
    :root {
        --purdue-gold: #C28E0E;
        --purdue-black: #000000;
        --purdue-gray: #373A36;
    }
    .main-header {
        border-bottom: 3px solid #C28E0E;
        padding-bottom: 8px;
        margin-bottom: 12px;
    }
    .stButton>button {
        background-color: #000000;
        color: #C28E0E;
        border: 2px solid #C28E0E;
        font-weight: 600;
        border-radius: 6px;
        padding: 0.6rem 1.2rem;
    }
    .stButton>button:hover {
        background-color: #C28E0E;
        color: #000000;
    }
    @media (max-width: 768px) {
        .stButton>button {
            width: 100% !important;
            min-height: 56px !important;
            font-size: 1.15rem !important;
            margin-top: 8px !important;
            margin-bottom: 8px !important;
            border-radius: 8px !important;
        }
        .stTextInput input {
            font-size: 1.1rem !important;
            min-height: 48px !important;
        }
    }
    </style>
""", unsafe_allow_html=True)

# ----------------- IN-MEMORY DEVICE RELAY -----------------
@st.cache_resource
def get_shared_sessions():
    return {}

shared_sessions = get_shared_sessions()

def optimize_image(image: Image.Image, max_dim: int = 1600) -> Image.Image:
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    width, height = image.size
    if max(width, height) > max_dim:
        scale = max_dim / float(max(width, height))
        new_size = (int(width * scale), int(height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    return image

def render_pdf_guide():
    pdf_path = os.path.join(os.path.dirname(__file__), "USER_GUIDE.pdf")
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        st.download_button(
            label="📥 Download Official User Guide (PDF)",
            data=pdf_bytes,
            file_name="DSB_GradeScript_User_Guide.pdf",
            mime="application/pdf",
            use_container_width=True
        )

        st.divider()

        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
        pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="650" type="application/pdf" style="border-radius: 6px; border: 1px solid #ccc;"></iframe>'
        st.markdown(pdf_display, unsafe_allow_html=True)
    else:
        st.warning("`USER_GUIDE.pdf` was not found in the repository root. Please upload it to your GitHub repo.")

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

MODEL_NAME = "gemini-3.8-flash"
api_key = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", ""))

if not api_key:
    st.error("Configuration Error: GEMINI_API_KEY is not defined in Streamlit Secrets.")
    st.stop()

# Robust call wrapper with exponential backoff & jitter for 503 / 429 spikes
def generate_content_with_retry(client, model_name, contents, config, max_retries=4, base_delay=2.0, status_container=None):
    for attempt in range(1, max_retries + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config
            )
        except Exception as e:
            err_str = str(e).upper()
            is_transient = any(code in err_str for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "HIGH DEMAND"])
            
            if is_transient and attempt < max_retries:
                sleep_duration = (base_delay ** attempt) + random.uniform(0.2, 0.8)
                msg = f"⏳ Upstream service load encountered. Retrying in {sleep_duration:.1f}s (Attempt {attempt} of {max_retries})..."
                if status_container:
                    status_container.info(msg)
                time.sleep(sleep_duration)
            else:
                raise e

# Query parameter handling for automatic phone pairing
query_mode = st.query_params.get("mode", "")
query_session = st.query_params.get("session", "")
default_mode_index = 1 if query_mode == "mobile" else 0

device_mode = st.sidebar.radio(
    "Device Mode",
    ["💻 Laptop (Cockpit & Evaluator)", "📱 Mobile (Scanner Companion)"],
    index=default_mode_index
)

# ==============================================================================
# 📱 MOBILE SCANNER COMPANION MODE
# ==============================================================================
if device_mode == "📱 Mobile (Scanner Companion)":
    st.markdown("<h2 class='main-header'>📱 Mobile Scanner Companion</h2>", unsafe_allow_html=True)
    st.caption("Snap exam pages and beam them directly to your laptop cockpit.")

    room_code = st.text_input(
        "Session PIN", 
        value=query_session, 
        max_chars=4, 
        placeholder="e.g., 1042"
    ).strip()
    
    student_id = st.text_input("Student Identifier (Optional)", placeholder="Leave blank if written on exam")

    if "mobile_pages" not in st.session_state:
        st.session_state.mobile_pages = []

    st.markdown("#### Capture Method")
    cam_type = st.radio(
        "Camera Type",
        ["📸 Native Phone Camera (Enables Hardware Flash/Torch)", "🌐 In-Browser Webcam"],
        help="Use Native Phone Camera to turn on your phone's LED flash and eliminate shadows on the paper."
    )

    if "Native Phone Camera" in cam_type:
        st.caption("💡 *Tip: Tapping below opens your phone's native camera. Enable flash in camera options to kill paper shadows.*")
        uploaded_shot = st.file_uploader("Snap / Upload Page", type=["jpg", "jpeg", "png"], key="native_cam")
        if uploaded_shot:
            if st.button("➕ Confirm & Add This Page", use_container_width=True):
                img = Image.open(uploaded_shot)
                st.session_state.mobile_pages.append(optimize_image(img))
                st.success(f"Page {len(st.session_state.mobile_pages)} saved!")
                st.rerun()
    else:
        cam_shot = st.camera_input("Snap Exam Page")
        if cam_shot:
            img = Image.open(cam_shot)
            img = optimize_image(img)
            if st.button("➕ Add This Page", use_container_width=True):
                st.session_state.mobile_pages.append(img)
                st.success(f"Page {len(st.session_state.mobile_pages)} added!")
                st.rerun()

    if st.session_state.mobile_pages:
        st.info(f"📄 {len(st.session_state.mobile_pages)} page(s) ready for this student.")
        
        if st.button("🚀 Beam to Laptop Cockpit", use_container_width=True):
            if not room_code:
                st.error("Please enter or scan the 4-digit code shown on your laptop.")
            else:
                shared_sessions[room_code] = {
                    "student_id": student_id,
                    "pages": list(st.session_state.mobile_pages),
                    "timestamp": time.time(),
                    "processed": False
                }
                st.session_state.mobile_pages = []
                st.success("Transmitted! Head to your laptop cockpit to evaluate.")
                st.rerun()

        if st.button("🗑️ Clear Pages", use_container_width=True):
            st.session_state.mobile_pages = []
            st.rerun()

# ==============================================================================
# 💻 LAPTOP COCKPIT MODE
# ==============================================================================
else:
    head_col1, head_col2 = st.columns([3, 1])
    with head_col1:
        st.markdown("<h1 class='main-header'>DSB GradeScript</h1>", unsafe_allow_html=True)
        st.caption("Daniels School of Business | Multi-Page Handwritten Exam Cockpit")
    with head_col2:
        with st.popover("📖 User Guide (PDF)", use_container_width=True):
            render_pdf_guide()

    if "grading_log" not in st.session_state:
        st.session_state.grading_log = []
    if "session_code" not in st.session_state:
        st.session_state.session_code = str(random.randint(1000, 9999))
    if "pinned_key_data" not in st.session_state:
        st.session_state.pinned_key_data = None
        st.session_state.pinned_key_name = ""
        st.session_state.pinned_key_type = ""

    # Sidebar: Controls, QR Pairing & Gradebook
    with st.sidebar:
        st.subheader("Mobile Link")
        st.metric(label="Pairing PIN", value=st.session_state.session_code)
        
        base_app_url = "https://dsb-gradescript.streamlit.app"
        pair_url = f"{base_app_url}/?mode=mobile&session={st.session_state.session_code}"
        
        qr = qrcode.QRCode(box_size=3, border=1)
        qr.add_data(pair_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        st.image(buf.getvalue(), caption="Scan with Phone Camera", width=140)

        st.divider()
        strictness = st.selectbox(
            "Scoring Strictness",
            [
                "Strict (Penalize missing intermediate steps, labels & units)",
                "Standard (Deduct for numerical error; minor deduction for formatting/units)",
                "Lenient (Focus primarily on mathematical/final answer accuracy)"
            ]
        )
        flag_for_review = st.checkbox("🚩 Flag Current for Faculty Review", value=False)
        
        st.divider()
        st.subheader("Session Exports")
        if st.session_state.grading_log:
            df_log = pd.DataFrame(st.session_state.grading_log)
            st.dataframe(df_log[["OrgDefinedId", "Score", "Flagged"]], hide_index=True)
            
            csv_data = df_log[["OrgDefinedId", "Score", "Flagged", "Timestamp"]].to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 1. Gradebook CSV (LMS Import)",
                data=csv_data,
                file_name=f"dsb_grades_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )
            
            full_report_text = f"# DSB GradeScript - Session Evaluation Dossier\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            for row in st.session_state.grading_log:
                full_report_text += f"## Student: {row['OrgDefinedId']}\n"
                full_report_text += f"- **Score:** {row['Score']}\n"
                full_report_text += f"- **Flagged:** {row['Flagged']}\n"
                full_report_text += f"- **Timestamp:** {row['Timestamp']}\n\n"
                full_report_text += "### Evaluation Details\n"
                full_report_text += f"{row['Full_Feedback']}\n\n"
                full_report_text += "---\n\n"

            st.download_button(
                label="📄 2. Complete Dossier (All Students)",
                data=full_report_text.encode("utf-8"),
                file_name=f"dsb_full_dossier_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
                mime="text/plain",
                use_container_width=True
            )
        else:
            st.caption("No submissions recorded yet.")

        st.divider()
        st.caption("DSB GradeScript v1.2.0 | Daniels School of Business")
        if st.button("Log Out"):
            st.session_state.authenticated = False
            st.rerun()

    # Cockpit Body: Rubric Setup vs. Live Ingestion Queue
    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1. Rubric & Master Key")
        
        st.info("💡 **Tip:** If your Master Answer Key PDF already details questions, answers, point values, and deduction notes, you can upload it below and leave the text boxes blank.")

        st.markdown("##### Official Master Answer Key (PDF or Image)")
        if st.session_state.pinned_key_data is not None:
            st.success(f"📌 **Pinned Master Key Active:** `{st.session_state.pinned_key_name}`")
            if st.button("Clear / Replace Master Key"):
                st.session_state.pinned_key_data = None
                st.session_state.pinned_key_name = ""
                st.session_state.pinned_key_type = ""
                st.rerun()
        else:
            key_file = st.file_uploader(
                "Upload Master Solution Key", 
                type=["pdf", "png", "jpg", "jpeg"],
                help="Upload an annotated exam key containing questions, solutions, point distributions, or grading notes."
            )
            if key_file:
                st.session_state.pinned_key_data = key_file.getvalue()
                st.session_state.pinned_key_name = key_file.name
                st.session_state.pinned_key_type = "pdf" if key_file.name.lower().endswith(".pdf") else "image"
                st.rerun()

        st.divider()

        exam_prompt = st.text_area(
            "Exam Problem Statement (Optional if included in Master Key)",
            value=st.session_state.get("saved_prompt", ""),
            height=90,
            placeholder="Optional: Enter problem prompt or scenario context if not using an annotated key..."
        )
        rubric_text = st.text_area(
            "Itemized Rubric / Deduction Rules (Optional if included in Master Key)",
            value=st.session_state.get("saved_rubric", ""),
            height=130,
            placeholder="Optional: Define point distributions and penalty rules if not using an annotated key..."
        )
        st.session_state["saved_prompt"] = exam_prompt
        st.session_state["saved_rubric"] = rubric_text

    with col2:
        st.subheader("2. Incoming Submissions Queue")
        
        poll_col1, poll_col2 = st.columns([1, 1])
        with poll_col1:
            if st.button("🔄 Refresh Incoming Queue", use_container_width=True):
                st.rerun()

        curr_code = st.session_state.session_code
        mobile_data = shared_sessions.get(curr_code)

        pages_to_grade = []
        incoming_student = ""

        if mobile_data and not mobile_data.get("processed", False):
            incoming_student = mobile_data.get("student_id", "")
            pages_to_grade = mobile_data.get("pages", [])
            st.success(f"📥 Received {len(pages_to_grade)} page(s) from phone! (Student: {incoming_student or 'Detecting...'})")
            
            p_cols = st.columns(min(len(pages_to_grade), 3))
            for i, p_img in enumerate(pages_to_grade):
                p_cols[i % 3].image(p_img, caption=f"Page {i+1}", use_container_width=True)

            run_eval = st.button("🚀 Evaluate Submission", use_container_width=True)
        else:
            st.info(f"Waiting for mobile companion (PIN: **{curr_code}**)...")
            st.caption("Or upload a local PDF/Images directly below:")
            manual_file = st.file_uploader("Manual File Upload", type=["pdf", "png", "jpg", "jpeg"], key="manual_up")
            run_eval = False
            if manual_file:
                if manual_file.name.lower().endswith(".pdf"):
                    pdf_bytes = manual_file.getvalue()
                    pages_to_grade = [types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")]
                else:
                    pages_to_grade = [optimize_image(Image.open(manual_file))]
                run_eval = st.button("Evaluate Manual Upload", use_container_width=True)

    # Evaluation Execution Pipeline
    if run_eval and pages_to_grade:
        if not rubric_text and not st.session_state.pinned_key_data:
            st.warning("Please upload a Master Solution Key or enter an itemized rubric.")
        else:
            status_box = st.empty()
            with st.spinner("Deciphering handwriting, applying rubric, and standardizing evaluation..."):
                client = genai.Client(api_key=api_key)

                system_instruction = f"""
                You are DSB GradeScript, an objective, rigorous exam evaluator for the Daniels School of Business.
                Evaluate this multi-page handwritten student exam.
                
                Grading Policy:
                - Strictness Level: {strictness}
                - Key Source: Rely on the provided Master Answer Key Document (including any explicit point allocations, totals, and carried-forward error rules marked on the pages) and/or the text rubric.
                
                You MUST format your output strictly and consistently using the following exact headings:
                
                ### 1. STUDENT WORK SUMMARY
                [Concise transcription of handwritten calculations, intermediate steps, and reasoning]
                
                ### 2. ITEMIZED SCORECARD
                | Component | Points Possible | Points Earned | Deduction Details |
                |---|---|---|---|
                [Row for each graded component]
                
                ### 3. TOTAL SCORE
                **Score:** [Points Earned] / [Points Possible]
                
                ### 4. INSTRUCTOR FEEDBACK
                [Clear feedback distinguishing mathematical errors vs. missing labels, units, or terminology]

                Finally, append a strict JSON block at the very end:
                ```json
                {{
                  "student_id": "Extracted ID or 'Unspecified'",
                  "points_earned": 8.5,
                  "points_possible": 10.0,
                  "feedback_summary": "1-2 sentence overview of slips or missing labels"
                }}
                ```
                """

                content_payload = []
                if st.session_state.pinned_key_data is not None:
                    content_payload.append("=== MASTER ANSWER KEY DOCUMENT ===")
                    if st.session_state.pinned_key_type == "pdf":
                        content_payload.append(types.Part.from_bytes(data=st.session_state.pinned_key_data, mime_type="application/pdf"))
                    else:
                        content_payload.append(optimize_image(Image.open(io.BytesIO(st.session_state.pinned_key_data))))

                content_payload.append("=== STUDENT SUBMISSION ===")
                for page in pages_to_grade:
                    content_payload.append(page)

                if exam_prompt.strip() or rubric_text.strip():
                    content_payload.append(f"""
                    === PROBLEM STATEMENT ===
                    {exam_prompt if exam_prompt.strip() else 'Refer directly to Master Answer Key Document'}

                    === ITEMIZED RUBRIC & CRITERIA ===
                    {rubric_text if rubric_text.strip() else 'Refer directly to Master Answer Key Document'}
                    """)
                else:
                    content_payload.append("Note: Evaluate purely against the questions, answers, point totals, and grading notes in the Master Answer Key Document.")

                try:
                    response = generate_content_with_retry(
                        client=client,
                        model_name=MODEL_NAME,
                        contents=content_payload,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.1,
                            max_output_tokens=8192
                        ),
                        max_retries=4,
                        base_delay=2.0,
                        status_container=status_box
                    )
                    status_box.empty()

                    eval_text = response.text
                    extracted_id = incoming_student if incoming_student else "Unspecified"
                    score_val = ""
                    
                    json_match = re.search(r"```json\s*(\{.*?\})\s*```", eval_text, re.DOTALL)
                    if json_match:
                        try:
                            parsed = json.loads(json_match.group(1))
                            if not incoming_student and parsed.get("student_id") not in ("Unspecified", ""):
                                extracted_id = str(parsed.get("student_id"))
                            e = parsed.get("points_earned", "")
                            p = parsed.get("points_possible", "")
                            score_val = f"{e}/{p}" if e != "" and p != "" else str(e)
                        except Exception:
                            pass

                    clean_display = re.sub(r"```json\s*\{.*?\}\s*```", "", eval_text, flags=re.DOTALL).strip()

                    st.session_state["latest_eval"] = clean_display
                    st.session_state["current_student"] = extracted_id
                    st.session_state["current_score"] = score_val

                    if curr_code in shared_sessions:
                        shared_sessions[curr_code]["processed"] = True

                except Exception as e:
                    status_box.empty()
                    st.error(f"Evaluation error: {str(e)}")

    # Evaluation Output, Download & Reconciliation
    if "latest_eval" in st.session_state:
        st.divider()
        st.subheader(f"Evaluation: {st.session_state.get('current_student', 'Student')}")
        
        st.markdown(st.session_state["latest_eval"])

        st.markdown("##### Copyable Feedback Block")
        st.text_area(
            "Select all & copy directly into LMS / student notes:",
            value=st.session_state["latest_eval"],
            height=160,
            key="copyable_feedback_area"
        )

        student_file_content = f"# Evaluation Report: {st.session_state.get('current_student', 'Student')}\nScore: {st.session_state.get('current_score', '')}\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{st.session_state['latest_eval']}"
        st.download_button(
            label="📥 Download Single Student Report (.txt)",
            data=student_file_content.encode("utf-8"),
            file_name=f"report_{st.session_state.get('current_student', 'student')}.txt",
            mime="text/plain"
        )

        st.divider()
        st.subheader("Reconcile & Append to Master Gradebook")
        
        rec_col1, rec_col2, rec_col3 = st.columns([1.5, 1.5, 2])
        with rec_col1:
            confirmed_id = st.text_input("Confirm Student ID (OrgDefinedId)", value=st.session_state.get("current_student", ""))
        with rec_col2:
            confirmed_score = st.text_input("Score", value=st.session_state.get("current_score", ""))
        with rec_col3:
            st.write("")
            st.write("")
            if st.button("✅ Append to Gradebook & Next", use_container_width=True):
                st.session_state.grading_log.append({
                    "OrgDefinedId": confirmed_id,
                    "Score": confirmed_score,
                    "Flagged": "YES" if flag_for_review else "NO",
                    "Full_Feedback": st.session_state["latest_eval"],
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                
                if curr_code in shared_sessions:
                    del shared_sessions[curr_code]
                if "latest_eval" in st.session_state:
                    del st.session_state["latest_eval"]
                st.success(f"Logged {confirmed_id}! Ready for next student.")
                st.rerun()
