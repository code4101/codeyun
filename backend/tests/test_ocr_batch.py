import base64
from pathlib import Path
import pytest
from fastapi import HTTPException
from backend.core.ocr.batch import OcrBatchRequest, predict_encoded_batch


def test_batch_contract_preserves_order_and_cleans_temp_files():
    paths = []
    def predict(items, **kwargs):
        paths.extend(items)
        return [{"document": {"value": p.read_bytes().decode()}} for p in items]
    req = OcrBatchRequest(images=[base64.b64encode(b).decode() for b in [b"one", b"two"]])
    result = predict_encoded_batch(req, predict)
    assert [r["document"]["value"] for r in result["results"]] == ["one", "two"]
    assert all(not p.exists() for p in paths)


def test_invalid_batch_is_rejected_before_inference():
    with pytest.raises(HTTPException) as error:
        predict_encoded_batch(OcrBatchRequest(images=["!!!"]), lambda *a, **kw: pytest.fail("called"))
    assert error.value.status_code == 400
