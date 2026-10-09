from pathlib import Path
import os
from dotenv import load_dotenv


# Find root directory (this file is in src/ai_manager_test/, so go up three levels)
root_dir = Path(__file__).resolve().parent.parent.parent
env_path = root_dir / ".env"



print(f"Looking for .env at: {env_path}")
print(f"Does .env exist? -> {env_path.exists()}")

# Try loading the .env file explicitly
load_dotenv(dotenv_path=env_path)

# Check if the variable was retrieved
api_key = os.getenv("QWEN_API_KEY")
if api_key:
    print(f"SUCCESS: QWEN_API_KEY detected! (Starts with: {api_key[:5]}...)")
else:
    print("ERROR: QWEN_API_KEY is missing or empty in .env")