"""Cloud connections shared by settings, model discovery, and inference.

Names are persisted in task assignments and model references. The three legacy
names and credential slots therefore remain stable, including the editable
OpenAI-Compatible connection. The named OpenAI service has its own slot.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from graphlink_task_config import API_PROVIDER_ANTHROPIC, API_PROVIDER_GEMINI, API_PROVIDER_OPENAI


@dataclass(frozen=True)
class CloudProvider:
    name: str
    key_slot: str
    group: str
    description: str
    base_url: str
    docs_url: str
    env_vars: tuple[str, ...]
    label: str = ""
    protocol: str = "openai"
    supports_catalog: bool = True
    supports_image_generation: bool = False
    supports_tools: bool = True
    replay_reasoning: bool = False
    endpoint_hint: str = ""
    endpoint_pattern: str = ""

    @property
    def editable_endpoint(self) -> bool:
        return bool(self.endpoint_hint)

    @property
    def secret_field(self) -> str:
        return f"{self.key_slot}_api_key"

    def endpoint(self, submitted_url: str | None) -> str:
        """Resolve the destination before loading any stored or environment key."""
        if not self.editable_endpoint:
            return self.base_url
        url = (submitted_url or "").strip().rstrip("/")
        if not url:
            raise ValueError(f"Please enter the Base URL for {self.label or self.name}.")
        # Keep credentials out of URL fields, which are displayed in clear text.
        # Cloud deployment profiles accept only the provider's documented format.
        parsed = urlsplit(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or "\\" in url
                or any(c.isspace() or ord(c) < 32 for c in url)):
            raise ValueError("Enter an HTTP(S) Base URL without credentials, a query, or a fragment.")
        if self.endpoint_pattern and not re.fullmatch(self.endpoint_pattern, url):
            raise ValueError(f"Enter a Base URL matching {self.endpoint_hint}.")
        return url

    def environment_key_allowed(self, base_url: str | None) -> bool:
        # A custom endpoint must never inherit the official OpenAI credential.
        # Tenant-specific deployments require deliberate entry on first setup.
        if self.editable_endpoint:
            return bool(self.base_url and self.base_url == (base_url or "").rstrip("/"))
        return True


DEFAULT_CLOUD_PROVIDER = "OpenAI"
LAB = "Model labs"
CLOUD = "Cloud platforms"
GATEWAY = "Model gateways"

_PROVIDERS = (
    CloudProvider(DEFAULT_CLOUD_PROVIDER, "openai_direct", LAB, "GPT and OpenAI reasoning models.",
                  "https://api.openai.com/v1", "https://platform.openai.com/docs/models",
                  ("OPENAI_API_KEY",), supports_image_generation=True),
    CloudProvider(API_PROVIDER_ANTHROPIC, "anthropic", LAB, "Claude models through the native Messages API.",
                  "https://api.anthropic.com/v1", "https://platform.claude.com/docs/en/api/overview",
                  ("GRAPHLINK_ANTHROPIC_API_KEY", "GRAPHITE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"), protocol="anthropic"),
    CloudProvider(API_PROVIDER_GEMINI, "gemini", LAB, "Gemini models through Google AI Studio.",
                  "https://generativelanguage.googleapis.com/v1beta", "https://ai.google.dev/gemini-api/docs/models",
                  ("GRAPHLINK_GEMINI_API_KEY", "GRAPHITE_GEMINI_API_KEY", "GEMINI_API_KEY"),
                  protocol="gemini", supports_image_generation=True),
    CloudProvider("xAI", "xai", LAB, "Grok models through the xAI API.",
                  "https://api.x.ai/v1", "https://docs.x.ai/developers/rest-api-reference/inference/chat-completions",
                  ("XAI_API_KEY",), supports_image_generation=True),
    CloudProvider("Meta", "meta", LAB, "Muse models through the Meta Model API.",
                  "https://api.meta.ai/v1", "https://dev.meta.ai/docs/api-reference", ("MODEL_API_KEY",)),
    CloudProvider("Mistral AI", "mistral", LAB, "Mistral models on La Plateforme.",
                  "https://api.mistral.ai/v1", "https://docs.mistral.ai/api/endpoint/models", ("MISTRAL_API_KEY",)),
    CloudProvider("DeepSeek", "deepseek", LAB, "DeepSeek chat and reasoning models.",
                  "https://api.deepseek.com", "https://api-docs.deepseek.com", ("DEEPSEEK_API_KEY",), replay_reasoning=True),
    CloudProvider("Cohere", "cohere", LAB, "Command models through Cohere’s compatibility API.",
                  "https://api.cohere.ai/compatibility/v1", "https://docs.cohere.com/docs/compatibility-api",
                  ("COHERE_API_KEY",)),
    CloudProvider("Moonshot AI", "moonshot", LAB, "Kimi models on Moonshot’s international endpoint.",
                  "https://api.moonshot.ai/v1", "https://platform.kimi.ai/docs/get-your-api-key", ("MOONSHOT_API_KEY",), replay_reasoning=True),
    CloudProvider("Z.AI", "zai", LAB, "GLM models with a standard Z.AI platform key.",
                  "https://api.z.ai/api/paas/v4", "https://docs.z.ai/guides/overview/quick-start", ("ZAI_API_KEY",),
                  supports_catalog=False, replay_reasoning=True),
    CloudProvider("MiniMax", "minimax", LAB, "MiniMax text models on the international API.",
                  "https://api.minimax.io/v1", "https://platform.minimax.io/docs/api-reference/text-openai-api",
                  ("MINIMAX_API_KEY",)),
    CloudProvider("Baidu Qianfan", "qianfan", LAB, "ERNIE and partner models on Baidu AI Cloud.",
                  "https://qianfan.baidubce.com/v2", "https://ai.baidu.com/ai-doc/WENXINWORKSHOP/2m3fihw8s",
                  ("QIANFAN_API_KEY",)),
    CloudProvider("ByteDance ModelArk", "modelark", LAB,
                  "Seed and Doubao models on BytePlus or Volcengine. Use a model ID or deployment ID from your region.",
                  "https://ark.ap-southeast.bytepluses.com/api/v3", "https://docs.byteplus.com/en/docs/modelark/region-availability",
                  ("ARK_API_KEY",), endpoint_hint="https://ark.ap-southeast.bytepluses.com/api/v3",
                  endpoint_pattern=r"https://ark\.(?:ap-southeast\.bytepluses\.com|cn-beijing\.volces\.com)/api/v3"),
    CloudProvider("Tencent Hunyuan", "hunyuan", LAB, "Hunyuan language and vision models through Tencent Cloud.",
                  "https://api.hunyuan.cloud.tencent.com/v1", "https://cloud.tencent.com/document/product/1729/111007",
                  ("HUNYUAN_API_KEY",), supports_catalog=False),
    CloudProvider("Alibaba Cloud Model Studio", "dashscope", CLOUD, "Qwen and partner models in your DashScope region.",
                  "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                  "https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope",
                  ("DASHSCOPE_API_KEY",), supports_catalog=False,
                  endpoint_hint="https://<workspace>.<region>.maas.aliyuncs.com/compatible-mode/v1",
                  endpoint_pattern=r"https://(?:dashscope(?:-[a-z0-9-]+)?|cn-hongkong\.dashscope|[a-z0-9-]+\.[a-z0-9-]+\.maas)\.aliyuncs\.com/compatible-mode/v1"),
    CloudProvider("Amazon Bedrock", "bedrock", CLOUD, "Bedrock models in your AWS region. Use a Bedrock API key.",
                  "https://bedrock-mantle.us-east-1.api.aws/v1",
                  "https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html",
                  ("AWS_BEARER_TOKEN_BEDROCK",),
                  endpoint_hint="https://bedrock-mantle.<region>.api.aws/v1",
                  endpoint_pattern=r"https://bedrock-mantle\.[a-z0-9-]+\.api\.aws/v1"),
    CloudProvider("Azure OpenAI", "azure", CLOUD, "Enter your Foundry resource URL and use the model deployment name.",
                  "", "https://learn.microsoft.com/azure/ai-foundry/openai/api-version-lifecycle", (),
                  supports_catalog=False, endpoint_hint="https://<resource>.openai.azure.com/openai/v1",
                  endpoint_pattern=r"https://[a-z0-9-]+\.(?:openai\.azure\.com|services\.ai\.azure\.com)/openai/v1"),
    CloudProvider("Google Vertex AI", "vertex", CLOUD,
                  "Use a Google Cloud OAuth access token. Replace it when it expires with gcloud auth print-access-token.",
                  "", "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/openai/auth-and-credentials",
                  (), supports_catalog=False,
                  endpoint_hint="https://aiplatform.googleapis.com/v1/projects/<project>/locations/<location>/endpoints/openapi",
                  endpoint_pattern=r"https://(?:[a-z0-9-]+-)?aiplatform\.googleapis\.com/v1(?:beta1)?/projects/[a-z0-9-]+/locations/[a-z0-9-]+/endpoints/openapi"),
    CloudProvider("NVIDIA", "nvidia", CLOUD, "Hosted NIM endpoints for Nemotron, Llama and partner models.",
                  "https://integrate.api.nvidia.com/v1", "https://docs.api.nvidia.com/nim/reference/llm-apis",
                  ("NVIDIA_API_KEY",)),
    CloudProvider("Groq", "groq", CLOUD, "Open models hosted on Groq inference hardware.",
                  "https://api.groq.com/openai/v1", "https://console.groq.com/docs/openai", ("GROQ_API_KEY",)),
    CloudProvider("Cerebras", "cerebras", CLOUD, "Chat models hosted on Cerebras Inference.",
                  "https://api.cerebras.ai/v1", "https://inference-docs.cerebras.ai/resources/openai", ("CEREBRAS_API_KEY",)),
    CloudProvider("Together AI", "together", CLOUD, "Serverless open models and dedicated deployments.",
                  "https://api.together.ai/v1", "https://docs.together.ai/docs/inference/openai-compatibility",
                  ("TOGETHER_API_KEY",)),
    CloudProvider("Fireworks AI", "fireworks", CLOUD, "Discover public serverless models or enter a deployment ID.",
                  "https://api.fireworks.ai/inference/v1", "https://docs.fireworks.ai/tools-sdks/openai-compatibility",
                  ("FIREWORKS_API_KEY",)),
    CloudProvider("Perplexity", "perplexity", GATEWAY, "Models hosted on Perplexity Orchestrator.",
                  "https://api.perplexity.ai/orchestrator/v1", "https://docs.perplexity.ai/docs/orchestrator/quickstart",
                  ("PERPLEXITY_API_KEY",)),
    CloudProvider("Hugging Face", "huggingface", GATEWAY, "Route Hub models to Hugging Face Inference Providers.",
                  "https://router.huggingface.co/v1", "https://huggingface.co/docs/inference-providers", ("HF_TOKEN",)),
    CloudProvider("OpenRouter", "openrouter", GATEWAY, "Use one key across multiple model labs and hosts.",
                  "https://openrouter.ai/api/v1", "https://openrouter.ai/docs/quickstart", ("OPENROUTER_API_KEY",)),
    CloudProvider(API_PROVIDER_OPENAI, "openai", "Custom", "Connect a self-hosted or other OpenAI-compatible endpoint.",
                  "https://api.openai.com/v1", "", ("GRAPHLINK_OPENAI_API_KEY", "GRAPHITE_OPENAI_API_KEY", "OPENAI_API_KEY"),
                  label="Custom endpoint", endpoint_hint="https://your-endpoint.example/v1", supports_image_generation=True),
)

CLOUD_PROVIDERS = {profile.name: profile for profile in _PROVIDERS}
