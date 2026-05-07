import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.longcat.chat/anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "LongCat-Flash-Lite")

AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")
DIANPING_APP_KEY = os.getenv("DIANPING_APP_KEY", "")
DIANPING_APP_SECRET = os.getenv("DIANPING_APP_SECRET", "")

USE_POI_DB = os.getenv("USE_POI_DB", "false").lower() == "true"
