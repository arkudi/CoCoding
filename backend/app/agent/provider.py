import logging
import time
from typing import Any, Self

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from app.agent.types import AssistantTurn, ModelClient, ToolCall

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (0.25, 0.5)


class ModelProviderError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class ModelProtocolError(ModelProviderError):
    def __init__(self, safe_message: str = "The model provider returned an invalid response.") -> None:
        super().__init__("protocol_error", safe_message)


class DeepSeekClient(ModelClient):
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_settings(cls, settings: Any) -> Self:
        return cls(
            client=OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            ),
            model=settings.deepseek_model,
        )

    def complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> AssistantTurn:
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    extra_body={"thinking": {"type": "disabled"}},
                )
                return self._to_turn(completion)
            except (RateLimitError, APITimeoutError, APIConnectionError) as error:
                if self._retry_or_raise(error, attempt):
                    continue
                raise AssertionError("unreachable")
            except APIStatusError as error:
                if error.status_code >= 500 and self._retry_or_raise(error, attempt):
                    continue
                raise self._provider_error(error) from error
            except OpenAIError as error:
                self._log_error(error, attempt)
                raise self._provider_error(error) from error

        raise AssertionError("unreachable")

    @staticmethod
    def _to_turn(completion: Any) -> AssistantTurn:
        if not completion.choices:
            raise ModelProtocolError()

        message = completion.choices[0].message
        tool_calls = tuple(
            ToolCall(
                id=tool_call.id,
                name=tool_call.function.name,
                arguments_json=tool_call.function.arguments,
            )
            for tool_call in (message.tool_calls or ())
        )
        return AssistantTurn(content=message.content, tool_calls=tool_calls)

    def _retry_or_raise(self, error: OpenAIError, attempt: int) -> bool:
        self._log_error(error, attempt)
        if attempt == len(_RETRY_DELAYS):
            raise self._provider_error(error) from error
        time.sleep(_RETRY_DELAYS[attempt])
        return True

    @staticmethod
    def _log_error(error: OpenAIError, attempt: int) -> None:
        logger.warning(
            "Model provider request failed (type=%s, attempt=%d)",
            type(error).__name__,
            attempt + 1,
        )

    @staticmethod
    def _provider_error(error: OpenAIError) -> ModelProviderError:
        if isinstance(error, AuthenticationError):
            return ModelProviderError(
                "authentication_failed", "The model provider credentials are invalid."
            )
        if isinstance(error, BadRequestError):
            return ModelProviderError(
                "invalid_request", "The model provider request is invalid."
            )
        if isinstance(error, (RateLimitError, APITimeoutError, APIConnectionError)) or (
            isinstance(error, APIStatusError) and error.status_code >= 500
        ):
            return ModelProviderError(
                "provider_unavailable", "The model provider is temporarily unavailable."
            )
        return ModelProviderError("provider_error", "The model provider request failed.")
