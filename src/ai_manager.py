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

def get_resume_file_path() -> str:
    """Returns the absolute path to the resume file."""
    # Get the directory where this python script is located
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Return to Project root to access data folder
    project_root = os.path.abspath(os.path.join(base_dir, ".."))
    
    # Define the path to the resume file
    return os.path.join(project_root, "data", "nevin_chua.pdf")
        

resume_text = read_resume_file(get_resume_file_path())
print(f"Resume text extracted:\n{resume_text[:200]}...")  
