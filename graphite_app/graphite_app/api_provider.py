import base64
import io
import json
import os
from urllib.parse import urlparse
import urllib.error
import urllib.request

import ollama

import graphite_config as config

try:
    from PIL import Image
except ImportError:
    Image = None

USE_API_MODE = False
API_PROVIDER_TYPE = None
API_CLIENT = None
API_KEY = None
API_BASE_URL = None
API_MODELS = {
    config.TASK_TITLE: None,
    config.TASK_CHAT: None,
    config.TASK_CHART: None,
    config.TASK_IMAGE_GEN: None,
    config.TASK_WEB_VALIDATE: None,
    config.TASK_WEB_SUMMARIZE: None
}

GEMINI_MODELS_STATIC = sorted([
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash"
])

GEMINI_IMAGE_MODELS_STATIC = sorted([
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
])


def _convert_to_gemini_messages(messages: list) -> tuple:
    if Image is None and any(isinstance(msg.get('content'), list) for msg in messages):
        raise ImportError("Pillow library is required for image support with Gemini. Please install it with: pip install Pillow")

    system_prompt = None
    gemini_history =[]
    
    for msg in messages:
        if msg['role'] == 'system':
            system_prompt = msg['content']
            continue
        
        role = 'model' if msg['role'] == 'assistant' else 'user'
        
        content = msg['content']
        parts =[]
        if isinstance(content, list):
            for part in content:
                if part.get('type') == 'text':
                    parts.append(part.get('text', ''))
                elif part.get('type') == 'image_bytes':
                    image_data = part.get('data')
                    if image_data:
                        try:
                            img = Image.open(io.BytesIO(image_data))
                            parts.append(img)
                        except Exception as e:
                            print(f"Warning: Could not process image data. Error: {e}")
                            parts.append("[Image could not be processed]")
        else:
            parts.append(str(content))

        if not gemini_history and role == 'model':
            gemini_history.append({'role': 'user', 'parts': ["(Please continue)"]})

        if gemini_history and gemini_history[-1]['role'] == role:
            if role == 'user':
                gemini_history[-1]['parts'].extend(parts)
                continue
            else:
                gemini_history.append({'role': 'user', 'parts': ["(Continuing...)"]})

        gemini_history.append({
            'role': role,
            'parts': parts
        })
        
    return system_prompt, gemini_history


def _prepare_ollama_messages(messages: list) -> list:
    processed_messages =[]
    for msg in messages:
        content = msg.get('content')
        if isinstance(content, list):
            text_parts =[]
            image_parts =[]
            for part in content:
                if part.get('type') == 'text':
                    text_parts.append(part.get('text', ''))
                elif part.get('type') == 'image_bytes':
                    image_data = part.get('data')
                    if image_data:
                        image_parts.append(image_data)
            
            new_msg = {
                'role': msg['role'],
                'content': "\n".join(text_parts),
            }
            if image_parts:
                new_msg['images'] = image_parts
            processed_messages.append(new_msg)
        else:
            processed_messages.append(msg)
    return processed_messages


def _decode_base64_image(image_data: str) -> bytes:
    try:
        return base64.b64decode(image_data)
    except Exception as e:
        raise RuntimeError(f"Failed to decode generated image payload: {e}") from e


def _extract_openai_image_bytes(response) -> bytes:
    data_items = getattr(response, 'data', None)
    if not data_items:
        raise RuntimeError("Image endpoint returned no image payload.")

    first_item = data_items[0]
    b64_json = getattr(first_item, 'b64_json', None)
    if not b64_json and isinstance(first_item, dict):
        b64_json = first_item.get('b64_json')
    if b64_json:
        return _decode_base64_image(b64_json)

    image_url = getattr(first_item, 'url', None)
    if not image_url and isinstance(first_item, dict):
        image_url = first_item.get('url')
    if image_url:
        try:
            with urllib.request.urlopen(image_url, timeout=120) as resp:
                return resp.read()
        except Exception as e:
            raise RuntimeError(f"Image endpoint returned a URL, but the image download failed: {e}") from e

    raise RuntimeError("Image endpoint response did not include b64_json or a URL.")


def _extract_gemini_image_bytes(payload: dict) -> bytes:
    for candidate in payload.get('candidates', []):
        content = candidate.get('content', {})
        for part in content.get('parts', []):
            inline_data = part.get('inline_data') or part.get('inlineData')
            if inline_data and inline_data.get('data'):
                return _decode_base64_image(inline_data['data'])

    prompt_feedback = payload.get('promptFeedback', {})
    block_reason = prompt_feedback.get('blockReason') or prompt_feedback.get('block_reason')
    if block_reason:
        raise RuntimeError(f"Gemini blocked the image request: {block_reason}")

    model_text = []
    for candidate in payload.get('candidates', []):
        content = candidate.get('content', {})
        for part in content.get('parts', []):
            if part.get('text'):
                model_text.append(part['text'])
    if model_text:
        raise RuntimeError(f"Gemini returned text instead of image data: {' '.join(model_text).strip()}")

    raise RuntimeError("Gemini did not return image data.")


def _get_gemini_api_key() -> str:
    api_key = API_KEY or os.environ.get('GRAPHITE_GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("Gemini API key not configured. Open Settings and save your Gemini API key.")
    return api_key


def _is_local_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def generate_image(prompt: str, size: str = "1024x1024") -> bytes:
    if not prompt or not prompt.strip():
        raise ValueError("Image prompt cannot be empty.")

    if not USE_API_MODE:
        raise RuntimeError("Image generation is only available in API Endpoint mode.")

    if not API_CLIENT:
        raise RuntimeError("API client not initialized. Configure API settings first.")

    api_model = API_MODELS.get(config.TASK_IMAGE_GEN)
    if not api_model and API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
        api_model = "gemini-2.5-flash-image"
    if not api_model:
        raise RuntimeError(
            "No image generation model configured.\n"
            "Please select one in API Settings."
        )

    try:
        if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            if not hasattr(API_CLIENT, 'images') or not hasattr(API_CLIENT.images, 'generate'):
                raise RuntimeError("The configured OpenAI-compatible client does not expose an images.generate API.")

            response = API_CLIENT.images.generate(
                model=api_model,
                prompt=prompt,
                size=size,
            )
            return _extract_openai_image_bytes(response)

        if API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            api_key = _get_gemini_api_key()
            endpoint = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{api_model}:generateContent?key={api_key}"
            )
            request_body = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["IMAGE"]
                }
            }

            request = urllib.request.Request(
                endpoint,
                data=json.dumps(request_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                error_payload = e.read().decode("utf-8", errors="replace")
                try:
                    parsed_error = json.loads(error_payload)
                    message = parsed_error.get('error', {}).get('message') or error_payload
                except json.JSONDecodeError:
                    message = error_payload
                raise RuntimeError(f"Gemini image request failed: {message}") from e

            return _extract_gemini_image_bytes(payload)

        raise RuntimeError(f"Unsupported API provider: {API_PROVIDER_TYPE}")
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str or "resourceexhausted" in error_str:
            raise RuntimeError(
                "Image generation quota exceeded.\n\n"
                "Please use a lower-cost image model or verify billing is enabled for the selected provider."
            ) from e
        raise


def chat(task: str, messages: list, **kwargs) -> dict:
    try:
        if not USE_API_MODE:
            model = config.OLLAMA_MODELS.get(task)
            if not model:
                raise ValueError(f"No Ollama model configured for task: {task}")
            
            ollama_messages = _prepare_ollama_messages(messages)

            ollama_kwargs = kwargs.copy()
            if task == config.TASK_CHAT and ('qwen3' in model.lower() or 'deepseek' in model.lower()):
                ollama_kwargs['think'] = True
            
            response = ollama.chat(model=model, messages=ollama_messages, **ollama_kwargs)
            
            full_response_content = response['message'].get('content', '')
            reasoning = response['message'].get('thinking')

            if reasoning:
                full_response_content = f"<think>{reasoning}</think>\n{full_response_content}"

            return {
                'message': {
                    'content': full_response_content,
                    'role': 'assistant'
                }
            }
        
        else:
            if not API_CLIENT:
                raise RuntimeError("API client not initialized. Configure API settings first.")

            api_model = None
            if task == config.TASK_WEB_VALIDATE and API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
                api_model = API_MODELS.get(task) or "gemini-3.1-flash-lite-preview"
            else:
                api_model = API_MODELS.get(task)

            if not api_model:
                raise RuntimeError(
                    f"No API model configured for task '{task}'.\n"
                    f"Please configure models in API Settings."
                )

            if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
                response = API_CLIENT.chat.completions.create(
                    model=api_model,
                    messages=messages,
                    **kwargs
                )
                return {
                    'message': {
                        'content': response.choices[0].message.content,
                        'role': 'assistant'
                    }
                }
            elif API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
                system_prompt, gemini_history = _convert_to_gemini_messages(messages)
                
                model_config = {}
                if system_prompt:
                    model_config['system_instruction'] = system_prompt

                gemini_model = API_CLIENT.GenerativeModel(api_model, **model_config)
                
                response = gemini_model.generate_content(
                    contents=gemini_history,
                    generation_config=kwargs,
                    request_options={"retry": None, "timeout": 60}
                )
                
                try:
                    response_text = response.text
                except ValueError as ve:
                    if "blocked" in str(ve).lower() or "safety" in str(ve).lower():
                        raise RuntimeError("The response was blocked by Google's Safety Filters.")
                    raise ve
                
                return {
                    'message': {
                        'content': response_text,
                        'role': 'assistant'
                    }
                }
            else:
                raise RuntimeError(f"Unsupported API provider: {API_PROVIDER_TYPE}")
    
    except Exception as e:
        error_str = str(e).lower()
        
        if "429" in error_str or "quota" in error_str or "resourceexhausted" in error_str:
            if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
                raise RuntimeError(
                    "OpenAI-compatible API quota exceeded or rate limited.\n\n"
                    "Please verify billing, rate limits, and the selected model for your endpoint."
                )
            raise RuntimeError(
                "Google Gemini API Quota Exceeded.\n\n"
                "Note: Google does not offer a free tier for their 'Pro' models. "
                "Please switch your default task models to a 'Flash' model in the API Settings, "
                "or link a billing account in Google AI Studio."
            )
            
        if "connection refused" in error_str or "connecterror" in error_str or "connection error" in error_str or "all connection attempts failed" in error_str:
            if not USE_API_MODE:
                raise ConnectionError("Failed to connect to local Ollama server. Please ensure the Ollama app is running and accessible.")
            else:
                raise ConnectionError(f"Failed to connect to the API endpoint. Please verify your Base URL in settings and your network connection.\n\nDetails: {str(e)}")
        raise e


def initialize_api(provider: str, api_key: str, base_url: str = None):
    global API_PROVIDER_TYPE, API_CLIENT, API_KEY, API_BASE_URL
    API_PROVIDER_TYPE = provider
    API_KEY = api_key
    API_BASE_URL = base_url

    if provider == config.API_PROVIDER_OPENAI:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package required. Install dependencies with: pip install -r requirements.txt")
        
        if not base_url:
            base_url = 'https://api.openai.com/v1'

        if not api_key:
            if _is_local_base_url(base_url):
                api_key = "dummy-key-for-local"
                API_KEY = api_key
            else:
                raise RuntimeError("OpenAI-compatible API key not configured. Open Settings and save your API key.")

        API_CLIENT = OpenAI(api_key=api_key, base_url=base_url)

    elif provider == config.API_PROVIDER_GEMINI:
        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError("google-generativeai package required. Install dependencies with: pip install -r requirements.txt")
        
        genai.configure(api_key=api_key)
        API_CLIENT = genai
    else:
        raise ValueError(f"Unknown API provider: {provider}")

    return API_CLIENT


def get_available_models():
    if not API_CLIENT:
        raise RuntimeError("API client not initialized")

    try:
        if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            models = API_CLIENT.models.list()
            return sorted([model.id for model in models.data])
        elif API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            return GEMINI_MODELS_STATIC
        else:
            return[]
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models from endpoint: {str(e)}")

def set_mode(use_api: bool):
    global USE_API_MODE
    USE_API_MODE = use_api

def set_task_model(task: str, api_model: str):
    if task in API_MODELS:
        API_MODELS[task] = api_model

def get_task_models() -> dict:
    return API_MODELS.copy()

def get_mode() -> str:
    return "API" if USE_API_MODE else "Ollama"

def is_configured() -> bool:
    return API_CLIENT is not None and all(API_MODELS.values())
