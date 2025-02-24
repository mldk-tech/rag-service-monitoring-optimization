"""Text generation module for RAG responses."""

from typing import Dict, Any, Optional, List, Union
import logging
import time
import asyncio
from dataclasses import dataclass
from functools import wraps
from contextlib import asynccontextmanager
import numpy as np
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field, validator
from typing_extensions import AsyncGenerator

from rag_service.monitoring.metrics import MetricsManager

class GenerationError(Exception):
    """Base class for generation-related errors."""
    pass

class ModelLoadError(GenerationError):
    """Raised when model loading fails."""
    pass

class GenerationTimeout(GenerationError):
    """Raised when generation times out."""
    pass

class InvalidInputError(GenerationError):
    """Raised when input validation fails."""
    pass

@dataclass
class GenerationResult:
    """Model for generation results."""
    text: str
    metadata: Dict[str, Any]
    generation_time: float
    token_count: int
    model_name: str

    def __post_init__(self):
        """Validate fields after initialization."""
        if self.generation_time < 0:
            raise ValueError("generation_time must be non-negative")
        if self.token_count < 0:
            raise ValueError("token_count must be non-negative")
        if not self.text:
            raise ValueError("text cannot be empty")
        if not self.model_name:
            raise ValueError("model_name cannot be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "text": self.text,
            "metadata": self.metadata,
            "generation_time": self.generation_time,
            "token_count": self.token_count,
            "model_name": self.model_name
        }

class GenerationConfig(BaseModel):
    """Configuration for text generation."""
    model_name: str = Field(..., min_length=1)
    max_tokens: int = Field(1024, ge=1, le=4096)
    temperature: float = Field(0.7, ge=0.0, le=1.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    frequency_penalty: float = Field(0.0, ge=-2.0, le=2.0)
    presence_penalty: float = Field(0.0, ge=-2.0, le=2.0)
    stop_sequences: Optional[List[str]] = None
    timeout_seconds: float = Field(30.0, ge=0.0)
    max_retries: int = Field(3, ge=0)
    retry_delay_seconds: float = Field(1.0, ge=0.0)
    device: str = Field("cpu")
    batch_size: int = Field(1, ge=1)

    @validator("stop_sequences")
    def validate_stop_sequences(cls, v):
        """Validate stop sequences."""
        if v is not None:
            if not all(isinstance(s, str) for s in v):
                raise ValueError("All stop sequences must be strings")
            if not all(s for s in v):
                raise ValueError("Stop sequences cannot be empty")
        return v

class GenerationManager:
    """Manager for text generation operations."""

    def __init__(self, metrics_manager: MetricsManager, config: Dict[str, Any]):
        """Initialize generation manager.
        
        Args:
            metrics_manager: Metrics manager instance
            config: Generation configuration
            
        Raises:
            ValueError: If configuration is invalid
        """
        if not isinstance(metrics_manager, MetricsManager):
            raise TypeError("metrics_manager must be an instance of MetricsManager")
        
        self.metrics_manager = metrics_manager
        self.config = self._validate_config(config)
        self.tracer = trace.get_tracer(__name__)
        self.model = None
        self.tokenizer = None
        self._lock = asyncio.Lock()
        self._generation_semaphore = asyncio.Semaphore(
            self.config.get("max_concurrent_generations", 10)
        )

    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate configuration.
        
        Args:
            config: Raw configuration dictionary
            
        Returns:
            Validated configuration
            
        Raises:
            ValueError: If configuration is invalid
        """
        required_fields = {"model", "api_key", "max_concurrent_generations"}
        missing_fields = required_fields - set(config.keys())
        if missing_fields:
            raise ValueError(f"Missing required config fields: {missing_fields}")
            
        return config

    async def initialize(self):
        """Initialize models and tokenizers."""
        try:
            # Load model configuration
            model_config = self.config.get("model", {})
            self.generation_config = GenerationConfig(
                model_name=model_config.get("name", "gpt-3.5-turbo"),
                max_tokens=model_config.get("max_tokens", 1024),
                temperature=model_config.get("temperature", 0.7),
                top_p=model_config.get("top_p", 0.9),
                frequency_penalty=model_config.get("frequency_penalty", 0.0),
                presence_penalty=model_config.get("presence_penalty", 0.0),
                stop_sequences=model_config.get("stop_sequences", None),
                timeout_seconds=model_config.get("timeout_seconds", 30.0),
                max_retries=model_config.get("max_retries", 3),
                retry_delay_seconds=model_config.get("retry_delay_seconds", 1.0),
                device=model_config.get("device", "cpu"),
                batch_size=model_config.get("batch_size", 1)
            )

            # Initialize model and tokenizer with retries
            async with self._retry_context():
                await self._load_model()
                
            logging.info(
                "Generation manager initialized with model: %s",
                self.generation_config.model_name
            )

        except Exception as e:
            logging.error("Failed to initialize generation manager: %s", str(e))
            raise ModelLoadError("Failed to initialize generation manager") from e

    @asynccontextmanager
    async def _retry_context(self):
        """Retry context manager with exponential backoff."""
        retries = 0
        while True:
            try:
                yield
                break
            except Exception as e:
                retries += 1
                if retries >= self.generation_config.max_retries:
                    raise
                delay = self.generation_config.retry_delay_seconds * (2 ** (retries - 1))
                logging.warning(
                    "Retry %d/%d after error: %s. Waiting %.1f seconds...",
                    retries,
                    self.generation_config.max_retries,
                    str(e),
                    delay
                )
                await asyncio.sleep(delay)

    async def _load_model(self):
        """Load model and tokenizer."""
        async with self._lock:
            # This is a placeholder for actual model loading
            # In a real implementation, you would:
            # 1. Load the appropriate model (e.g., from HuggingFace, OpenAI, etc.)
            # 2. Load the tokenizer
            # 3. Move model to appropriate device (CPU/GPU)
            # 4. Set up any model-specific configurations
            # 5. Validate model outputs
            pass

    @MetricsManager.get_instance().with_metrics("generation", "generate")
    async def generate(
        self,
        prompt: str,
        context: Optional[List[str]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stream: bool = False
    ) -> Union[GenerationResult, AsyncGenerator[str, None]]:
        """Generate text based on prompt and context.
        
        Args:
            prompt: Generation prompt
            context: Optional context documents
            max_tokens: Optional override for max tokens
            temperature: Optional override for temperature
            stream: Whether to stream the output
            
        Returns:
            Generation result or async generator for streaming
            
        Raises:
            GenerationError: If generation fails
            GenerationTimeout: If generation times out
            InvalidInputError: If input validation fails
        """
        # Input validation
        if not prompt:
            raise InvalidInputError("Prompt cannot be empty")
        if len(prompt) > self.config.get("max_prompt_length", 4096):
            raise InvalidInputError("Prompt too long")
            
        start_time = time.time()
        
        async with self._generation_semaphore:
            with self.tracer.start_span("generate_text") as span:
                try:
                    # Prepare generation inputs
                    inputs = await self._prepare_inputs(prompt, context)
                    
                    # Get generation parameters
                    params = self._get_generation_params(max_tokens, temperature)
                    
                    # Set up timeout
                    timeout = params.get("timeout_seconds", 30.0)
                    
                    try:
                        async with asyncio.timeout(timeout):
                            if stream:
                                return self._generate_stream(inputs, params)
                            else:
                                # Generate text (placeholder)
                                generated_text = "This is a placeholder generated text."
                                token_count = len(generated_text.split())
                    except asyncio.TimeoutError:
                        raise GenerationTimeout(
                            f"Generation timed out after {timeout} seconds"
                        )
                    
                    # Record metrics
                    duration = time.time() - start_time
                    self.metrics_manager.record_model_inference(
                        model_type=self.generation_config.model_name,
                        duration=duration,
                        batch_size=1,
                        success=True
                    )
                    
                    span.set_attribute("token_count", token_count)
                    span.set_attribute("model_name", self.generation_config.model_name)
                    span.set_status(Status(StatusCode.OK))
                    
                    return GenerationResult(
                        text=generated_text,
                        metadata={
                            "model": self.generation_config.model_name,
                            "params": params,
                            "input_tokens": len(inputs.split()),
                            "total_tokens": token_count + len(inputs.split())
                        },
                        generation_time=duration,
                        token_count=token_count,
                        model_name=self.generation_config.model_name
                    )

                except Exception as e:
                    duration = time.time() - start_time
                    self.metrics_manager.record_model_inference(
                        model_type=self.generation_config.model_name,
                        duration=duration,
                        batch_size=1,
                        success=False
                    )
                    
                    span.set_status(Status(StatusCode.ERROR))
                    logging.error("Generation failed: %s", str(e))
                    
                    if isinstance(e, (GenerationTimeout, InvalidInputError)):
                        raise
                    raise GenerationError("Generation failed") from e

    async def _generate_stream(
        self,
        inputs: str,
        params: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """Stream generation results.
        
        Args:
            inputs: Prepared input text
            params: Generation parameters
            
        Yields:
            Generated text chunks
        """
        # This is a placeholder for streaming implementation
        chunks = ["This ", "is ", "a ", "streaming ", "response."]
        for chunk in chunks:
            await asyncio.sleep(0.1)  # Simulate generation time
            yield chunk

    async def _prepare_inputs(
        self,
        prompt: str,
        context: Optional[List[str]] = None
    ) -> str:
        """Prepare inputs for generation.
        
        Args:
            prompt: Generation prompt
            context: Optional context documents
            
        Returns:
            Prepared input text
            
        Raises:
            InvalidInputError: If inputs are invalid
        """
        # Sanitize inputs
        prompt = prompt.strip()
        if context:
            context = [c.strip() for c in context if c.strip()]
            
        # Apply length limits
        max_context_length = self.config.get("max_context_length", 2048)
        if context:
            total_context_length = sum(len(c.split()) for c in context)
            if total_context_length > max_context_length:
                # Truncate context while preserving most recent documents
                truncated_context = []
                current_length = 0
                for doc in reversed(context):
                    doc_length = len(doc.split())
                    if current_length + doc_length <= max_context_length:
                        truncated_context.insert(0, doc)
                        current_length += doc_length
                    else:
                        break
                context = truncated_context
                
        # Combine context and prompt using template
        template = self.config.get("prompt_template", "{context}\n\n{prompt}")
        context_text = " ".join(context) if context else ""
        return template.format(context=context_text, prompt=prompt)

    def _get_generation_params(
        self,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None
    ) -> Dict[str, Any]:
        """Get generation parameters.
        
        Args:
            max_tokens: Optional override for max tokens
            temperature: Optional override for temperature
            
        Returns:
            Generation parameters
            
        Raises:
            ValueError: If parameters are invalid
        """
        params = self.generation_config.dict()
        
        if max_tokens is not None:
            if not 1 <= max_tokens <= 4096:
                raise ValueError("max_tokens must be between 1 and 4096")
            params["max_tokens"] = max_tokens
            
        if temperature is not None:
            if not 0 <= temperature <= 1:
                raise ValueError("temperature must be between 0 and 1")
            params["temperature"] = temperature
            
        return params

    async def close(self):
        """Clean up resources."""
        async with self._lock:
            try:
                if self.model:
                    # Free model resources
                    self.model = None
                    
                if self.tokenizer:
                    # Free tokenizer resources
                    self.tokenizer = None
                    
                # Clear any caches
                if hasattr(self, '_cache'):
                    self._cache.clear()
                    
                # Cancel any pending tasks
                tasks = [t for t in asyncio.all_tasks() 
                        if t != asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                        
                logging.info("Generation manager cleaned up successfully")
                
            except Exception as e:
                logging.error("Error during cleanup: %s", str(e))
                raise 