"""
ETH Crypto Prediction System - 可视化仪表板
使用 Dash + Plotly 构建实时交互图表，包含具体时间

✅ 彻底优化版（2026-03-09）：
- 仅可视化层统一展示北京时间（Asia/Shanghai）
- fmt_bj_time：无时区 => 视为北京时间；带时区 => 转北京时间（解决 alerts 跨天 +8）
- now_bj_str：直接取北京时间
- to_bj_index：可配置 naive index 的假设时区（默认 UTC）
- 将翻译映射/样式常量上提，减少每次 interval 重建开销
- Alerts 表字段值中英互转（priority/type/signal/status/strategy/direction）支持反向映射
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

try:
    import dash
    from dash import dcc, html, dash_table, callback_context
    from dash.dependencies import Input, Output, State
    import dash_bootstrap_components as dbc

    HAS_DASH = True
except ImportError:
    HAS_DASH = False

import config

# ==============================
# ✅ 时区/格式
# ==============================
_BJ_TZ = "Asia/Shanghai"
_TS_FMT = "%Y-%m-%d %H:%M:%S"

# ==============================
# ✅ Alerts 字段值翻译（英文->中文）
# ==============================
PRIORITY_ZH = {"CRITICAL": "紧急", "HIGH": "高", "MEDIUM": "中", "LOW": "低"}
SIGNAL_ZH = {
    "BUY": "买入",
    "SELL": "卖出",
    "HOLD": "观望",
    "ALERT": "提示",
    "STRONG_BUY": "强买入",
    "STRONG_SELL": "强卖出",
}
TYPE_ZH = {
    "TECHNICAL_SIGNAL": "技术信号",
    "RSI_OVERBOUGHT": "RSI超买",
    "RSI_OVERSOLD": "RSI超卖",
    "ML_PREDICTION": "机器学习预测",
    "VOLUME_SPIKE": "成交量异常",
    "PRICE_SPIKE": "价格异动",
}
STATUS_ZH = {"OPEN": "未平仓", "CLOSED": "已平仓"}
STRATEGY_ZH = {"LONG": "做多", "SHORT": "做空"}
DIRECTION_ZH = {"UP": "看涨", "DOWN": "看跌", "HOLD": "观望"}

# 反向映射（中文->英文）
PRIORITY_EN = {v: k for k, v in PRIORITY_ZH.items()}
SIGNAL_EN = {v: k for k, v in SIGNAL_ZH.items()}
TYPE_EN = {v: k for k, v in TYPE_ZH.items()}
STATUS_EN = {v: k for k, v in STATUS_ZH.items()}
STRATEGY_EN = {v: k for k, v in STRATEGY_ZH.items()}
DIRECTION_EN = {v: k for k, v in DIRECTION_ZH.items()}

# Alerts 表列名翻译
ALERT_COL_ZH = {
    "timestamp": "时间",
    "priority": "级别",
    "type": "类型",
    "signal": "信号",
    "price": "价格",
    "message": "内容",
    "score": "强度",
    "rsi": "RSI",
    "confidence": "置信度",
    "details": "细节",
    "volume_ratio": "成交量倍数",
    "change": "24h涨跌幅(%)",
    "priority_raw": "priority_raw",
}

# ==============================
# ✅ DataTable 样式常量（避免重复创建）
# ==============================
STYLE_TABLE_SCROLL_X = {"overflowX": "auto"}
STYLE_TABLE_ALERTS = {"overflowX": "auto", "maxHeight": "420px", "overflowY": "auto"}
STYLE_TABLE_TRADES = {"overflowX": "auto", "maxHeight": "500px", "overflowY": "auto"}

STYLE_CELL_DARK_CENTER = {"textAlign": "center", "backgroundColor": "#303030", "color": "white"}
STYLE_HEADER_DARK = {"backgroundColor": "#404040", "fontWeight": "bold"}

STYLE_CELL_ALERTS = {
    "textAlign": "left",
    "backgroundColor": "#303030",
    "color": "white",
    "padding": "6px",
    "fontSize": "12px",
    "minWidth": "80px",
    "maxWidth": "420px",
    "whiteSpace": "normal",
    "height": "auto",
}
STYLE_CELL_TRADES = {
    "textAlign": "center",
    "backgroundColor": "#303030",
    "color": "white",
    "padding": "6px",
    "fontSize": "12px",
    "minWidth": "90px",
    "width": "90px",
    "maxWidth": "240px",
    "whiteSpace": "normal",
}

ALERTS_PRIORITY_HIGHLIGHT = [
    {"if": {"filter_query": '{priority_raw} = "CRITICAL"'}, "backgroundColor": "rgba(255, 23, 68, 0.22)"},
    {"if": {"filter_query": '{priority_raw} = "HIGH"'}, "backgroundColor": "rgba(255, 193, 7, 0.18)"},
    {"if": {"filter_query": '{priority_raw} = "MEDIUM"'}, "backgroundColor": "rgba(3, 169, 244, 0.14)"},
    {"if": {"filter_query": '{priority_raw} = "LOW"'}, "backgroundColor": "rgba(76, 175, 80, 0.12)"},
]


# ==============================
# ✅ 时间处理 helpers
# ==============================
def fmt_bj_time(x: Any, fmt: str = _TS_FMT) -> str:
    """
    展示用：统一输出北京时间字符串
    - 输入带时区（Z / +08:00 / tz-aware Timestamp）：按真实时区转北京
    - 输入不带时区（alerts.py 的 'YYYY-mm-dd HH:MM:SS'）：视为北京时间，不做 +8
    """
    if x is None:
        return ""
    try:
        ts = pd.to_datetime(x, errors="coerce")
        if pd.isna(ts):
            return ""
        if isinstance(ts, pd.Timestamp):
            if ts.tzinfo is None:
                ts = ts.tz_localize(_BJ_TZ)
            else:
                ts = ts.tz_convert(_BJ_TZ)
            return ts.strftime(fmt)
        return str(x)
    except Exception:
        return str(x)


def now_bj_str(fmt: str = _TS_FMT) -> str:
    return pd.Timestamp.now(tz=_BJ_TZ).strftime(fmt)


def to_bj_index(df: pd.DataFrame, assume_naive: str = "UTC") -> pd.DataFrame:
    """
    仅用于可视化：复制 df 并把 index 转成北京时间
    assume_naive:
      - "UTC": naive index 视为 UTC（推荐：交易所 OHLCV 毫秒时间戳通常是 UTC）
      - "BJ":  naive index 视为北京时间（如果你明确 upstream 已经是北京时间）
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    try:
        idx = pd.to_datetime(out.index, errors="coerce")
        if getattr(idx, "tz", None) is None:
            base = "UTC" if (assume_naive or "UTC").upper() == "UTC" else _BJ_TZ
            idx = idx.tz_localize(base)
        out.index = idx.tz_convert(_BJ_TZ)
    except Exception:
        pass
    return out


# ==============================
# ✅ 结构化/翻译 helpers
# ==============================
def _jsonify_if_needed(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    return v


def _translate_value(field: str, value: Any, lang: str) -> Any:
    """根据字段名翻译值（支持反向）"""
    if not isinstance(value, str):
        return value
    v = value.strip()

    if lang == "zh":
        if field == "priority":
            return PRIORITY_ZH.get(v, v)
        if field == "signal":
            return SIGNAL_ZH.get(v, v)
        if field == "type":
            return TYPE_ZH.get(v, v)
        if field == "status":
            return STATUS_ZH.get(v, v)
        if field == "strategy":
            return STRATEGY_ZH.get(v, v)
        if field in ("direction", "pred_direction"):
            return DIRECTION_ZH.get(v, v)
        return v

    # lang == "en"
    if field == "priority":
        return PRIORITY_EN.get(v, v)
    if field == "signal":
        return SIGNAL_EN.get(v, v)
    if field == "type":
        return TYPE_EN.get(v, v)
    if field == "status":
        return STATUS_EN.get(v, v)
    if field == "strategy":
        return STRATEGY_EN.get(v, v)
    if field in ("direction", "pred_direction"):
        return DIRECTION_EN.get(v, v)
    return v


def _fmt_price_usd(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return str(x or "")


def _datatable(columns: List[Dict[str, str]], data: List[Dict[str, Any]], **kwargs):
    """统一创建 DataTable，避免每次写重复参数"""
    return dash_table.DataTable(columns=columns, data=data, **kwargs)


class Visualization:
    """可视化引擎 - 图表生成与实时仪表板"""

    def __init__(self):
        self.app = None
        self.latest_data = {}

    # ================================================================
    # Plotly 静态图表
    # ================================================================

    def create_candlestick_chart(
        self,
        df: pd.DataFrame,
        title: str = "ETH/USDT K线图",
        indicators: list = None,
        timeframe: str = "1h",
    ) -> go.Figure:
        df = to_bj_index(df, assume_naive="UTC")

        fig = make_subplots(
            rows=4,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.5, 0.15, 0.15, 0.2],
            subplot_titles=[f"{title} [{timeframe}]", "MACD", "RSI", "成交量"],
        )

        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="ETH/USDT",
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            ),
            row=1,
            col=1,
        )

        if "bb_upper" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["bb_upper"], name="BB上轨", line=dict(color="rgba(173,216,230,0.5)", width=1)), row=1, col=1)
            fig.add_trace(
                go.Scatter(x=df.index, y=df["bb_lower"], name="BB下轨", line=dict(color="rgba(173,216,230,0.5)", width=1), fill="tonexty", fillcolor="rgba(173,216,230,0.1)"),
                row=1,
                col=1,
            )

        ema_colors = {"ema_12": "#FFD700", "ema_26": "#FF6347", "ema_50": "#4169E1", "ema_200": "#FF69B4"}
        for ema, color in ema_colors.items():
            if ema in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df[ema], name=ema.upper(), line=dict(color=color, width=1)), row=1, col=1)

        sma_colors = {"sma_7": "#00FF00", "sma_25": "#FFA500", "sma_99": "#800080", "sma_200": "#FF1493"}
        for sma, color in sma_colors.items():
            if sma in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df[sma], name=sma.upper(), line=dict(color=color, width=1, dash="dot")), row=1, col=1)

        if "parabolic_sar" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["parabolic_sar"], name="SAR", mode="markers", marker=dict(size=3, color="#FFD700")), row=1, col=1)

        if "macd" in df.columns and "macd_hist" in df.columns and "macd_signal" in df.columns:
            colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df["macd_hist"]]
            fig.add_trace(go.Bar(x=df.index, y=df["macd_hist"], name="MACD Hist", marker_color=colors), row=2, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["macd"], name="MACD", line=dict(color="#2196F3", width=1.5)), row=2, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df["macd_signal"], name="Signal", line=dict(color="#FF9800", width=1.5)), row=2, col=1)

        if "rsi" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["rsi"], name="RSI", line=dict(color="#9C27B0", width=1.5)), row=3, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", row=3, col=1)
            fig.add_hrect(y0=30, y1=70, fillcolor="rgba(128,128,128,0.1)", row=3, col=1)

        if "open" in df.columns and "close" in df.columns and "volume" in df.columns:
            vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(df["close"], df["open"])]
            fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="成交量", marker_color=vol_colors), row=4, col=1)
            if "volume_sma_20" in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df["volume_sma_20"], name="Vol MA20", line=dict(color="#FFA500", width=1)), row=4, col=1)

        if "signal" in df.columns:
            buy_signals = df[df["signal"].isin(["BUY", "STRONG_BUY"])]
            sell_signals = df[df["signal"].isin(["SELL", "STRONG_SELL"])]

            if not buy_signals.empty and "low" in buy_signals.columns:
                fig.add_trace(
                    go.Scatter(
                        x=buy_signals.index,
                        y=buy_signals["low"] * 0.998,
                        mode="markers",
                        name="买入信号",
                        marker=dict(symbol="triangle-up", size=12, color="#00E676"),
                        text=[f"买入 {fmt_bj_time(idx, '%Y-%m-%d %H:%M')}" for idx in buy_signals.index],
                    ),
                    row=1,
                    col=1,
                )

            if not sell_signals.empty and "high" in sell_signals.columns:
                fig.add_trace(
                    go.Scatter(
                        x=sell_signals.index,
                        y=sell_signals["high"] * 1.002,
                        mode="markers",
                        name="卖出信号",
                        marker=dict(symbol="triangle-down", size=12, color="#FF1744"),
                        text=[f"卖出 {fmt_bj_time(idx, '%Y-%m-%d %H:%M')}" for idx in sell_signals.index],
                    ),
                    row=1,
                    col=1,
                )

        fig.update_layout(
            template="plotly_dark",
            height=900,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis_rangeslider_visible=False,
            title_text=f"{title} [{timeframe}] (更新于 {now_bj_str()})",
        )
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], tickformat="%Y-%m-%d\n%H:%M")
        return fig

    def create_trader_analysis_chart(self, traders_df: pd.DataFrame, trades_df: pd.DataFrame) -> go.Figure:
        fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=["各交易所Top交易员ROI", "交易员买卖分布", "价格区间买卖统计", "交易员收益排名"],
            specs=[[{"type": "bar"}, {"type": "pie"}], [{"type": "bar"}, {"type": "bar"}]],
        )

        if not traders_df.empty and "exchange" in traders_df.columns:
            for exchange in traders_df["exchange"].unique():
                ex_df = traders_df[traders_df["exchange"] == exchange].head(10)
                y = ex_df["roi"] if "roi" in ex_df.columns else None
                x = ex_df["nickname"] if "nickname" in ex_df.columns else ex_df.get("trader_id", None)
                if y is not None and x is not None:
                    fig.add_trace(go.Bar(x=x, y=y, name=f"{exchange} Top10"), row=1, col=1)

        if not trades_df.empty and "side" in trades_df.columns:
            buy_count = int((trades_df["side"] == "BUY").sum())
            sell_count = int((trades_df["side"] == "SELL").sum())
            fig.add_trace(go.Pie(labels=["买入", "卖出"], values=[buy_count, sell_count], marker_colors=["#26a69a", "#ef5350"], hole=0.4), row=1, col=2)

            if "price" in trades_df.columns:
                tmp = trades_df[["price", "side"]].copy()
                tmp["price_range"] = (tmp["price"] // 50) * 50
                price_stats = tmp.groupby(["price_range", "side"]).size().unstack(fill_value=0)
                if "BUY" in price_stats.columns:
                    fig.add_trace(go.Bar(x=price_stats.index.astype(str), y=price_stats.get("BUY", 0), name="买入", marker_color="#26a69a"), row=2, col=1)
                if "SELL" in price_stats.columns:
                    fig.add_trace(go.Bar(x=price_stats.index.astype(str), y=price_stats.get("SELL", 0), name="卖出", marker_color="#ef5350"), row=2, col=1)

        if not traders_df.empty and "pnl" in traders_df.columns:
            top20 = traders_df.nlargest(20, "pnl")
            x = top20["nickname"] if "nickname" in top20.columns else top20.get("trader_id", None)
            if x is not None:
                fig.add_trace(go.Bar(x=x, y=top20["pnl"], name="PNL", marker_color="#FFD700"), row=2, col=2)

        fig.update_layout(template="plotly_dark", height=800, title=f"交易员分析 (更新于 {now_bj_str()})", showlegend=True)
        return fig

    def create_prediction_chart(self, df: pd.DataFrame, prediction: dict) -> go.Figure:
        df = to_bj_index(df, assume_naive="UTC")

        fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=["ETH价格走势与预测", "模型置信度", "信号强度历史", "预测方向分布"],
            specs=[[{"type": "scatter"}, {"type": "bar"}], [{"type": "scatter"}, {"type": "pie"}]],
        )

        fig.add_trace(go.Scatter(x=df.index, y=df["close"], name="ETH价格", line=dict(color="#2196F3", width=2)), row=1, col=1)

        if prediction and prediction.get("direction") and not df.empty:
            last_price = float(df["close"].iloc[-1])
            last_ts = df.index[-1]
            direction = prediction.get("direction", "N/A")
            confidence = float(prediction.get("confidence", 0) or 0)
            color = "#00E676" if direction == "UP" else "#FF1744" if direction == "DOWN" else "#FFC107"

            fig.add_trace(
                go.Scatter(
                    x=[last_ts],
                    y=[last_price],
                    mode="markers",
                    name="最新价",
                    marker=dict(size=14, color=color, line=dict(color="white", width=2)),
                    hovertemplate="时间: %{x}<br>价格: %{y:.2f}<extra></extra>",
                ),
                row=1,
                col=1,
            )

            info_text = f"预测方向: {direction}<br>置信度: {confidence:.1%}<br>更新时间: {now_bj_str()}"
            fig.add_annotation(
                x=0.99,
                y=0.98,
                xref="paper",
                yref="paper",
                xanchor="right",
                yanchor="top",
                text=info_text,
                showarrow=False,
                font=dict(color="white", size=12),
                bgcolor="rgba(0,0,0,0.6)",
                bordercolor=color,
                borderwidth=1,
            )

        if prediction and prediction.get("details"):
            models = list(prediction["details"].keys())
            confidences = [prediction["details"][m].get("confidence", 0) for m in models]
            colors = ["#26a69a" if prediction["details"][m].get("direction") == "UP" else "#ef5350" for m in models]
            fig.add_trace(go.Bar(x=models, y=confidences, name="置信度", marker_color=colors, text=[f"{float(c):.1%}" for c in confidences], textposition="auto"), row=1, col=2)

        if "signal_score" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["signal_score"], name="信号强度", fill="tozeroy", line=dict(color="#9C27B0")), row=2, col=1)
            fig.add_hline(y=3, line_dash="dash", line_color="green", row=2, col=1)
            fig.add_hline(y=-3, line_dash="dash", line_color="red", row=2, col=1)

        if prediction and prediction.get("details"):
            up_count = sum(1 for d in prediction["details"].values() if d.get("direction") == "UP")
            down_count = sum(1 for d in prediction["details"].values() if d.get("direction") == "DOWN")
            fig.add_trace(go.Pie(labels=["看涨", "看跌"], values=[up_count, down_count], marker_colors=["#26a69a", "#ef5350"], hole=0.4), row=2, col=2)

        fig.update_layout(template="plotly_dark", height=800, title=f"ETH 预测分析 (更新于 {now_bj_str()})")
        fig.update_xaxes(tickformat="%Y-%m-%d\n%H:%M", row=1, col=1)
        fig.update_xaxes(tickformat="%Y-%m-%d\n%H:%M", row=2, col=1)
        return fig

    def create_price_level_chart(self, price_levels: list) -> go.Figure:
        if not price_levels:
            return go.Figure()

        df = pd.DataFrame(price_levels)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df["buy_volume"], y=[f"${p:.0f}-${q:.0f}" for p, q in zip(df["price_min"], df["price_max"])], orientation="h", name="买入量", marker_color="#26a69a"))
        fig.add_trace(go.Bar(x=-df["sell_volume"], y=[f"${p:.0f}-${q:.0f}" for p, q in zip(df["price_min"], df["price_max"])], orientation="h", name="卖出量", marker_color="#ef5350"))

        fig.update_layout(template="plotly_dark", height=600, title=f"ETH 价格区间买卖分布 (更新于 {now_bj_str()})", xaxis_title="成交量 (USDT)", yaxis_title="价格区间", barmode="relative")
        return fig

    # ================================================================
    # Dash 实时仪表板
    # ================================================================

    def start_dashboard(self, data_callback=None):
        if not HAS_DASH:
            print("[VIS] Dash 未安装，无法启动仪表板")
            return

        self.app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], title="ETH 预测系统")

        self.app.layout = dbc.Container(
            [
                dbc.Row([dbc.Col(html.H1("🔮 ETH 加密货币预测系统", className="text-center text-primary my-3"))]),
                dbc.Row(
                    [
                        dbc.Col(dbc.Card([dbc.CardBody([html.H4(id="current-price", className="card-title text-center"), html.P(id="price-change", className="text-center"), html.P(id="update-time", className="text-center text-muted")])], color="dark", outline=True), width=3),
                        dbc.Col(dbc.Card([dbc.CardBody([html.H4(id="prediction-direction", className="card-title text-center"), html.P(id="prediction-confidence", className="text-center"), html.P(id="prediction-action", className="text-center")])], color="dark", outline=True), width=3),
                        dbc.Col(dbc.Card([dbc.CardBody([html.H4(id="signal-type", className="card-title text-center"), html.P(id="signal-score", className="text-center"), html.P(id="rsi-value", className="text-center")])], color="dark", outline=True), width=3),
                        dbc.Col(dbc.Card([dbc.CardBody([html.H4(id="trader-count", className="card-title text-center"), html.P(id="buy-sell-ratio", className="text-center"), html.P(id="volume-info", className="text-center")])], color="dark", outline=True), width=3),
                    ],
                    className="mb-4",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            dbc.ButtonGroup(
                                [
                                    dbc.Button("1分钟", id="btn-1m", color="outline-primary", size="sm", n_clicks=0),
                                    dbc.Button("5分钟", id="btn-5m", color="outline-primary", size="sm", n_clicks=0),
                                    dbc.Button("15分钟", id="btn-15m", color="outline-primary", size="sm", n_clicks=0),
                                    dbc.Button("1小时", id="btn-1h", color="primary", size="sm", n_clicks=0),
                                    dbc.Button("4小时", id="btn-4h", color="outline-primary", size="sm", n_clicks=0),
                                    dbc.Button("日线", id="btn-1d", color="outline-primary", size="sm", n_clicks=0),
                                ]
                            ),
                            className="text-center mb-3",
                        )
                    ]
                ),
                dbc.Row([dbc.Col(dcc.Graph(id="candlestick-chart", config={"displayModeBar": True}), width=12)], className="mb-4"),
                dbc.Row([dbc.Col(dcc.Graph(id="prediction-chart"), width=6), dbc.Col(dcc.Graph(id="trader-chart"), width=6)], className="mb-4"),
                dbc.Row([dbc.Col(dcc.Graph(id="price-level-chart"), width=6), dbc.Col([html.H4("实时交易信号", className="text-center"), html.Div(id="signal-table")], width=6)], className="mb-4"),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H3("🚨 Alerts / 预警提醒", className="text-center"),
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            dbc.ButtonGroup(
                                                [
                                                    dbc.Button("中文", id="btn-lang-zh", color="primary", size="sm", n_clicks=0),
                                                    dbc.Button("English", id="btn-lang-en", color="outline-primary", size="sm", n_clicks=0),
                                                ],
                                                className="mb-2",
                                            ),
                                            className="text-center",
                                            width=12,
                                        )
                                    ]
                                ),
                                html.Div(id="alerts-summary"),
                                html.Hr(),
                                dbc.Row([dbc.Col([html.H4("New Alerts（本次刷新新增）", className="text-center"), html.Div(id="new-alerts-table")], width=6), dbc.Col([html.H4("Recent Alerts（历史记录）", className="text-center"), html.Div(id="alerts-table")], width=6)]),
                            ],
                            width=12,
                        )
                    ],
                    className="mb-4",
                ),
                dbc.Row([dbc.Col([html.H4("Top 交易员详情", className="text-center"), html.Div(id="trader-table")], width=12)], className="mb-4"),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H4("交易明细（按交易员筛选）", className="text-center"),
                                dcc.Dropdown(id="selected-trader-id", options=[], value=None, placeholder="选择 trader_id（可搜索）", clearable=True, searchable=True),
                                html.Div(id="trade-records-table"),
                            ],
                            width=12,
                        )
                    ],
                    className="mb-4",
                ),
                dcc.Interval(id="interval-component", interval=config.VIS_CONFIG["refresh_interval"], n_intervals=0),
                dcc.Store(id="selected-timeframe", data="1h"),
                dcc.Store(id="alerts-lang", data="zh"),
            ],
            fluid=True,
            className="bg-dark",
        )

        self._register_callbacks(data_callback)
        print(f"[VIS] 仪表板启动于 http://{config.VIS_CONFIG['dash_host']}:{config.VIS_CONFIG['dash_port']}")
        self.app.run(host=config.VIS_CONFIG["dash_host"], port=config.VIS_CONFIG["dash_port"], debug=False)

    def _register_callbacks(self, data_callback):
        @self.app.callback(
            [
                Output("selected-timeframe", "data"),
                Output("btn-1m", "color"),
                Output("btn-5m", "color"),
                Output("btn-15m", "color"),
                Output("btn-1h", "color"),
                Output("btn-4h", "color"),
                Output("btn-1d", "color"),
            ],
            [Input("btn-1m", "n_clicks"), Input("btn-5m", "n_clicks"), Input("btn-15m", "n_clicks"), Input("btn-1h", "n_clicks"), Input("btn-4h", "n_clicks"), Input("btn-1d", "n_clicks")],
            [State("selected-timeframe", "data")],
        )
        def select_timeframe(n1, n5, n15, n1h, n4h, n1d, current_tf):
            triggered = callback_context.triggered[0]["prop_id"] if callback_context.triggered else ""
            mapping = {"btn-1m.n_clicks": "1m", "btn-5m.n_clicks": "5m", "btn-15m.n_clicks": "15m", "btn-1h.n_clicks": "1h", "btn-4h.n_clicks": "4h", "btn-1d.n_clicks": "1d"}
            tf = mapping.get(triggered, current_tf or "1h")

            def c(name):
                return "primary" if tf == name else "outline-primary"

            return (tf, c("1m"), c("5m"), c("15m"), c("1h"), c("4h"), c("1d"))

        @self.app.callback(
            [Output("alerts-lang", "data"), Output("btn-lang-zh", "color"), Output("btn-lang-en", "color")],
            [Input("btn-lang-zh", "n_clicks"), Input("btn-lang-en", "n_clicks")],
            [State("alerts-lang", "data")],
        )
        def select_alerts_lang(nzh, nen, current_lang):
            triggered = callback_context.triggered[0]["prop_id"] if callback_context.triggered else ""
            lang = current_lang or "zh"
            if triggered == "btn-lang-zh.n_clicks":
                lang = "zh"
            elif triggered == "btn-lang-en.n_clicks":
                lang = "en"
            return lang, ("primary" if lang == "zh" else "outline-primary"), ("primary" if lang == "en" else "outline-primary")

        @self.app.callback(
            [
                Output("current-price", "children"),
                Output("price-change", "children"),
                Output("update-time", "children"),
                Output("prediction-direction", "children"),
                Output("prediction-confidence", "children"),
                Output("prediction-action", "children"),
                Output("signal-type", "children"),
                Output("signal-score", "children"),
                Output("rsi-value", "children"),
                Output("trader-count", "children"),
                Output("buy-sell-ratio", "children"),
                Output("volume-info", "children"),
                Output("candlestick-chart", "figure"),
                Output("prediction-chart", "figure"),
                Output("trader-chart", "figure"),
                Output("price-level-chart", "figure"),
                Output("signal-table", "children"),
                Output("trader-table", "children"),
                Output("selected-trader-id", "options"),
                Output("trade-records-table", "children"),
                Output("alerts-summary", "children"),
                Output("new-alerts-table", "children"),
                Output("alerts-table", "children"),
            ],
            [Input("interval-component", "n_intervals"), Input("selected-timeframe", "data"), Input("selected-trader-id", "value"), Input("alerts-lang", "data")],
        )
        def update_dashboard(n, timeframe, selected_trader_id, alerts_lang):
            lang = alerts_lang or "zh"
            data = data_callback(timeframe=timeframe or "1h") if data_callback else {}

            df = data.get("klines_df", pd.DataFrame())
            prediction = data.get("prediction", {}) or {}
            analysis = data.get("analysis", {}) or {}
            traders_df = data.get("traders_df", pd.DataFrame())
            trades_df = data.get("trades_df", pd.DataFrame())
            price_levels = data.get("price_levels", [])
            market = data.get("market_data", {}) or {}

            alerts = data.get("alerts", []) or []
            new_alerts = data.get("new_alerts", []) or []

            now = now_bj_str()

            price = market.get("price", analysis.get("price", 0))
            change = market.get("change_24h", 0)
            price_text = f"${price:,.2f}" if price else "获取中..."
            change_text = f"24h: {change:+.2f}%" if change else ""
            change_color = "text-success" if change and change > 0 else "text-danger"

            pred_dir = prediction.get("direction", "---")
            pred_conf = float(prediction.get("confidence", 0) or 0)
            pred_dir_text = f"预测: {pred_dir}"
            pred_conf_text = f"置信度: {pred_conf:.1%}" if pred_conf else ""

            signal = analysis.get("signal", "---")
            sig_score = float(analysis.get("signal_score", 0) or 0)
            rsi = float(analysis.get("rsi", 0) or 0)

            action = "持有观望"
            if pred_dir == "UP" and pred_conf > 0.6:
                action = "🟢 建议买入"
            elif pred_dir == "DOWN" and pred_conf > 0.6:
                action = "🔴 建议卖出"

            trader_count = len(traders_df) if not traders_df.empty else 0
            buy_count = int((trades_df["side"] == "BUY").sum()) if (not trades_df.empty and "side" in trades_df.columns) else 0
            sell_count = int((trades_df["side"] == "SELL").sum()) if (not trades_df.empty and "side" in trades_df.columns) else 0

            candle_fig = self.create_candlestick_chart(df, timeframe=timeframe or "1h") if not df.empty else go.Figure()
            pred_fig = self.create_prediction_chart(df, prediction) if not df.empty else go.Figure()
            trader_fig = self.create_trader_analysis_chart(traders_df, trades_df)
            level_fig = self.create_price_level_chart(price_levels)

            signal_table = self._create_signal_table(df) if not df.empty else html.P("等待数据...")
            trader_table = self._create_trader_table(traders_df) if not traders_df.empty else html.P("等待数据...")

            trader_options = []
            if not trades_df.empty and "trader_id" in trades_df.columns:
                trader_ids = sorted(trades_df["trader_id"].dropna().unique().tolist())
                trader_options = [{"label": tid, "value": tid} for tid in trader_ids]

            trade_records_view = html.P("请选择交易员以查看交易明细...")
            if not trades_df.empty:
                filtered = trades_df.copy()
                if selected_trader_id and "trader_id" in filtered.columns:
                    filtered = filtered[filtered["trader_id"] == selected_trader_id]

                for col in ("open_time", "close_time", "update_time"):
                    if col in filtered.columns:
                        filtered[col] = filtered[col].apply(fmt_bj_time)

                filtered = filtered.replace({np.nan: ""})
                trade_records_view = _datatable(
                    columns=[{"name": c, "id": c} for c in filtered.columns],
                    data=filtered.to_dict("records"),
                    style_table=STYLE_TABLE_TRADES,
                    style_cell=STYLE_CELL_TRADES,
                    style_header=STYLE_HEADER_DARK,
                    filter_action="native",
                    sort_action="native",
                    page_action="native",
                    page_size=20,
                )

            alerts_summary = self._create_alerts_summary(new_alerts=new_alerts, alerts=alerts, lang=lang)
            new_alerts_table = self._create_alerts_table(new_alerts, lang=lang, title_prefix="NEW")
            alerts_table = self._create_alerts_table(alerts, lang=lang, title_prefix="HISTORY")

            return (
                price_text,
                html.Span(change_text, className=change_color),
                f"更新: {now} | TF: {timeframe}",
                pred_dir_text,
                pred_conf_text,
                action,
                f"信号: {signal}",
                f"强度: {sig_score:.1f}",
                f"RSI: {rsi:.1f}" if rsi else "RSI: ---",
                f"交易员: {trader_count}",
                f"买/卖: {buy_count}/{sell_count}",
                f"24h量: {market.get('volume_24h', 0):,.0f}" if market.get("volume_24h") else "",
                candle_fig,
                pred_fig,
                trader_fig,
                level_fig,
                signal_table,
                trader_table,
                trader_options,
                trade_records_view,
                alerts_summary,
                new_alerts_table,
                alerts_table,
            )

    def _create_signal_table(self, df: pd.DataFrame):
        recent = df.tail(20)[["close", "signal", "signal_score", "rsi"]].copy()
        recent = recent.reset_index()
        recent.columns = ["时间", "价格", "信号", "强度", "RSI"]
        recent["时间"] = recent["时间"].apply(lambda x: fmt_bj_time(x, "%Y-%m-%d %H:%M"))
        recent["价格"] = recent["价格"].map("${:,.2f}".format)
        recent["强度"] = recent["强度"].map("{:.1f}".format)
        recent["RSI"] = recent["RSI"].map("{:.1f}".format)

        return _datatable(
            columns=[{"name": c, "id": c} for c in recent.columns],
            data=recent.to_dict("records"),
            style_table=STYLE_TABLE_SCROLL_X,
            style_cell=STYLE_CELL_DARK_CENTER,
            style_header=STYLE_HEADER_DARK,
            style_data_conditional=[
                {"if": {"filter_query": '{信号} = "BUY" || {信号} = "STRONG_BUY"'}, "backgroundColor": "rgba(38, 166, 154, 0.3)"},
                {"if": {"filter_query": '{信号} = "SELL" || {信号} = "STRONG_SELL"'}, "backgroundColor": "rgba(239, 83, 80, 0.3)"},
            ],
            page_size=10,
        )

    def _create_trader_table(self, traders_df: pd.DataFrame):
        display = traders_df.head(30).copy()
        cols_to_show = ["exchange", "nickname", "trader_id", "roi", "pnl", "win_rate", "trade_count"]
        available_cols = [c for c in cols_to_show if c in display.columns]
        display = display[available_cols]

        col_map = {"exchange": "交易所", "nickname": "昵称", "trader_id": "ID", "roi": "ROI%", "pnl": "盈亏", "win_rate": "胜率", "trade_count": "交易次数"}
        display.columns = [col_map.get(c, c) for c in display.columns]

        return _datatable(
            columns=[{"name": c, "id": c} for c in display.columns],
            data=display.to_dict("records"),
            style_table=STYLE_TABLE_SCROLL_X,
            style_cell=STYLE_CELL_DARK_CENTER,
            style_header=STYLE_HEADER_DARK,
            sort_action="native",
            page_size=15,
        )

    def _create_alerts_summary(self, new_alerts: list, alerts: list, lang: str = "zh"):
        if not new_alerts:
            text = "本次刷新无新增 alerts" if lang == "zh" else "No new alerts in this refresh."
            return html.Div(
                text,
                style={
                    "padding": "10px",
                    "backgroundColor": "rgba(38,166,154,0.10)",
                    "border": "1px solid rgba(38,166,154,0.35)",
                    "borderRadius": "6px",
                    "textAlign": "center",
                },
            )

        priorities = [a.get("priority", "LOW") for a in new_alerts]
        order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
        top = sorted(priorities, key=lambda p: order.get(p, 0), reverse=True)[0]

        top_display = PRIORITY_ZH.get(top, top) if lang == "zh" else top

        color_map = {"CRITICAL": "rgba(255,23,68,0.18)", "HIGH": "rgba(255,193,7,0.16)", "MEDIUM": "rgba(3,169,244,0.14)", "LOW": "rgba(76,175,80,0.12)"}
        border_map = {"CRITICAL": "rgba(255,23,68,0.55)", "HIGH": "rgba(255,193,7,0.55)", "MEDIUM": "rgba(3,169,244,0.55)", "LOW": "rgba(76,175,80,0.55)"}

        text = f"本次新增 {len(new_alerts)} 条 | 最高级别: {top_display} | 历史缓存: {len(alerts)} 条" if lang == "zh" else f"New: {len(new_alerts)} | Top priority: {top_display} | Cached: {len(alerts)}"

        return html.Div(
            text,
            style={
                "padding": "10px",
                "backgroundColor": color_map.get(top, "rgba(255,255,255,0.06)"),
                "border": f"1px solid {border_map.get(top, 'rgba(255,255,255,0.2)')}",
                "borderRadius": "6px",
                "textAlign": "center",
                "fontWeight": "bold",
            },
        )

    def _create_alerts_table(self, alerts: list, lang: str = "zh", title_prefix: str = ""):
        if not alerts:
            return html.P("暂无数据" if lang == "zh" else "No data")

        rows = list(alerts)[::-1]

        keys = set()
        for a in rows:
            if isinstance(a, dict):
                keys.update(a.keys())
        keys.add("priority_raw")

        preferred = ["timestamp", "priority", "type", "signal", "price", "message"]
        rest = [k for k in sorted(keys) if k not in preferred]
        columns = preferred + rest

        normalized: List[Dict[str, Any]] = []
        for a in rows:
            if not isinstance(a, dict):
                continue

            raw_p = a.get("priority", "")

            r: Dict[str, Any] = {}
            for k in columns:
                if k == "priority_raw":
                    v = raw_p
                else:
                    v = a.get(k, "")

                if k == "timestamp":
                    v = fmt_bj_time(v, _TS_FMT)

                v = _jsonify_if_needed(v)
                v = _translate_value(k, v, lang=lang)
                r[k] = v

            normalized.append(r)

        df = pd.DataFrame(normalized)

        if "price" in df.columns:
            df["price"] = df["price"].apply(_fmt_price_usd)

        visible_cols = [c for c in df.columns if c != "priority_raw"]
        dash_columns = [{"name": ALERT_COL_ZH.get(c, c), "id": c} for c in visible_cols] if lang == "zh" else [{"name": c, "id": c} for c in visible_cols]

        return _datatable(
            columns=dash_columns,
            data=df.to_dict("records"),
            hidden_columns=["priority_raw"],
            style_table=STYLE_TABLE_ALERTS,
            style_cell=STYLE_CELL_ALERTS,
            style_header=STYLE_HEADER_DARK,
            style_data_conditional=ALERTS_PRIORITY_HIGHLIGHT,
            filter_action="native",
            sort_action="native",
            page_action="native",
            page_size=10 if title_prefix == "NEW" else 15,
        )

    def save_charts(self, charts: dict, output_dir: str = None):
        if output_dir is None:
            output_dir = os.path.join(config.DATA_DIR, "charts")
        os.makedirs(output_dir, exist_ok=True)

        for name, fig in charts.items():
            path = os.path.join(output_dir, f"{name}.html")
            fig.write_html(path)
            print(f"[VIS] 图表已保存: {path}")
