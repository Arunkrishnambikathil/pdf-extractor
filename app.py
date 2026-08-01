import streamlit as st
import fitz  # PyMuPDF
import re
import pandas as pd
import io
import time
import zipfile
import requests
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

def to_direct_download_link(url):
    """Convert a Dropbox or Google Drive share link into a direct-download link."""
    url = url.strip()

    # Dropbox: swap dl=0 for dl=1, or append it
    if "dropbox.com" in url:
        if "dl=0" in url:
            return url.replace("dl=0", "dl=1")
        if "dl=1" in url:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}dl=1"

    # Google Drive: extract the file ID and build the direct-download URL
    if "drive.google.com" in url:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", url) or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
        if match:
            file_id = match.group(1)
            return f"https://drive.google.com/uc?export=download&id={file_id}"

    return url  # assume it's already a direct link


def download_zip_from_url(url, status_callback=None):
    """Download a (possibly large) file from a URL, handling Google Drive's
    large-file confirmation step, and return the raw bytes."""
    direct_url = to_direct_download_link(url)
    session = requests.Session()
    response = session.get(direct_url, stream=True, timeout=60)

    # Google Drive shows a "can't scan for viruses" interstitial for big files;
    # this cookie-based token lets us confirm and get the real file.
    token = next((v for k, v in response.cookies.items() if k.startswith("download_warning")), None)
    if token:
        response = session.get(direct_url, params={"confirm": token}, stream=True, timeout=60)

    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    buffer = io.BytesIO()
    downloaded = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buffer.write(chunk)
            downloaded += len(chunk)
            if status_callback and total:
                status_callback(downloaded / total)
    buffer.seek(0)
    return buffer.read()


tab_zip, tab_individual, tab_link = st.tabs([
    "📦 Upload a ZIP",
    "📄 Upload PDFs individually",
    "🔗 Paste a Dropbox/Drive link (fastest for large batches)",
])

file_data = []  # list of (name, bytes)

with tab_zip:
    st.write("For many files, put your PDFs into one or more `.zip` files and upload them here — "
             "it's much faster than uploading each PDF one by one. "
             "If you have a very large batch, split it into a few smaller ZIPs "
             "(e.g. 100 files each) and select them all at once below.")
    zip_files = st.file_uploader(
        "Choose ZIP file(s)", type="zip", accept_multiple_files=True, key="zip_uploader"
    )
    if zip_files:
        for zip_file in zip_files:
            with zipfile.ZipFile(zip_file) as z:
                pdf_names = [n for n in z.namelist() if n.lower().endswith(".pdf") and not n.startswith("__MACOSX")]
                for name in pdf_names:
                    file_data.append((name.split("/")[-1], z.read(name)))
        st.success(f"Found {len(file_data)} PDF(s) across {len(zip_files)} ZIP file(s).")

with tab_individual:
    st.write("For a small number of files, you can upload them directly here instead.")
    uploaded_files = st.file_uploader(
        "Choose PDF files", type="pdf", accept_multiple_files=True, key="individual_uploader"
    )
    if uploaded_files:
        st.info(f"{len(uploaded_files)} file(s) selected.")
        file_data = [(f.name, f.read()) for f in uploaded_files]

with tab_link:
    st.write(
        "1. Zip your PDFs, upload the zip to **Dropbox** or **Google Drive**\n"
        "2. Set sharing to \"Anyone with the link\"\n"
        "3. Paste the share link below — the server will fetch it directly, "
        "which is often much faster and more reliable than uploading through the browser."
    )
    link = st.text_input("Paste your Dropbox or Google Drive share link")
    if link and st.button("⬇️ Fetch ZIP from link"):
        link_progress = st.progress(0)
        link_status = st.empty()

        def _update(frac):
            link_progress.progress(min(frac, 1.0))
            link_status.write(f"Downloading… {int(min(frac, 1.0) * 100)}%")

        try:
            link_status.write("Connecting…")
            zip_bytes = download_zip_from_url(link, status_callback=_update)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                pdf_names = [n for n in z.namelist() if n.lower().endswith(".pdf") and not n.startswith("__MACOSX")]
                for name in pdf_names:
                    file_data.append((name.split("/")[-1], z.read(name)))
            link_status.write(f"✅ Downloaded and found {len(file_data)} PDF(s).")
        except Exception as e:
            st.error(f"Couldn't fetch that link: {e}")

if st.button("🚀 Process Files", type="primary", disabled=not file_data):
    progress = st.progress(0)
    status = st.empty()
    run_start = time.perf_counter()

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
