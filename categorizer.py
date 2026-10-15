import json
import torch
from transformers import DistilBertTokenizerFast, DistilBertForSequenceClassification

MODEL_PATH = "models/category_model"

_model     = None
_tokenizer = None
_id2label  = None

def load_model():
    """Load the fine-tuned model once (lazy loading)."""
    global _model, _tokenizer, _id2label

    if _model is None:
        print("Loading categorization model...")
        _tokenizer = DistilBertTokenizerFast.from_pretrained(MODEL_PATH)
        _model     = DistilBertForSequenceClassification.from_pretrained(MODEL_PATH)
        _model.eval()

        with open(f"{MODEL_PATH}/label_map.json") as f:
            label_map = json.load(f)
        _id2label = {int(k): v for k, v in label_map["id2label"].items()}

        print("Categorization model loaded.")


def predict_category(item_name: str) -> tuple[str, float]:
    """
    Predict category for a single item name.
    Returns: (category_name, confidence_score)
    """
    load_model()

    inputs = _tokenizer(
        item_name.lower(),
        return_tensors="pt",
        truncation=True,
        padding=True,
        max_length=32
    )

    with torch.no_grad():
        outputs    = _model(**inputs)
        probs      = torch.softmax(outputs.logits, dim=1)
        confidence = probs.max().item()
        pred_id    = probs.argmax().item()

    category = _id2label[pred_id]

    # If confidence is very low, label as "Other" rather than a wrong category
    if confidence < 0.35:
        category = "Other"

    return category, round(confidence, 3)


def categorize_items(items: list[dict]) -> list[dict]:
    """
    Add category and confidence to each item dict.
    Input:  [{"item_name": "Basmati Rice", "amount": 340.0}, ...]
    Output: [{"item_name": "Basmati Rice", "amount": 340.0, "category": "Food", "confidence": 0.97}, ...]
    """
    categorized = []
    for item in items:
        category, confidence = predict_category(item["item_name"])
        categorized.append({
            **item,
            "category":   category,
            "confidence": confidence
        })
    return categorized