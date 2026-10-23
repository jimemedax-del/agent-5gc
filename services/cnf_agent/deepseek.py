"""DeepSeek-specific SDK adapter; credentials and reasoning stay out of traces."""
from importlib.metadata import PackageNotFoundError, version
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator

from .provider import ProviderCallError
from .schemas import (
    Contract, Extraction, Interpretation, InterpretationReply, Message, ModelReply,
    ReadFacts, ReadRequest, Requirement,
)


PROMPTS = Path(__file__).with_name('prompts')


class DeepSeekSettings(Contract):
    base_url: str = 'https://api.deepseek.com'
    model: str = Field(default='deepseek-flash', min_length=1)
    api_key: SecretStr = Field(repr=False)
    reasoning_effort: Literal['high'] = 'high'
    thinking: Literal['enabled'] = 'enabled'
    timeout_seconds: float = Field(default=45, gt=0, le=120)
    max_output_tokens: int = Field(default=4096, ge=256, le=8192)

    @field_validator('base_url')
    @classmethod
    def official_endpoint(cls, value):
        if value.rstrip('/') not in ('https://api.deepseek.com', 'https://api.deepseek.com/v1'):
            raise ValueError('This adapter only sends credentials to the official DeepSeek endpoint')
        return value.rstrip('/')

    @field_validator('api_key')
    @classmethod
    def nonempty_key(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError('Missing DeepSeek credential')
        return value

    @classmethod
    def load(cls, path: Path | None = None):
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path else {}
            if os.environ.get('DEEPSEEK_API_KEY'):
                data['api_key'] = os.environ['DEEPSEEK_API_KEY']
            return cls.model_validate(data)
        except (OSError, ValueError, ValidationError):
            raise ValueError('DeepSeek configuration is missing or invalid; credential details are hidden') from None


class ReadArguments(Contract):
    scope: Literal['overview']


READ_TOOL = {'type': 'function', 'function': {
    'name': 'inspect_network', 'description': 'Read aggregate network resource observations; never writes configuration.',
    'parameters': ReadArguments.model_json_schema(),
}}


class DeepSeekProvider:
    def __init__(self, settings: DeepSeekSettings, client=None):
        self.settings = settings
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=settings.api_key.get_secret_value(), base_url=settings.base_url,
                            timeout=settings.timeout_seconds, max_retries=0)
        self.client = client
        # Thinking tool conversations require reasoning_content on the next request.
        # Keep it ephemeral; never put it in SQLite, JSONL or terminal output.
        self._read_contexts: dict[str, list] = {}

    def close(self):
        self._read_contexts.clear()
        self.client.close()

    def _complete(self, messages: list, max_tokens: int, *, tools=False, final_json=False):
        estimated_input_bound = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 512
        if tools:
            estimated_input_bound += len(json.dumps(READ_TOOL))
        output_limit = min(self.settings.max_output_tokens, max_tokens - estimated_input_bound)
        if output_limit < 256:
            raise ProviderCallError('insufficient_token_budget')
        args = dict(model=self.settings.model, messages=messages, stream=False, max_tokens=output_limit,
                    reasoning_effort=self.settings.reasoning_effort,
                    extra_body={'thinking': {'type': self.settings.thinking}})
        if tools:
            args.update(tools=[READ_TOOL], tool_choice='none' if final_json else 'auto')
        if not tools or final_json:
            args['response_format'] = {'type': 'json_object'}
        try:
            response = self.client.chat.completions.create(**args)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            code = f'deepseek_http_{status}' if isinstance(status, int) else 'deepseek_transport_error'
            raise ProviderCallError(code) from None
        usage = response.usage
        tokens = getattr(usage, 'total_tokens', None)
        if not isinstance(tokens, int) or tokens < 0:
            raise ProviderCallError('missing_token_usage')
        details = getattr(usage, 'completion_tokens_details', None)
        try:
            sdk_version = version('openai')
        except PackageNotFoundError:
            sdk_version = 'injected-test-client'
        metadata = dict(provider='deepseek', requested_model=self.settings.model,
                        returned_model=response.model, response_id=response.id, sdk_version=sdk_version,
                        prompt_hash=hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest(),
                        prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                        reasoning_tokens=getattr(details, 'reasoning_tokens', None))
        if not response.choices or response.choices[0].finish_reason not in ('stop', 'tool_calls'):
            raise ProviderCallError('incomplete_model_response', tokens, metadata)
        return response.choices[0].message, tokens, metadata

    def _json_reply(self, messages, max_tokens, schema):
        message, tokens, metadata = self._complete(messages, max_tokens)
        try:
            value = schema.model_validate_json(message.content)
        except (ValueError, TypeError):
            raise ProviderCallError('invalid_structured_output', tokens, metadata) from None
        return value, tokens, metadata

    def extract(self, messages: tuple[Message, ...], max_tokens: int) -> ModelReply:
        prompt = (PROMPTS / 'extract.txt').read_text(encoding='utf-8')
        schema = json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
        context = [{'role': 'system', 'content': prompt + '\nJSON Schema:\n' + schema},
                   {'role': 'user', 'content': json.dumps([m.model_dump() for m in messages], ensure_ascii=False)}]
        extraction, tokens, metadata = self._json_reply(context, max_tokens, Extraction)
        return ModelReply(extraction=extraction, tokens=tokens, metadata=metadata)

    def request_read(self, requirement: Requirement, max_tokens: int) -> ReadRequest:
        context = [{'role': 'system', 'content': (PROMPTS / 'request_read.txt').read_text(encoding='utf-8')},
                   {'role': 'user', 'content': requirement.model_dump_json()}]
        message, tokens, metadata = self._complete(context, max_tokens, tools=True)
        calls = message.tool_calls or []
        try:
            if len(calls) != 1 or calls[0].function.name != 'inspect_network':
                raise ValueError('Tool not permitted')
            arguments = ReadArguments.model_validate_json(calls[0].function.arguments)
            call = ReadRequest(tool_call_id=calls[0].id, scope=arguments.scope, tokens=tokens, metadata=metadata)
        except (ValueError, TypeError):
            raise ProviderCallError('invalid_read_tool_call', tokens, metadata) from None
        self._read_contexts[call.tool_call_id] = context + [message.model_dump(exclude_none=True)]
        return call

    def explain(self, call: ReadRequest, facts: ReadFacts, max_tokens: int) -> InterpretationReply:
        context = self._read_contexts.pop(call.tool_call_id)
        context.append({'role': 'tool', 'tool_call_id': call.tool_call_id, 'content': facts.model_dump_json()})
        context.append({'role': 'system', 'content': (PROMPTS / 'explain.txt').read_text(encoding='utf-8')})
        # The same tool definitions accompany the thinking history; the result must be final JSON.
        message, tokens, metadata = self._complete(context, max_tokens, tools=True, final_json=True)
        try:
            if message.tool_calls:
                raise ValueError('Repeated tool request is not permitted')
            interpretation = Interpretation.model_validate_json(message.content)
        except (ValueError, TypeError):
            raise ProviderCallError('invalid_read_interpretation', tokens, metadata) from None
        return InterpretationReply(interpretation=interpretation, tokens=tokens, metadata=metadata)
