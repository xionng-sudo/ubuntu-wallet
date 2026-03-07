"""
ETH Crypto Prediction System - 机器学习预测模块
包含 LSTM、XGBoost、LightGBM 集成学习 + 自动学习机制
"""
import json
import os
import warnings
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import MinMaxScaler

import config

warnings.filterwarnings("ignore")

ML_CFG = config.ML_CONFIG


class MLPredictor:
    """集成机器学习预测引擎"""

    def __init__(self):
        self.models = {}
        self.scalers = {}
        self.feature_columns = []
        self.is_trained = False
        self.training_history = []
        self.model_dir = config.MODEL_DIR

    # ================================================================
    # 特征工程
    # ================================================================

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """构建特征矩阵"""
        if df.empty:
            return df

        df = df.copy()

        # ─── 价格特征 ───
        df["returns"] = df["close"].pct_change()
        df["log_returns"] = np.log(df["close"] / df["close"].shift(1))
        df["price_range"] = (df["high"] - df["low"]) / df["close"]
        df["body_size"] = abs(df["close"] - df["open"]) / df["close"]
        df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
        df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]

        # ─── 滞后特征 ───
        for lag in [1, 2, 3, 5, 10, 20]:
            df[f"return_lag_{lag}"] = df["returns"].shift(lag)
            df[f"volume_lag_{lag}"] = df["volume"].shift(lag)

        # ─── 滚动特征 ───
        for window in [5, 10, 20, 50]:
            df[f"rolling_mean_{window}"] = df["close"].rolling(window).mean()
            df[f"rolling_std_{window}"] = df["close"].rolling(window).std()
            df[f"rolling_vol_mean_{window}"] = df["volume"].rolling(window).mean()
            df[f"price_to_ma_{window}"] = df["close"] / df[f"rolling_mean_{window}"]

        # ─── 波动率特征 ───
        df["volatility_5"] = df["returns"].rolling(5).std()
        df["volatility_20"] = df["returns"].rolling(20).std()
        df["volatility_ratio"] = df["volatility_5"] / df["volatility_20"].replace(0, np.nan)

        # ─── 时间特征 ───
        if hasattr(df.index, "hour"):
            df["hour"] = df.index.hour
            df["day_of_week"] = df.index.dayofweek
            df["is_weekend"] = (df.index.dayofweek >= 5).astype(int)

        # ─── 目标变量（未来收益率）───
        for horizon in ML_CFG["prediction_horizon"]:
            df[f"target_{horizon}h"] = df["close"].pct_change(periods=horizon).shift(-horizon)
            df[f"target_dir_{horizon}h"] = (df[f"target_{horizon}h"] > 0).astype(int)

        # 先补齐 trader_*（即使没有 trader_data，也要保证列存在，避免预测阶段 KeyError）
        for col in ["trader_buy_ratio", "trader_sell_ratio", "trader_net_flow"]:
            if col not in df.columns:
                df[col] = 0.0

        # 删除 NaN
        df.dropna(inplace=True)

        return df

    def apply_trader_features(self, df: pd.DataFrame, trader_data) -> pd.DataFrame:
        """
        预测阶段也融合交易员特征。
        trader_data 允许：
        - dict（类似 Go Collector 原始 trades_dict）
        - DataFrame（你现在的 trades_df：包含 side/amount 等）
        - None
        """
        if df is None or df.empty:
            return df

        df = df.copy()

        if trader_data is None:
            # 保证列存在
            for col in ["trader_buy_ratio", "trader_sell_ratio", "trader_net_flow"]:
                if col not in df.columns:
                    df[col] = 0.0
            return df

        # case 1: trades_df（DataFrame）
        if isinstance(trader_data, pd.DataFrame):
            trades_df = trader_data
            if trades_df.empty or "side" not in trades_df.columns:
                for col in ["trader_buy_ratio", "trader_sell_ratio", "trader_net_flow"]:
                    if col not in df.columns:
                        df[col] = 0.0
                return df

            buy_count = int((trades_df["side"] == "BUY").sum())
            sell_count = int((trades_df["side"] == "SELL").sum())

            total_buy_amount = 0.0
            total_sell_amount = 0.0

            if "amount" in trades_df.columns:
                total_buy_amount = float(trades_df.loc[trades_df["side"] == "BUY", "amount"].fillna(0).sum())
                total_sell_amount = float(trades_df.loc[trades_df["side"] == "SELL", "amount"].fillna(0).sum())
            else:
                # 没有 amount 就按数量近似
                if "quantity" in trades_df.columns:
                    total_buy_amount = float(trades_df.loc[trades_df["side"] == "BUY", "quantity"].fillna(0).sum())
                    total_sell_amount = float(trades_df.loc[trades_df["side"] == "SELL", "quantity"].fillna(0).sum())

            total = buy_count + sell_count
            if total > 0:
                df["trader_buy_ratio"] = buy_count / total
                df["trader_sell_ratio"] = sell_count / total
                denom = max(total_buy_amount + total_sell_amount, 1.0)
                df["trader_net_flow"] = (total_buy_amount - total_sell_amount) / denom
            else:
                df["trader_buy_ratio"] = 0.5
                df["trader_sell_ratio"] = 0.5
                df["trader_net_flow"] = 0.0

            return df

        # case 2: dict（你 auto_learn 用的那种 trader_data）
        if isinstance(trader_data, dict):
            return self._merge_trader_features(df, trader_data)

        # 其它类型：兜底
        for col in ["trader_buy_ratio", "trader_sell_ratio", "trader_net_flow"]:
            if col not in df.columns:
                df[col] = 0.0
        return df

    def get_feature_columns(self, df: pd.DataFrame) -> list:
        """获取特征列名"""
        exclude = ["open", "high", "low", "close", "volume", "signal", "exchange", "symbol", "interval"]
        exclude += [col for col in df.columns if col.startswith("target_")]
        exclude += [col for col in df.columns if col.startswith("signal")]
        exclude += [col for col in df.columns if col.startswith("ichimoku_chikou")]

        features = [
            col
            for col in df.columns
            if col not in exclude and df[col].dtype in [np.float64, np.int64, np.float32, np.int32]
        ]

        self.feature_columns = features
        return features

    # ================================================================
    # XGBoost 模型
    # ================================================================

    def train_xgboost(self, df: pd.DataFrame, target_col: str = "target_dir_1h"):
        """训练 XGBoost 模型"""
        try:
            import xgboost as xgb
        except ImportError:
            print("[ML] XGBoost 未安装，跳过")
            return None

        features = self.get_feature_columns(df)
        if target_col not in df.columns:
            print(f"[ML] 目标列 {target_col} 不存在")
            return None

        X = df[features].values
        y = df[target_col].values

        split = int(len(X) * ML_CFG["train_test_split"])
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        scaler = MinMaxScaler()
        X_train = scaler.fit_transform(X_train)
        X_test = scaler.transform(X_test)

        model = xgb.XGBClassifier(
            n_estimators=ML_CFG["xgboost_n_estimators"],
            max_depth=ML_CFG["xgboost_max_depth"],
            learning_rate=ML_CFG["xgboost_learning_rate"],
            objective="binary:logistic",
            eval_metric="logloss",
            use_label_encoder=False,
            random_state=42,
        )

        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

        y_pred = model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        print(f"[ML] XGBoost 准确率: {accuracy:.4f}")

        self.models["xgboost"] = model
        self.scalers["xgboost"] = scaler

        self._save_model("xgboost", model, scaler, accuracy)
        return {"model": "xgboost", "accuracy": accuracy}

    # ================================================================
    # LightGBM 模型
    # ================================================================

    def train_lightgbm(self, df: pd.DataFrame, target_col: str = "target_dir_1h"):
        """训练 LightGBM 模型"""
        try:
            import lightgbm as lgb
        except ImportError:
            print("[ML] LightGBM 未安装，跳过")
            return None

        features = self.get_feature_columns(df)
        if target_col not in df.columns:
            return None

        X = df[features].values
        y = df[target_col].values

        split = int(len(X) * ML_CFG["train_test_split"])
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        scaler = MinMaxScaler()
        X_train = scaler.fit_transform(X_train)
        X_test = scaler.transform(X_test)

        model = lgb.LGBMClassifier(
            n_estimators=ML_CFG["lightgbm_n_estimators"],
            max_depth=ML_CFG["lightgbm_max_depth"],
            learning_rate=ML_CFG["lightgbm_learning_rate"],
            objective="binary",
            random_state=42,
            verbose=-1,
        )

        model.fit(X_train, y_train, eval_set=[(X_test, y_test)])

        y_pred = model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        print(f"[ML] LightGBM 准确率: {accuracy:.4f}")

        self.models["lightgbm"] = model
        self.scalers["lightgbm"] = scaler

        self._save_model("lightgbm", model, scaler, accuracy)
        return {"model": "lightgbm", "accuracy": accuracy}

    # ================================================================
    # LSTM 模型
    # ================================================================

    def train_lstm(self, df: pd.DataFrame, target_col: str = "target_dir_1h"):
        """训练 LSTM 深度学习模型"""
        try:
            import torch
            import torch.nn as nn
            from torch.utils.data import DataLoader, TensorDataset
        except ImportError:
            print("[ML] PyTorch 未安装，跳过 LSTM")
            return None

        features = self.get_feature_columns(df)
        if target_col not in df.columns:
            return None

        X = df[features].values
        y = df[target_col].values

        scaler = MinMaxScaler()
        X_scaled = scaler.fit_transform(X)

        lookback = ML_CFG["lookback_period"]

        X_seq, y_seq = [], []
        for i in range(lookback, len(X_scaled)):
            X_seq.append(X_scaled[i - lookback : i])
            y_seq.append(y[i])

        X_seq = np.array(X_seq)
        y_seq = np.array(y_seq)

        split = int(len(X_seq) * ML_CFG["train_test_split"])
        X_train = torch.FloatTensor(X_seq[:split])
        X_test = torch.FloatTensor(X_seq[split:])
        y_train = torch.LongTensor(y_seq[:split])
        y_test = torch.LongTensor(y_seq[split:])

        class LSTMModel(nn.Module):
            def __init__(self, input_size, hidden_size, num_layers, num_classes, dropout):
                super().__init__()
                self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=dropout)
                self.fc1 = nn.Linear(hidden_size, 64)
                self.relu = nn.ReLU()
                self.dropout = nn.Dropout(dropout)
                self.fc2 = nn.Linear(64, num_classes)

            def forward(self, x):
                out, _ = self.lstm(x)
                out = out[:, -1, :]
                out = self.dropout(self.relu(self.fc1(out)))
                out = self.fc2(out)
                return out

        input_size = X_train.shape[2]
        model = LSTMModel(
            input_size=input_size,
            hidden_size=ML_CFG["lstm_units"],
            num_layers=ML_CFG["lstm_layers"],
            num_classes=2,
            dropout=ML_CFG["dropout"],
        )

        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=ML_CFG["learning_rate"])

        train_dataset = TensorDataset(X_train, y_train)
        train_loader = DataLoader(train_dataset, batch_size=ML_CFG["batch_size"], shuffle=False)

        best_accuracy = 0
        patience_counter = 0
        epochs = ML_CFG["epochs"]

        model.train()
        for epoch in range(epochs):
            total_loss = 0
            for batch_X, batch_y in train_loader:
                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()

            if (epoch + 1) % 10 == 0:
                model.eval()
                with torch.no_grad():
                    test_outputs = model(X_test)
                    _, predicted = torch.max(test_outputs, 1)
                    accuracy = (predicted == y_test).sum().item() / len(y_test)

                    if accuracy > best_accuracy:
                        best_accuracy = accuracy
                        patience_counter = 0
                    else:
                        patience_counter += 1

                    print(
                        f"[ML] LSTM Epoch {epoch+1}/{epochs}, "
                        f"Loss: {total_loss/len(train_loader):.4f}, Acc: {accuracy:.4f}"
                    )

                model.train()

                if patience_counter >= ML_CFG["early_stopping_patience"]:
                    print(f"[ML] LSTM 早停于 Epoch {epoch+1}")
                    break

        model.eval()
        with torch.no_grad():
            test_outputs = model(X_test)
            _, predicted = torch.max(test_outputs, 1)
            final_accuracy = (predicted == y_test).sum().item() / len(y_test)

        print(f"[ML] LSTM 最终准确率: {final_accuracy:.4f}")

        self.models["lstm"] = model
        self.scalers["lstm"] = scaler

        model_path = os.path.join(self.model_dir, "lstm_model.pt")
        torch.save(model.state_dict(), model_path)

        self._save_model_meta("lstm", final_accuracy)
        return {"model": "lstm", "accuracy": final_accuracy}

    # ================================================================
    # 集成预测
    # ================================================================

    def predict(self, df: pd.DataFrame) -> dict:
        """使用多模型集成进行预测"""
        if not self.models:
            print("[ML] 没有已训练的模型")
            return {"direction": "HOLD", "confidence": 0, "details": {}}

        features = self.get_feature_columns(df) if not self.feature_columns else self.feature_columns
        if not features:
            return {"direction": "HOLD", "confidence": 0, "details": {}}

        print("======== pandas features 调试输出 ========")
        print("需要的特征名: ", features)
        print("DataFrame里的列名: ", list(df.columns))
        missing = set(features) - set(df.columns)
        if missing:
            print("【!!! 缺失的特征:】", missing)
        else:
            print("所有需要的特征都在 DataFrame 里，没有缺失。")
        extra = set(df.columns) - set(features)
        print("DataFrame里多出来的列（可忽略）:", extra)
        print("=======================================")
        # === 特征列表调试输出结束 ===

        # 兜底：缺失的特征直接补 0，避免 KeyError
        if missing:
            df = df.copy()
            for c in missing:
                df[c] = 0.0

        latest = df[features].iloc[-1:].values
        predictions = {}

        # XGBoost 预测
        if "xgboost" in self.models and "xgboost" in self.scalers:
            try:
                X = self.scalers["xgboost"].transform(latest)
                pred = self.models["xgboost"].predict(X)[0]
                prob = self.models["xgboost"].predict_proba(X)[0]
                predictions["xgboost"] = {
                    "direction": "UP" if pred == 1 else "DOWN",
                    "confidence": float(max(prob)),
                }
            except Exception as e:
                print(f"[ML] XGBoost 预测失败: {e}")

        # LightGBM 预测
        if "lightgbm" in self.models and "lightgbm" in self.scalers:
            try:
                X = self.scalers["lightgbm"].transform(latest)
                pred = self.models["lightgbm"].predict(X)[0]
                prob = self.models["lightgbm"].predict_proba(X)[0]
                predictions["lightgbm"] = {
                    "direction": "UP" if pred == 1 else "DOWN",
                    "confidence": float(max(prob)),
                }
            except Exception as e:
                print(f"[ML] LightGBM 预测失败: {e}")

        # LSTM 预测
        if "lstm" in self.models and "lstm" in self.scalers:
            try:
                import torch

                lookback = ML_CFG["lookback_period"]
                X_all = df[features].iloc[-lookback:].values
                X_scaled = self.scalers["lstm"].transform(X_all)
                X_seq = torch.FloatTensor(X_scaled).unsqueeze(0)

                self.models["lstm"].eval()
                with torch.no_grad():
                    output = self.models["lstm"](X_seq)
                    prob = torch.softmax(output, dim=1).numpy()[0]
                    pred = int(np.argmax(prob))

                predictions["lstm"] = {
                    "direction": "UP" if pred == 1 else "DOWN",
                    "confidence": float(max(prob)),
                }
            except Exception as e:
                print(f"[ML] LSTM 预测失败: {e}")

        if not predictions:
            return {"direction": "HOLD", "confidence": 0, "details": {}}

        up_votes = sum(1 for p in predictions.values() if p["direction"] == "UP")
        down_votes = sum(1 for p in predictions.values() if p["direction"] == "DOWN")
        total = len(predictions)

        avg_confidence = float(np.mean([p["confidence"] for p in predictions.values()]))

        if up_votes > down_votes:
            direction = "UP"
            vote_ratio = up_votes / total
        elif down_votes > up_votes:
            direction = "DOWN"
            vote_ratio = down_votes / total
        else:
            direction = "HOLD"
            vote_ratio = 0.5

        final_confidence = avg_confidence * vote_ratio

        return {
            "direction": direction,
            "confidence": float(final_confidence),
            "vote_ratio": float(vote_ratio),
            "details": predictions,
            "timestamp": datetime.now().isoformat(),
        }

    # ================================================================
    # 自动学习（增量训练）
    # ================================================================

    def auto_learn(self, df: pd.DataFrame, trader_data: dict = None):
        """
        自动学习机制：
        1. 从新的市场数据中学习
        2. 融合交易员数据作为特征
        3. 定期重新训练模型
        """
        print("[ML] ===== 开始自动学习 =====")

        df_prepared = self.prepare_features(df)
        if df_prepared.empty:
            print("[ML] 数据准备后为空，跳过学习")
            return

        if trader_data:
            df_prepared = self._merge_trader_features(df_prepared, trader_data)

        results = {}

        print("[ML] 训练 XGBoost...")
        xgb_result = self.train_xgboost(df_prepared)
        if xgb_result:
            results["xgboost"] = xgb_result

        print("[ML] 训练 LightGBM...")
        lgb_result = self.train_lightgbm(df_prepared)
        if lgb_result:
            results["lightgbm"] = lgb_result

        print("[ML] 训练 LSTM...")
        lstm_result = self.train_lstm(df_prepared)
        if lstm_result:
            results["lstm"] = lstm_result

        self.is_trained = bool(results)

        self.training_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "data_size": len(df_prepared),
                "results": results,
            }
        )

        self._save_training_history()

        print(f"[ML] ===== 自动学习完成: {len(results)} 个模型已更新 =====")
        return results

    def _merge_trader_features(self, df: pd.DataFrame, trader_data: dict) -> pd.DataFrame:
        """将交易员信息合并为额外特征（训练/预测通用）"""
        if not trader_data:
            return df

        buy_count = 0
        sell_count = 0
        total_buy_amount = 0.0
        total_sell_amount = 0.0

        for trader_id, trades in trader_data.items():
            for trade in trades:
                if isinstance(trade, dict):
                    if trade.get("side") == "BUY":
                        buy_count += 1
                        total_buy_amount += float(trade.get("amount", 0) or 0)
                    else:
                        sell_count += 1
                        total_sell_amount += float(trade.get("amount", 0) or 0)

        total = buy_count + sell_count
        if total > 0:
            df["trader_buy_ratio"] = buy_count / total
            df["trader_sell_ratio"] = sell_count / total
            denom = max(total_buy_amount + total_sell_amount, 1.0)
            df["trader_net_flow"] = (total_buy_amount - total_sell_amount) / denom
        else:
            df["trader_buy_ratio"] = 0.5
            df["trader_sell_ratio"] = 0.5
            df["trader_net_flow"] = 0.0

        return df

    # ================================================================
    # 模型持久化
    # ================================================================

    def _save_model(self, name: str, model, scaler, accuracy: float):
        """保存模型到文件"""
        model_path = os.path.join(self.model_dir, f"{name}_model.pkl")
        scaler_path = os.path.join(self.model_dir, f"{name}_scaler.pkl")

        joblib.dump(model, model_path)
        joblib.dump(scaler, scaler_path)

        self._save_model_meta(name, accuracy)
        print(f"[ML] 模型 {name} 已保存")

    def _save_model_meta(self, name: str, accuracy: float):
        """保存模型元信息"""
        meta_path = os.path.join(self.model_dir, "model_meta.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                meta = json.load(f)

        meta[name] = {
            "accuracy": accuracy,
            "trained_at": datetime.now().isoformat(),
            "features": self.feature_columns[:10],
        }

        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    def load_models(self):
        """从文件加载模型"""
        for name in ["xgboost", "lightgbm"]:
            model_path = os.path.join(self.model_dir, f"{name}_model.pkl")
            scaler_path = os.path.join(self.model_dir, f"{name}_scaler.pkl")

            if os.path.exists(model_path) and os.path.exists(scaler_path):
                self.models[name] = joblib.load(model_path)
                self.scalers[name] = joblib.load(scaler_path)
                print(f"[ML] 模型 {name} 已加载")

        meta_path = os.path.join(self.model_dir, "model_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f:
                meta = json.load(f)
            if meta:
                self.is_trained = True

    def _save_training_history(self):
        """保存训练历史"""
        path = os.path.join(self.model_dir, "training_history.json")
        with open(path, "w") as f:
            json.dump(self.training_history[-100:], f, indent=2)
