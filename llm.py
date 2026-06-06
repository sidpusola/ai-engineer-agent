"""
The 'brain' of the agent: a thin wrapper around the local Qwen model.

Loads Qwen2.5-Coder-3B-Instruct once and exposes generate(), which streams
tokens to the terminal (so you can watch it think) and returns the full text.
"""

import threading

import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TextIteratorStreamer,
    BitsAndBytesConfig,
)

MODEL_NAME = "Qwen/Qwen2.5-Coder-14B-Instruct"


class LLM:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[llm] loading {MODEL_NAME} on {self.device.upper()} ...")
        self.tok = AutoTokenizer.from_pretrained(MODEL_NAME)

        if self.device == "cuda":
            # 4-bit NF4 quantization — fits the 14B in ~9 GB of VRAM.
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                MODEL_NAME,
                quantization_config=quant,
                device_map={"": 0},   # force the whole model onto GPU 0
            )
        else:
            # CPU fallback (slow) — no quantization.
            self.model = AutoModelForCausalLM.from_pretrained(
                MODEL_NAME, dtype=torch.float32
            )

        self.model.eval()
        name = torch.cuda.get_device_name(0) if self.device == "cuda" else "CPU"
        print(f"[llm] ready on {name}\n")

    def stream(
        self,
        system: str,
        user: str,
        max_new_tokens: int = 1200,
        temperature: float = 0.3,
    ):
        """Yield generated text chunks as the model produces them."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        inputs = self.tok.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        ).to(self.device)

        streamer = TextIteratorStreamer(
            self.tok, skip_prompt=True, skip_special_tokens=True
        )
        kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=max_new_tokens,
            do_sample=temperature > 0,
            temperature=max(temperature, 1e-5),
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=self.tok.eos_token_id,
        )

        thread = threading.Thread(target=self.model.generate, kwargs=kwargs)
        thread.start()
        for piece in streamer:
            yield piece

    def generate(
        self,
        system: str,
        user: str,
        max_new_tokens: int = 1200,
        temperature: float = 0.3,
        stream: bool = True,
    ) -> str:
        """Run one chat completion. Optionally prints to stdout; returns full text."""
        chunks = []
        for piece in self.stream(system, user, max_new_tokens, temperature):
            chunks.append(piece)
            if stream:
                print(piece, end="", flush=True)
        if stream:
            print()
        return "".join(chunks)
