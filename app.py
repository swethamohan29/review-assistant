# app.py
import os
import json
import streamlit as st
from pypdf import PdfReader
import docx
import anthropic

st.set_page_config(
    page_title="Legal Operations - Lease Agreement Review Assistant",
    page_icon="⚖️",
    layout="wide"
)

# ---------------- Styling ----------------

st.markdown("""
<style>
.stApp { background: #f4f6f9; }
.main-title {
    font-size: 30px; font-weight: 800; color: #1a2b4c;
    margin-bottom: 0px;
}
.subtitle { color: #5a6472; font-size: 15px; margin-bottom: 24px; }
.status-clean {
    background: #e6f7ee; border: 1px solid #34c38f; color: #1e7a52;
    padding: 14px 18px; border-radius: 10px; font-weight: 700; font-size: 16px;
}
.status-flagged {
    background: #fdeaea; border: 1px solid #e05353; color: #a12626;
    padding: 14px 18px; border-radius: 10px; font-weight: 700; font-size: 16px;
}
.match-card {
    background: #eefaf1; border-left: 5px solid #2fb872; padding: 12px 16px;
    border-radius: 8px; margin-bottom: 10px;
}
.deviation-card {
    background: #fff0ee; border-left: 5px solid #e4573d; padding: 12px 16px;
    border-radius: 8px; margin-bottom: 10px;
}
.missing-card {
    background: #fff8e6; border-left: 5px solid #e6a917; padding: 12px 16px;
    border-radius: 8px; margin-bottom: 10px;
}
.quote-box {
    background: #ffffff; border: 1px dashed #c9c2b0; border-radius: 6px;
    padding: 8px 12px; margin-top: 6px; font-style: italic; color: #3a3f47;
}
.summary-box {
    background: #eaf1ff; border-left: 5px solid #3f7ce0; padding: 14px 18px;
    border-radius: 8px; margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">⚖️ Legal Operations — Lease Agreement Review Assistant</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Reviews a lease against your company\'s standard positions and flags every deviation, quoted at the source. It does not approve — a human reviewer does.</div>', unsafe_allow_html=True)

# ---------------- Sidebar: standard positions ----------------

st.sidebar.header("📋 Company Standard Positions")

deposit_min = st.sidebar.number_input("Minimum deposit (months of rent)", min_value=0.0, value=1.0, step=0.5)
deposit_max = st.sidebar.number_input("Maximum deposit (months of rent)", min_value=0.0, value=2.0, step=0.5)

notice_min = st.sidebar.number_input("Minimum notice period (days)", min_value=0, value=30, step=5)
notice_max = st.sidebar.number_input("Maximum notice period (days)", min_value=0, value=90, step=5)

required_clauses_text = st.sidebar.text_area(
    "Required clauses (one per line)",
    value="Maintenance responsibility clearly assigned\n"
          "Deposit return timeline specified\n"
          "Rent escalation terms specified\n"
          "Grounds for termination specified"
)

prohibited_terms_text = st.sidebar.text_area(
    "Terms the company never accepts (one per line)",
    value="Non-refundable security deposit\n"
          "Automatic renewal without notice to tenant\n"
          "Unilateral rent increase at landlord's sole discretion\n"
          "Waiver of tenant's statutory legal rights\n"
          "Penalty clauses disproportionate to the breach"
)

required_clauses = [c.strip() for c in required_clauses_text.splitlines() if c.strip()]
prohibited_terms = [t.strip() for t in prohibited_terms_text.splitlines() if t.strip()]

# ---------------- File extraction ----------------

def extract_text_from_pdf(file) -> str:
    reader = PdfReader(file)
    text = ""
    for page in reader.pages:
        content = page.extract_text()
        if content:
            text += content + "\n"
    return text

def extract_text_from_docx(file) -> str:
    document = docx.Document(file)
    return "\n".join(p.text for p in document.paragraphs)

def extract_text(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(uploaded_file)
    elif name.endswith(".docx"):
        return extract_text_from_docx(uploaded_file)
    return uploaded_file.read().decode("utf-8", errors="ignore")

# ---------------- Review engine ----------------

SYSTEM_PROMPT = """You are a legal operations review assistant for a property management company.
You review lease agreements clause by clause against the company's standard positions, provided
to you as: an acceptable deposit range, an acceptable notice-period range, a list of required
clauses, and a list of terms the company never accepts.

Rules you must follow exactly:
1. Every match and every deviation must quote the exact clause text from the agreement that supports it.
   Never paraphrase the quote — copy it as written.
2. If a required protection (e.g. maintenance responsibility, deposit return timeline) is not present
   anywhere in the agreement, report it under "missing" — silence is a finding, not something to skip.
3. If a prohibited term appears in the agreement, it is always a deviation, quoted directly, regardless
   of how it is phrased.
4. If the deposit or notice period falls outside the acceptable range, quote the clause stating the
   figure and explain the deviation in plain terms.
5. Do not approve, endorse, or recommend signing the agreement. You flag and explain; a human decides.
6. If the agreement fully matches the standards with no deviations and no missing protections, the
   overall_status is "Clean" and deviations/missing should be empty arrays.
7. The key_terms_summary must contain the 3 to 4 terms a signer most needs to understand, in plain
   language, regardless of whether they are matches or deviations.

Respond with ONLY a JSON object, no markdown fences, no preamble, in exactly this shape:
{
  "overall_status": "Clean" or "Flagged",
  "matches": [
    {"item": "short label", "quote": "exact clause text", "note": "why this matches the standard"}
  ],
  "deviations": [
    {"item": "short label", "quote": "exact clause text", "standard": "what the standard requires", "deviation": "plain statement of how it deviates"}
  ],
  "missing": [
    {"item": "short label", "explanation": "what protection is absent and why it matters"}
  ],
  "key_terms_summary": ["plain-language point 1", "plain-language point 2", "..."]
}
"""

def build_user_prompt(lease_text: str) -> str:
    return f"""Company standard positions:
- Acceptable security deposit: {deposit_min} to {deposit_max} months of rent
- Acceptable notice period: {notice_min} to {notice_max} days
- Required clauses: {", ".join(required_clauses) if required_clauses else "None specified"}
- Never-accepted terms: {", ".join(prohibited_terms) if prohibited_terms else "None specified"}

Lease agreement text:

{lease_text[:20000]}
"""

def review_lease(lease_text: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(lease_text)}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text").strip()
    cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)

# ---------------- UI: upload + run ----------------

uploaded_file = st.file_uploader("Upload lease agreement", type=["pdf", "docx", "doc"])

if st.button("Run Review", type="primary"):
    if uploaded_file is None:
        st.error("Please upload a lease agreement.")
    else:
        with st.spinner("Reading agreement clause by clause..."):
            lease_text = extract_text(uploaded_file)

        if not lease_text.strip():
            st.error("Could not extract readable text from this file.")
        else:
            try:
                with st.spinner("Comparing against standard positions..."):
                    result = review_lease(lease_text)

                st.markdown("---")

                if result.get("overall_status") == "Clean":
                    st.markdown('<div class="status-clean">✅ CLEAN — Matches company standard positions</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="status-flagged">🚩 FLAGGED — Deviations or missing protections found</div>', unsafe_allow_html=True)

                col1, col2, col3 = st.columns(3)
                col1.metric("Matches", len(result.get("matches", [])))
                col2.metric("Deviations", len(result.get("deviations", [])))
                col3.metric("Missing protections", len(result.get("missing", [])))

                st.markdown("### 🟢 Matches with Standard")
                if result.get("matches"):
                    for m in result["matches"]:
                        st.markdown(f"""
                        <div class="match-card">
                        <b>{m.get('item','')}</b><br>{m.get('note','')}
                        <div class="quote-box">"{m.get('quote','')}"</div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.caption("No matches recorded.")

                st.markdown("### 🔴 Deviations from Standard")
                if result.get("deviations"):
                    for d in result["deviations"]:
                        st.markdown(f"""
                        <div class="deviation-card">
                        <b>{d.get('item','')}</b><br>
                        Standard: {d.get('standard','')}<br>
                        Deviation: {d.get('deviation','')}
                        <div class="quote-box">"{d.get('quote','')}"</div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.caption("No deviations found.")

                st.markdown("### 🟡 Missing Required Protections")
                if result.get("missing"):
                    for mi in result["missing"]:
                        st.markdown(f"""
                        <div class="missing-card">
                        <b>{mi.get('item','')}</b><br>{mi.get('explanation','')}
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.caption("No missing protections — all required clauses are present.")

                st.markdown("### 🔵 What the Signer Most Needs to Know")
                for point in result.get("key_terms_summary", []):
                    st.markdown(f'<div class="summary-box">{point}</div>', unsafe_allow_html=True)

                st.caption("This report flags and explains findings for a human reviewer's judgment. It is not an approval or legal opinion.")

            except json.JSONDecodeError:
                st.error("The review service returned an unexpected format. Please try again.")
            except Exception as exc:
                st.error(f"Review failed: {exc}")