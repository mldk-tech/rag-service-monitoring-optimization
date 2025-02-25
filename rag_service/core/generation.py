"""Text generation module for RAG responses."""

from typing import Dict, Any, Optional, List, Union, Tuple
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
        """Initialize the generation manager and load models."""
        with self.tracer.start_as_current_span("generation_manager_initialize") as span:
            try:
                # Load the model based on configuration
                await self._load_model()
                
                # Initialize token counter
                self.token_counter = 0
                
                # Initialize cache for generation results
                self.cache = {}
                
                # Set initialization flag
                self.initialized = True
                
                span.set_status(Status(StatusCode.OK))
                return True
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                self.metrics_manager.model_load_errors.labels(
                    model=self.config["model_name"]
                ).inc()
                logger.error(
                    "generation_manager_initialization_failed",
                    error=str(e),
                    exc_info=True
                )
                self.initialized = False
                raise ModelLoadError(f"Failed to initialize generation manager: {str(e)}")

    async def _load_model(self):
        """Load the language model based on configuration."""
        model_name = self.config["model_name"]
        device = self.config.get("device", "cpu")
        
        with self.tracer.start_as_current_span("load_model") as span:
            span.set_attribute("model_name", model_name)
            span.set_attribute("device", device)
            
            start_time = time.time()
            
            try:
                # Check if model is a local model or API-based
                if model_name.startswith("openai/"):
                    # OpenAI API integration
                    self.model_type = "openai"
                    self.api_key = self.config.get("api_key")
                    if not self.api_key:
                        raise ValueError("API key is required for OpenAI models")
                    
                    # No actual model loading needed for API-based models
                    self.model = None
                    self.tokenizer = None
                    
                elif model_name.startswith("anthropic/"):
                    # Anthropic API integration
                    self.model_type = "anthropic"
                    self.api_key = self.config.get("api_key")
                    if not self.api_key:
                        raise ValueError("API key is required for Anthropic models")
                    
                    # No actual model loading needed for API-based models
                    self.model = None
                    self.tokenizer = None
                    
                elif model_name.startswith("huggingface/"):
                    # Hugging Face model hub integration
                    self.model_type = "huggingface"
                    
                    # Extract the actual model name from the prefix
                    hf_model_name = model_name.replace("huggingface/", "")
                    
                    # Import required libraries
                    from transformers import AutoModelForCausalLM, AutoTokenizer
                    import torch
                    
                    # Load tokenizer and model
                    self.tokenizer = AutoTokenizer.from_pretrained(hf_model_name)
                    
                    # Load model with appropriate configuration
                    self.model = AutoModelForCausalLM.from_pretrained(
                        hf_model_name,
                        torch_dtype=torch.float16 if device != "cpu" else torch.float32,
                        device_map=device if device != "cpu" else None,
                        low_cpu_mem_usage=True if device != "cpu" else False
                    )
                    
                else:
                    # Local model implementation
                    self.model_type = "local"
                    
                    # Import required libraries
                    from transformers import AutoModelForCausalLM, AutoTokenizer
                    import torch
                    
                    # Load tokenizer and model
                    self.tokenizer = AutoTokenizer.from_pretrained(model_name)
                    
                    # Load model with appropriate configuration
                    self.model = AutoModelForCausalLM.from_pretrained(
                        model_name,
                        torch_dtype=torch.float16 if device != "cpu" else torch.float32,
                        device_map=device if device != "cpu" else None,
                        low_cpu_mem_usage=True if device != "cpu" else False
                    )
                
                # Record successful model loading
                load_time = time.time() - start_time
                self.metrics_manager.model_load_time.labels(
                    model=model_name
                ).observe(load_time)
                
                logger.info(
                    "model_loaded",
                    model=model_name,
                    model_type=self.model_type,
                    load_time=load_time
                )
                
                span.set_status(Status(StatusCode.OK))
                return True
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                self.metrics_manager.model_load_errors.labels(
                    model=model_name
                ).inc()
                logger.error(
                    "model_load_failed",
                    model=model_name,
                    error=str(e),
                    exc_info=True
                )
                raise ModelLoadError(f"Failed to load model {model_name}: {str(e)}")

    @MetricsManager.get_instance().with_metrics("generation", "generate")
    async def generate(
        self,
        prompt: str,
        context: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None
    ) -> GenerationResult:
        """Generate text based on prompt and context."""
        if not self.initialized:
            raise GenerationError("Generation manager not initialized")
        
        with self.tracer.start_as_current_span("generate_text") as span:
            span.set_attribute("model", self.config["model_name"])
            span.set_attribute("prompt_length", len(prompt))
            
            # Apply default values from config if not provided
            max_tokens = max_tokens or self.config.get("max_tokens", 1024)
            temperature = temperature or self.config.get("temperature", 0.7)
            top_p = top_p or self.config.get("top_p", 0.9)
            stop_sequences = stop_sequences or self.config.get("stop_sequences")
            
            # Record parameters in span
            span.set_attribute("max_tokens", max_tokens)
            span.set_attribute("temperature", temperature)
            span.set_attribute("top_p", top_p)
            
            # Prepare full prompt with context if provided
            full_prompt = self._prepare_prompt(prompt, context)
            
            # Check cache for identical requests
            cache_key = self._get_cache_key(full_prompt, max_tokens, temperature, top_p)
            if cache_key in self.cache:
                self.metrics_manager.generation_cache_hits.inc()
                logger.debug("generation_cache_hit", cache_key=cache_key)
                return self.cache[cache_key]
            
            # Start generation timer
            start_time = time.time()
            
            try:
                # Generate text based on model type
                if self.model_type == "openai":
                    result = await self._generate_openai(
                        full_prompt, max_tokens, temperature, top_p, stop_sequences
                    )
                elif self.model_type == "anthropic":
                    result = await self._generate_anthropic(
                        full_prompt, max_tokens, temperature, top_p, stop_sequences
                    )
                elif self.model_type in ["huggingface", "local"]:
                    result = await self._generate_local(
                        full_prompt, max_tokens, temperature, top_p, stop_sequences
                    )
                else:
                    raise GenerationError(f"Unsupported model type: {self.model_type}")
                
                # Calculate generation time
                generation_time = time.time() - start_time
                
                # Create result object
                generation_result = GenerationResult(
                    text=result["text"],
                    metadata=result["metadata"],
                    generation_time=generation_time,
                    token_count=result["token_count"],
                    model_name=self.config["model_name"]
                )
                
                # Update metrics
                self.metrics_manager.generation_time.labels(
                    model=self.config["model_name"]
                ).observe(generation_time)
                self.metrics_manager.generation_tokens.labels(
                    model=self.config["model_name"]
                ).observe(result["token_count"])
                self.token_counter += result["token_count"]
                
                # Cache the result
                self.cache[cache_key] = generation_result
                
                # Log success
                logger.info(
                    "text_generation_success",
                    model=self.config["model_name"],
                    generation_time=generation_time,
                    token_count=result["token_count"]
                )
                
                span.set_status(Status(StatusCode.OK))
                return generation_result
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                self.metrics_manager.generation_errors.labels(
                    model=self.config["model_name"],
                    error_type=type(e).__name__
                ).inc()
                logger.error(
                    "text_generation_failed",
                    model=self.config["model_name"],
                    error=str(e),
                    exc_info=True
                )
                raise GenerationError(f"Text generation failed: {str(e)}")

    async def generate_stream(
        self,
        prompt: str,
        context: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None
    ) -> AsyncGenerator[str, None]:
        """Generate text in streaming mode."""
        if not self.initialized:
            raise GenerationError("Generation manager not initialized")
        
        with self.tracer.start_as_current_span("generate_stream") as span:
            span.set_attribute("model", self.config["model_name"])
            span.set_attribute("prompt_length", len(prompt))
            
            # Apply default values from config if not provided
            max_tokens = max_tokens or self.config.get("max_tokens", 1024)
            temperature = temperature or self.config.get("temperature", 0.7)
            top_p = top_p or self.config.get("top_p", 0.9)
            stop_sequences = stop_sequences or self.config.get("stop_sequences")
            
            # Record parameters in span
            span.set_attribute("max_tokens", max_tokens)
            span.set_attribute("temperature", temperature)
            span.set_attribute("top_p", top_p)
            
            # Prepare full prompt with context if provided
            full_prompt = self._prepare_prompt(prompt, context)
            
            # Start generation timer
            start_time = time.time()
            token_count = 0
            
            try:
                # Generate text stream based on model type
                if self.model_type == "openai":
                    async for chunk, metadata in self._generate_openai_stream(
                        full_prompt, max_tokens, temperature, top_p, stop_sequences
                    ):
                        token_count += metadata.get("tokens", 1)
                        yield chunk
                        
                elif self.model_type == "anthropic":
                    async for chunk, metadata in self._generate_anthropic_stream(
                        full_prompt, max_tokens, temperature, top_p, stop_sequences
                    ):
                        token_count += metadata.get("tokens", 1)
                        yield chunk
                        
                elif self.model_type in ["huggingface", "local"]:
                    async for chunk, metadata in self._generate_local_stream(
                        full_prompt, max_tokens, temperature, top_p, stop_sequences
                    ):
                        token_count += metadata.get("tokens", 1)
                        yield chunk
                        
                else:
                    raise GenerationError(f"Unsupported model type: {self.model_type}")
                
                # Calculate generation time
                generation_time = time.time() - start_time
                
                # Update metrics
                self.metrics_manager.generation_time.labels(
                    model=self.config["model_name"]
                ).observe(generation_time)
                self.metrics_manager.generation_tokens.labels(
                    model=self.config["model_name"]
                ).observe(token_count)
                self.token_counter += token_count
                
                # Log success
                logger.info(
                    "stream_generation_success",
                    model=self.config["model_name"],
                    generation_time=generation_time,
                    token_count=token_count
                )
                
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                self.metrics_manager.generation_errors.labels(
                    model=self.config["model_name"],
                    error_type=type(e).__name__
                ).inc()
                logger.error(
                    "stream_generation_failed",
                    model=self.config["model_name"],
                    error=str(e),
                    exc_info=True
                )
                raise GenerationError(f"Stream generation failed: {str(e)}")

    async def _generate_openai(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop_sequences: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Generate text using OpenAI API."""
        import openai
        
        # Configure API key
        openai.api_key = self.api_key
        
        # Extract model name without prefix
        model = self.config["model_name"].replace("openai/", "")
        
        try:
            # Make API call
            response = await openai.ChatCompletion.acreate(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=stop_sequences,
                stream=False
            )
            
            # Extract generated text
            text = response.choices[0].message.content
            
            # Extract token usage
            token_count = response.usage.total_tokens
            
            return {
                "text": text,
                "token_count": token_count,
                "metadata": {
                    "model": model,
                    "finish_reason": response.choices[0].finish_reason,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens
                }
            }
            
        except Exception as e:
            logger.error(
                "openai_generation_error",
                error=str(e),
                model=model,
                exc_info=True
            )
            raise GenerationError(f"OpenAI generation failed: {str(e)}")

    async def _generate_openai_stream(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop_sequences: Optional[List[str]]
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """Generate text stream using OpenAI API."""
        import openai
        
        # Configure API key
        openai.api_key = self.api_key
        
        # Extract model name without prefix
        model = self.config["model_name"].replace("openai/", "")
        
        try:
            # Make streaming API call
            response = await openai.ChatCompletion.acreate(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=stop_sequences,
                stream=True
            )
            
            # Process streaming response
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    metadata = {
                        "tokens": 1,  # Approximate token count per chunk
                        "model": model
                    }
                    yield content, metadata
                    
        except Exception as e:
            logger.error(
                "openai_stream_generation_error",
                error=str(e),
                model=model,
                exc_info=True
            )
            raise GenerationError(f"OpenAI stream generation failed: {str(e)}")

    async def _generate_anthropic(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop_sequences: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Generate text using Anthropic API."""
        import anthropic
        
        # Configure client
        client = anthropic.Anthropic(api_key=self.api_key)
        
        # Extract model name without prefix
        model = self.config["model_name"].replace("anthropic/", "")
        
        try:
            # Make API call
            response = await client.completions.create(
                model=model,
                prompt=f"{anthropic.HUMAN_PROMPT} {prompt}{anthropic.AI_PROMPT}",
                max_tokens_to_sample=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop_sequences=stop_sequences or []
            )
            
            # Extract generated text
            text = response.completion
            
            # Estimate token count (Anthropic doesn't provide this directly)
            token_count = len(prompt.split()) + len(text.split())
            
            return {
                "text": text,
                "token_count": token_count,
                "metadata": {
                    "model": model,
                    "stop_reason": response.stop_reason
                }
            }
            
        except Exception as e:
            logger.error(
                "anthropic_generation_error",
                error=str(e),
                model=model,
                exc_info=True
            )
            raise GenerationError(f"Anthropic generation failed: {str(e)}")

    async def _generate_anthropic_stream(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop_sequences: Optional[List[str]]
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """Generate text stream using Anthropic API."""
        import anthropic
        
        # Configure client
        client = anthropic.Anthropic(api_key=self.api_key)
        
        # Extract model name without prefix
        model = self.config["model_name"].replace("anthropic/", "")
        
        try:
            # Make streaming API call
            response = await client.completions.create(
                model=model,
                prompt=f"{anthropic.HUMAN_PROMPT} {prompt}{anthropic.AI_PROMPT}",
                max_tokens_to_sample=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop_sequences=stop_sequences or [],
                stream=True
            )
            
            # Process streaming response
            async for chunk in response:
                if chunk.completion:
                    metadata = {
                        "tokens": 1,  # Approximate token count per chunk
                        "model": model
                    }
                    yield chunk.completion, metadata
                    
        except Exception as e:
            logger.error(
                "anthropic_stream_generation_error",
                error=str(e),
                model=model,
                exc_info=True
            )
            raise GenerationError(f"Anthropic stream generation failed: {str(e)}")

    async def _generate_local(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop_sequences: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Generate text using local model."""
        import torch
        
        try:
            # Tokenize input
            inputs = self.tokenizer(prompt, return_tensors="pt")
            input_ids = inputs.input_ids.to(self.model.device)
            
            # Count input tokens
            input_token_count = len(inputs.input_ids[0])
            
            # Generate text
            with torch.no_grad():
                outputs = self.model.generate(
                    input_ids,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    do_sample=temperature > 0,
                    pad_token_id=self.tokenizer.eos_token_id,
                    stopping_criteria=self._create_stopping_criteria(stop_sequences) if stop_sequences else None
                )
            
            # Decode output tokens
            output_token_count = len(outputs[0]) - input_token_count
            generated_text = self.tokenizer.decode(
                outputs[0][input_token_count:],
                skip_special_tokens=True
            )
            
            # Apply stop sequence truncation manually if needed
            if stop_sequences:
                for stop_seq in stop_sequences:
                    if stop_seq in generated_text:
                        generated_text = generated_text[:generated_text.index(stop_seq)]
            
            return {
                "text": generated_text,
                "token_count": input_token_count + output_token_count,
                "metadata": {
                    "model": self.config["model_name"],
                    "input_tokens": input_token_count,
                    "output_tokens": output_token_count
                }
            }
            
        except Exception as e:
            logger.error(
                "local_generation_error",
                error=str(e),
                model=self.config["model_name"],
                exc_info=True
            )
            raise GenerationError(f"Local model generation failed: {str(e)}")

    async def _generate_local_stream(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop_sequences: Optional[List[str]]
    ) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
        """Generate text stream using local model."""
        import torch
        
        try:
            # Tokenize input
            inputs = self.tokenizer(prompt, return_tensors="pt")
            input_ids = inputs.input_ids.to(self.model.device)
            
            # Count input tokens
            input_token_count = len(inputs.input_ids[0])
            
            # Generate text token by token for streaming
            generated_ids = input_ids.clone()
            past_key_values = None
            
            for _ in range(max_tokens):
                # Generate next token
                with torch.no_grad():
                    if past_key_values is None:
                        outputs = self.model(
                            generated_ids,
                            return_dict=True,
                            use_cache=True
                        )
                    else:
                        outputs = self.model(
                            generated_ids[:, -1:],
                            past_key_values=past_key_values,
                            return_dict=True,
                            use_cache=True
                        )
                
                # Get logits and past key values
                logits = outputs.logits[:, -1, :]
                past_key_values = outputs.past_key_values
                
                # Apply temperature and top_p sampling
                if temperature > 0:
                    logits = logits / temperature
                    filtered_logits = self._top_p_filtering(logits, top_p)
                    probs = torch.softmax(filtered_logits, dim=-1)
                    next_token = torch.multinomial(probs, num_samples=1)
                else:
                    next_token = torch.argmax(logits, dim=-1).unsqueeze(-1)
                
                # Append next token to generated sequence
                generated_ids = torch.cat([generated_ids, next_token], dim=-1)
                
                # Decode the new token
                new_token_text = self.tokenizer.decode(
                    next_token[0],
                    skip_special_tokens=True
                )
                
                # Check for stop sequences
                if stop_sequences:
                    # Get the full text generated so far
                    full_text = self.tokenizer.decode(
                        generated_ids[0][input_token_count:],
                        skip_special_tokens=True
                    )
                    
                    # Check if any stop sequence is in the text
                    should_stop = False
                    for stop_seq in stop_sequences:
                        if stop_seq in full_text:
                            should_stop = True
                            break
                            
                    if should_stop:
                        break
                
                # Check for EOS token
                if next_token[0].item() == self.tokenizer.eos_token_id:
                    break
                
                # Yield the new token
                if new_token_text:
                    metadata = {
                        "tokens": 1,
                        "model": self.config["model_name"]
                    }
                    yield new_token_text, metadata
                    
                # Small delay to simulate streaming
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(
                "local_stream_generation_error",
                error=str(e),
                model=self.config["model_name"],
                exc_info=True
            )
            raise GenerationError(f"Local model stream generation failed: {str(e)}")

    def _top_p_filtering(self, logits, top_p):
        """Apply top-p (nucleus) filtering to logits."""
        import torch
        
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        cumulative_probs = torch.cumsum(torch.softmax(sorted_logits, dim=-1), dim=-1)
        
        # Remove tokens with cumulative probability above the threshold
        sorted_indices_to_remove = cumulative_probs > top_p
        # Shift the indices to the right to keep also the first token above the threshold
        sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[..., :-1].clone()
        sorted_indices_to_remove[..., 0] = 0
        
        # Scatter sorted tensors to original indexing
        indices_to_remove = sorted_indices_to_remove.scatter(
            -1, sorted_indices, sorted_indices_to_remove
        )
        filtered_logits = logits.clone()
        filtered_logits[indices_to_remove] = -float('Inf')
        return filtered_logits

    def _create_stopping_criteria(self, stop_sequences):
        """Create stopping criteria for text generation."""
        from transformers import StoppingCriteria, StoppingCriteriaList
        
        class StopSequenceCriteria(StoppingCriteria):
            def __init__(self, tokenizer, stop_sequences, input_length):
                self.tokenizer = tokenizer
                self.stop_sequences = stop_sequences
                self.input_length = input_length
                
            def __call__(self, input_ids, scores, **kwargs):
                # Get the generated text so far
                generated_text = self.tokenizer.decode(
                    input_ids[0][self.input_length:],
                    skip_special_tokens=True
                )
                
                # Check if any stop sequence is in the text
                for stop_seq in self.stop_sequences:
                    if stop_seq in generated_text:
                        return True
                return False
        
        # Create stopping criteria
        stopping_criteria = StoppingCriteriaList([
            StopSequenceCriteria(
                self.tokenizer,
                stop_sequences,
                len(self.tokenizer(prompt, return_tensors="pt").input_ids[0])
            )
        ])
        
        return stopping_criteria

    def _prepare_prompt(self, prompt: str, context: Optional[List[Dict[str, Any]]]) -> str:
        """Prepare full prompt with context."""
        if not context:
            return prompt
            
        # Format context into text
        context_text = ""
        for item in context:
            if "content" in item:
                context_text += f"{item['content']}\n\n"
                
        # Combine context and prompt
        full_prompt = f"{context_text}\n{prompt}"
        return full_prompt

    def _get_cache_key(self, prompt: str, max_tokens: int, temperature: float, top_p: float) -> str:
        """Generate cache key for generation requests."""
        import hashlib
        
        # Create a string representation of the request
        request_str = f"{prompt}|{max_tokens}|{temperature}|{top_p}"
        
        # Generate hash
        return hashlib.md5(request_str.encode()).hexdigest()

    async def close(self):
        """Clean up resources."""
        with self.tracer.start_as_current_span("generation_manager_close") as span:
            try:
                # Clean up model resources if needed
                self.model = None
                self.tokenizer = None
                
                # Clear cache
                self.cache.clear()
                
                logger.info(
                    "generation_manager_closed",
                    token_count=self.token_counter
                )
                
                span.set_status(Status(StatusCode.OK))
                return True
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "generation_manager_close_failed",
                    error=str(e),
                    exc_info=True
                )
                return False 