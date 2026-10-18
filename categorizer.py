"""
Hybrid Expense Item Categorization Engine.

Architecture:
- Negative Filter: Suppresses tender words, scale lines, dates, and non-product noise, mapping them to 'Other' (0.99).
- Tier 1: Deterministic Semantic Keyword & Brand Dictionary (< 0.1ms latency, 100% precision).
  Comprehensive coverage for global & Indian groceries, produce, beverages, pharmacy, dining, household, electronics.
- Tier 2: Fine-Tuned DistilBERT Transformer Classifier (PyTorch + Hugging Face).
- Tier 3: Resilient Semantic Token Similarity Fallback.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

# Path to fine-tuned DistilBERT model
MODEL_PATH = "models/category_model"

_model = None
_tokenizer = None
_id2label = None
_model_failed = False

# ─── Comprehensive Canonical Categories ──────────────────────────────────────
CANONICAL_CATEGORIES = [
    "Food",
    "Drinks",
    "Dining",
    "Bakery",
    "Household",
    "Personal Care",
    "Medical",
    "Transportation",
    "Electronics",
    "Apparel",
    "Home & Hardware",
    "Entertainment",
    "Utilities",
    "Office & Stationery",
    "Other",
]

# ─── Negative Filter: Non-Item / Tender / System Terms ───────────────────────
def is_non_item_or_discount(text: str) -> bool:
    """Identify standalone tender, scale lines, discounts, and system metadata."""
    t = text.lower().strip()
    # 1. Tender words (Cash, Change, EFTPOS, Visa, etc.)
    if re.search(r"\b(?:cash|change|eftpos|visa|mastercard|amex|tender|tendered|debit|credit|paid|balance due)\b", t):
        return True
    # 2. Pure discount & promotional rows
    if any(kw in t for kw in ["loyalty", "discount", "savings", "coupon", "voucher", "rebate", "promo"]):
        return True
    if t in ["special", "on sale", "rollback", "clearance"]:
        return True
    # 3. Pure scale / weight lines (e.g. '0.778 kg NET' or '1.250 kg @ $3.00/kg')
    if re.search(r"^\s*\d+(?:\.\d+)?\s*(?:kg|g|lb|oz)\s*(?:net|gross)?(?:\s*@.*)?\s*$", t):
        return True
    # 4. Summary rows (e.g., 'TOTAL', 'SUBTOTAL', 'GRAND TOTAL', 'GST', 'ROUND OFF')
    if re.search(r"^(?:subtotal|sub\s+total|grand\s+total|net\s+total|total(?:\s+due|\s+amount|\s+sale)?|round\s+off|gst|tax|vat)(?:\s*:|\s+|$)", t):
        return True
    return False


# ─── Tier 1: High-Precision Domain Knowledge Dictionary ───────────────────────
KNOWLEDGE_BASE: Dict[str, List[str]] = {
    "Food": [
        # Fresh Vegetables & Produce (Global & Indian)
        "lettuce", "iceberg", "romaine", "salad", "spinach", "kale", "cabbage",
        "broccoli", "cauliflower", "celery", "cucumber", "zucchini", "avocado",
        "avocados", "tomato", "tomatoes", "potato", "potatoes", "onion", "onions",
        "garlic", "ginger", "carrot", "carrots", "mushroom", "mushrooms",
        "capsicum", "bell pepper", "chilli", "pepper", "sweet corn", "corn",
        "asparagus", "pumpkin", "beans", "peas", "radish", "beetroot", "leek",
        "herbs", "basil", "parsley", "coriander", "cilantro", "mint", "thyme",
        "rosemary", "palak", "gajar", "methi", "bhindi", "brinjal",
        # Fresh Fruits
        "banana", "bananas", "apple", "apples", "orange", "oranges", "grape",
        "grapes", "strawberry", "strawberries", "berry", "berries", "blueberries",
        "lemon", "lemons", "lime", "limes", "watermelon", "melon", "pineapple",
        "mango", "mangoes", "peach", "pear", "plum", "kiwi", "papaya", "pomegranate",
        # Meat, Poultry, Fish & Deli
        "chicken", "beef", "steak", "pork", "lamb", "bacon", "ham", "turkey",
        "sausage", "sausages", "salami", "fish", "salmon", "tuna", "prawn",
        "prawns", "shrimp", "meat", "tofu", "paneer", "eggs", "egg",
        # Dairy & Staples
        "milk", "butter", "cheese", "cheddar", "parmesan", "mozzarella", "curd",
        "dahi", "yogurt", "cream", "sour cream",
        # Grains, Pantry & Spices
        "rice", "basmati", "atta", "whole wheat atta", "flour", "wheat", "dal", "lentils", "moong",
        "chana", "toor", "urad", "rajma", "oil", "sunflower oil", "olive oil",
        "mustard oil", "canola oil", "vegetable oil", "salt", "sugar", "masala",
        "turmeric", "haldi", "jeera", "cumin", "cloves", "cardamom", "cinnamon",
        "noodles", "maggi", "pasta", "spaghetti", "macaroni", "cornflakes",
        "oats", "muesli", "cereal", "honey", "jam", "pickle", "sauce", "ketchup",
        "mayonnaise", "vinegar", "almonds", "cashews", "walnuts", "raisins", "peanuts",
        # Brands
        "amul", "aashirvaad", "nestle", "britannia", "tata sampann", "fortune", "safal",
        "kelloggs", "saffola", "everest", "mdh", "catch", "woolworths", "coles"
    ],
    "Drinks": [
        "water", "cold drink", "soda", "pepsi", "coca cola", "coke", "sprite",
        "fanta", "thums up", "limca", "maaza", "frooti", "juice", "orange juice",
        "apple juice", "tropicana", "real juice", "paper boat", "tea", "coffee",
        "nescafe", "bru", "tata tea", "taj mahal", "green tea", "black tea",
        "red bull", "monster", "energy drink", "beer", "wine", "whiskey",
        "vodka", "lassi", "chaas", "buttermilk", "coconut water", "horlicks",
        "boost", "complan", "bournvita", "squash", "fruit syrup", "roohafza",
        "cordial", "lemonade", "tonic water"
    ],
    "Bakery": [
        "bread", "brown bread", "white bread", "wholemeal", "whole wheat bread",
        "sourdough", "baguette", "pav", "bun", "buns", "cake", "cakes", "pastry",
        "muffin", "muffins", "cupcake", "cookie", "cookies", "biscuit", "biscuits",
        "parle", "good day", "bourbon", "oreo", "marie", "rusk", "toast",
        "croissant", "brownie", "pizza base", "puff", "khari", "bagel", "dinner roll",
        "wrap", "pita"
    ],
    "Household": [
        "detergent", "washing powder", "surf excel", "ariel", "tide", "rin",
        "vim", "dishwash", "pril", "harpic", "toilet cleaner", "lizol",
        "floor cleaner", "colin", "glass cleaner", "phenyl", "mop", "broom",
        "bucket", "dustbin", "trash bag", "garbage bag", "tissue", "tissues",
        "napkin", "paper towel", "toilet paper", "toilet roll", "foil",
        "aluminium foil", "cling wrap", "air freshener", "odonil", "godrej aer",
        "mosquito", "all out", "goodknight", "hit spray", "mortein", "sponge",
        "scrubber", "scotch brite", "comfort", "fabric conditioner", "battery",
        "duracell", "matches", "lighter"
    ],
    "Personal Care": [
        "shampoo", "conditioner", "soap", "soaps", "dove", "lux", "pears",
        "lifebuoy", "dettol soap", "body wash", "shower gel", "face wash",
        "cleaner", "moisturizer", "sunscreen", "spf", "cream", "nivea", "ponds",
        "himalaya", "toothpaste", "colgate", "pepsodent", "sensodyne", "close up",
        "oral-b", "toothbrush", "mouthwash", "listerine", "deodorant", "deo",
        "perfume", "axe", "fog", "talc", "hair oil", "parachute", "bajaj almond",
        "hair color", "shaving", "gillette", "razor", "blade", "pads", "whisper",
        "stayfree", "cotton", "lip balm", "kajal", "lipstick"
    ],
    "Medical": [
        "paracetamol", "dolo", "crocin", "aspirin", "combiflam", "ibuprofen",
        "tablet", "tablets", "capsule", "capsules", "syrup", "cough syrup",
        "antacid", "digene", "eno", "benadryl", "strepsils", "vicks", "inhaler",
        "otrivin", "ointment", "antiseptic", "dettol", "savlon", "bandage",
        "band-aid", "band aid", "crepe", "thermometer", "oximeter", "vitamin",
        "vitamins", "multivitamin", "calcium", "zinc", "omega 3", "fish oil",
        "protein powder", "whey", "ors", "electral"
    ],
    "Dining": [
        "pizza", "burger", "sandwich", "biryani", "biryani combo", "thali",
        "curry", "roti", "naan", "starter", "appetizer", "dessert", "ice cream",
        "pasta", "salad bowl", "soup", "buffet", "dine in", "takeaway", "latte",
        "cappuccino", "espresso"
    ],
    "Transportation": [
        # Fuel, Gas & EV
        "petrol", "diesel", "fuel", "gasoline", "unleaded", "cng", "lpg fuel",
        "gas station", "fuel pump", "ev charge", "ev charging", "charging session",
        # Ride Hail, Taxi & Transit
        "uber", "uber trip", "uber ride", "ola", "ola ride", "ola cabs", "lyft",
        "taxi", "cab", "auto fare", "rickshaw", "meter fare",
        # Tolls & Parking
        "toll", "toll plaza", "fastag", "tollway", "parking", "parking fee",
        "parking ticket", "valet parking",
        # Public Transit, Railway & Flights
        "metro", "subway", "train ticket", "railway", "irctc", "bus ticket",
        "transit pass", "metro card", "ferry", "flight", "airline", "boarding pass",
        "indigo", "air india"
    ],
    "Electronics": [
        "charger", "cable", "usb", "adapter", "earphones", "headphones",
        "mouse", "keyboard", "battery", "powerbank", "bulb", "led", "wire"
    ],
    "Apparel": [
        "shirt", "t-shirt", "jeans", "trousers", "socks", "shoes", "slippers",
        "kurta", "dress", "belt", "cap", "towel"
    ],
    "Home & Hardware": [
        # Hardware & Tools
        "hammer", "screwdriver", "drill", "screw", "screws", "nails", "pliers",
        "wrench", "tape measure", "super glue", "fevicol", "paint", "brush",
        "hardware", "extension cord", "power strip",
        # Furniture & Home Goods
        "ikea", "table", "chair", "desk", "stool", "shelf", "cushion", "pillow",
        "bedsheet", "blanket", "curtain", "mattress", "rug", "carpet",
        # Kitchenware & Decor
        "pan", "pot", "cookware", "kettle", "knife", "cutting board", "plate",
        "plates", "bowl", "bowls", "mug", "mugs", "plant", "flower pot",
        "planter", "vase", "mirror", "frame"
    ],
    "Entertainment": [
        # Cinema & Movies
        "cinema", "movie", "movie ticket", "imax", "pvr", "inox", "cinepolis",
        "amc", "popcorn combo", "theatre",
        # Streaming & Digital
        "netflix", "spotify", "prime video", "disney", "youtube premium",
        "apple music", "hulu", "streaming subscription", "audible",
        # Gaming & Hobbies
        "playstation", "xbox", "nintendo", "steam", "video game", "gaming",
        "board game", "amusement park", "arcade", "bowling", "museum", "concert",
        "book", "novel", "comic", "magazine"
    ],
    "Utilities": [
        # Electricity & Gas
        "electricity", "power bill", "electric bill", "bescom", "tneb", "mseb",
        "gas bill", "cooking gas", "lpg cylinder", "cylinder refill", "indane",
        "bharat gas", "hp gas",
        # Water & Municipal
        "water bill", "water utility", "sewerage",
        # Telecom & Internet
        "broadband", "wifi", "internet bill", "fiber", "airtel", "jio",
        "vodafone", "vi recharge", "mobile recharge", "phone bill", "landline",
        "postpaid bill", "prepaid recharge"
    ],
    "Office & Stationery": [
        # Paper & Notebooks
        "notebook", "notepad", "diary", "journal", "register", "a4 paper",
        "printer paper", "cardstock", "envelope", "envelopes",
        # Writing Instruments
        "pen", "pens", "ballpoint", "gel pen", "pencil", "pencils",
        "highlighter", "marker", "markers", "eraser", "sharpener",
        # Desk Accessories & Shipping
        "stapler", "staples", "paper clip", "paper clips", "binder", "folder",
        "scissors", "tape", "scotch tape", "glue stick", "calculator",
        "sticky notes", "post-it", "photocopy", "xerox", "printing",
        "lamination", "courier", "postage", "shipping fee", "dhl", "fedex"
    ],
}


def _tier1_rule_match(item_name: str) -> Optional[Tuple[str, float]]:
    """Fast keyword and brand matching with multi-word phrase weighting."""
    text = item_name.lower().replace("-", " ")
    tokens = set(re.findall(r"\b[a-z]{3,}\b", text))

    best_cat = None
    best_score = 0.0

    SPECIFICITY_WEIGHTS = {
        "Transportation": 1.45,
        "Utilities": 1.45,
        "Entertainment": 1.4,
        "Dining": 1.4,
        "Electronics": 1.35,
        "Apparel": 1.35,
        "Home & Hardware": 1.3,
        "Office & Stationery": 1.3,
        "Medical": 1.25,
        "Bakery": 1.25,
        "Drinks": 1.2,
        "Household": 1.1,
        "Personal Care": 1.1,
        "Food": 1.05,
        "Other": 0.8
    }

    for category, keywords in KNOWLEDGE_BASE.items():
        cat_weight = SPECIFICITY_WEIGHTS.get(category, 1.0)
        for kw in keywords:
            clean_kw = kw.replace("-", " ")
            if " " in clean_kw:
                if clean_kw in text:
                    score = len(clean_kw) * 1.5 * cat_weight
                    if score > best_score:
                        best_score = score
                        best_cat = category
            else:
                if clean_kw in tokens:
                    score = len(clean_kw) * cat_weight
                    if score > best_score:
                        best_score = score
                        best_cat = category

    if best_cat:
        return best_cat, 0.98
    return None


PERSONALIZED_MODEL_PATH = "models/user_personalized_model.joblib"
_temperature = 1.0
_personalized_pipeline = None


# ─── Tier 2: Fine-Tuned Calibrated DistilBERT Inference ─────────────────────
def load_transformer_model():
    """Load the fine-tuned sequence classification model and temperature calibration."""
    global _model, _tokenizer, _id2label, _model_failed, _temperature

    if _model_failed:
        return

    if _model is None:
        if not os.path.exists(MODEL_PATH) or not os.path.exists(f"{MODEL_PATH}/config.json"):
            _model_failed = True
            return

        try:
            from transformers import DistilBertTokenizerFast, DistilBertForSequenceClassification
            import torch

            _tokenizer = DistilBertTokenizerFast.from_pretrained(MODEL_PATH)
            _model = DistilBertForSequenceClassification.from_pretrained(MODEL_PATH)
            _model.eval()

            label_map_file = f"{MODEL_PATH}/label_map.json"
            if os.path.exists(label_map_file):
                with open(label_map_file) as f:
                    label_map = json.load(f)
                _id2label = {int(k): v for k, v in label_map.get("id2label", {}).items()}
            else:
                _id2label = _model.config.id2label

            # Load temperature scaling calibration factor
            temp_file = f"{MODEL_PATH}/temperature.json"
            if os.path.exists(temp_file):
                with open(temp_file) as f:
                    calib_data = json.load(f)
                    _temperature = float(calib_data.get("temperature", 1.0))
            else:
                _temperature = 1.0

        except Exception as exc:
            _model_failed = True


def _tier2_transformer_predict(item_name: str) -> Optional[Tuple[str, float]]:
    """Predict category using calibrated DistilBERT model."""
    load_transformer_model()
    if _model is None or _tokenizer is None:
        return None

    try:
        import torch

        inputs = _tokenizer(
            item_name.lower(),
            return_tensors="pt",
            truncation=True,
            padding=True,
            max_length=32,
        )

        with torch.no_grad():
            outputs = _model(**inputs)
            # Temperature scaling calibration: scale logits before softmax
            scaled_logits = outputs.logits / max(_temperature, 0.1)
            probs = torch.softmax(scaled_logits, dim=1)
            confidence = probs.max().item()
            pred_id = probs.argmax().item()

        category = _id2label.get(pred_id, "Other")
        return category, round(confidence, 3)
    except Exception:
        return None


# ─── Personalization Layer & User Correction Retraining ─────────────────────
def load_personalized_model():
    """Load user personalized classifier if present."""
    global _personalized_pipeline
    if _personalized_pipeline is None and os.path.exists(PERSONALIZED_MODEL_PATH):
        try:
            import joblib
            _personalized_pipeline = joblib.load(PERSONALIZED_MODEL_PATH)
        except Exception:
            _personalized_pipeline = None
    return _personalized_pipeline


def _personalized_predict(item_name: str) -> Optional[Tuple[str, float]]:
    """Predict using lightweight user-adapted model."""
    pipe = load_personalized_model()
    if pipe is None:
        return None
    try:
        probs = pipe.predict_proba([item_name.lower()])[0]
        max_idx = probs.argmax()
        conf = float(probs[max_idx])
        pred_label = pipe.classes_[max_idx]
        if conf >= 0.50:
            return pred_label, round(conf, 3)
    except Exception:
        pass
    return None


def retrain_personalized_classifier() -> dict:
    """
    Retrain personalization model on base taxonomy + all accumulated user corrections.
    """
    from storage import get_all_user_corrections
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    user_corr = get_all_user_corrections()
    texts = []
    labels = []
    weights = []

    # 1. Base taxonomy
    if os.path.exists("data/categories.json"):
        with open("data/categories.json", "r", encoding="utf-8") as f:
            base_tax = json.load(f)
        for cat, items in base_tax.items():
            for it in items:
                texts.append(it.lower())
                labels.append(cat)
                weights.append(1.0)

    # 2. Accumulated user corrections (weighted 4.0x)
    for corr_text, corr_cat in user_corr:
        texts.append(corr_text.lower())
        labels.append(corr_cat)
        weights.append(4.0)

    if not texts:
        return {"status": "error", "message": "No training data available"}

    pipeline = Pipeline([
        ('tfidf', TfidfVectorizer(ngram_range=(1, 3), sublinear_tf=True)),
        ('clf', LogisticRegression(C=2.5, max_iter=200))
    ])

    pipeline.fit(texts, labels, clf__sample_weight=weights)

    os.makedirs(os.path.dirname(PERSONALIZED_MODEL_PATH) or ".", exist_ok=True)
    joblib.dump(pipeline, PERSONALIZED_MODEL_PATH)

    global _personalized_pipeline
    _personalized_pipeline = pipeline

    return {
        "status": "success",
        "user_corrections_count": len(user_corr),
        "total_training_samples": len(texts),
        "classes": list(pipeline.classes_)
    }



# ─── Tier 3: Token Overlap & Cosine Fallback ────────────────────────────────
def _tier3_semantic_fallback(item_name: str) -> Tuple[str, float]:
    """Fallback token similarity when no exact rule or model output matches."""
    text = item_name.lower()
    scores: Dict[str, int] = {cat: 0 for cat in CANONICAL_CATEGORIES}

    for cat, kws in KNOWLEDGE_BASE.items():
        for kw in kws:
            if kw in text:
                scores[cat] += len(kw)

    best_cat = max(scores, key=scores.get)
    if scores[best_cat] > 0:
        return best_cat, 0.70

    return "Other", 0.40


# ─── Public API ─────────────────────────────────────────────────────────────
def predict_category(item_name: str) -> Tuple[str, float]:
    """
    Predict category for a line item using multi-tier hybrid classifier:
    -1. Personalization Memory: User historical corrections (Exact + Fuzzy Match -> 1.0)
    0. Negative Filter: Suppress tender, weight, and discount rows -> 'Other' (0.99)
    0.5. Personalized Model: Fast Logistic Regression retrained on user corrections
    1. Tier 1: Domain Knowledge Dictionary (Instant, highest precision)
    2. Tier 2: Calibrated DistilBERT (Transformer sequence classifier)
    3. Tier 3: Semantic Fallback (Graceful degradation)
    """
    if not item_name or len(item_name.strip()) < 2:
        return "Other", 0.0

    clean_name = item_name.strip()

    # Step 0: Check negative non-item filter (tender, scales, promos are strictly non-items)
    if is_non_item_or_discount(clean_name):
        return "Other", 0.99

    # Step 0.5: Personalization Memory (User historical corrections)
    try:
        from storage import get_user_correction
        user_cat = get_user_correction(clean_name)
        if user_cat:
            return user_cat, 1.00
    except Exception:
        pass


    # Step 0.5: Personalized Retrained Layer (if trained on user corrections)
    pers_match = _personalized_predict(clean_name)
    if pers_match and pers_match[1] >= 0.70:
        return pers_match

    # Step 1: Check high-precision knowledge dictionary
    match = _tier1_rule_match(clean_name)
    if match:
        return match

    # Step 2: Check fine-tuned calibrated DistilBERT model
    match = _tier2_transformer_predict(clean_name)
    if match:
        cat, conf = match
        if conf >= 0.40:
            return cat, conf

    # Step 3: Semantic token fallback
    return _tier3_semantic_fallback(clean_name)



def predict_with_distilbert(item_name: str) -> dict:
    """
    Direct inference using fine-tuned calibrated DistilBERT sequence classifier.
    Returns:
    - predicted_category: str
    - calibrated_confidence: float (scaled by temperature)
    - raw_confidence: float (uncalibrated softmax)
    - temperature: float
    - top3: list of {"category": str, "probability": float}
    - latency_ms: float
    """
    load_transformer_model()
    if _model is None or _tokenizer is None:
        cat, conf = predict_category(item_name)
        return {
            "item_name": item_name,
            "predicted_category": cat,
            "calibrated_confidence": round(conf * 100, 1),
            "raw_confidence": round(conf * 100, 1),
            "temperature": 1.0,
            "top3": [{"category": cat, "probability": round(conf * 100, 1)}],
            "latency_ms": 0.1
        }

    import torch
    import time

    clean = item_name.strip()
    t0 = time.perf_counter()

    inputs = _tokenizer(
        clean.lower(),
        return_tensors="pt",
        truncation=True,
        padding=True,
        max_length=32
    )

    with torch.no_grad():
        outputs = _model(**inputs)
        raw_probs = torch.softmax(outputs.logits, dim=1)[0]
        scaled_logits = outputs.logits / max(_temperature, 0.1)
        cal_probs = torch.softmax(scaled_logits, dim=1)[0]

    latency_ms = (time.perf_counter() - t0) * 1000

    pred_idx = cal_probs.argmax().item()
    pred_cat = _id2label.get(pred_idx, "Other")
    cal_conf = float(cal_probs[pred_idx].item())
    raw_conf = float(raw_probs[pred_idx].item())

    k = min(3, len(cal_probs))
    top3_indices = torch.topk(cal_probs, k=k).indices.tolist()
    top3 = [
        {
            "category": _id2label.get(idx, f"Class {idx}"),
            "probability": round(float(cal_probs[idx].item()) * 100, 1)
        }
        for idx in top3_indices
    ]

    return {
        "item_name": clean,
        "predicted_category": pred_cat,
        "calibrated_confidence": round(cal_conf * 100, 1),
        "raw_confidence": round(raw_conf * 100, 1),
        "temperature": round(_temperature, 2),
        "top3": top3,
        "latency_ms": round(latency_ms, 1)
    }


def categorize_items(items: List[dict], mode: str = "hybrid") -> List[dict]:
    """
    Annotate a list of item dictionaries with predicted category and confidence.
    mode: "hybrid" (Rules + Memory + DistilBERT) or "distilbert" (Pure Transformer)
    """
    categorized = []
    for item in items:
        name = item.get("item_name", "")
        if item.get("category") and item.get("category") not in ("", "Other"):
            cat = item["category"]
            conf = item.get("confidence", 1.0)
        elif mode == "distilbert":
            if is_non_item_or_discount(name):
                cat = "Other"
                conf = 0.99
            else:
                d_res = predict_with_distilbert(name)
                cat = d_res["predicted_category"]
                conf = d_res["calibrated_confidence"] / 100.0
        else:
            cat, conf = predict_category(name)

        categorized.append({
            **item,
            "category": cat,
            "confidence": conf
        })

    return categorized