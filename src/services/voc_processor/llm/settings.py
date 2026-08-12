from easydict import EasyDict as ed

# VLLM配置
VLLM_CFG = ed()
VLLM_CFG.URL = "http://192.168.1.20:6015/v1"
VLLM_CFG.MODEL_NAME = "Qwen3-30B-A3B"
VLLM_CFG.API_KEY = "local"