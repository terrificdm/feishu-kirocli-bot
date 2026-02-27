import json
import os
import shutil
from dotenv import load_dotenv

load_dotenv()


class Config:
    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")
    BOT_NAME: str = os.getenv("BOT_NAME", "")
    KIRO_CLI_PATH: str = os.getenv("KIRO_CLI_PATH", "kiro-cli")
    WORKING_DIR: str = os.getenv("WORKING_DIR", "/tmp/feishu-kirocli-bot-workspaces")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # Idle timeout in seconds (0 = never timeout)
    IDLE_TIMEOUT: int = int(os.getenv("IDLE_TIMEOUT", "300"))  # Default 5 minutes
    
    # MCP servers configuration (JSON string or empty)
    # Example: '[{"name": "github", "url": "..."}]'
    _MCP_SERVERS_JSON: str = os.getenv("MCP_SERVERS", "[]")

    @property
    def MCP_SERVERS(self) -> list[dict]:
        """Parse MCP_SERVERS from JSON string."""
        try:
            return json.loads(self._MCP_SERVERS_JSON)
        except json.JSONDecodeError:
            return []

    def validate(self):
        assert self.FEISHU_APP_ID, "FEISHU_APP_ID is required"
        assert self.FEISHU_APP_SECRET, "FEISHU_APP_SECRET is required"
        assert self.BOT_NAME, "BOT_NAME is required"
        assert shutil.which(self.KIRO_CLI_PATH), f"kiro-cli not found at: {self.KIRO_CLI_PATH}"
