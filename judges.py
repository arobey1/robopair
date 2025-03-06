from fastchat.model import (
    get_conversation_template
)
import re
import common
from system_prompts.judges import (
    get_judge_system_prompt, 
    get_robot_judge_system_prompt, 
)
from system_prompts.unitree_go2 import get_go2_syntax_checker_system_prompt
from system_prompts.dolphin import get_dolphin_syntax_checker_system_prompt
from system_prompts.jackal import get_jackal_syntax_checker_system_prompt
from language_models import GPT


class JudgeBase:
    def __init__(self, args):
        self.max_n_tokens = args.judge_max_n_tokens
        self.temperature = args.judge_temperature
        self.judge_name = args.judge_model
        self.goal = args.goal
        self.target_str = args.target_str

        if args.algorithm == 'robopair':
            self.system_prompt = get_robot_judge_system_prompt(args.goal, args.target_str)
        else:
            self.system_prompt = get_judge_system_prompt(args.goal, args.target_str)
        
    def get_prompt(self, attack_prompt, target_response):
        return f"[PROMPT]:{attack_prompt}\n[ASSISTANT'S RESPONSE]: {target_response}"
    
    def score(self, prompt_list, response_list):
        raise NotImplementedError

    def process_output(self, raw_output):
        pattern = r'\[\[(\d+)\]\]'
        match = re.search(pattern, raw_output)
        output = int(match.group(1)) if match else None
        if output is None:
            # TODO: Implement a louder version
            print(f"Error in processing judge output: {raw_output}" )
            output = 1.1
        return output
               
class SyntaxCheckerJudge(JudgeBase):
    def __init__(self, args):
        super(SyntaxCheckerJudge, self).__init__(args)
        
        self.judge_model = GPT(model_name = self.judge_name)

        if args.robot == 'unitree':
            self.syntax_system_prompt = get_go2_syntax_checker_system_prompt()
        elif args.robot == 'dolphin':
            self.syntax_system_prompt = get_dolphin_syntax_checker_system_prompt()
        elif args.robot == 'jackal':
            self.syntax_system_prompt = get_jackal_syntax_checker_system_prompt()
        else:
            raise NotImplementedError

    def create_conv(self, full_prompt):
        conv = get_conversation_template(self.judge_name)
        conv.set_system_message(self.syntax_system_prompt)
        conv.append_message(conv.roles[0], full_prompt)
        return conv.to_openai_api_messages()

    def score(self, attack_prompt_list, target_response_list):
        convs_list = [self.create_conv(self.get_prompt(prompt, response)) for prompt, response in zip(attack_prompt_list, target_response_list)]
        raw_outputs = self.judge_model.batched_generate(convs_list, 
                                                        max_n_tokens = self.max_n_tokens,
                                                        temperature = self.temperature)
        outputs = [self.process_output(raw_output) for raw_output in raw_outputs]
        return outputs

class GPTJudge(JudgeBase):
    def __init__(self, args):
        super(GPTJudge, self).__init__(args)
        
        self.judge_model = GPT(model_name = self.judge_name)

    def create_conv(self, full_prompt):
        conv = get_conversation_template(self.judge_name)
        conv.set_system_message(self.system_prompt)
        conv.append_message(conv.roles[0], full_prompt)
        return conv.to_openai_api_messages()

    def score(self, attack_prompt_list, target_response_list):
        convs_list = [self.create_conv(self.get_prompt(prompt, response)) for prompt, response in zip(attack_prompt_list, target_response_list)]
        raw_outputs = self.judge_model.batched_generate(convs_list, 
                                                        max_n_tokens = self.max_n_tokens,
                                                        temperature = self.temperature)
        outputs = [self.process_output(raw_output) for raw_output in raw_outputs]
        return outputs