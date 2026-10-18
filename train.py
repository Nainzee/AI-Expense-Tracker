"""
Fine-tuning DistilBERT for receipt item categorization with:
1. Strict split BEFORE augmentation (eliminates train/val data leakage).
2. 'Other / non-item' negative class suppression.
3. Post-hoc Temperature Scaling calibration (Guo et al., 2017).
4. Expected Calibration Error (ECE) reporting before and after calibration.
"""

import json
import os
import random
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import (
    DistilBertTokenizerFast,
    DistilBertForSequenceClassification,
    get_linear_schedule_with_warmup
)
from torch.optim import AdamW
from sklearn.metrics import classification_report

# ─── Reproducibility ────────────────────────────────────────────────────────
def set_seed(seed: int = 42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

set_seed(42)

# ─── Config ─────────────────────────────────────────────────────────────────
DATA_PATH   = "data/categories.json"
SAVE_PATH   = "models/category_model"
MAX_LEN     = 32
BATCH_SIZE  = 16
EPOCHS      = 3
LR          = 5e-5

os.makedirs(SAVE_PATH, exist_ok=True)

# ─── Data Augmentation (Applied ONLY to Training Set) ────────────────────────
def augment_train_item(item_name: str, category: str) -> list[str]:
    """Generate realistic variations only for items in the training partition."""
    variants = [item_name]
    if category in ["Food", "Bakery", "Household"]:
        variants.extend([
            f"{item_name} 1kg",
            f"{item_name} 500g",
            f"{item_name} pack",
            f"organic {item_name}",
            f"{item_name} x2",
        ])
    elif category == "Drinks":
        variants.extend([
            f"{item_name} 500ml",
            f"{item_name} 1L",
            f"{item_name} can",
            f"cold {item_name}",
            f"{item_name} bottle",
        ])
    elif category == "Other":
        variants.extend([
            f"qty 1 {item_name}",
            f"total {item_name}",
            f"{item_name} approved",
        ])
    else:
        variants.extend([
            f"{item_name} pack",
            f"buy {item_name}",
        ])
    return list(dict.fromkeys(variants))


# ─── Calibration Metrics ────────────────────────────────────────────────────
def compute_ece(probs: np.ndarray, labels: np.ndarray, n_bins: int = 10) -> float:
    """
    Compute Expected Calibration Error (ECE).
    ECE = sum_{m=1}^M (|B_m| / N) * |acc(B_m) - conf(B_m)|
    """
    confidences = np.max(probs, axis=1)
    predictions = np.argmax(probs, axis=1)
    accuracies = (predictions == labels)

    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n = len(labels)

    for i in range(n_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]
        in_bin = (confidences > bin_lower) & (confidences <= bin_upper)
        prop_in_bin = np.sum(in_bin) / n

        if prop_in_bin > 0:
            accuracy_in_bin = np.mean(accuracies[in_bin])
            avg_confidence_in_bin = np.mean(confidences[in_bin])
            ece += np.abs(avg_confidence_in_bin - accuracy_in_bin) * prop_in_bin

    return float(ece)


class TemperatureScaler(nn.Module):
    """
    Temperature Scaling for Probability Calibration (Guo et al., 2017).
    A single scalar parameter T > 0 scales logits before softmax.
    """
    def __init__(self):
        super().__init__()
        self.temperature = nn.Parameter(torch.ones(1) * 1.0)

    def forward(self, logits: torch.Tensor) -> torch.Tensor:
        return logits / self.temperature

    def calibrate(self, logits: torch.Tensor, labels: torch.Tensor) -> float:
        """Find optimal temperature on validation set using NLL loss."""
        self.temperature.data.fill_(1.0)
        optimizer = torch.optim.LBFGS([self.temperature], lr=0.02, max_iter=50, line_search_fn="strong_wolfe")
        criterion = nn.CrossEntropyLoss()

        def _step():
            optimizer.zero_grad()
            scaled_logits = self.forward(logits)
            loss = criterion(scaled_logits, labels)
            loss.backward()
            return loss

        optimizer.step(_step)
        with torch.no_grad():
            self.temperature.clamp_(min=0.1, max=10.0)
        return float(self.temperature.item())



# ─── Dataset Class ──────────────────────────────────────────────────────────
class ItemDataset(Dataset):
    def __init__(self, texts, labels, tokenizer):
        self.encodings = tokenizer(
            texts,
            truncation=True,
            padding=True,
            max_length=MAX_LEN,
            return_tensors="pt"
        )
        self.labels = torch.tensor(labels, dtype=torch.long)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return {
            "input_ids": self.encodings["input_ids"][idx],
            "attention_mask": self.encodings["attention_mask"][idx],
            "labels": self.labels[idx]
        }


# ─── Main Training & Calibration Routine ────────────────────────────────────
def train_and_calibrate():
    print("=" * 60)
    print("Step 1: Loading & Splitting Data BEFORE Augmentation")
    print("=" * 60)

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    label_names = sorted(raw_data.keys())
    label2id = {l: i for i, l in enumerate(label_names)}
    id2label = {i: l for l, i in label2id.items()}
    num_labels = len(label_names)

    print(f"Taxonomy ({num_labels} classes): {label_names}")

    # Split raw items BEFORE augmentation to prevent data leakage
    train_items_map = {}
    val_items_map = {}
    train_texts, train_labels = [], []
    val_texts, val_labels = [], []

    for cat in label_names:
        items = list(dict.fromkeys([x.strip().lower() for x in raw_data.get(cat, []) if x.strip()]))
        random.shuffle(items)

        # 80/20 train/val split per category
        val_count = max(2, int(len(items) * 0.20))
        val_items = items[:val_count]
        train_items = items[val_count:]

        train_items_map[cat] = train_items
        val_items_map[cat] = val_items

        # Augment ONLY training items
        for it in train_items:
            for variant in augment_train_item(it, cat):
                train_texts.append(variant)
                train_labels.append(label2id[cat])

        # Validation items are kept clean (no synthetic leakage)
        for it in val_items:
            val_texts.append(it)
            val_labels.append(label2id[cat])

    # Verify zero intersection between train and val root items
    all_train_roots = set(sum(train_items_map.values(), []))
    all_val_roots = set(sum(val_items_map.values(), []))
    overlap = all_train_roots.intersection(all_val_roots)
    assert len(overlap) == 0, f"DATA LEAKAGE DETECTED: Overlap between train and val roots: {overlap}"
    print(f"✅ Data split verification passed: 0 item overlap between train and validation sets.")
    print(f"  Training samples (augmented):   {len(train_texts)}")
    print(f"  Validation samples (unseen):    {len(val_texts)}")

    # Shuffle training set
    combined = list(zip(train_texts, train_labels))
    random.shuffle(combined)
    train_texts, train_labels = zip(*combined)
    train_texts, train_labels = list(train_texts), list(train_labels)

    # Tokenizer & Datasets
    print("\nLoading DistilBERT tokenizer...")
    tokenizer = DistilBertTokenizerFast.from_pretrained("distilbert-base-uncased")
    train_dataset = ItemDataset(train_texts, train_labels, tokenizer)
    val_dataset = ItemDataset(val_texts, val_labels, tokenizer)

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE)

    # Model
    print("Initializing DistilBERT sequence classification model...")
    model = DistilBertForSequenceClassification.from_pretrained(
        "distilbert-base-uncased",
        num_labels=num_labels,
        id2label=id2label,
        label2id=label2id
    )

    device = torch.device("cpu")
    model.to(device)

    optimizer = AdamW(model.parameters(), lr=LR, weight_decay=0.01)
    total_steps = len(train_loader) * EPOCHS
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(0.1 * total_steps),
        num_training_steps=total_steps
    )

    print("\n" + "=" * 60)
    print(f"Step 2: Fine-Tuning DistilBERT on CPU ({EPOCHS} Epochs)")
    print("=" * 60)

    for epoch in range(EPOCHS):
        model.train()
        total_loss = 0.0

        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = outputs.loss
            total_loss += loss.item()

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()

        avg_loss = total_loss / len(train_loader)
        print(f"Epoch {epoch+1}/{EPOCHS} | Train Loss: {avg_loss:.4f}")

    # Validation & Logits Collection
    print("\n" + "=" * 60)
    print("Step 3: Validation Evaluation & Confidence Calibration")
    print("=" * 60)

    model.eval()
    val_logits_list = []
    val_labels_list = []

    with torch.no_grad():
        for batch in val_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            val_logits_list.append(outputs.logits)
            val_labels_list.append(labels)

    val_logits = torch.cat(val_logits_list, dim=0)
    val_labels_t = torch.cat(val_labels_list, dim=0)

    # Uncalibrated (T=1.0)
    uncal_probs = torch.softmax(val_logits, dim=1).numpy()
    val_preds = np.argmax(uncal_probs, axis=1)
    val_targets = val_labels_t.numpy()

    uncal_nll = nn.CrossEntropyLoss()(val_logits, val_labels_t).item()
    ece_before = compute_ece(uncal_probs, val_targets)

    print("\nClassification Report (Held-out Unseen Validation Items):")
    print(classification_report(val_targets, val_preds, target_names=label_names, zero_division=0))

    # Temperature Scaling Calibration
    scaler = TemperatureScaler()
    opt_temp = scaler.calibrate(val_logits, val_labels_t)

    with torch.no_grad():
        cal_logits = scaler(val_logits)
        cal_probs = torch.softmax(cal_logits, dim=1).numpy()
        cal_nll = nn.CrossEntropyLoss()(cal_logits, val_labels_t).item()
        ece_after = compute_ece(cal_probs, val_targets)

    print("-" * 60)
    print(f"Confidence Calibration Results (Temperature Scaling):")
    print(f"  Optimal Temperature (T*):      {opt_temp:.4f}")
    print(f"  NLL Loss Before (T=1.0):       {uncal_nll:.4f}")
    print(f"  NLL Loss After  (T={opt_temp:.2f}):      {cal_nll:.4f}")
    print(f"  ECE Before Calibration:        {ece_before * 100:.2f}%")
    print(f"  ECE After Calibration:         {ece_after * 100:.2f}%")
    print(f"  Relative ECE Reduction:        {((ece_before - ece_after) / max(ece_before, 1e-6)) * 100:.1f}%")
    print("-" * 60)

    # Save artifacts
    print(f"\nSaving fine-tuned calibrated model to {SAVE_PATH}...")
    model.save_pretrained(SAVE_PATH)
    tokenizer.save_pretrained(SAVE_PATH)

    with open(f"{SAVE_PATH}/label_map.json", "w", encoding="utf-8") as f:
        json.dump({"id2label": id2label, "label2id": label2id}, f, indent=2)

    calib_meta = {
        "temperature": round(opt_temp, 4),
        "ece_before": round(ece_before, 4),
        "ece_after": round(ece_after, 4),
        "nll_before": round(uncal_nll, 4),
        "nll_after": round(cal_nll, 4),
        "num_classes": num_labels,
        "classes": label_names
    }
    with open(f"{SAVE_PATH}/temperature.json", "w", encoding="utf-8") as f:
        json.dump(calib_meta, f, indent=2)

    print(f"Calibration metadata saved to {SAVE_PATH}/temperature.json.")
    print("Training and calibration finished successfully.")
    return calib_meta


if __name__ == "__main__":
    train_and_calibrate()