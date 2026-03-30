#!/usr/bin/env python3
"""
transformer_predictor.py -- Temporal Transformer for NEPSE stock prediction.

Companion to gru_predictor.py -- same input format, same output format,
but uses a Transformer encoder architecture instead of GRU.

Architecture (TemporalTransformer):
  - Input projection: 14 features -> d_model
  - Learnable positional encoding (15+1 positions)
  - Learnable [CLS] token prepended to sequence
  - N Transformer encoder layers (pre-norm, multi-head self-attention + FFN)
  - CLS token output -> FC classification head -> 5 classes

Key differences from GRU:
  - Attends to ALL timesteps simultaneously (not sequential)
  - Multi-head attention captures multiple temporal patterns
  - No vanishing gradient problem over long sequences
  - Positional encoding provides temporal awareness

Features (14 per timestep, lookback=15):
  price_position, intraday_range, pct_change, vol_ratio, close_norm,
  sma5_ratio, sma20_ratio, momentum_3d, momentum_5d, volatility_5d,
  rsi14, macd_signal, body_ratio, hl_pos_5d

Target (5-class softmax):
  0 = STRONG DOWN (<-2%)
  1 = DOWN        (-2% to -0.5%)
  2 = NEUTRAL     (-0.5% to +0.5%)
  3 = UP          (+0.5% to +2%)
  4 = STRONG UP   (>+2%)

Usage:
  python ml/transformer_predictor.py --train --symbol NABIL
  python ml/transformer_predictor.py --train               # all stocks with 100+ days
  python ml/transformer_predictor.py --predict --symbol NABIL
"""
import os, sys, json, argparse, math
from datetime import datetime
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

# Import shared functions from gru_predictor
try:
    from ml.gru_predictor import (
        load_stock_history, compute_features, mixup_batch, mixup_criterion,
        HISTORY_DIR, MODEL_DIR, N_FEATURES, N_CLASSES, LOOKBACK,
    )
except ImportError:
    from gru_predictor import (
        load_stock_history, compute_features, mixup_batch, mixup_criterion,
        HISTORY_DIR, MODEL_DIR, N_FEATURES, N_CLASSES, LOOKBACK,
    )

# ---- Transformer Configuration -----------------------------------------------

TRANSFORMER_CONFIG = {
    'd_model': 64,
    'nhead': 4,
    'num_layers': 3,
    'dim_feedforward': 128,
    'dropout': 0.2,
    'lr': 0.001,
    'batch_size': 32,
    'weight_decay': 0.01,
}


# ---- Temporal Transformer Model ---------------------------------------------

def build_transformer_model(config: dict):
    """
    Build a lightweight Temporal Transformer for stock prediction.

    Input:  (B, T=15, F=14) -- 15 timesteps, 14 features
    Output: (B, 5)          -- 5-class logits

    Architecture:
      Input Projection (14 -> d_model)
      Positional Encoding (learnable, 15+1 positions)
      Learnable [CLS] token prepended
      N Transformer Encoder Layers (pre-norm, multi-head self-attention + FFN)
      CLS token output -> FC head -> 5 classes
    """
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        raise RuntimeError('PyTorch not installed. Run: pip install torch')

    d_model = config.get('d_model', 64)
    nhead = config.get('nhead', 4)
    num_layers = config.get('num_layers', 3)
    dim_feedforward = config.get('dim_feedforward', 128)
    dropout = config.get('dropout', 0.2)
    n_features = config.get('n_features', N_FEATURES)
    n_classes = config.get('n_classes', N_CLASSES)

    class TemporalTransformer(nn.Module):
        """
        Lightweight Transformer for stock price prediction.

        Input: (B, T=15, F=14) -- 15 timesteps, 14 features

        Architecture:
          Input Projection (14 -> d_model)
          Positional Encoding (learnable, 15 positions)
          N Transformer Encoder Layers (multi-head self-attention + FFN)
          CLS token pooling (prepend a learnable [CLS] token)
          FC Head -> 5 classes

        Key differences from GRU:
          - Attends to ALL timesteps simultaneously (not sequential)
          - Multi-head attention captures multiple temporal patterns
          - No vanishing gradient problem over long sequences
          - Positional encoding gives temporal awareness
        """

        def __init__(self):
            super().__init__()
            self.d_model = d_model

            # Input projection
            self.input_proj = nn.Linear(n_features, d_model)

            # Learnable positional encoding (15 timesteps + 1 CLS token)
            self.pos_embed = nn.Parameter(torch.randn(1, 16, d_model) * 0.02)

            # Learnable CLS token
            self.cls_token = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)

            # Transformer encoder
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, dim_feedforward=dim_feedforward,
                dropout=dropout, batch_first=True, activation='gelu',
                norm_first=True,  # Pre-norm (more stable training)
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

            # Classification head
            self.head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, dim_feedforward),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(dim_feedforward, n_classes),
            )

        def forward(self, x):
            B, T, F = x.shape
            x = self.input_proj(x)  # (B, T, d_model)

            # Prepend CLS token
            cls = self.cls_token.expand(B, -1, -1)
            x = torch.cat([cls, x], dim=1)  # (B, T+1, d_model)

            # Add positional encoding
            x = x + self.pos_embed[:, :T + 1, :]

            # Transformer encode
            x = self.encoder(x)  # (B, T+1, d_model)

            # CLS token output
            cls_out = x[:, 0, :]  # (B, d_model)

            return self.head(cls_out)  # (B, n_classes)

    return TemporalTransformer()


# ---- Training ----------------------------------------------------------------

def train_symbol_transformer(symbol: str, lookback: int = LOOKBACK,
                             epochs: int = 60) -> dict:
    """
    Train a Transformer model for a single stock symbol.

    Uses same data pipeline as gru_predictor (load_stock_history, compute_features),
    same temporal train/val/test split (80/10/10), FP16 mixed precision, AdamW with
    cosine annealing, mixup augmentation, label smoothing, early stopping, and
    isotonic calibration on validation set.

    Saves model to data/models/{symbol}_transformer.pt
    """
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

    # ---- Load data ----
    records = load_stock_history(symbol)
    if len(records) < lookback + 60:
        print('[!] %s: insufficient data (%d records, need %d+)' % (
            symbol, len(records), lookback + 60))
        return {}

    X, y = compute_features(records, lookback)
    if len(X) < 80:
        print('[!] %s: too few samples after feature engineering (%d)' % (symbol, len(X)))
        return {}

    counts = np.bincount(y, minlength=N_CLASSES)
    labels_short = ['SDOWN', 'DOWN', 'NEUT', 'UP', 'SUP']
    dist_str = '  '.join('%s:%d' % (l, c) for l, c in zip(labels_short, counts))
    print('[OK] %s: %d samples  Distribution: %s' % (symbol, len(X), dist_str))

    # ---- Temporal train/val/test split (80/10/10) ----
    n = len(X)
    i_val = int(n * 0.80)
    i_test = int(n * 0.90)
    X_train, y_train = X[:i_val], y[:i_val]
    X_val, y_val = X[i_val:i_test], y[i_val:i_test]
    X_test, y_test = X[i_test:], y[i_test:]
    print('[OK] Train=%d  Val=%d  Test=%d' % (len(X_train), len(X_val), len(X_test)))

    # ---- Build model ----
    config = dict(TRANSFORMER_CONFIG)
    model = build_transformer_model(config).to(device)

    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print('[OK] Transformer parameters: %s' % f'{total_params:,}')

    # ---- Optimizer and scheduler ----
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config['lr'], weight_decay=config['weight_decay'])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    # ---- Class-weighted loss with label smoothing ----
    class_counts = np.bincount(y_train, minlength=N_CLASSES).astype(float)
    w = 1.0 / (class_counts + 1.0)
    w = w / w.sum() * N_CLASSES
    weights_t = torch.tensor(w, dtype=torch.float32).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights_t, label_smoothing=0.1)

    # ---- Prepare tensors ----
    Xt = torch.tensor(X_train).to(device)
    yt = torch.tensor(y_train).to(device)
    Xv = torch.tensor(X_val).to(device)
    yv = torch.tensor(y_val).to(device)
    Xtest = torch.tensor(X_test).to(device)
    ytest = torch.tensor(y_test).to(device)

    loader = DataLoader(TensorDataset(Xt, yt),
                        batch_size=config['batch_size'], shuffle=True, drop_last=True)

    # ---- Training loop with FP16 mixed precision ----
    scaler = GradScaler('cuda') if use_amp else None
    best_val = 0.0
    best_state = None
    no_improve = 0
    patience = 12

    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
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
            total_loss += loss.item()
        scheduler.step()

        # ---- Validation ----
        model.eval()
        with torch.no_grad():
            val_acc = (model(Xv).argmax(dim=1) == yv).float().mean().item()

        if val_acc > best_val:
            best_val = val_acc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print('  Early stop at epoch %d (best val=%.4f)' % (epoch + 1, best_val))
                break

        if (epoch + 1) % 10 == 0:
            print('  Epoch %3d/%d  loss=%.4f  val=%.4f  best=%.4f' % (
                epoch + 1, epochs, total_loss / max(len(loader), 1), val_acc, best_val))

    # ---- Evaluate on test set ----
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

    print('[RESULT] %s -- val=%.3f  test=%.3f  dir=%.3f  prec=%.3f  (%d epochs)' % (
        symbol, best_val, test_acc, dir_acc, prec, epoch + 1))

    # ---- Fit isotonic calibration on validation set ----
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

    # ---- Save model ----
    safe_name = symbol.replace('/', '_')
    model_path = MODEL_DIR / ('%s_transformer.pt' % safe_name)
    torch.save({
        'model_state': best_state,
        'config': config,
        'lookback': lookback,
        'n_features': N_FEATURES,
        'val_acc': best_val,
        'test_acc': test_acc,
        'dir_acc': dir_acc,
        'precision': prec,
        'trained_at': datetime.now().isoformat(),
        'n_records': len(records),
        'class_dist': counts.tolist(),
        'calibrators': calibrators,
    }, str(model_path))
    print('[OK] Model saved: %s' % model_path)

    return {
        'symbol': symbol,
        'val_acc': best_val,
        'test_acc': test_acc,
        'dir_acc': dir_acc,
        'precision': prec,
        'config': config,
    }


# ---- Prediction --------------------------------------------------------------

def predict_transformer(symbol: str) -> dict:
    """Load trained Transformer model and predict next trading day direction."""
    try:
        import torch
    except ImportError:
        return {'error': 'PyTorch not found. Check /scratch/C00621463/pypackages install.'}

    safe_name = symbol.replace('/', '_')
    model_path = MODEL_DIR / ('%s_transformer.pt' % safe_name)
    if not model_path.exists():
        return {'error': 'No Transformer model for %s. Run --train --symbol %s first.' % (
            symbol, symbol)}

    ckpt = torch.load(str(model_path), map_location='cpu', weights_only=False)
    config = ckpt['config']
    lookback = ckpt.get('lookback', LOOKBACK)

    records = load_stock_history(symbol)
    if len(records) < lookback + 30:
        return {'error': 'Insufficient history for prediction'}

    # Use last (lookback+50) records to compute features for the final window
    X, _ = compute_features(records[-(lookback + 50):], lookback)
    if len(X) == 0:
        return {'error': 'Feature computation failed'}

    model = build_transformer_model(config)
    model.load_state_dict(ckpt['model_state'])
    model.eval()

    import torch
    last_x = torch.tensor(X[-1:])
    with torch.no_grad():
        logits = model(last_x)
        probs = torch.softmax(logits, dim=1).numpy()[0]

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

    labels = ['STRONG DOWN', 'DOWN', 'NEUTRAL', 'UP', 'STRONG UP']
    signals = {0: 'STRONG SELL', 1: 'SELL', 2: 'HOLD', 3: 'BUY', 4: 'STRONG BUY'}
    bullish = float(probs[3] + probs[4])
    bearish = float(probs[0] + probs[1])

    return {
        'symbol': symbol,
        'prediction': labels[pred],
        'signal': signals[pred],
        'confidence': float(probs[pred]),
        'bullish_prob': bullish,
        'bearish_prob': bearish,
        'probabilities': {labels[i]: round(float(probs[i]), 4) for i in range(5)},
        'model_val_acc': ckpt.get('val_acc', 0),
        'model_dir_acc': ckpt.get('dir_acc', 0),
        'model_prec': ckpt.get('precision', 0),
        'trained_at': ckpt.get('trained_at', 'unknown'),
        'current_price': records[-1].get('lp', 0) if records else 0,
    }


# ---- Helpers -----------------------------------------------------------------

def _print_summary(results):
    if not results:
        return
    accs = [r['test_acc'] for r in results]
    dir_accs = [r['dir_acc'] for r in results]
    best_idx = int(np.argmax(accs))
    print('\n' + '=' * 65)
    print('TRANSFORMER TRAINING COMPLETE: %d stocks' % len(results))
    print('  Avg test acc:     %.4f' % np.mean(accs))
    print('  Avg directional:  %.4f' % np.mean(dir_accs))
    print('  Best: %s (test=%.4f dir=%.4f)' % (
        results[best_idx]['symbol'], accs[best_idx], dir_accs[best_idx]))
    print('=' * 65)


# ---- Main / CLI --------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Temporal Transformer Predictor for NEPSE')
    parser.add_argument('--train', action='store_true', help='Train model')
    parser.add_argument('--predict', action='store_true', help='Predict next day')
    parser.add_argument('--symbol', type=str, default='', help='Stock symbol (or empty = all)')
    parser.add_argument('--epochs', type=int, default=60, help='Training epochs')
    parser.add_argument('--min-days', type=int, default=120, help='Min records to train')
    parser.add_argument('--skip-trained', action='store_true',
                        help='Skip stocks with existing transformer models')
    args = parser.parse_args()

    if args.train:
        if args.symbol:
            print('\n' + '=' * 65)
            print('  Training Temporal Transformer: %s' % args.symbol)
            print('=' * 65)
            result = train_symbol_transformer(args.symbol, epochs=args.epochs)
            if result:
                print('\nFinal result:', result)
        else:
            # Train all stocks with sufficient data
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
                symbols = sorted(s for s, c in stock_counts.items()
                                 if c >= args.min_days)
            print('[OK] Found %d stocks with %d+ days of data' % (
                len(symbols), args.min_days))

            if args.skip_trained:
                already = set()
                for p in MODEL_DIR.glob('*_transformer.pt'):
                    already.add(p.stem.replace('_transformer', ''))
                before = len(symbols)
                symbols = [s for s in symbols if s.replace('/', '_') not in already]
                print('[OK] Skipping %d already-trained, %d remaining' % (
                    before - len(symbols), len(symbols)))

            results = []
            for i, sym in enumerate(symbols, 1):
                print('\n[%d/%d] %s' % (i, len(symbols), sym))
                res = train_symbol_transformer(sym, epochs=args.epochs)
                if res:
                    results.append(res)
            _print_summary(results)

    elif args.predict:
        if not args.symbol:
            print('[!] Specify --symbol for prediction')
            return
        result = predict_transformer(args.symbol)
        print('\n' + '=' * 55)
        print('  TRANSFORMER PREDICTION: %s' % args.symbol)
        print('=' * 55)
        for k, v in result.items():
            if k != 'probabilities':
                print('  %-20s: %s' % (k, v))
        if 'probabilities' in result:
            print('\n  Class Probabilities:')
            for cls, p in result['probabilities'].items():
                bar = '#' * int(p * 40)
                print('    %-12s: %.4f  %s' % (cls, p, bar))

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
