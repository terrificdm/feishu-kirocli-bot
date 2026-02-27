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
    LOG_FILE: str = os.getenv("LOG_FILE", "")
    
    # Idle timeout in seconds (0 = never timeout)
    IDLE_TIMEOUT: int = int(os.getenv("IDLE_TIMEOUT", "300"))  # Default 5 minutes
    
    # Workspace mode: "per_chat" (default) or "fixed"
    # - per_chat: Each chat gets its own subdirectory under WORKING_DIR
    # - fixed: All chats share the same WORKING_DIR (useful for project-specific .kiro config)
    WORKSPACE_MODE: str = os.getenv("WORKSPACE_MODE", "per_chat").lower()
    
    # Note: MCP servers can be configured via:
    # - Global: ~/.kiro/settings/mcp.json
    # - Workspace: {WORKING_DIR}/.kiro/settings/mcp.json (use WORKSPACE_MODE=fixed)

    def validate(self):
        assert self.FEISHU_APP_ID, "FEISHU_APP_ID is required"
        assert self.FEISHU_APP_SECRET, "FEISHU_APP_SECRET is required"
        assert self.BOT_NAME, "BOT_NAME is required"
        assert shutil.which(self.KIRO_CLI_PATH), f"kiro-cli not found at: {self.KIRO_CLI_PATH}"
        assert self.WORKSPACE_MODE in ("per_chat", "fixed"), \
            f"WORKSPACE_MODE must be 'per_chat' or 'fixed', got: {self.WORKSPACE_MODE}"
