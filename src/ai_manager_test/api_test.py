from pathlib import Path
import os
import sys
from dotenv import load_dotenv


# Find root directory (this file is in src/ai_manager_test/, so go up three levels)
root_dir = Path(__file__).resolve().parent.parent.parent
env_path = root_dir / ".env"

# All output goes through the input/output layer, which lives in src/
sys.path.append(str(root_dir / "src"))
import io_manager  # noqa: E402



io_manager.display_message(f"Looking for .env at: {env_path}")
io_manager.display_message(f"Does .env exist? -> {env_path.exists()}")

# Try loading the .env file explicitly
load_dotenv(dotenv_path=env_path)

# Check if the variable was retrieved
api_key = os.getenv("QWEN_API_KEY")
if api_key:
    io_manager.display_message(f"SUCCESS: QWEN_API_KEY detected! (Starts with: {api_key[:5]}...)")
else:
    io_manager.display_message("ERROR: QWEN_API_KEY is missing or empty in .env")