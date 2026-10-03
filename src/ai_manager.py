import logging
import os

logging.basicConfig(level=logging.INFO)

# ==========================================
# 1. FILE READING
# ==========================================

def read_resume_file(file_path: str) -> str:
    """Reads resume text from a .txt or .pdf file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Resume file not found at: {file_path}")

    if file_path.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = "".join([page.extract_text() or "" for page in reader.pages])
        return text
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
        
        
# Path to the current script (e.g., Project/src/agent.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # src folder

#Return to Project root to access data folder
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..")) 

# Define the path to the resume file
RESUME_FILE_PATH = os.path.join(PROJECT_ROOT, "data", "nevin_chua.pdf")

resume_text = read_resume_file(RESUME_FILE_PATH)
print(f"Resume text extracted:\n{resume_text[:200]}...")  # Print first 200 chars
