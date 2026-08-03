"""
Image generation tool using Ollama.
"""
import base64
import os
import logging
from pathlib import Path
from tools.google_auth import get_service

_log = logging.getLogger(__name__)

# Schema for tool registration
GENERATE_LOCAL_IMAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_local_image",
        "description": "Generate an image locally using an Ollama image generation model.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Description of the image to generate"
                },
                "model": {
                    "type": "string",
                    "description": "Ollama image generation model (default: x/z-image-turbo)",
                    "default": "x/z-image-turbo"
                },
                "output_path": {
                    "type": "string",
                    "description": "Absolute path to save the generated PNG (defaults to ~/Downloads/generated_image.png)",
                    "default": ""
                },
                "width": {
                    "type": "integer",
                    "description": "Width of the generated image in pixels",
                    "default": 512
                },
                "height": {
                    "type": "integer",
                    "description": "Height of the generated image in pixels",
                    "default": 512
                },
                "steps": {
                    "type": "integer",
                    "description": "Number of diffusion steps",
                    "default": 20
                }
            },
            "required": ["prompt"]
        }
    }
}

def generate_local_image(
    prompt: str,
    model: str = "x/z-image-turbo",
    output_path: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 20
) -> str:
    """
    Generate an image using Ollama's experimental image generation models.
    """
    import ollama
    
    # Resolve output path
    if not output_path:
        downloads_dir = Path.home() / "Downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        # Generate a unique filename using timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = downloads_dir / f"image_{timestamp}.png"
    else:
        dest = Path(output_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        _log.info(f"Generating image via Ollama model '{model}' with prompt: '{prompt}'")
        
        # Call Ollama API
        response = ollama.generate(
            model=model,
            prompt=prompt,
            width=width,
            height=height,
            steps=steps
        )
        
        # Extract base64 image data
        image_b64 = getattr(response, 'image', None) or response.get('image')
        if not image_b64:
            return (
                f"[Error] Image generation response did not contain image data. "
                f"Make sure model '{model}' is a supported image generation model."
            )
            
        # Decode and save to file
        image_data = base64.b64decode(image_b64)
        dest.write_bytes(image_data)
        
        return f"Successfully generated image and saved to {dest.resolve()}"
        
    except ollama.ResponseError as e:
        if "not found" in str(e).lower():
            return (
                f"[Error] Model '{model}' not found. Please run `ollama pull {model}` "
                "to download the image generation model and try again."
            )
        return f"[Error] Ollama response error: {e}"
    except Exception as e:
        return f"[Error] Failed to generate image: {e}"
