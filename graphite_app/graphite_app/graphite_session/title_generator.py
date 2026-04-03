from datetime import datetime

import ollama

import api_provider
import graphite_config as config


class TitleGenerator:
    """Generate concise titles for new chat sessions."""

    def __init__(self):
        self.system_prompt = """You are a title generation assistant. Your only job is to create short,
        2-3 word titles based on conversation content. Rules:
        - ONLY output the title, nothing else
        - Keep it between 2-3 words
        - Use title case
        - Make it descriptive but concise
        - NO punctuation
        - NO explanations
        - NO additional text"""

    def generate_title(self, message):
        try:
            title = ""
            if api_provider.USE_API_MODE:
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Create a 2-3 word title for this message: {message}"},
                ]
                response = api_provider.chat(task=config.TASK_TITLE, messages=messages)
                title = response["message"]["content"].strip()
            else:
                model = config.OLLAMA_MODELS.get(config.TASK_TITLE)
                if not model:
                    raise ValueError(f"No Ollama model configured for task: {config.TASK_TITLE}")

                response = ollama.generate(
                    model=model,
                    system=self.system_prompt,
                    prompt=f"Create a 2-3 word title for this message: {message}",
                )
                title = response["response"].strip()

            return " ".join(title.split()[:3])
        except Exception as exc:
            print(f"Title generation failed: {exc}")
            return f"Chat {datetime.now().strftime('%Y%m%d_%H%M')}"
