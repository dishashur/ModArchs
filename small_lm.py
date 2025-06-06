"""
This code draws strong inspiration and borrows heavily from the implementation available at https://github.com/karpathy/nanoGPT.
"""

import torch
import torch.nn as nn
from torch.nn import functional as F
import numpy as np
import matplotlib.pyplot as plt
import os
import json
import sys


sys.stdout.flush()

#experiment_name = mod_arch1, change use attention only with linear layer, no mlp no nothing
#                = mod_arch2, use linear_attention+ffn  with first 1 layer, then use only ffwd for the rest
#things to experiment with iterations, n_heads, bloc_size, batched_gradients
experiment_name = "Mod_arch2"
result_path = f"results/{experiment_name}"
if not os.path.exists(result_path):
    os.makedirs(result_path)
batch_size = 64  # how many independent sequences will we process in parallel?
block_size = 128  # what is the maximum context length for predictions?
max_iters = 3000
eval_interval = 50
learning_rate = 0.0003
device = "cuda" if torch.cuda.is_available() else "cpu"
eval_iters = 200
n_embd = 384
n_head = 4
n_layer = 3
n_hidden_layers = 1
dropout = 0.2
hidden_size = block_size


print("now reading", flush = True)
with open("../hw4/input.txt", "r", encoding="utf-8") as f:
    text = f.read()

# here are all the unique characters that occur in this text
chars = sorted(list(set(text)))
vocab_size = len(chars)
# create a mapping from characters to integers
stoi = {ch: i for i, ch in enumerate(chars)}
itos = {i: ch for i, ch in enumerate(chars)}


def encode(s):
    return [stoi[c] for c in s]  # encoder: take a string, output a list of integers


def decode(l):
    return "".join(
        [itos[i] for i in l]
    )  # decoder: take a list of integers, output a string


# Train and test splits
data = torch.tensor(encode(text), dtype=torch.long)
n = int(0.9 * len(data))  # first 90% will be train, rest val
train_data = data[:n]
val_data = data[n:]

# data loading
print("train_data.device",train_data.device)

def get_batch(split):
    # generate a small batch of data of inputs x and targets y
    data = train_data if split == "train" else val_data
    ix = torch.randint(len(data) - block_size, (batch_size,))
    x = torch.stack([data[i : i + block_size] for i in ix])
    y = torch.stack([data[i + 1 : i + block_size + 1] for i in ix])
    x, y = x.to(device), y.to(device)
    return x, y


@torch.no_grad()
def estimate_loss(model):
    out = {}
    model.eval()
    for split in ["train", "val"]:
        losses = torch.zeros(eval_iters)
        for k in range(eval_iters):
            X, Y = get_batch(split)
            logits, loss = model(X, Y)
            losses[k] = loss.item()
        out[split] = losses.mean()
    model.train()
    return out


class MLPHead(nn.Module):
    """one moded head of self-attention"""

    def __init__(self, head_size):
        super().__init__()
        self.nnet = nn.Linear(n_embd, block_size, bias = False)
        self.value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))

        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        # input of size (batch, time-step, channels)
        # output of size (batch, time-step, head size)
        B, T, C = x.shape
        wei = self.nnet(x)
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float("-inf"))  # (B, T, T)
        wei = F.softmax(wei, dim=-1)  # (B, T, T)
        wei = self.dropout(wei)
        # perform the weighted aggregation of the values
        v = self.value(x)  # (B,T,hs)
        out = wei @ v  # (B, T, T) @ (B, T, hs) -> (B, T, hs)
        return out


class Head(nn.Module):
    """one head of self-attention"""

    def __init__(self, head_size):
        super().__init__()
        self.key = nn.Linear(n_embd, head_size, bias=False)
        self.query = nn.Linear(n_embd, head_size, bias=False)
        self.value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))

        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        # input of size (batch, time-step, channels)
        # output of size (batch, time-step, head size)
        B, T, C = x.shape
        k = self.key(x)  # (B,T,hs)
        q = self.query(x)  # (B,T,hs)
        # compute attention scores ("affinities")
        wei = (
            q @ k.transpose(-2, -1) * k.shape[-1] ** -0.5
        )  # (B, T, hs) @ (B, hs, T) -> (B, T, T)
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float("-inf"))  # (B, T, T)
        wei = F.softmax(wei, dim=-1)  # (B, T, T)
        wei = self.dropout(wei)
        # perform the weighted aggregation of the values
        v = self.value(x)  # (B,T,hs)
        out = wei @ v  # (B, T, T) @ (B, T, hs) -> (B, T, hs)
        return out


class MultiHeadAttention(nn.Module):
    """multiple heads of self-attention in parallel"""

    def __init__(self, num_heads, head_size, mlp_attention=False):
        super().__init__()
        if mlp_attention:
            self.heads = nn.ModuleList([MLPHead(head_size) for _ in range(num_heads)])
        else:
            self.heads = nn.ModuleList([Head(head_size) for _ in range(num_heads)])
        self.proj = nn.Linear(head_size * num_heads, n_embd)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        out = self.dropout(self.proj(out))
        return out


class FeedFoward(nn.Module):
    """a simple linear layer followed by a non-linearity"""

    def __init__(self, n_embd):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.ReLU(),
            nn.Linear(4 * n_embd, n_embd),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class Block(nn.Module):
    """Transformer block: communication followed by computation"""

    def __init__(self, n_embd, n_head, mlp_attention=False):
        # n_embd: embedding dimension, n_head: the number of heads we'd like
        super().__init__()
        head_size = n_embd // n_head
        self.sa = MultiHeadAttention(n_head, head_size, mlp_attention=mlp_attention)
        self.ffwd = FeedFoward(n_embd)
        self.ln1 = nn.LayerNorm(n_embd)
        self.ln2 = nn.LayerNorm(n_embd)

    def forward(self, x):
        x = x + self.sa(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x


class GPTLanguageModel(nn.Module):
    def __init__(self, mlp_attention=False):
        super().__init__()
        # each token directly reads off the logits for the next token from a lookup table
        self.token_embedding_table = nn.Embedding(vocab_size, n_embd)
        self.position_embedding_table = nn.Embedding(block_size, n_embd)
        if mlp_attention:
            head_size = n_embd // n_head
            self.blocks = nn.Sequential(
                MultiHeadAttention(n_head, head_size, mlp_attention=mlp_attention),
                *[FeedFoward(n_embd) for _ in range(n_layer-1)]
            )
        else:
            self.blocks = nn.Sequential(
                *[
                    Block(n_embd, n_head=n_head, mlp_attention=False)
                                        for _ in range(n_layer)
                ]
        )      
        self.ln_f = nn.LayerNorm(n_embd)  # final layer norm
        self.lm_head = nn.Linear(n_embd, vocab_size)

        # better init, not covered in the original GPT video, but important, will cover in followup video
        self.apply(self._init_weights)

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, idx, targets=None):
        B, T = idx.shape

        # idx and targets are both (B,T) tensor of integers
        tok_emb = self.token_embedding_table(idx)  # (B,T,C)
        pos_emb = self.position_embedding_table(torch.arange(T, device=device))  # (T,C)
        x = tok_emb + pos_emb  # (B,T,C)
        x = self.blocks(x)  # (B,T,C)
        x = self.ln_f(x)  # (B,T,C)
        logits = self.lm_head(x)  # (B,T,vocab_size)

        if targets is None:
            loss = None
        else:
            B, T, C = logits.shape
            logits = logits.view(B * T, C)
            targets = targets.view(B * T)
            loss = F.cross_entropy(logits, targets)

        return logits, loss

    def generate(self, idx, max_new_tokens):
        # idx is (B, T) array of indices in the current context
        for _ in range(max_new_tokens):
            # crop idx to the last block_size tokens
            idx_cond = idx[:, -block_size:]
            # get the predictions
            logits, loss = self(idx_cond)
            # focus only on the last time step
            logits = logits[:, -1, :]  # becomes (B, C)
            # apply softmax to get probabilities
            probs = F.softmax(logits, dim=-1)  # (B, C)
            # sample from the distribution
            idx_next = torch.multinomial(probs, num_samples=1)  # (B, 1)
            # append sampled index to the running sequence
            idx = torch.cat((idx, idx_next), dim=1)  # (B, T+1)
        return idx


mlp_attention_model = GPTLanguageModel(mlp_attention=True)
model = GPTLanguageModel()
m = model.to(device)
mlp_attention_m = mlp_attention_model.to(device)
original_params = sum(p.numel() for p in m.parameters()) / 1e6
mlp_attention_params = sum(p.numel() for p in mlp_attention_m.parameters()) / 1e6
# print the number of parameters in the model
print(f"Original Model: {original_params} M parameters")
print(f"MLP Attention Model: {mlp_attention_params} M parameters")

# create a PyTorch optimizer
optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
train_loss = []
val_loss = []

mlp_attention_optimizer = torch.optim.AdamW(
    mlp_attention_model.parameters(), lr=learning_rate
)
mlp_attention_train_loss = []
mlp_attention_val_loss = []
x_val = []

print("startin iters")
for iter in range(max_iters):
    # every once in a while evaluate the loss on train and val sets
    if iter % eval_interval == 0 or iter == max_iters - 1:
        x_val.append(iter)
        losses = estimate_loss(model=model)
        train_loss.append(losses["train"])
        val_loss.append(losses["val"])
        print(
            f"Original model: step {iter}: train loss {losses['train']:.4f}, val loss {losses['val']:.4f}"
        )
        losses = estimate_loss(model=mlp_attention_model)
        mlp_attention_train_loss.append(losses["train"])
        mlp_attention_val_loss.append(losses["val"])
        print(
            f"MLP Attention model: step {iter}: train loss {losses['train']:.4f}, val loss {losses['val']:.4f}"
        )

    # sample a batch of data
    xb, yb = get_batch("train")

    # evaluate the loss
    logits, loss = model(xb, yb)
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    optimizer.step()

    mlp_attention_logits, mlp_attention_loss = mlp_attention_model(xb, yb)
    mlp_attention_optimizer.zero_grad(set_to_none=True)
    mlp_attention_loss.backward()
    mlp_attention_optimizer.step()


hyper_params = {
    "batch_size": batch_size,  # how many independent sequences will we process in parallel?
    "block_size": block_size,  # what is the maximum context length for predictions?
    "max_iters": max_iters,
    "eval_interval": eval_interval,
    "learning_rate": learning_rate,
    "eval_iters": eval_iters,
    "n_embd": n_embd,
    "n_head": n_head,
    "n_layer": n_layer,
    "dropout": dropout,
    "n_hidden_layers": n_hidden_layers,
    "hidden_size": block_size,
    "original_params_million": original_params,
    "mlp_attention_params_million": mlp_attention_params,
    "train_loss": [val.tolist() for val in train_loss],
    "val_loss": [val.tolist() for val in val_loss],
    "mlp_attention_train_loss": [val.tolist() for val in mlp_attention_train_loss],
    "mlp_attention_val_loss": [val.tolist() for val in mlp_attention_val_loss],
    "epochs": x_val,
}
with open(f"{result_path}/hyper_params_context{block_size}_nheads{n_head}.json", "w") as outfile:
    json.dump(hyper_params, outfile, indent=4)


with open(f"{result_path}/losses_context{block_size}_nheads{n_head}.npy", "wb") as f:
    np.save(f, np.array(train_loss))
    np.save(f, np.array(val_loss))
    np.save(f, np.array(mlp_attention_train_loss))
    np.save(f, np.array(mlp_attention_val_loss))


plt.plot(x_val[1:], train_loss[1:], label="Training Loss")
plt.plot(x_val[1:], val_loss[1:], label="Validation Loss")
plt.plot(x_val[1:], mlp_attention_train_loss[1:], label="FFN Training Loss")
plt.plot(x_val[1:], mlp_attention_val_loss[1:], label="FFN Validation Loss")
plt.title("Training and Validation Loss")

plt.xlabel("Epochs")
plt.ylabel("Loss")
plt.legend(loc="best")
plt.savefig(f"{result_path}/losses_context{block_size}_nheads{n_head}.png")
#plt.show()
