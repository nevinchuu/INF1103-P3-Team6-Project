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
def get_data_root() -> str:
    # Get the directory where this python script is located
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Return to Project root to access data folder
    project_root = os.path.abspath(os.path.join(base_dir, ".."))

    # Define the path to the resume file
    data_root = os.path.join(project_root, "data")
    return data_root

def get_resume_file_path(data_root: str) -> str:
    "Prompts the user for a resume name and returns the full path to the resume file."
    while True:
        filename = input("\nEnter the PDF resume filename in Data/")
    
        if not filename:
            print("Filename cannot be empty. Please try again.")
            continue

        if not filename.lower().endswith(".pdf"):
            print("Please provide a valid PDF filename (e.g., 'resume.pdf').")
            continue

        # Connects the path to the data folder and the filename provided by user
        target_path = os.path.join(data_root, filename)

        # Check if the file exists
        try:
            if not os.path.exists(target_path):
                raise FileNotFoundError(f"Resume file not found at: {target_path}")
        
            print(f"Resume file found: {target_path}")
            return target_path

        except FileNotFoundError as e:
            print(f"Exception occurred: {e}")


if __name__ == "__main__":
        try:
            resume_text = read_resume_file(get_resume_file_path(get_data_root()))
            print(f"Resume text extracted:\n{resume_text[:200]}...")  
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")