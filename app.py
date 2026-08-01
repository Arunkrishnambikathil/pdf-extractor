import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    t0 = time.perf_counter()
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    t_open = time.perf_counter()

    page = doc[0]
    blocks = page.get_text("blocks")
    t_extract = time.perf_counter()

    row = {
        "DATE": extract_date(blocks),
        "IMPORTER / EXPORTER": get_text_by_position(blocks, 209, 121),
        "MARKS & NUMBERS": extract_marks_numbers(blocks),
        "CARRIER NAME": get_text_by_position(blocks, 401, 177),
        "INTERCESSOR CO.": get_text_by_position(blocks, 209, 149),
    }
    page_count = doc.page_count
    file_size_kb = len(file_bytes) / 1024
    doc.close()

    timing = {
        "open_sec": round(t_open - t0, 3),
        "extract_sec": round(t_extract - t_open, 3),
        "total_sec": round(time.perf_counter() - t0, 3),
        "pages": page_count,
        "size_kb": round(file_size_kb, 1),
    }
    return row, timing

# --------------------------------------------------
# UI
# --------------------------------------------------
uploaded_files = st.file_uploader(
    "Choose PDF files", type="pdf", accept_multiple_files=True
)

if uploaded_files:
    st.info(f"{len(uploaded_files)} file(s) selected.")

if st.button("🚀 Process Files", type="primary", disabled=not uploaded_files):
    progress = st.progress(0)
    status = st.empty()
    run_start = time.perf_counter()

    # Read all bytes up front (uploaded_file.read() must happen on the main thread)
    file_data = [(f.name, f.read()) for f in uploaded_files]

    results_by_index = {}
    timings_by_index = {}
    errors = []
    done_count = 0

    max_workers = min(8, len(file_data))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(process_pdf, data): idx
            for idx, (name, data) in enumerate(file_data)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            name = file_data[idx][0]
            try:
                row, timing = future.result()
                results_by_index[idx] = row
                timings_by_index[idx] = {"file": name, **timing}
            except Exception as e:
                errors.append(f"{name}: {e}")
            done_count += 1
            status.write(f"Processed {done_count} of {len(file_data)} files…")
            progress.progress(done_count / len(file_data))

    run_total = round(time.perf_counter() - run_start, 2)

    # Preserve original upload order in the output
    results = [results_by_index[i] for i in sorted(results_by_index)]

    for err in errors:
        st.error(f"Failed on {err}")

    status.write(f"✅ Done in {run_total} seconds for {len(file_data)} file(s).")

    # ---- Diagnostic timing table (temporary, to find the bottleneck) ----
    if timings_by_index:
        timing_df = pd.DataFrame(
            [timings_by_index[i] for i in sorted(timings_by_index)]
        )
        with st.expander("🔍 Timing diagnostics (click to expand)"):
            st.write(f"Wall-clock total: **{run_total} sec** "
                     f"({round(run_total / len(file_data), 3)} sec/file average, "
                     f"with {max_workers} parallel workers)")
            st.write(f"Sum of individual `open` times: "
                     f"{round(timing_df['open_sec'].sum(), 2)} sec")
            st.write(f"Sum of individual `extract` times: "
                     f"{round(timing_df['extract_sec'].sum(), 2)} sec")
            st.dataframe(timing_df.sort_values("total_sec", ascending=False))

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
