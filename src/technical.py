"""
Technical Analysis Engine for NEPSE stocks.

Computes indicators: SMA, EMA, RSI, MACD, Bollinger Bands, ATR,
VWAP, support/resistance levels, pivot points, and volume analysis.
"""

import numpy as np
import pandas as pd

from src.config import TA_CONFIG


class TechnicalAnalysis:
    """Full technical analysis suite on OHLCV data."""

    def __init__(self, price_data: list[dict]):
        self.df = pd.DataFrame(price_data)
        if self.df.empty:
            raise ValueError("No price data provided for technical analysis")

        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            self.df[col] = pd.to_numeric(self.df[col], errors="coerce")

        self.df.dropna(subset=["close"], inplace=True)

        # Sort chronologically (oldest first) - critical for TA calculations
        if "date" in self.df.columns:
            self.df["date"] = pd.to_datetime(self.df["date"], errors="coerce")
            self.df.sort_values("date", inplace=True)

        self.df.reset_index(drop=True, inplace=True)
        self.cfg = TA_CONFIG

    # ── Moving Averages ────────────────────────────────────────

    def compute_sma(self) -> pd.DataFrame:
        for period in self.cfg["sma_periods"]:
            self.df[f"SMA_{period}"] = self.df["close"].rolling(window=period).mean()
        return self.df

    def compute_ema(self) -> pd.DataFrame:
        for period in self.cfg["ema_periods"]:
            self.df[f"EMA_{period}"] = self.df["close"].ewm(span=period, adjust=False).mean()
        return self.df

    # ── RSI ────────────────────────────────────────────────────

    def compute_rsi(self) -> pd.DataFrame:
        period = self.cfg["rsi_period"]
        delta = self.df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        # Use Wilder's smoothing after initial window
        for i in range(period, len(self.df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

        rs = avg_gain / avg_loss.replace(0, np.nan)
        self.df["RSI"] = 100 - (100 / (1 + rs))
        return self.df

    # ── MACD ───────────────────────────────────────────────────

    def compute_macd(self) -> pd.DataFrame:
        fast = self.cfg["macd_fast"]
        slow = self.cfg["macd_slow"]
        signal_period = self.cfg["macd_signal"]

        ema_fast = self.df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = self.df["close"].ewm(span=slow, adjust=False).mean()

        self.df["MACD"] = ema_fast - ema_slow
        self.df["MACD_Signal"] = self.df["MACD"].ewm(span=signal_period, adjust=False).mean()
        self.df["MACD_Hist"] = self.df["MACD"] - self.df["MACD_Signal"]
        return self.df

    # ── Bollinger Bands ────────────────────────────────────────

    def compute_bollinger(self) -> pd.DataFrame:
        period = self.cfg["bb_period"]
        std_mult = self.cfg["bb_std"]

        self.df["BB_Mid"] = self.df["close"].rolling(window=period).mean()
        rolling_std = self.df["close"].rolling(window=period).std()
        self.df["BB_Upper"] = self.df["BB_Mid"] + (rolling_std * std_mult)
        self.df["BB_Lower"] = self.df["BB_Mid"] - (rolling_std * std_mult)
        self.df["BB_Width"] = (self.df["BB_Upper"] - self.df["BB_Lower"]) / self.df["BB_Mid"]
        bb_range = self.df["BB_Upper"] - self.df["BB_Lower"]
        self.df["BB_Position"] = np.where(
            bb_range != 0,
            (self.df["close"] - self.df["BB_Lower"]) / bb_range,
            0.5,
        )
        return self.df

    # ── ATR (Average True Range) ───────────────────────────────

    def compute_atr(self) -> pd.DataFrame:
        period = self.cfg["atr_period"]
        high = self.df["high"]
        low = self.df["low"]
        close = self.df["close"].shift(1)

        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        self.df["ATR"] = true_range.rolling(window=period).mean()
        self.df["ATR_Pct"] = (self.df["ATR"] / self.df["close"]) * 100
        return self.df

    # ── Volume Analysis ────────────────────────────────────────

    def compute_volume_analysis(self) -> pd.DataFrame:
        period = self.cfg["volume_ma_period"]
        self.df["Vol_MA"] = self.df["volume"].rolling(window=period).mean()
        self.df["Vol_Ratio"] = self.df["volume"] / self.df["Vol_MA"]

        # On-Balance Volume (OBV)
        obv = [0]
        for i in range(1, len(self.df)):
            if self.df["close"].iloc[i] > self.df["close"].iloc[i - 1]:
                obv.append(obv[-1] + self.df["volume"].iloc[i])
            elif self.df["close"].iloc[i] < self.df["close"].iloc[i - 1]:
                obv.append(obv[-1] - self.df["volume"].iloc[i])
            else:
                obv.append(obv[-1])
        self.df["OBV"] = obv

        # Volume-Price Trend
        prev_close = self.df["close"].shift(1).replace(0, np.nan)
        self.df["VPT"] = (
            self.df["volume"] * (self.df["close"].diff() / prev_close)
        ).fillna(0).cumsum()

        return self.df

    # ── Support & Resistance ───────────────────────────────────

    def find_support_resistance(self) -> dict:
        """Find key support and resistance levels using pivot points."""
        lookback = self.cfg["pivot_lookback"]
        highs = self.df["high"].values
        lows = self.df["low"].values
        closes = self.df["close"].values

        resistance_levels = []
        support_levels = []

        for i in range(lookback, len(self.df) - lookback):
            # Local high = resistance
            if highs[i] == max(highs[i - lookback : i + lookback + 1]):
                resistance_levels.append(highs[i])
            # Local low = support
            if lows[i] == min(lows[i - lookback : i + lookback + 1]):
                support_levels.append(lows[i])

        # Cluster nearby levels (within 2% of each other)
        resistance_levels = self._cluster_levels(resistance_levels)
        support_levels = self._cluster_levels(support_levels)

        current_price = closes[-1] if len(closes) > 0 else 0

        # Sort: nearest supports below price, nearest resistances above
        supports = sorted([s for s in support_levels if s < current_price], reverse=True)
        resistances = sorted([r for r in resistance_levels if r > current_price])

        # Classic pivot points from last session
        last_high = highs[-1]
        last_low = lows[-1]
        last_close = closes[-1]
        pivot = (last_high + last_low + last_close) / 3
        r1 = 2 * pivot - last_low
        s1 = 2 * pivot - last_high
        r2 = pivot + (last_high - last_low)
        s2 = pivot - (last_high - last_low)

        return {
            "current_price": current_price,
            "support_levels": supports[:5],
            "resistance_levels": resistances[:5],
            "pivot_point": round(pivot, 2),
            "pivot_r1": round(r1, 2),
            "pivot_r2": round(r2, 2),
            "pivot_s1": round(s1, 2),
            "pivot_s2": round(s2, 2),
        }

    @staticmethod
    def _cluster_levels(levels: list, threshold: float = 0.02) -> list:
        """Cluster nearby price levels together, keeping the average."""
        if not levels:
            return []
        levels = sorted(levels)
        clusters = [[levels[0]]]
        for level in levels[1:]:
            if (level - clusters[-1][-1]) / clusters[-1][-1] < threshold:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        return [round(sum(c) / len(c), 2) for c in clusters]

    # ── Trend Detection ────────────────────────────────────────

    def detect_trend(self) -> dict:
        """Detect current trend using multiple methods."""
        close = self.df["close"]
        n = len(close)

        # 1. Price vs Moving Averages
        current = close.iloc[-1]
        sma_20 = self.df.get("SMA_20", pd.Series([np.nan])).iloc[-1]
        sma_50 = self.df.get("SMA_50", pd.Series([np.nan])).iloc[-1]
        sma_200 = self.df.get("SMA_200", pd.Series([np.nan])).iloc[-1]

        ma_signals = []
        if not np.isnan(sma_20):
            ma_signals.append("BULLISH" if current > sma_20 else "BEARISH")
        if not np.isnan(sma_50):
            ma_signals.append("BULLISH" if current > sma_50 else "BEARISH")
        if not np.isnan(sma_200):
            ma_signals.append("BULLISH" if current > sma_200 else "BEARISH")

        # 2. Golden/Death Cross
        cross = "NONE"
        if not np.isnan(sma_50) and not np.isnan(sma_200):
            if sma_50 > sma_200:
                cross = "GOLDEN_CROSS (Bullish)"
            else:
                cross = "DEATH_CROSS (Bearish)"

        # 3. Price momentum (rate of change)
        roc_5 = ((current - close.iloc[-6]) / close.iloc[-6] * 100) if n > 5 else 0
        roc_20 = ((current - close.iloc[-21]) / close.iloc[-21] * 100) if n > 20 else 0

        # 4. Higher highs / Lower lows (last 20 periods)
        recent = self.df.tail(20)
        higher_highs = sum(
            recent["high"].iloc[i] > recent["high"].iloc[i - 1]
            for i in range(1, len(recent))
        )
        higher_lows = sum(
            recent["low"].iloc[i] > recent["low"].iloc[i - 1]
            for i in range(1, len(recent))
        )
        hh_hl_ratio = (higher_highs + higher_lows) / (2 * (len(recent) - 1)) if len(recent) > 1 else 0.5

        # Overall trend score: -1 (strong bearish) to +1 (strong bullish)
        bull_count = ma_signals.count("BULLISH")
        bear_count = ma_signals.count("BEARISH")
        trend_score = (bull_count - bear_count) / max(len(ma_signals), 1)
        trend_score += 0.2 if roc_20 > 5 else (-0.2 if roc_20 < -5 else 0)
        trend_score += 0.2 if hh_hl_ratio > 0.6 else (-0.2 if hh_hl_ratio < 0.4 else 0)
        trend_score = max(-1, min(1, trend_score))

        if trend_score > 0.3:
            trend = "BULLISH"
        elif trend_score < -0.3:
            trend = "BEARISH"
        else:
            trend = "SIDEWAYS"

        return {
            "overall_trend": trend,
            "trend_score": round(trend_score, 2),
            "ma_signals": ma_signals,
            "cross_signal": cross,
            "roc_5d": round(roc_5, 2),
            "roc_20d": round(roc_20, 2),
            "hh_hl_ratio": round(hh_hl_ratio, 2),
        }

    # ── Run All ────────────────────────────────────────────────

    def run_all(self) -> dict:
        """Compute all technical indicators and return summary."""
        self.compute_sma()
        self.compute_ema()
        self.compute_rsi()
        self.compute_macd()
        self.compute_bollinger()
        self.compute_atr()
        self.compute_volume_analysis()

        sr = self.find_support_resistance()
        trend = self.detect_trend()

        # Get latest values
        last = self.df.iloc[-1]
        return {
            "price": {
                "close": last["close"],
                "open": last["open"],
                "high": last["high"],
                "low": last["low"],
                "volume": last["volume"],
            },
            "moving_averages": {
                f"SMA_{p}": round(last.get(f"SMA_{p}", np.nan), 2)
                for p in self.cfg["sma_periods"]
            }
            | {
                f"EMA_{p}": round(last.get(f"EMA_{p}", np.nan), 2)
                for p in self.cfg["ema_periods"]
            },
            "rsi": round(last.get("RSI", np.nan), 2),
            "macd": {
                "macd": round(last.get("MACD", np.nan), 2),
                "signal": round(last.get("MACD_Signal", np.nan), 2),
                "histogram": round(last.get("MACD_Hist", np.nan), 2),
            },
            "bollinger": {
                "upper": round(last.get("BB_Upper", np.nan), 2),
                "mid": round(last.get("BB_Mid", np.nan), 2),
                "lower": round(last.get("BB_Lower", np.nan), 2),
                "width": round(last.get("BB_Width", np.nan), 4),
                "position": round(last.get("BB_Position", np.nan), 4),
            },
            "atr": {
                "atr": round(last.get("ATR", np.nan), 2),
                "atr_pct": round(last.get("ATR_Pct", np.nan), 2),
            },
            "volume": {
                "current": int(last.get("volume", 0)),
                "avg_20d": round(last.get("Vol_MA", np.nan), 0),
                "ratio": round(last.get("Vol_Ratio", np.nan), 2),
                "obv": round(last.get("OBV", 0), 0),
            },
            "support_resistance": sr,
            "trend": trend,
            "dataframe": self.df,
        }
