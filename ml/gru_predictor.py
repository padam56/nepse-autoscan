#!/usr/bin/env python3
"""
gru_predictor.py — 3-layer GRU with GA + PSO hyperparameter optimization.

Architecture (fixed 3 GRU layers):
  - Input projection → GRU1 → LayerNorm → GRU2 → LayerNorm → GRU3 → LayerNorm
  - Skip connection: layer1 output projected → added to layer3 output (residual)
  - Attention pooling over all timesteps (better than last-timestep only)
  - FC head: Linear → BN → Dropout → Linear → 5 classes
  - GA: optimizes hidden_units[3], dropout, fc_units
  - PSO: optimizes learning rate, batch size

Features (14 per timestep, lookback=15):
  price_position, intraday_range, pct_change, vol_ratio, close_norm,
  sma5_ratio, sma20_ratio, momentum_3d, momentum_5d, volatility_5d,
  rsi14, macd_signal, body_ratio, hl_pos_5d

Generalization:
  - LayerNorm after each GRU (more stable than BN for sequences)
  - Residual skip from GRU1 → GRU3 (gradient highway)
  - Label smoothing 0.1 (prevents overconfident predictions)
  - Mixup augmentation (interpolates samples, reduces overfitting)
  - Early stopping with patience=10
  - Gradient clipping 1.0
  - Weight decay (L2 via AdamW)
  - CosineAnnealing LR schedule

Usage:
  python ml/gru_predictor.py --train --symbol ALICL
  python ml/gru_predictor.py --train               # all stocks with 100+ days
  python ml/gru_predictor.py --predict --symbol ALICL
  python ml/gru_predictor.py --backtest --symbol ALICL
"""
import os, sys, json, argparse, random, math
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Try /scratch packages first (PyTorch installed there due to home quota)
SCRATCH_PKGS = '/scratch/C00621463/pypackages'
if os.path.exists(SCRATCH_PKGS):
    sys.path.insert(0, SCRATCH_PKGS)

sys.path.insert(0, str(ROOT / 'lib'))
sys.stdout.reconfigure(line_buffering=True)

import numpy as np
from sklearn.isotonic import IsotonicRegression

HISTORY_DIR = ROOT / 'data' / 'price_history'
MODEL_DIR   = ROOT / 'data' / 'models'
MODEL_DIR.mkdir(exist_ok=True)

N_FEATURES = 14   # features per timestep
N_CLASSES  = 5    # STRONG DOWN / DOWN / NEUTRAL / UP / STRONG UP
LOOKBACK   = 15   # timestep window (more context for 3 GRU layers)


# ─── Feature Engineering ─────────────────────────────────────────────────────

def load_stock_history(symbol: str) -> list:
    """Load chronological OHLCV records for a symbol from daily snapshot files."""
    records = {}
    if not HISTORY_DIR.exists():
        return []
    for f in sorted(HISTORY_DIR.glob('*.json')):
        try:
            day = json.loads(f.read_text())
            stocks = day.get('stocks', {})
            if symbol in stocks:
                records[day['date']] = stocks[symbol]
        except Exception:
            continue
    return [{'date': d, **v} for d, v in sorted(records.items())]


def compute_features(records: list, lookback: int = LOOKBACK) -> tuple:
    """
    Build (X, y) arrays from OHLCV history.

    Features per timestep (14 total):
    0.  price_position   (close-low)/(high-low)          0=at low, 1=at high
    1.  intraday_range   (high-low)/prev_close            volatility proxy
    2.  pct_change       (close-prev_close)/prev_close    daily return
    3.  vol_ratio        (volume/20d_avg_vol) - 1         volume anomaly
    4.  close_norm       close/20d_sma - 1                price level vs trend
    5.  sma5_ratio       close/5d_sma - 1                 short momentum
    6.  sma20_ratio      close/20d_sma - 1                medium momentum
    7.  momentum_3d      3d cumulative return
    8.  momentum_5d      5d cumulative return
    9.  volatility_5d    5d std of returns (realized vol)
    10. rsi14            RSI(14) normalized to [-1, 1]
    11. macd_signal      (ema12-ema26)/close normalized
    12. body_ratio       (close-open)/(high-low)          candlestick body
    13. hl_pos_5d        (close-5d_low)/(5d_high-5d_low)  5d price position

    Target (next-day return category):
      4 = STRONG UP   (>+2%)
      3 = UP          (+0.5% to +2%)
      2 = NEUTRAL     (-0.5% to +0.5%)
      1 = DOWN        (-2% to -0.5%)
      0 = STRONG DOWN (<-2%)
    """
    if len(records) < lookback + 30:
        return np.array([]), np.array([])

    closes = np.array([float(r.get('lp', r.get('close', 0))) for r in records])
    highs  = np.array([float(r.get('h', closes[i])) for i, r in enumerate(records)])
    lows   = np.array([float(r.get('l', closes[i])) for i, r in enumerate(records)])
    opens  = np.array([float(r.get('op', closes[i])) for i, r in enumerate(records)])
    vols   = np.array([float(r.get('q', 1)) for r in records])

    # Replace zeros to avoid division issues
    closes = np.where(closes <= 0, np.nan, closes)
    closes = np.where(np.isnan(closes), np.nanmean(closes), closes)

    n = len(records)
    X, y = [], []

    for t in range(30, n - 1):
        window = []
        for k in range(t - lookback + 1, t + 1):
            c = closes[k]
            h = max(highs[k], c)
            l = min(lows[k], c)
            o = opens[k] if opens[k] > 0 else c
            v = vols[k] if vols[k] > 0 else 1
            prev_c = closes[k - 1] if k > 0 else c

            # 0. Price position in day's range
            day_range = h - l
            price_pos = (c - l) / day_range if day_range > 0 else 0.5

            # 1. Intraday range as % of prev close
            intraday_range = day_range / prev_c if prev_c > 0 else 0

            # 2. Daily pct change
            pct = (c - prev_c) / prev_c if prev_c > 0 else 0

            # 3. Volume anomaly (deviation from 20d avg)
            vol_window = vols[max(0, k - 20):k]
            avg_vol = vol_window.mean() if len(vol_window) > 0 else v
            vol_ratio = (v / avg_vol - 1) if avg_vol > 0 else 0.0
            vol_ratio = np.clip(vol_ratio, -1.5, 4.0)

            # 4-6. Price vs moving averages
            sma5  = closes[max(0, k - 5):k + 1].mean()
            sma20 = closes[max(0, k - 20):k + 1].mean()
            close_norm  = (c / sma20 - 1) if sma20 > 0 else 0
            sma5_ratio  = (c / sma5  - 1) if sma5  > 0 else 0
            sma20_ratio = (c / sma20 - 1) if sma20 > 0 else 0

            # 7-8. Momentum (cumulative returns)
            m3 = (c / closes[k - 3] - 1) if k >= 3 and closes[k - 3] > 0 else 0
            m5 = (c / closes[k - 5] - 1) if k >= 5 and closes[k - 5] > 0 else 0

            # 9. Realized volatility (5d std of daily returns)
            slice5 = closes[max(0, k - 5):k + 1]
            rets5 = np.diff(slice5) / slice5[:-1] if len(slice5) > 1 else np.array([0.0])
            vol5 = rets5.std() if len(rets5) > 1 else 0.0

            # 10. RSI(14)
            gains, losses = [], []
            for j in range(max(1, k - 14), k + 1):
                d = closes[j] - closes[j - 1]
                (gains if d > 0 else losses).append(abs(d))
            avg_g = np.mean(gains) if gains else 1e-6
            avg_l = np.mean(losses) if losses else 1e-6
            rsi = 100 - 100 / (1 + avg_g / avg_l)
            rsi_norm = (rsi - 50) / 50  # normalize to [-1, 1]

            # 11. MACD histogram (12-26 period)
            ema12 = closes[max(0, k - 12):k + 1].mean()
            ema26 = closes[max(0, k - 26):k + 1].mean()
            macd = (ema12 - ema26) / c if c > 0 else 0

            # 12. Body ratio: candlestick body size and direction
            body_ratio = (c - o) / day_range if day_range > 0 else 0  # [-1, 1]

            # 13. 5-day high-low position
            slice5_h = highs[max(0, k - 5):k + 1]
            slice5_l = lows[max(0, k - 5):k + 1]
            hi5 = slice5_h.max() if len(slice5_h) > 0 else h
            lo5 = slice5_l.min() if len(slice5_l) > 0 else l
            hl5_range = hi5 - lo5
            hl_pos_5d = (c - lo5) / hl5_range if hl5_range > 0 else 0.5

            window.append([
                price_pos,
                np.clip(intraday_range, 0, 0.15),
                np.clip(pct, -0.10, 0.10),
                vol_ratio,
                np.clip(close_norm, -0.3, 0.3),
                np.clip(sma5_ratio, -0.2, 0.2),
                np.clip(sma20_ratio, -0.3, 0.3),
                np.clip(m3, -0.15, 0.15),
                np.clip(m5, -0.20, 0.20),
                np.clip(vol5, 0, 0.05),
                rsi_norm,
                np.clip(macd, -0.05, 0.05),
                np.clip(body_ratio, -1, 1),
                hl_pos_5d,
            ])

        # Target: next day's return
        next_ret = (closes[t + 1] - closes[t]) / closes[t] if closes[t] > 0 else 0
        if next_ret > 0.02:
            label = 4
        elif next_ret > 0.005:
            label = 3
        elif next_ret > -0.005:
            label = 2
        elif next_ret > -0.02:
            label = 1
        else:
            label = 0

        X.append(window)
        y.append(label)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


# ─── GRU Model (3 layers, fixed) ─────────────────────────────────────────────

def build_gru_model(config: dict):
    """
    Build 3-layer GRU with:
    - Input projection to h0
    - GRU1 → LayerNorm → Dropout
    - GRU2 → LayerNorm → Dropout
    - GRU3 → LayerNorm
    - Skip connection: proj(GRU1 output) added to GRU3 output (residual)
    - Attention pooling over all timesteps
    - FC: Linear(h2→fc) → BN → Dropout → Linear(fc→5)
    """
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        raise RuntimeError('PyTorch not installed. Run: pip install torch')

    h = config['hidden_units']  # list of 3 ints: [h0, h1, h2]
    dropout = config['dropout']
    fc_units = config['fc_units']

    class GRUPredictor(nn.Module):
        def __init__(self):
            super().__init__()

            # Input projection: raw features → h0
            self.input_proj = nn.Sequential(
                nn.Linear(N_FEATURES, h[0]),
                nn.LayerNorm(h[0]),
                nn.ReLU(),
            )

            # GRU Layer 1: h0 → h0
            self.gru1 = nn.GRU(h[0], h[0], batch_first=True)
            self.ln1  = nn.LayerNorm(h[0])
            self.drop1 = nn.Dropout(dropout)

            # GRU Layer 2: h0 → h1
            self.gru2 = nn.GRU(h[0], h[1], batch_first=True)
            self.ln2  = nn.LayerNorm(h[1])
            self.drop2 = nn.Dropout(dropout)

            # GRU Layer 3: h1 → h2
            self.gru3 = nn.GRU(h[1], h[2], batch_first=True)
            self.ln3  = nn.LayerNorm(h[2])
            self.drop3 = nn.Dropout(dropout)

            # Skip connection: project GRU1 output (h0) → h2 for residual
            self.skip_proj = nn.Linear(h[0], h[2], bias=False)

            # Attention pooling over timesteps
            self.attn_score = nn.Linear(h[2], 1)

            # FC classification head
            self.fc1    = nn.Linear(h[2], fc_units)
            self.bn_fc  = nn.BatchNorm1d(fc_units)
            self.drop_fc = nn.Dropout(dropout * 0.5)
            self.fc2    = nn.Linear(fc_units, N_CLASSES)
            self.relu   = nn.ReLU()

        def forward(self, x):
            # x: [B, T, N_FEATURES]

            # Input projection
            x = self.input_proj(x)  # [B, T, h0]

            # GRU Layer 1
            out1, _ = self.gru1(x)      # [B, T, h0]
            out1 = self.ln1(out1)
            out1 = self.drop1(out1)

            # GRU Layer 2
            out2, _ = self.gru2(out1)   # [B, T, h1]
            out2 = self.ln2(out2)
            out2 = self.drop2(out2)

            # GRU Layer 3
            out3, _ = self.gru3(out2)   # [B, T, h2]
            out3 = self.ln3(out3)

            # Residual: add projected layer-1 output (gradient highway)
            out3 = out3 + self.skip_proj(out1)  # [B, T, h2]
            out3 = self.drop3(out3)

            # Attention pooling: weighted sum over timesteps
            scores = self.attn_score(out3)          # [B, T, 1]
            weights = torch.softmax(scores, dim=1)  # [B, T, 1]
            context = (weights * out3).sum(dim=1)   # [B, h2]

            # FC head
            out = self.relu(self.fc1(context))      # [B, fc_units]
            out = self.bn_fc(out)
            out = self.drop_fc(out)
            out = self.fc2(out)                      # [B, N_CLASSES]
            return out

    return GRUPredictor()


# ─── Mixup Augmentation ───────────────────────────────────────────────────────

def mixup_batch(x, y, alpha=0.2):
    """
    Mixup: interpolate pairs of samples to create synthetic training data.
    Reduces overfitting significantly on thin NEPSE data.
    Returns mixed_x, y_a, y_b, lam for mixup loss computation.
    """
    import torch
    lam = np.random.beta(alpha, alpha) if alpha > 0 else 1.0
    batch_size = x.size(0)
    idx = torch.randperm(batch_size, device=x.device)
    mixed_x = lam * x + (1 - lam) * x[idx]
    y_a, y_b = y, y[idx]
    return mixed_x, y_a, y_b, lam


def mixup_criterion(criterion, pred, y_a, y_b, lam):
    return lam * criterion(pred, y_a) + (1 - lam) * criterion(pred, y_b)


# ─── Training ────────────────────────────────────────────────────────────────

def train_model(X_train, y_train, X_val, y_val, config: dict,
                n_epochs: int = 30, verbose: bool = False) -> float:
    """Train GRU model. Returns best validation accuracy."""
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        return 0.0

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = build_gru_model(config).to(device)

    # AdamW: built-in weight decay (L2 reg), decoupled from adaptive LR
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config['lr'], weight_decay=config.get('weight_decay', 1e-3)
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=n_epochs)

    # Class-weighted loss with label smoothing (prevents overconfident predictions)
    class_counts = np.bincount(y_train, minlength=N_CLASSES).astype(float)
    weights = 1.0 / (class_counts + 1.0)
    weights = weights / weights.sum() * N_CLASSES
    weights_t = torch.tensor(weights, dtype=torch.float32).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights_t, label_smoothing=0.1)

    Xt = torch.tensor(X_train).to(device)
    yt = torch.tensor(y_train).to(device)
    Xv = torch.tensor(X_val).to(device)
    yv = torch.tensor(y_val).to(device)

    loader = DataLoader(TensorDataset(Xt, yt),
                        batch_size=config['batch_size'], shuffle=True, drop_last=True)

    best_val_acc = 0.0
    best_state   = None
    patience     = 10
    no_improve   = 0

    for epoch in range(n_epochs):
        model.train()
        for xb, yb in loader:
            # Mixup augmentation (alpha=0.2 → mild interpolation)
            xb_mix, ya, yb_mix, lam = mixup_batch(xb, yb, alpha=0.2)
            optimizer.zero_grad()
            out = model(xb_mix)
            loss = mixup_criterion(criterion, out, ya, yb_mix, lam)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_pred = model(Xv).argmax(dim=1)
            val_acc = (val_pred == yv).float().mean().item()

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state   = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve   = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                break

        if verbose and (epoch + 1) % 10 == 0:
            print('    Epoch %2d/%d  val_acc=%.4f  best=%.4f' % (
                epoch + 1, n_epochs, val_acc, best_val_acc))

    return best_val_acc


# ─── Genetic Algorithm ────────────────────────────────────────────────────────

class GeneticOptimizer:
    """
    GA to optimize 3-layer GRU architecture:
    - hidden_units: [h0, h1, h2], each in {32, 64, 128, 256}
      (coarsening allowed: h0 >= h1 >= h2 is not enforced — let evolution decide)
    - dropout:  {0.1, 0.2, 0.3, 0.4, 0.5}
    - fc_units: {32, 64, 128, 256}
    n_layers is fixed at 3.
    """

    UNITS_OPTIONS = [32, 64, 128, 256]
    DROP_OPTIONS  = [0.1, 0.2, 0.3, 0.4, 0.5]
    FC_OPTIONS    = [32, 64, 128, 256]

    def __init__(self, pop_size: int = 10, generations: int = 6,
                 crossover_rate: float = 0.7, mutation_rate: float = 0.3):
        self.pop_size = pop_size
        self.generations = generations
        self.crossover_rate = crossover_rate
        self.mutation_rate = mutation_rate

    def random_individual(self, lr: float = 0.001, batch_size: int = 32) -> dict:
        return {
            'n_layers':     3,
            'hidden_units': [random.choice(self.UNITS_OPTIONS) for _ in range(3)],
            'dropout':      random.choice(self.DROP_OPTIONS),
            'fc_units':     random.choice(self.FC_OPTIONS),
            'lr':           lr,
            'batch_size':   batch_size,
            'weight_decay': 1e-3,
        }

    def crossover(self, p1: dict, p2: dict) -> dict:
        if random.random() > self.crossover_rate:
            return {k: (list(v) if isinstance(v, list) else v) for k, v in p1.items()}
        return {
            'n_layers':     3,
            'hidden_units': [random.choice([p1['hidden_units'][i], p2['hidden_units'][i]])
                             for i in range(3)],
            'dropout':      random.choice([p1['dropout'], p2['dropout']]),
            'fc_units':     random.choice([p1['fc_units'], p2['fc_units']]),
            'lr':           p1['lr'],
            'batch_size':   p1['batch_size'],
            'weight_decay': p1.get('weight_decay', 1e-3),
        }

    def mutate(self, ind: dict) -> dict:
        ind = {k: (list(v) if isinstance(v, list) else v) for k, v in ind.items()}
        for i in range(3):
            if random.random() < self.mutation_rate:
                ind['hidden_units'][i] = random.choice(self.UNITS_OPTIONS)
        if random.random() < self.mutation_rate:
            ind['dropout'] = random.choice(self.DROP_OPTIONS)
        if random.random() < self.mutation_rate:
            ind['fc_units'] = random.choice(self.FC_OPTIONS)
        return ind

    def optimize(self, X_train, y_train, X_val, y_val, lr: float, batch_size: int) -> tuple:
        """Returns (best_config, best_val_accuracy)."""
        pop = [self.random_individual(lr, batch_size) for _ in range(self.pop_size)]
        best_config  = None
        best_fitness = 0.0

        for gen in range(self.generations):
            fitnesses = []
            for ind in pop:
                acc = train_model(X_train, y_train, X_val, y_val, ind, n_epochs=15)
                fitnesses.append(acc)
                if acc > best_fitness:
                    best_fitness = acc
                    best_config  = {k: (list(v) if isinstance(v, list) else v)
                                    for k, v in ind.items()}

            print('  GA gen %d/%d: best=%.4f avg=%.4f  arch=%s drop=%.1f fc=%d' % (
                gen + 1, self.generations, best_fitness, np.mean(fitnesses),
                best_config['hidden_units'], best_config['dropout'], best_config['fc_units']))

            # Tournament selection
            new_pop = []
            for _ in range(self.pop_size):
                a, b = random.sample(range(len(pop)), 2)
                new_pop.append(pop[a] if fitnesses[a] >= fitnesses[b] else pop[b])

            # Crossover + mutation
            children = []
            for i in range(0, self.pop_size, 2):
                c1 = self.crossover(new_pop[i], new_pop[(i + 1) % self.pop_size])
                c2 = self.crossover(new_pop[(i + 1) % self.pop_size], new_pop[i])
                children.extend([self.mutate(c1), self.mutate(c2)])

            # Elitism: preserve best individual unchanged
            best_idx = int(np.argmax(fitnesses))
            pop = children[:self.pop_size - 1] + [pop[best_idx]]

        return best_config, best_fitness


# ─── Particle Swarm Optimization ─────────────────────────────────────────────

class PSOOptimizer:
    """
    PSO to optimize continuous hyperparameters:
    - learning rate:  [1e-4, 3e-2]  (log scale)
    - batch size:     [16, 128]     (power-of-2 discretized)
    - weight decay:   [1e-5, 1e-2]  (log scale)

    Particle position: [log10(lr), log2(batch), log10(wd)]
    """

    LR_BOUNDS    = (-4.0, -1.5)   # 1e-4 to ~3e-2
    BATCH_BOUNDS = (4.0, 7.0)     # 2^4=16 to 2^7=128
    WD_BOUNDS    = (-5.0, -2.0)   # 1e-5 to 1e-2

    def __init__(self, n_particles: int = 8, iterations: int = 10,
                 inertia: float = 0.729, c1: float = 1.494, c2: float = 1.494):
        # Clerc-Kennedy constriction coefficients (theoretically convergent)
        self.n    = n_particles
        self.iters = iterations
        self.w    = inertia
        self.c1   = c1
        self.c2   = c2

    def decode(self, pos: np.ndarray) -> tuple:
        lr    = float(10 ** np.clip(pos[0], *self.LR_BOUNDS))
        batch = int(2 ** round(np.clip(pos[1], *self.BATCH_BOUNDS)))
        wd    = float(10 ** np.clip(pos[2], *self.WD_BOUNDS))
        return lr, batch, wd

    def optimize(self, X_train, y_train, X_val, y_val, arch_config: dict) -> tuple:
        """Returns (best_lr, best_batch, best_wd, best_val_accuracy)."""
        lo = np.array([self.LR_BOUNDS[0], self.BATCH_BOUNDS[0], self.WD_BOUNDS[0]])
        hi = np.array([self.LR_BOUNDS[1], self.BATCH_BOUNDS[1], self.WD_BOUNDS[1]])

        positions = np.random.uniform(lo, hi, (self.n, 3))
        velocities = np.random.uniform(-(hi - lo) * 0.1, (hi - lo) * 0.1, (self.n, 3))
        p_best_pos = positions.copy()
        p_best_fit = np.zeros(self.n)
        g_best_pos = positions[0].copy()
        g_best_fit = 0.0

        for it in range(self.iters):
            for i in range(self.n):
                lr, batch, wd = self.decode(positions[i])
                cfg = {k: (list(v) if isinstance(v, list) else v)
                       for k, v in arch_config.items()}
                cfg['lr'] = lr
                cfg['batch_size'] = batch
                cfg['weight_decay'] = wd
                fitness = train_model(X_train, y_train, X_val, y_val, cfg, n_epochs=12)

                if fitness > p_best_fit[i]:
                    p_best_fit[i] = fitness
                    p_best_pos[i] = positions[i].copy()

                if fitness > g_best_fit:
                    g_best_fit = fitness
                    g_best_pos = positions[i].copy()

            # Velocity update with inertia + cognitive + social
            r1 = np.random.rand(self.n, 3)
            r2 = np.random.rand(self.n, 3)
            velocities = (
                self.w  * velocities
                + self.c1 * r1 * (p_best_pos - positions)
                + self.c2 * r2 * (g_best_pos - positions)
            )
            positions = np.clip(positions + velocities, lo, hi)

            best_lr, best_batch, best_wd = self.decode(g_best_pos)
            print('  PSO iter %2d/%d: best=%.4f  lr=%.6f  batch=%d  wd=%.6f' % (
                it + 1, self.iters, g_best_fit, best_lr, best_batch, best_wd))

        best_lr, best_batch, best_wd = self.decode(g_best_pos)
        return best_lr, best_batch, best_wd, g_best_fit


# ─── Full Training Pipeline ───────────────────────────────────────────────────

def train_symbol(symbol: str, lookback: int = LOOKBACK,
                 ga_pop: int = 10, ga_gens: int = 6,
                 pso_particles: int = 8, pso_iters: int = 10,
                 final_epochs: int = 80) -> dict:
    """Full pipeline: load → features → GA → PSO → final train → save model."""
    print('\n' + '=' * 65)
    print('  Training 3-Layer GRU: %s' % symbol)
    print('=' * 65)

    try:
        import torch
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        print('[OK] Device: %s' % device)
        if device.type == 'cuda':
            print('[OK] GPU: %s  VRAM: %.1fGB' % (
                torch.cuda.get_device_name(0),
                torch.cuda.get_device_properties(0).total_memory / 1e9))
    except ImportError:
        print('[!] PyTorch not found. Check /scratch/C00621463/pypackages install.')
        return {}

    records = load_stock_history(symbol)
    if len(records) < lookback + 60:
        print('[!] Insufficient data: %d records (need %d+)' % (len(records), lookback + 60))
        return {}

    print('[OK] Loaded %d records (%s to %s)' % (
        len(records), records[0]['date'], records[-1]['date']))

    X, y = compute_features(records, lookback)
    if len(X) < 80:
        print('[!] Too few samples after feature engineering: %d' % len(X))
        return {}

    counts = np.bincount(y, minlength=N_CLASSES)
    labels = ['SDOWN', 'DOWN', 'NEUT', 'UP', 'SUP']
    dist_str = '  '.join('%s:%d' % (l, c) for l, c in zip(labels, counts))
    print('[OK] Samples: %d  Distribution: %s' % (len(X), dist_str))

    # Temporal train/val/test split (80/10/10) — NO shuffling before split (time-series!)
    n = len(X)
    i_val  = int(n * 0.80)
    i_test = int(n * 0.90)
    X_train, y_train = X[:i_val],         y[:i_val]
    X_val,   y_val   = X[i_val:i_test],   y[i_val:i_test]
    X_test,  y_test  = X[i_test:],        y[i_test:]
    print('[OK] Train=%d  Val=%d  Test=%d' % (len(X_train), len(X_val), len(X_test)))

    # ── Step 1: GA architecture search ──────────────────────────────────────
    print('\n[GA] Architecture search  pop=%d  generations=%d' % (ga_pop, ga_gens))
    ga = GeneticOptimizer(pop_size=ga_pop, generations=ga_gens)
    best_arch, ga_acc = ga.optimize(X_train, y_train, X_val, y_val, lr=0.001, batch_size=32)
    print('[GA] Best arch: units=%s  dropout=%.2f  fc=%d  (val_acc=%.4f)' % (
        best_arch['hidden_units'], best_arch['dropout'], best_arch['fc_units'], ga_acc))

    # ── Step 2: PSO hyperparameter fine-tuning ───────────────────────────────
    print('\n[PSO] Fine-tuning LR / batch / weight_decay  particles=%d  iters=%d' % (
        pso_particles, pso_iters))
    pso = PSOOptimizer(n_particles=pso_particles, iterations=pso_iters)
    best_lr, best_batch, best_wd, pso_acc = pso.optimize(
        X_train, y_train, X_val, y_val, best_arch)
    print('[PSO] Best: lr=%.6f  batch=%d  wd=%.6f  (val_acc=%.4f)' % (
        best_lr, best_batch, best_wd, pso_acc))

    # ── Step 3: Final training with best hyperparameters ─────────────────────
    best_arch['lr']           = best_lr
    best_arch['batch_size']   = best_batch
    best_arch['weight_decay'] = best_wd

    print('\n[FINAL] Training %d epochs with best hyperparameters...' % final_epochs)
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        model  = build_gru_model(best_arch).to(device)

        total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        print('[FINAL] Model parameters: %s' % f'{total_params:,}')

        optimizer = torch.optim.AdamW(model.parameters(), lr=best_lr, weight_decay=best_wd)
        # Warm restart schedule: better for thin data
        scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer, T_0=20, T_mult=2)

        class_counts = np.bincount(y_train, minlength=N_CLASSES).astype(float)
        weights = 1.0 / (class_counts + 1.0)
        weights = weights / weights.sum() * N_CLASSES
        weights_t = torch.tensor(weights, dtype=torch.float32).to(device)
        criterion = nn.CrossEntropyLoss(weight=weights_t, label_smoothing=0.1)

        Xt     = torch.tensor(X_train).to(device)
        yt     = torch.tensor(y_train).to(device)
        Xv     = torch.tensor(X_val).to(device)
        yv     = torch.tensor(y_val).to(device)
        Xtest  = torch.tensor(X_test).to(device)
        ytest  = torch.tensor(y_test).to(device)

        loader = DataLoader(TensorDataset(Xt, yt),
                            batch_size=best_batch, shuffle=True, drop_last=True)

        best_val    = 0.0
        best_state  = None
        no_improve  = 0
        patience    = 15  # more patience in final training

        for epoch in range(final_epochs):
            model.train()
            total_loss = 0.0
            for xb, yb in loader:
                xb_mix, ya, yb_mix, lam = mixup_batch(xb, yb, alpha=0.2)
                optimizer.zero_grad()
                out  = model(xb_mix)
                loss = mixup_criterion(criterion, out, ya, yb_mix, lam)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()
            scheduler.step()

            model.eval()
            with torch.no_grad():
                val_pred = model(Xv).argmax(dim=1)
                val_acc  = (val_pred == yv).float().mean().item()

            if val_acc > best_val:
                best_val   = val_acc
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    print('  Early stop at epoch %d (best val=%.4f)' % (epoch + 1, best_val))
                    break

            if (epoch + 1) % 10 == 0:
                print('  Epoch %3d/%d  loss=%.4f  val=%.4f  best=%.4f' % (
                    epoch + 1, final_epochs, total_loss / max(len(loader), 1), val_acc, best_val))

        # ── Evaluate on test set ─────────────────────────────────────────────
        model.load_state_dict({k: v.to(device) for k, v in best_state.items()})
        model.eval()
        with torch.no_grad():
            test_logits = model(Xtest)
            test_pred   = test_logits.argmax(dim=1)
            test_acc    = (test_pred == ytest).float().mean().item()
            test_probs  = torch.softmax(test_logits, dim=1).cpu().numpy()

        # Directional accuracy (non-neutral predictions only)
        tp = test_pred.cpu().numpy()
        ty = y_test
        actual_dir = (ty >= 3).astype(int) - (ty <= 1).astype(int)
        pred_dir   = (tp >= 3).astype(int) - (tp <= 1).astype(int)
        mask = actual_dir != 0
        dir_acc = float((actual_dir[mask] == pred_dir[mask]).mean()) if mask.sum() > 0 else 0.0

        # Precision on directional calls (when we say UP/DOWN, how often right?)
        dir_mask_pred = pred_dir != 0
        if dir_mask_pred.sum() > 0:
            prec = float((actual_dir[dir_mask_pred] == pred_dir[dir_mask_pred]).mean())
        else:
            prec = 0.0

        print('\n[RESULT] %s — val=%.4f  test=%.4f  dir_acc=%.4f  precision=%.4f' % (
            symbol, best_val, test_acc, dir_acc, prec))

        # ── Fit isotonic calibration on validation set ───────────────────────
        calibrators = {}
        try:
            with torch.no_grad():
                val_logits = model(Xv)
                val_probs = torch.softmax(val_logits, dim=1).cpu().numpy()
            for cls in range(N_CLASSES):
                ir = IsotonicRegression(out_of_bounds='clip')
                ir.fit(val_probs[:, cls], (y_val == cls).astype(float))
                calibrators[cls] = ir
            print('[OK] Isotonic calibrators fitted on %d validation samples' % len(y_val))
        except Exception as e:
            print('[!] Calibration fitting failed: %s' % e)
            calibrators = {}

        # ── Save model ───────────────────────────────────────────────────────
        safe_name = symbol.replace('/', '_')
        model_path = MODEL_DIR / ('%s_gru.pt' % safe_name)
        torch.save({
            'model_state':  best_state,
            'config':       best_arch,
            'lookback':     lookback,
            'n_features':   N_FEATURES,
            'val_acc':      best_val,
            'test_acc':     test_acc,
            'dir_acc':      dir_acc,
            'precision':    prec,
            'trained_at':   datetime.now().isoformat(),
            'n_records':    len(records),
            'class_dist':   counts.tolist(),
            'calibrators':  calibrators,
        }, str(model_path))
        print('[OK] Model saved: %s' % model_path)

        return {
            'symbol':    symbol,
            'val_acc':   best_val,
            'test_acc':  test_acc,
            'dir_acc':   dir_acc,
            'precision': prec,
            'config':    best_arch,
        }

    except Exception as e:
        print('[!] Training failed: %s' % e)
        import traceback; traceback.print_exc()
        return {}


# ─── Prediction ──────────────────────────────────────────────────────────────

def predict_next_day(symbol: str) -> dict:
    """Load trained model and predict next trading day direction."""
    try:
        import torch
    except ImportError:
        return {'error': 'PyTorch not found. Check /scratch/C00621463/pypackages install.'}

    safe_name = symbol.replace('/', '_')
    model_path = MODEL_DIR / ('%s_gru.pt' % safe_name)
    if not model_path.exists():
        return {'error': 'No model for %s. Run --train --symbol %s first.' % (symbol, symbol)}

    ckpt    = torch.load(str(model_path), map_location='cpu', weights_only=False)
    config  = ckpt['config']
    lookback = ckpt.get('lookback', LOOKBACK)

    records = load_stock_history(symbol)
    if len(records) < lookback + 30:
        return {'error': 'Insufficient history for prediction'}

    # Use last (lookback+30) records to compute features for the final window
    X, _ = compute_features(records[-(lookback + 50):], lookback)
    if len(X) == 0:
        return {'error': 'Feature computation failed'}

    model = build_gru_model(config)
    model.load_state_dict(ckpt['model_state'])
    model.eval()

    import torch
    last_x = torch.tensor(X[-1:])
    with torch.no_grad():
        logits = model(last_x)
        probs  = torch.softmax(logits, dim=1).numpy()[0]

    # Apply isotonic calibration if available
    if 'calibrators' in ckpt and ckpt['calibrators']:
        try:
            cal = ckpt['calibrators']
            for cls in range(N_CLASSES):
                probs[cls] = cal[cls].predict([probs[cls]])[0]
            prob_sum = probs.sum()
            if prob_sum > 0:
                probs = probs / prob_sum  # renormalize
        except Exception:
            pass  # fall back to raw softmax probs

    pred = int(np.argmax(probs))

    labels   = ['STRONG DOWN', 'DOWN', 'NEUTRAL', 'UP', 'STRONG UP']
    signals  = {0: 'STRONG SELL', 1: 'SELL', 2: 'HOLD', 3: 'BUY', 4: 'STRONG BUY'}
    bullish  = float(probs[3] + probs[4])
    bearish  = float(probs[0] + probs[1])

    return {
        'symbol':        symbol,
        'prediction':    labels[pred],
        'signal':        signals[pred],
        'confidence':    float(probs[pred]),
        'bullish_prob':  bullish,
        'bearish_prob':  bearish,
        'probabilities': {labels[i]: round(float(probs[i]), 4) for i in range(5)},
        'model_val_acc': ckpt.get('val_acc', 0),
        'model_dir_acc': ckpt.get('dir_acc', 0),
        'model_prec':    ckpt.get('precision', 0),
        'trained_at':    ckpt.get('trained_at', 'unknown'),
        'current_price': records[-1].get('lp', 0) if records else 0,
    }


# ─── Backtest ─────────────────────────────────────────────────────────────────

def backtest_symbol(symbol: str) -> dict:
    """Walk-forward backtest: for each test-set day, predict and record outcome."""
    records = load_stock_history(symbol)
    lookback = LOOKBACK
    X, y = compute_features(records, lookback)
    if len(X) < 80:
        return {'error': 'Insufficient data'}

    n       = len(X)
    i_test  = int(n * 0.90)
    X_test  = X[i_test:]
    y_test  = y[i_test:]

    safe_name = symbol.replace('/', '_')
    model_path = MODEL_DIR / ('%s_gru.pt' % safe_name)
    if not model_path.exists():
        return {'error': 'No model. Run --train first.'}

    try:
        import torch
        ckpt   = torch.load(str(model_path), map_location='cpu', weights_only=False)
        model  = build_gru_model(ckpt['config'])
        model.load_state_dict(ckpt['model_state'])
        model.eval()

        Xtest  = torch.tensor(X_test)
        with torch.no_grad():
            logits = model(Xtest)
            preds  = logits.argmax(dim=1).numpy()
            probs  = torch.softmax(logits, dim=1).numpy()

    except Exception as e:
        return {'error': str(e)}

    # Metrics
    acc       = float((preds == y_test).mean())
    dir_pred  = (preds >= 3).astype(int) - (preds <= 1).astype(int)
    dir_actual = (y_test >= 3).astype(int) - (y_test <= 1).astype(int)
    mask = dir_actual != 0
    dir_acc = float((dir_pred[mask] == dir_actual[mask]).mean()) if mask.sum() > 0 else 0

    # Simulated P&L: trade when model says UP or DOWN (ignore NEUTRAL)
    closes   = [float(r.get('lp', 0)) for r in records]
    n_offset = i_test + 30  # offset into closes array
    pnl      = 0.0
    trades   = 0
    for i, (p, d) in enumerate(zip(preds, dir_pred)):
        if d == 0:
            continue  # model says neutral, skip
        if n_offset + i + 1 >= len(closes):
            break
        entry  = closes[n_offset + i]
        exit_  = closes[n_offset + i + 1]
        ret    = (exit_ - entry) / entry if entry > 0 else 0
        pnl   += ret * d  # d = +1 for long, -1 for short
        trades += 1

    return {
        'symbol':    symbol,
        'test_days': len(X_test),
        'accuracy':  round(acc, 4),
        'dir_acc':   round(dir_acc, 4),
        'trades':    trades,
        'pnl_pct':   round(pnl * 100, 2),
        'avg_per_trade': round(pnl / trades * 100, 3) if trades > 0 else 0,
    }


# ─── Default config from GA/PSO runs (avoids per-stock search) ───────────────
DEFAULT_CONFIG = {
    'n_layers':     3,
    'hidden_units': [128, 128, 128],
    'dropout':      0.3,
    'fc_units':     128,
    'lr':           0.0015,
    'batch_size':   32,
    'weight_decay': 0.003,
}


def train_symbol_fast(symbol: str, lookback: int = LOOKBACK,
                      epochs: int = 60) -> dict:
    """Fast training: skip GA/PSO, use proven defaults, FP16 mixed precision."""
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
        from torch.amp import autocast, GradScaler
    except ImportError:
        print('[!] PyTorch not found.')
        return {}

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    use_amp = device.type == 'cuda'

    records = load_stock_history(symbol)
    if len(records) < lookback + 60:
        return {}

    X, y = compute_features(records, lookback)
    if len(X) < 80:
        return {}

    counts = np.bincount(y, minlength=N_CLASSES)

    n = len(X)
    i_val  = int(n * 0.80)
    i_test = int(n * 0.90)
    X_train, y_train = X[:i_val],       y[:i_val]
    X_val,   y_val   = X[i_val:i_test], y[i_val:i_test]
    X_test,  y_test  = X[i_test:],      y[i_test:]

    config = dict(DEFAULT_CONFIG)
    model = build_gru_model(config).to(device)

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config['lr'], weight_decay=config['weight_decay'])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    class_counts = np.bincount(y_train, minlength=N_CLASSES).astype(float)
    w = 1.0 / (class_counts + 1.0)
    w = w / w.sum() * N_CLASSES
    weights_t = torch.tensor(w, dtype=torch.float32).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights_t, label_smoothing=0.1)

    Xt = torch.tensor(X_train).to(device)
    yt = torch.tensor(y_train).to(device)
    Xv = torch.tensor(X_val).to(device)
    yv = torch.tensor(y_val).to(device)
    Xtest = torch.tensor(X_test).to(device)
    ytest = torch.tensor(y_test).to(device)

    loader = DataLoader(TensorDataset(Xt, yt),
                        batch_size=config['batch_size'], shuffle=True, drop_last=True)

    scaler = GradScaler('cuda') if use_amp else None
    best_val = 0.0
    best_state = None
    no_improve = 0

    for epoch in range(epochs):
        model.train()
        for xb, yb in loader:
            xb_mix, ya, yb_mix, lam = mixup_batch(xb, yb, alpha=0.2)
            optimizer.zero_grad(set_to_none=True)
            if use_amp:
                with autocast('cuda'):
                    out = model(xb_mix)
                    loss = mixup_criterion(criterion, out, ya, yb_mix, lam)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                out = model(xb_mix)
                loss = mixup_criterion(criterion, out, ya, yb_mix, lam)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_acc = (model(Xv).argmax(dim=1) == yv).float().mean().item()

        if val_acc > best_val:
            best_val = val_acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= 10:
                break

    # Evaluate
    model.load_state_dict({k: v.to(device) for k, v in best_state.items()})
    model.eval()
    with torch.no_grad():
        test_logits = model(Xtest)
        tp = test_logits.argmax(dim=1).cpu().numpy()
        test_acc = float((tp == y_test).mean())

    actual_dir = (y_test >= 3).astype(int) - (y_test <= 1).astype(int)
    pred_dir = (tp >= 3).astype(int) - (tp <= 1).astype(int)
    mask = actual_dir != 0
    dir_acc = float((actual_dir[mask] == pred_dir[mask]).mean()) if mask.sum() > 0 else 0.0
    dir_mask_pred = pred_dir != 0
    prec = float((actual_dir[dir_mask_pred] == pred_dir[dir_mask_pred]).mean()) if dir_mask_pred.sum() > 0 else 0.0

    # Fit isotonic calibration on validation set
    calibrators = {}
    try:
        with torch.no_grad():
            val_logits = model(Xv)
            val_probs = torch.softmax(val_logits, dim=1).cpu().numpy()
        for cls in range(N_CLASSES):
            ir = IsotonicRegression(out_of_bounds='clip')
            ir.fit(val_probs[:, cls], (y_val == cls).astype(float))
            calibrators[cls] = ir
    except Exception:
        calibrators = {}

    # Save
    safe_name = symbol.replace('/', '_')
    model_path = MODEL_DIR / ('%s_gru.pt' % safe_name)
    torch.save({
        'model_state': best_state, 'config': config, 'lookback': lookback,
        'n_features': N_FEATURES, 'val_acc': best_val, 'test_acc': test_acc,
        'dir_acc': dir_acc, 'precision': prec, 'trained_at': datetime.now().isoformat(),
        'n_records': len(records), 'class_dist': counts.tolist(),
        'calibrators': calibrators,
    }, str(model_path))

    print('[OK] %s  val=%.3f  test=%.3f  dir=%.3f  (%d epochs)' % (
        symbol, best_val, test_acc, dir_acc, epoch + 1))

    return {
        'symbol': symbol, 'val_acc': best_val, 'test_acc': test_acc,
        'dir_acc': dir_acc, 'precision': prec, 'config': config,
    }


def _print_summary(results):
    if not results:
        return
    accs     = [r['test_acc'] for r in results]
    dir_accs = [r['dir_acc']  for r in results]
    best_idx = int(np.argmax(accs))
    print('\n' + '=' * 65)
    print('TRAINING COMPLETE: %d stocks' % len(results))
    print('  Avg test acc:     %.4f' % np.mean(accs))
    print('  Avg directional:  %.4f' % np.mean(dir_accs))
    print('  Best: %s (test=%.4f dir=%.4f)' % (
        results[best_idx]['symbol'], accs[best_idx], dir_accs[best_idx]))
    print('=' * 65)


def _worker(task):
    """Worker function for parallel training."""
    sym, lookback, ga_pop, ga_gens, pso_n, pso_iters, epochs, idx, total = task
    print('\n[%d/%d] %s' % (idx, total, sym))
    return train_symbol(
        sym, lookback,
        ga_pop=ga_pop, ga_gens=ga_gens,
        pso_particles=pso_n, pso_iters=pso_iters,
        final_epochs=epochs,
    )


def _train_parallel(symbols, args):
    """Train multiple stocks in parallel using ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import torch

    n = len(symbols)
    tasks = [
        (sym, args.lookback, args.ga_pop, args.ga_gens,
         args.pso_n, args.pso_iters, args.epochs, i, n)
        for i, sym in enumerate(symbols, 1)
    ]

    print('[OK] Launching %d workers for %d stocks' % (args.workers, n))
    if torch.cuda.is_available():
        print('[OK] GPU: %s  VRAM: %.1fGB' % (
            torch.cuda.get_device_name(0),
            torch.cuda.get_device_properties(0).total_memory / 1e9))

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                res = fut.result()
                if res:
                    results.append(res)
                    done = len(results)
                    print('[PROGRESS] %d/%d complete (latest: %s)' % (done, n, sym))
            except Exception as e:
                print('[ERROR] %s failed: %s' % (sym, e))

    _print_summary(results)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='3-Layer GRU Predictor for NEPSE')
    parser.add_argument('--train',    action='store_true', help='Train model')
    parser.add_argument('--predict',  action='store_true', help='Predict next day')
    parser.add_argument('--backtest', action='store_true', help='Backtest on holdout set')
    parser.add_argument('--symbol',   type=str, default='', help='Stock symbol (or empty = all)')
    parser.add_argument('--lookback', type=int, default=LOOKBACK)
    parser.add_argument('--ga-pop',   type=int, default=10)
    parser.add_argument('--ga-gens',  type=int, default=6)
    parser.add_argument('--pso-n',    type=int, default=8)
    parser.add_argument('--pso-iters',type=int, default=10)
    parser.add_argument('--epochs',   type=int, default=80)
    parser.add_argument('--min-days', type=int, default=120, help='Min records to train')
    parser.add_argument('--workers',  type=int, default=1, help='Parallel training workers')
    parser.add_argument('--skip-trained', action='store_true', help='Skip stocks with existing models')
    parser.add_argument('--fast', action='store_true', help='Fast mode: skip GA/PSO, use defaults + FP16')
    args = parser.parse_args()

    if args.train:
        if args.symbol:
            result = train_symbol(
                args.symbol, args.lookback,
                ga_pop=args.ga_pop, ga_gens=args.ga_gens,
                pso_particles=args.pso_n, pso_iters=args.pso_iters,
                final_epochs=args.epochs,
            )
            if result:
                print('\nFinal result:', result)
        else:
            # Count days per stock
            if not HISTORY_DIR.exists():
                print('[!] No history data found in %s' % HISTORY_DIR)
                return
            stock_counts = {}
            for f in sorted(HISTORY_DIR.glob('*.json')):
                try:
                    day = json.loads(f.read_text())
                    for sym in day.get('stocks', {}):
                        stock_counts[sym] = stock_counts.get(sym, 0) + 1
                except Exception:
                    continue
            # Allow env var to restrict to a subset of symbols
            env_syms = os.environ.get('TRAIN_SYMBOLS', '')
            if env_syms:
                allowed = set(env_syms.split(','))
                symbols = sorted(s for s, c in stock_counts.items()
                                 if c >= args.min_days and s in allowed)
            else:
                symbols = sorted(s for s, c in stock_counts.items() if c >= args.min_days)
            print('[OK] Found %d stocks with %d+ days of data' % (len(symbols), args.min_days))

            if args.skip_trained:
                already = set()
                for p in MODEL_DIR.glob('*_gru.pt'):
                    already.add(p.stem.replace('_gru', ''))
                before = len(symbols)
                symbols = [s for s in symbols if s.replace('/', '_') not in already]
                print('[OK] Skipping %d already-trained, %d remaining' % (
                    before - len(symbols), len(symbols)))

            if args.fast:
                # Fast mode: skip GA/PSO, use proven defaults + FP16
                print('[FAST] Using default architecture, FP16 mixed precision')
                results = []
                for i, sym in enumerate(symbols, 1):
                    print('[%d/%d]' % (i, len(symbols)), end=' ')
                    res = train_symbol_fast(sym, args.lookback, epochs=args.epochs)
                    if res:
                        results.append(res)
                _print_summary(results)
            elif args.workers > 1 and len(symbols) > 1:
                _train_parallel(symbols, args)
            else:
                results = []
                for i, sym in enumerate(symbols, 1):
                    print('\n[%d/%d] %s' % (i, len(symbols), sym))
                    res = train_symbol(
                        sym, args.lookback,
                        ga_pop=args.ga_pop, ga_gens=args.ga_gens,
                        pso_particles=args.pso_n, pso_iters=args.pso_iters,
                        final_epochs=args.epochs,
                    )
                    if res:
                        results.append(res)
                _print_summary(results)

    elif args.predict:
        if not args.symbol:
            print('[!] Specify --symbol for prediction')
            return
        result = predict_next_day(args.symbol)
        print('\n' + '=' * 55)
        print('  GRU PREDICTION: %s' % args.symbol)
        print('=' * 55)
        for k, v in result.items():
            if k != 'probabilities':
                print('  %-20s: %s' % (k, v))
        if 'probabilities' in result:
            print('\n  Class Probabilities:')
            for label, prob in result['probabilities'].items():
                bar = '█' * int(prob * 30)
                print('    %-14s %5.1f%%  %s' % (label, prob * 100, bar))
        print('=' * 55)

    elif args.backtest:
        if not args.symbol:
            print('[!] Specify --symbol for backtest')
            return
        result = backtest_symbol(args.symbol)
        print('\n' + '=' * 55)
        print('  BACKTEST: %s' % args.symbol)
        print('=' * 55)
        for k, v in result.items():
            print('  %-20s: %s' % (k, v))
        print('=' * 55)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
