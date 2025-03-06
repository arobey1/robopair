import openai
import anthropic
import time
import torch
import gc
from typing import Dict, List
import os

### Dolphin
import os
import pandas as pd
from typing import Union
from PIL import Image
import mimetypes

import cv2

from dolphin.configs.lora_config import openflamingo_tuning_config
from dolphin.mllm.src.factory import create_model_and_transforms
from huggingface_hub import hf_hub_download
from peft import (
    get_peft_model,
    LoraConfig,
    get_peft_model_state_dict,
    PeftConfig,
    PeftModel
)
###


class LanguageModel:
    def __init__(self, model_name):
        self.model_name = model_name
    
    def batched_generate(self, prompts_list, max_n_tokens, temperature):
        """Generates responses for a batch of prompts using a language model."""
        raise NotImplementedError

class HuggingFace(LanguageModel):
    def __init__(self,model_name, model, tokenizer):
        self.model_name = model_name
        self.model = model 
        self.tokenizer = tokenizer
        self.eos_token_ids = [self.tokenizer.eos_token_id]

    def batched_generate(self, 
                        full_prompts_list,
                        max_n_tokens: int, 
                        temperature: float,
                        top_p: float = 1.0,):
        inputs = self.tokenizer(full_prompts_list, return_tensors='pt', padding=True)
        inputs = {k: v.to(self.model.device.index) for k, v in inputs.items()}
    
        # Batch generation
        if temperature > 0:
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_n_tokens, 
                do_sample=True,
                temperature=temperature,
                eos_token_id=self.eos_token_ids,
                top_p=top_p,
            )
        else:
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_n_tokens, 
                do_sample=False,
                eos_token_id=self.eos_token_ids,
            )
            
        # If the model is not an encoder-decoder type, slice off the input tokens
        if not self.model.config.is_encoder_decoder:
            output_ids = output_ids[:, inputs["input_ids"].shape[1]:]

        # Batch decoding
        outputs_list = self.tokenizer.batch_decode(output_ids, skip_special_tokens=True)

        for key in inputs:
            inputs[key].to('cpu')
        output_ids.to('cpu')
        del inputs, output_ids
        gc.collect()
        torch.cuda.empty_cache()

        return outputs_list

    def extend_eos_tokens(self):        
        # Add closing braces for Vicuna/Llama eos when using attacker model
        self.eos_token_ids.extend([
            self.tokenizer.encode("}")[1],
            29913, 
            9092,
            16675])

class GPT(LanguageModel):
    API_RETRY_SLEEP = 10
    API_ERROR_OUTPUT = "$ERROR$"
    API_QUERY_SLEEP = 1
    API_MAX_RETRY = 5
    API_TIMEOUT = 20

    def generate(self, conv: List[Dict], 
                max_n_tokens: int, 
                temperature: float,
                top_p: float):
        '''
        Args:
            conv: List of dictionaries, OpenAI API format
            max_n_tokens: int, max number of tokens to generate
            temperature: float, temperature for sampling
            top_p: float, top p for sampling
        Returns:
            str: generated response
        '''

        if os.getenv("OPENAI_API_KEY") is None:
            raise ValueError("OPENAI_API_KEY is not set")

        output = self.API_ERROR_OUTPUT
        for _ in range(self.API_MAX_RETRY):
            try:
                client = openai.OpenAI()
                completion = client.chat.completions.create(
                    model=self.model_name,
                    messages=conv,
                    max_tokens = max_n_tokens,
                    temperature = temperature,
                    top_p = top_p,
                    # request_timeout = self.API_TIMEOUT,
                )
                output = completion.choices[0].message.content
                break
            except Exception as e:
                print(e)
        
            time.sleep(1)
        return output 
    
    def batched_generate(self, 
                        convs_list: List[List[Dict]],
                        max_n_tokens: int, 
                        temperature: float,
                        top_p: float = 1.0,):
        return [self.generate(conv, max_n_tokens, temperature, top_p) for conv in convs_list]

class DolphinModel(LanguageModel):
    def __init__(self, model_name, video_path):
        super().__init__(model_name)
        self.video_path = video_path
        self.model, self.image_processor, self.tokenizer = self.load_pretrained_model()
        self.generation_kwargs = {
            'top_k': 0, 
            'no_repeat_ngram_size': 3, 
            'length_penalty': 1,
            'do_sample': False,
            'early_stopping': True
        }

    def batched_generate(
            self,
            full_prompts_list,
            max_n_tokens, 
            temperature,
            top_p
        ):
        vision_x, inputs = self.get_model_inputs(full_prompts_list[0])
        generated_tokens = self.model.generate(
            vision_x=vision_x.half().cuda(),
            lang_x=inputs["input_ids"].cuda(),
            attention_mask=inputs["attention_mask"].cuda(),
            num_beams=3,
            max_new_tokens=max_n_tokens,
            temperature=temperature,
            top_p=top_p,
            **self.generation_kwargs,
        )
        generated_tokens = generated_tokens.cpu().numpy()
        if isinstance(generated_tokens, tuple):
            generated_tokens = generated_tokens[0]

        generated_text = self.tokenizer.batch_decode(generated_tokens)

        print(
            f"Dolphin output:\n\n{generated_text}"
        )
        return generated_text

    def get_model_inputs(self, instruction):
        frames = self.get_image(self.video_path)
        vision_x = torch.stack(
            [self.image_processor(image) for image in frames], 
            dim=0
        ).unsqueeze(0).unsqueeze(0)
        assert vision_x.shape[2] == len(frames)
        prompt = [
            f"USER: <image> is a driving video. {instruction} GPT:<answer>"
        ]
        inputs = self.tokenizer(prompt, return_tensors="pt", ).to(self.model.device)
        return vision_x, inputs

    def load_pretrained_model(self):
        peft_config, peft_model_id = None, None
        peft_config = LoraConfig(**openflamingo_tuning_config)
        model, image_processor, tokenizer = create_model_and_transforms(
            clip_vision_encoder_path="ViT-L-14-336",
            clip_vision_encoder_pretrained="openai",
            lang_encoder_path="anas-awadalla/mpt-7b", # anas-awadalla/mpt-7b
            tokenizer_path="anas-awadalla/mpt-7b",  # anas-awadalla/mpt-7b
            cross_attn_every_n_layers=4,
            use_peft=True,
            peft_config=peft_config,
        )

        checkpoint_path = hf_hub_download("gray311/Dolphins", "checkpoint.pt")
        model.load_state_dict(torch.load(checkpoint_path), strict=False)
        model.half().cuda()

        return model, image_processor, tokenizer

    def extract_frames(self, video_path, num_frames=16):
        video = cv2.VideoCapture(video_path)
        total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_step = total_frames // num_frames
        frames = []

        for i in range(num_frames):
            video.set(cv2.CAP_PROP_POS_FRAMES, i * frame_step)
            ret, frame = video.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = Image.fromarray(frame).convert("RGB")
                frames.append(frame)

        video.release()
        return frames

    def get_content_type(self, file_path):
        content_type, _ = mimetypes.guess_type(file_path)
        return content_type

    def get_image(self, url: str) -> Union[Image.Image, list]:
        if "://" not in url:  # Local file
            content_type = self.get_content_type(url)
        else:  # Remote URL
            content_type = requests.head(url, stream=True, verify=False).headers.get("Content-Type")

        if "image" in content_type:
            if "://" not in url:  # Local file
                return Image.open(url)
            else:  # Remote URL
                return Image.open(requests.get(url, stream=True, verify=False).raw)
        elif "video" in content_type:
            video_path = "temp_video.mp4"
            if "://" not in url:  # Local file
                video_path = url
            else:  # Remote URL
                with open(video_path, "wb") as f:
                    f.write(requests.get(url, stream=True, verify=False).content)
            frames = self.extract_frames(video_path)
            if "://" in url:  # Only remove the temporary video file if it was downloaded
                os.remove(video_path)
            return frames
        else:
            raise ValueError("Invalid content type. Expected image or video.")