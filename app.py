import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
import io

st.set_page_config(page_title="PDF Data Extractor", page_icon="📄")

st.title("📄 PDF Data Extractor")
st.write(
    "1. Upload your PDF files below\n"
    "2. Click **Process Files**\n"
    "3. Click **Download** to get your Excel file"
)

# --------------------------------------------------
# Extraction logic (same as your original script)
# --------------------------------------------------
def get_text_by_position(blocks, x, y, x_tol=30, y_tol=8):
    for b in blocks:
        x0, y0, x1, y1, text = b[:5]
        if abs(x0 - x) <= x_tol and abs(y0 - y) <= y_tol:
            return text.replace("\n", " ").strip()
    return ""

def extract_marks_numbers(blocks):
    pattern = re.compile(r"[A-Z]{4}\d{7}/\S+")
    marks = []
    for b in blocks:
        text = b[4].replace("\n", " ")
        found = pattern.findall(text)
        if found:
            marks.extend(found)
    return " ".join(marks)

def extract_date(blocks):
    pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
    for b in blocks:
        text = b[4]
        m = pattern.search(text)
        if m:
            return m.group(0)
    return ""

def process_pdf(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page = doc[0]
    blocks = page.get_text("blocks")
    row = {
        "DATE": extract_date(blocks),
        "IMPORTER / EXPORTER": get_text_by_position(blocks, 209, 121),
        "MARKS & NUMBERS": extract_marks_numbers(blocks),
        "CARRIER NAME": get_text_by_position(blocks, 401, 177),
        "INTERCESSOR CO.": get_text_by_position(blocks, 209, 149),
    }
    doc.close()
    return row

# --------------------------------------------------
# UI
# --------------------------------------------------
uploaded_files = st.file_uploader(
    "Choose PDF files", type="pdf", accept_multiple_files=True
)

if uploaded_files:
    st.info(f"{len(uploaded_files)} file(s) selected.")

if st.button("🚀 Process Files", type="primary", disabled=not uploaded_files):
    results = []
    progress = st.progress(0)
    status = st.empty()

    for i, uploaded_file in enumerate(uploaded_files):
        status.write(f"Processing: **{uploaded_file.name}**")
        try:
            row = process_pdf(uploaded_file.read())
            results.append(row)
        except Exception as e:
            st.error(f"Failed on {uploaded_file.name}: {e}")
        progress.progress((i + 1) / len(uploaded_files))

    status.write("✅ Done!")

    if results:
        df = pd.DataFrame(
            results,
            columns=[
                "DATE",
                "IMPORTER / EXPORTER",
                "MARKS & NUMBERS",
                "CARRIER NAME",
                "INTERCESSOR CO.",
            ],
        )

        st.subheader("Preview")
        st.dataframe(df)

        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)

        st.download_button(
            label="⬇️ Download ONLY_4_FIELDS.xlsx",
            data=buffer,
            file_name="ONLY_4_FIELDS.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
