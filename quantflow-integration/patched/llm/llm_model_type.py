import os
from enum import Enum
from panda_server.config.env import DEEPSEEK_API_KEY

# Claude(Anthropic) key —— 设了它，内置聊天助手就走 Claude；没设则回退 DeepSeek
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


class LLMModelType(str, Enum):
    """
    LLM model type enumeration
    """
    DeepSeek_V3 = "deepseek-chat"
    DeepSeek_R1 = "deepseek-reasoner"
    Claude = "claude-opus-4-8"


# Model configuration mapping display names to model details
MODEL_CONFIG = {
    "DeepSeek-V3": {
        "provider": "openai",
        "model": LLMModelType.DeepSeek_V3,
        "base_url": "https://api.deepseek.com/v1",
        "api_key_name": DEEPSEEK_API_KEY,
        "version": "DeepSeek-V3-0324"
    },
    "DeepSeek-R1": {
        "provider": "openai",
        "model": LLMModelType.DeepSeek_R1,
        "base_url": "https://api.deepseek.com/v1",
        "api_key_name": DEEPSEEK_API_KEY,
        "version": "DeepSeek-R1-0528"
    },
    # 新增：Claude（Anthropic）。provider=anthropic 时 LLMService 走 Anthropic SDK
    "Claude": {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "base_url": None,
        "api_key_name": ANTHROPIC_API_KEY,
        "version": "claude-opus-4-8"
    },
}
