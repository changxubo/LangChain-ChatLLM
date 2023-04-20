
import os
from typing import Dict, List, Optional, Tuple, Union

import torch
from langchain.llms.base import LLM
from langchain.llms.utils import enforce_stop_tokens
from transformers import AutoModel, AutoTokenizer

os.environ["TOKENIZERS_PARALLELISM"] = "false"

DEVICE = "cpu"
DEVICE_ID = "0"
CUDA_DEVICE = f"{DEVICE}:{DEVICE_ID}" if DEVICE_ID else DEVICE


def torch_gc():
    if torch.cuda.is_available():
        with torch.cuda.device(CUDA_DEVICE):
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

def auto_configure_device_map(num_gpus: int) -> Dict[str, int]:
    # transformer.word_embeddings 占用1层
    # transformer.final_layernorm 和 lm_head 占用1层
    # transformer.layers 占用 28 层
    # 总共30层分配到num_gpus张卡上
    num_trans_layers = 28
    per_gpu_layers = 30 / num_gpus

    # bugfix: 在linux中调用torch.embedding传入的weight,input不在同一device上,导致RuntimeError
    # windows下 model.device 会被设置成 transformer.word_embeddings.device
    # linux下 model.device 会被设置成 lm_head.device
    # 在调用chat或者stream_chat时,input_ids会被放到model.device上
    # 如果transformer.word_embeddings.device和model.device不同,则会导致RuntimeError
    # 因此这里将transformer.word_embeddings,transformer.final_layernorm,lm_head都放到第一张卡上
    device_map = {'transformer.word_embeddings': 0,
                  'transformer.final_layernorm': 0, 'lm_head': 0}

    used = 2
    gpu_target = 0
    for i in range(num_trans_layers):
        if used >= per_gpu_layers:
            gpu_target += 1
            used = 0
        assert gpu_target < num_gpus
        device_map[f'transformer.layers.{i}'] = gpu_target
        used += 1

    return device_map



class ChatLLM(LLM):
    max_token: int = 10000
    temperature: float = 0.1
    top_p = 0.9
    history = []
    tokenizer: object = None
    model: object = None

    def __init__(self):
        super().__init__()

    @property
    def _llm_type(self) -> str:
        return "ChatLLM"

    def _call(self,
              prompt: str,
              stop: Optional[List[str]] = None) -> str:
        
        if self.model_name == 'Minimax':
            import requests

            group_id = os.getenv('group_id')
            api_key = os.getenv('api_key')

            url = f'https://api.minimax.chat/v1/text/chatcompletion?GroupId={group_id}'
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            request_body = {
                "model": "abab5-chat",
                "tokens_to_generate": 512,
                'messages': []
            }

            for i in self.history:
                h_input = i[0]
                h_reply = i[1]
                request_body['messages'].append({
                    "sender_type": "USER",
                    "text": h_input
                })
                request_body['messages'].append({"sender_type": "BOT", "text": h_reply})

            request_body['messages'].append({"sender_type": "USER", "text": prompt})
            resp = requests.post(url, headers=headers, json=request_body)
            response = resp.json()['reply']
            #  将当次的ai回复内容加入messages
            request_body['messages'].append({"sender_type": "BOT", "text": response})
            self.history.append((prompt, response))
        
        else:

            response, _ = self.model.chat(
                self.tokenizer,
                prompt,
                history=self.history,
                max_length=self.max_token,
                temperature=self.temperature,
            )
            torch_gc()
            if stop is not None:
                response = enforce_stop_tokens(response, stop)
            self.history = self.history+[[None, response]]
        return response

    def load_model(self,
                   model_name_or_path: str = "THUDM/chatglm-6b-int4",
                   llm_device=DEVICE,
                   device_map: Optional[Dict[str, int]] = None,
                   **kwargs):
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name_or_path,
            trust_remote_code=True
        )
        if torch.cuda.is_available() and llm_device.lower().startswith("cuda"):
            # 根据当前设备GPU数量决定是否进行多卡部署
            num_gpus = torch.cuda.device_count()
            if num_gpus < 2 and device_map is None:
                self.model = (
                    AutoModel.from_pretrained(
                        model_name_or_path, 
                        trust_remote_code=True, 
                        **kwargs)
                    .half()
                    .cuda()
                )
            else:
                from accelerate import dispatch_model

                model = AutoModel.from_pretrained(model_name_or_path, trust_remote_code=True, **kwargs).half()
                # 可传入device_map自定义每张卡的部署情况
                if device_map is None:
                    device_map = auto_configure_device_map(num_gpus)

                self.model = dispatch_model(model, device_map=device_map)
        else:
            self.model = (
                AutoModel.from_pretrained(
                    model_name_or_path,
                    trust_remote_code=True)
                .float()
                .to(llm_device)
            )
        self.model = self.model.eval()