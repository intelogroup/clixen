import sys
import os
from unittest.mock import patch, MagicMock
import pytest
import ollama

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tools.image_generation import generate_local_image

@patch("ollama.generate")
def test_generate_image_success(mock_generate, tmp_path):
    # Mock response to return a valid base64 image data
    mock_response = MagicMock()
    mock_response.image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==" # 1x1 PNG base64
    mock_generate.return_value = mock_response

    # Test call using a temporary directory path
    dest_path = tmp_path / "test_gen.png"
    result = generate_local_image(
        prompt="a single red dot",
        model="x/z-image-turbo",
        output_path=str(dest_path)
    )
    
    assert "Successfully generated image and saved to" in result
    assert dest_path.exists()
    # Read file and verify contents match decoded bytes
    assert len(dest_path.read_bytes()) > 0


@patch("ollama.generate")
def test_generate_image_missing_data(mock_generate, tmp_path):
    # Mock response to return None for image data
    mock_response = MagicMock()
    mock_response.image = None
    mock_response.get.return_value = None
    mock_generate.return_value = mock_response

    dest_path = tmp_path / "test_gen_fail.png"
    result = generate_local_image(
        prompt="a single red dot",
        model="x/z-image-turbo",
        output_path=str(dest_path)
    )
    
    assert "[Error] Image generation response did not contain image data" in result
    assert not dest_path.exists()


@patch("ollama.generate")
def test_generate_image_model_not_found(mock_generate):
    # Mock ResponseError from ollama
    mock_generate.side_effect = ollama.ResponseError("model not found")

    result = generate_local_image(
        prompt="a single red dot",
        model="invalid-model-name"
    )
    
    assert "Please run `ollama pull invalid-model-name`" in result


@patch("ollama.generate")
def test_generate_image_general_error(mock_generate):
    # Mock general Exception
    mock_generate.side_effect = Exception("connection timeout")

    result = generate_local_image(
        prompt="a single red dot",
        model="x/z-image-turbo"
    )
    
    assert "[Error] Failed to generate image: connection timeout" in result
