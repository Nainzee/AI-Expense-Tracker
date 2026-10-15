import json
import os
import random
import torch
import numpy as np
from torch.utils.data import Dataset, DataLoader
from transformers import (
    DistilBertTokenizerFast,
    DistilBertForSequenceClassification,
    get_linear_schedule_with_warmup
)
from torch.optim import AdamW
from sklearn.metrics import classification_report

# ─── Reproducibility ────────────────────────────────────────────────────────
random.seed(42)
np.random.seed(42)
torch.manual_seed(42)

# ─── Config ─────────────────────────────────────────────────────────────────
DATA_PATH   = "data/categories.json"
SAVE_PATH   = "models/category_model"
MAX_LEN     = 32       # Max tokens per item name (short text, so 32 is enough)
BATCH_SIZE  = 16
EPOCHS      = 3        # 3 epochs is enough for fine-tuning on small data
LR          = 5e-5     # Standard learning rate for DistilBERT fine-tuning

os.makedirs(SAVE_PATH, exist_ok=True)

# ─── 1. Load and prepare data ────────────────────────────────────────────────
print("Loading training data...")
with open(DATA_PATH, "r") as f:
    raw = json.load(f)

# Build label mappings
label_names = sorted(raw.keys())                       # e.g. ['Bakery', 'Drinks', ...]
label2id    = {l: i for i, l in enumerate(label_names)}
id2label    = {i: l for l, i in label2id.items()}
num_labels  = len(label_names)

print(f"Categories found: {label_names}")
print(f"Number of categories: {num_labels}")

# Build (text, label_id) pairs
# We also do basic augmentation: add context words around item names
# e.g. "bought rice" / "price of rice" / "rice 1kg" so model generalises better
def augment(item_name):
    templates = [
        item_name,
        f"{item_name} 1kg",
        f"{item_name} 500g",
        f"price of {item_name}",
        f"bought {item_name}",
        f"{item_name} pack",
        f"{item_name} x2",
    ]
    return templates

all_texts  = []
all_labels = []

for category, items in raw.items():
    for item in items:
        for variant in augment(item):
            all_texts.append(variant)
            all_labels.append(label2id[category])

# Shuffle data
combined = list(zip(all_texts, all_labels))
random.shuffle(combined)
all_texts, all_labels = zip(*combined)

# Train / validation split (90/10)
split     = int(0.9 * len(all_texts))
train_txt = list(all_texts[:split])
train_lbl = list(all_labels[:split])
val_txt   = list(all_texts[split:])
val_lbl   = list(all_labels[split:])

print(f"Training samples:   {len(train_txt)}")
print(f"Validation samples: {len(val_txt)}")

# ─── 2. Tokenizer ────────────────────────────────────────────────────────────
print("\nLoading DistilBERT tokenizer...")
tokenizer = DistilBertTokenizerFast.from_pretrained("distilbert-base-uncased")

# ─── 3. Dataset class ────────────────────────────────────────────────────────
class ItemDataset(Dataset):
    def __init__(self, texts, labels):
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
            "input_ids":      self.encodings["input_ids"][idx],
            "attention_mask": self.encodings["attention_mask"][idx],
            "labels":         self.labels[idx]
        }

train_dataset = ItemDataset(train_txt, train_lbl)
val_dataset   = ItemDataset(val_txt,   val_lbl)

train_loader  = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
val_loader    = DataLoader(val_dataset,   batch_size=BATCH_SIZE)

# ─── 4. Model ────────────────────────────────────────────────────────────────
print("Loading DistilBERT model...")
model = DistilBertForSequenceClassification.from_pretrained(
    "distilbert-base-uncased",
    num_labels=num_labels,
    id2label=id2label,
    label2id=label2id
)

# ─── 5. Optimizer and scheduler ─────────────────────────────────────────────
optimizer = AdamW(model.parameters(), lr=LR, weight_decay=0.01)
total_steps = len(train_loader) * EPOCHS
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=int(0.1 * total_steps),
    num_training_steps=total_steps
)

# ─── 6. Training loop ────────────────────────────────────────────────────────
print("\n" + "="*50)
print("Starting training...")
print("="*50)

device = torch.device("cpu")   # CPU training
model.to(device)

for epoch in range(EPOCHS):
    # ── Training phase
    model.train()
    total_loss = 0

    for step, batch in enumerate(train_loader):
        input_ids      = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        labels         = batch["labels"].to(device)

        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels
        )

        loss = outputs.loss
        total_loss += loss.item()

        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)  # Prevent exploding gradients
        optimizer.step()
        scheduler.step()
        optimizer.zero_grad()

        if (step + 1) % 10 == 0:
            print(f"  Epoch {epoch+1}/{EPOCHS} | Step {step+1}/{len(train_loader)} | Loss: {loss.item():.4f}")

    avg_loss = total_loss / len(train_loader)

    # ── Validation phase
    model.eval()
    correct = 0
    total   = 0
    all_preds  = []
    all_truths = []

    with torch.no_grad():
        for batch in val_loader:
            input_ids      = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels         = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            preds   = torch.argmax(outputs.logits, dim=1)

            correct     += (preds == labels).sum().item()
            total       += labels.size(0)
            all_preds   += preds.tolist()
            all_truths  += labels.tolist()

    accuracy = correct / total
    print(f"\nEpoch {epoch+1} Summary | Avg Loss: {avg_loss:.4f} | Val Accuracy: {accuracy*100:.1f}%\n")

# ─── 7. Final classification report ─────────────────────────────────────────
print("\nClassification Report:")
print(classification_report(all_truths, all_preds, target_names=label_names))

# ─── 8. Save model and tokenizer ─────────────────────────────────────────────
print(f"Saving model to {SAVE_PATH}...")
model.save_pretrained(SAVE_PATH)
tokenizer.save_pretrained(SAVE_PATH)

# Also save the label mapping so other files can load it
with open(f"{SAVE_PATH}/label_map.json", "w") as f:
    json.dump({"id2label": id2label, "label2id": label2id}, f, indent=2)

print("\nTraining complete! Model saved.")
print(f"Categories: {label_names}")