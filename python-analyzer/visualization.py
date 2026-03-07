"""
ETH Crypto Prediction System - 可视化仪表板
使用 Dash + Plotly 构建实时交互图表，包含具体时间
"""
import json
import os
import threading
from datetime import datetime

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
        """创建带技术指标的K线图"""
        fig = make_subplots(
            rows=4,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.5, 0.15, 0.15, 0.2],
            subplot_titles=[f"{title} [{timeframe}]", "MACD", "RSI", "成交量"],
        )

        # K线
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

        # 布林带
        if "bb_upper" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["bb_upper"],
                    name="BB上轨",
                    line=dict(color="rgba(173,216,230,0.5)", width=1),
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["bb_lower"],
                    name="BB下轨",
                    line=dict(color="rgba(173,216,230,0.5)", width=1),
                    fill="tonexty",
                    fillcolor="rgba(173,216,230,0.1)",
                ),
                row=1,
                col=1,
            )

        # EMA
        ema_colors = {"ema_12": "#FFD700", "ema_26": "#FF6347", "ema_50": "#4169E1", "ema_200": "#FF69B4"}
        for ema, color in ema_colors.items():
            if ema in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df.index,
                        y=df[ema],
                        name=ema.upper(),
                        line=dict(color=color, width=1),
                    ),
                    row=1,
                    col=1,
                )

        # SMA
        sma_colors = {"sma_7": "#00FF00", "sma_25": "#FFA500", "sma_99": "#800080", "sma_200": "#FF1493"}
        for sma, color in sma_colors.items():
            if sma in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df.index,
                        y=df[sma],
                        name=sma.upper(),
                        line=dict(color=color, width=1, dash="dot"),
                    ),
                    row=1,
                    col=1,
                )

        # Parabolic SAR
        if "parabolic_sar" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["parabolic_sar"],
                    name="SAR",
                    mode="markers",
                    marker=dict(size=3, color="#FFD700"),
                ),
                row=1,
                col=1,
            )

        # MACD
        if "macd" in df.columns and "macd_hist" in df.columns and "macd_signal" in df.columns:
            colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df["macd_hist"]]
            fig.add_trace(
                go.Bar(x=df.index, y=df["macd_hist"], name="MACD Hist", marker_color=colors),
                row=2,
                col=1,
            )
            fig.add_trace(
                go.Scatter(x=df.index, y=df["macd"], name="MACD", line=dict(color="#2196F3", width=1.5)),
                row=2,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["macd_signal"], name="Signal", line=dict(color="#FF9800", width=1.5)
                ),
                row=2,
                col=1,
            )

        # RSI
        if "rsi" in df.columns:
            fig.add_trace(
                go.Scatter(x=df.index, y=df["rsi"], name="RSI", line=dict(color="#9C27B0", width=1.5)),
                row=3,
                col=1,
            )
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", row=3, col=1)
            fig.add_hrect(y0=30, y1=70, fillcolor="rgba(128,128,128,0.1)", row=3, col=1)

        # 成交量
        if "open" in df.columns and "close" in df.columns and "volume" in df.columns:
            vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(df["close"], df["open"])]
            fig.add_trace(
                go.Bar(x=df.index, y=df["volume"], name="成交量", marker_color=vol_colors),
                row=4,
                col=1,
            )
            if "volume_sma_20" in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df.index,
                        y=df["volume_sma_20"],
                        name="Vol MA20",
                        line=dict(color="#FFA500", width=1),
                    ),
                    row=4,
                    col=1,
                )

        # 买卖信号标注
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
                        text=[f"买入 {idx.strftime('%Y-%m-%d %H:%M')}" for idx in buy_signals.index],
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
                        text=[f"卖出 {idx.strftime('%Y-%m-%d %H:%M')}" for idx in sell_signals.index],
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
            title_text=f"{title} [{timeframe}] (更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
        )

        fig.update_xaxes(
            rangebreaks=[dict(bounds=["sat", "mon"])],
            tickformat="%Y-%m-%d\n%H:%M",
        )

        return fig

    def create_trader_analysis_chart(self, traders_df: pd.DataFrame, trades_df: pd.DataFrame) -> go.Figure:
        """创建交易员分析图表"""
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
            fig.add_trace(
                go.Pie(
                    labels=["买入", "卖出"],
                    values=[buy_count, sell_count],
                    marker_colors=["#26a69a", "#ef5350"],
                    hole=0.4,
                ),
                row=1,
                col=2,
            )

            if "price" in trades_df.columns:
                tmp = trades_df[["price", "side"]].copy()
                tmp["price_range"] = (tmp["price"] // 50) * 50
                price_stats = tmp.groupby(["price_range", "side"]).size().unstack(fill_value=0)
                if "BUY" in price_stats.columns:
                    fig.add_trace(
                        go.Bar(
                            x=price_stats.index.astype(str),
                            y=price_stats.get("BUY", 0),
                            name="买入",
                            marker_color="#26a69a",
                        ),
                        row=2,
                        col=1,
                    )
                if "SELL" in price_stats.columns:
                    fig.add_trace(
                        go.Bar(
                            x=price_stats.index.astype(str),
                            y=price_stats.get("SELL", 0),
                            name="卖出",
                            marker_color="#ef5350",
                        ),
                        row=2,
                        col=1,
                    )

        if not traders_df.empty and "pnl" in traders_df.columns:
            top20 = traders_df.nlargest(20, "pnl")
            x = top20["nickname"] if "nickname" in top20.columns else top20.get("trader_id", None)
            if x is not None:
                fig.add_trace(
                    go.Bar(x=x, y=top20["pnl"], name="PNL", marker_color="#FFD700"),
                    row=2,
                    col=2,
                )

        fig.update_layout(
            template="plotly_dark",
            height=800,
            title=f"交易员分析 (更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
            showlegend=True,
        )

        return fig

    def create_prediction_chart(self, df: pd.DataFrame, prediction: dict) -> go.Figure:
        """创建预测结果可视化（增强：更清晰的标注）"""
        fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=["ETH价格走势与预测", "模型置信度", "信号强度历史", "预测方向分布"],
            specs=[[{"type": "scatter"}, {"type": "bar"}], [{"type": "scatter"}, {"type": "pie"}]],
        )

        # 价格走势
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["close"],
                name="ETH价格",
                line=dict(color="#2196F3", width=2),
            ),
            row=1,
            col=1,
        )

        # 增强预测标注：最后点 marker + 右上角固定信息框
        if prediction and prediction.get("direction") and not df.empty:
            last_price = float(df["close"].iloc[-1])
            last_ts = df.index[-1]

            direction = prediction.get("direction", "N/A")
            confidence = float(prediction.get("confidence", 0) or 0)

            color = "#00E676" if direction == "UP" else "#FF1744" if direction == "DOWN" else "#FFC107"

            # 最后价格点加粗 marker
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

            # 图右上角固定信息框（不会挡住曲线）
            info_text = (
                f"预测方向: {direction}<br>"
                f"置信度: {confidence:.1%}<br>"
                f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
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

        # 模型置信度
        if prediction and prediction.get("details"):
            models = list(prediction["details"].keys())
            confidences = [prediction["details"][m].get("confidence", 0) for m in models]
            colors = [
                "#26a69a" if prediction["details"][m].get("direction") == "UP" else "#ef5350"
                for m in models
            ]

            fig.add_trace(
                go.Bar(
                    x=models,
                    y=confidences,
                    name="置信度",
                    marker_color=colors,
                    text=[f"{float(c):.1%}" for c in confidences],
                    textposition="auto",
                ),
                row=1,
                col=2,
            )

        # 信号强度历史
        if "signal_score" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["signal_score"],
                    name="信号强度",
                    fill="tozeroy",
                    line=dict(color="#9C27B0"),
                ),
                row=2,
                col=1,
            )
            fig.add_hline(y=3, line_dash="dash", line_color="green", row=2, col=1)
            fig.add_hline(y=-3, line_dash="dash", line_color="red", row=2, col=1)

        # 预测方向分布
        if prediction and prediction.get("details"):
            up_count = sum(1 for d in prediction["details"].values() if d.get("direction") == "UP")
            down_count = sum(1 for d in prediction["details"].values() if d.get("direction") == "DOWN")
            fig.add_trace(
                go.Pie(
                    labels=["看涨", "看跌"],
                    values=[up_count, down_count],
                    marker_colors=["#26a69a", "#ef5350"],
                    hole=0.4,
                ),
                row=2,
                col=2,
            )

        fig.update_layout(
            template="plotly_dark",
            height=800,
            title=f"ETH 预测分析 (更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
        )
        fig.update_xaxes(tickformat="%Y-%m-%d\n%H:%M", row=1, col=1)
        fig.update_xaxes(tickformat="%Y-%m-%d\n%H:%M", row=2, col=1)

        return fig

    def create_price_level_chart(self, price_levels: list) -> go.Figure:
        """创建价格层级分析图"""
        if not price_levels:
            return go.Figure()

        df = pd.DataFrame(price_levels)
        fig = go.Figure()

        fig.add_trace(
            go.Bar(
                x=df["buy_volume"],
                y=[f"${p:.0f}-${q:.0f}" for p, q in zip(df["price_min"], df["price_max"])],
                orientation="h",
                name="买入量",
                marker_color="#26a69a",
            )
        )

        fig.add_trace(
            go.Bar(
                x=-df["sell_volume"],
                y=[f"${p:.0f}-${q:.0f}" for p, q in zip(df["price_min"], df["price_max"])],
                orientation="h",
                name="卖出量",
                marker_color="#ef5350",
            )
        )

        fig.update_layout(
            template="plotly_dark",
            height=600,
            title=f"ETH 价格区间买卖分布 (更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
            xaxis_title="成交量 (USDT)",
            yaxis_title="价格区间",
            barmode="relative",
        )

        return fig

    # ================================================================
    # Dash 实时仪表板
    # ================================================================

    def start_dashboard(self, data_callback=None):
        """启动 Dash 实时仪表板"""
        if not HAS_DASH:
            print("[VIS] Dash 未安装，无法启动仪表板")
            return

        self.app = dash.Dash(
            __name__,
            external_stylesheets=[dbc.themes.DARKLY],
            title="ETH 预测系统",
        )

        self.app.layout = dbc.Container(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            html.H1("🔮 ETH 加密货币预测系统", className="text-center text-primary my-3"),
                        ),
                    ]
                ),
                # 状态栏
                dbc.Row(
                    [
                        dbc.Col(
                            dbc.Card(
                                [
                                    dbc.CardBody(
                                        [
                                            html.H4(id="current-price", className="card-title text-center"),
                                            html.P(id="price-change", className="text-center"),
                                            html.P(id="update-time", className="text-center text-muted"),
                                        ]
                                    )
                                ],
                                color="dark",
                                outline=True,
                            ),
                            width=3,
                        ),
                        dbc.Col(
                            dbc.Card(
                                [
                                    dbc.CardBody(
                                        [
                                            html.H4(id="prediction-direction", className="card-title text-center"),
                                            html.P(id="prediction-confidence", className="text-center"),
                                            html.P(id="prediction-action", className="text-center"),
                                        ]
                                    )
                                ],
                                color="dark",
                                outline=True,
                            ),
                            width=3,
                        ),
                        dbc.Col(
                            dbc.Card(
                                [
                                    dbc.CardBody(
                                        [
                                            html.H4(id="signal-type", className="card-title text-center"),
                                            html.P(id="signal-score", className="text-center"),
                                            html.P(id="rsi-value", className="text-center"),
                                        ]
                                    )
                                ],
                                color="dark",
                                outline=True,
                            ),
                            width=3,
                        ),
                        dbc.Col(
                            dbc.Card(
                                [
                                    dbc.CardBody(
                                        [
                                            html.H4(id="trader-count", className="card-title text-center"),
                                            html.P(id="buy-sell-ratio", className="text-center"),
                                            html.P(id="volume-info", className="text-center"),
                                        ]
                                    )
                                ],
                                color="dark",
                                outline=True,
                            ),
                            width=3,
                        ),
                    ],
                    className="mb-4",
                ),
                # 时间框架选择
                dbc.Row(
                    [
                        dbc.Col(
                            [
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
                            ],
                            className="text-center mb-3",
                        ),
                    ]
                ),
                dbc.Row(
                    [
                        dbc.Col(dcc.Graph(id="candlestick-chart", config={"displayModeBar": True}), width=12),
                    ],
                    className="mb-4",
                ),
                dbc.Row(
                    [
                        dbc.Col(dcc.Graph(id="prediction-chart"), width=6),
                        dbc.Col(dcc.Graph(id="trader-chart"), width=6),
                    ],
                    className="mb-4",
                ),
                dbc.Row(
                    [
                        dbc.Col(dcc.Graph(id="price-level-chart"), width=6),
                        dbc.Col(
                            [
                                html.H4("实时交易信号", className="text-center"),
                                html.Div(id="signal-table"),
                            ],
                            width=6,
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H4("Top 交易员详情", className="text-center"),
                                html.Div(id="trader-table"),
                            ],
                            width=12,
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H4("交易明细（按交易员筛选）", className="text-center"),
                                dcc.Dropdown(
                                    id="selected-trader-id",
                                    options=[],
                                    value=None,
                                    placeholder="选择 trader_id（可搜索）",
                                    clearable=True,
                                    searchable=True,
                                ),
                                html.Div(id="trade-records-table"),
                            ],
                            width=12,
                        ),
                    ],
                    className="mb-4",
                ),
                dcc.Interval(
                    id="interval-component",
                    interval=config.VIS_CONFIG["refresh_interval"],
                    n_intervals=0,
                ),
                dcc.Store(id="selected-timeframe", data="1h"),
            ],
            fluid=True,
            className="bg-dark",
        )

        self._register_callbacks(data_callback)

        print(f"[VIS] 仪表板启动于 http://{config.VIS_CONFIG['dash_host']}:{config.VIS_CONFIG['dash_port']}")
        self.app.run(host=config.VIS_CONFIG["dash_host"], port=config.VIS_CONFIG["dash_port"], debug=False)

    def _register_callbacks(self, data_callback):
        """注册 Dash 回调函数"""

        # 1) 时间框架切换：按钮 -> Store + 按钮颜色
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
            [
                Input("btn-1m", "n_clicks"),
                Input("btn-5m", "n_clicks"),
                Input("btn-15m", "n_clicks"),
                Input("btn-1h", "n_clicks"),
                Input("btn-4h", "n_clicks"),
                Input("btn-1d", "n_clicks"),
            ],
            [State("selected-timeframe", "data")],
        )
        def select_timeframe(n1, n5, n15, n1h, n4h, n1d, current_tf):
            triggered = callback_context.triggered[0]["prop_id"] if callback_context.triggered else ""
            mapping = {
                "btn-1m.n_clicks": "1m",
                "btn-5m.n_clicks": "5m",
                "btn-15m.n_clicks": "15m",
                "btn-1h.n_clicks": "1h",
                "btn-4h.n_clicks": "4h",
                "btn-1d.n_clicks": "1d",
            }
            tf = mapping.get(triggered, current_tf or "1h")

            def c(name):
                return "primary" if tf == name else "outline-primary"

            return (tf, c("1m"), c("5m"), c("15m"), c("1h"), c("4h"), c("1d"))

        # 2) 主仪表板刷新：interval + selected-timeframe + selected-trader-id
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
            ],
            [
                Input("interval-component", "n_intervals"),
                Input("selected-timeframe", "data"),
                Input("selected-trader-id", "value"),
            ],
        )
        def update_dashboard(n, timeframe, selected_trader_id):
            data = {}
            if data_callback:
                # 关键：把 timeframe 传给回调，让后端重新拉对应周期K线
                data = data_callback(timeframe=timeframe or "1h")

            df = data.get("klines_df", pd.DataFrame())
            prediction = data.get("prediction", {})
            analysis = data.get("analysis", {})
            traders_df = data.get("traders_df", pd.DataFrame())
            trades_df = data.get("trades_df", pd.DataFrame())
            price_levels = data.get("price_levels", [])
            market = data.get("market_data", {})

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

                # 时间字段展示格式化
                for col in ["open_time", "close_time", "update_time"]:
                    if col in filtered.columns:
                        filtered[col] = pd.to_datetime(filtered[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

                filtered = filtered.replace({np.nan: ""})

                trade_records_view = dash_table.DataTable(
                    data=filtered.to_dict("records"),
                    columns=[{"name": c, "id": c} for c in filtered.columns],
                    style_table={"overflowX": "auto", "maxHeight": "500px", "overflowY": "auto"},
                    style_cell={
                        "textAlign": "center",
                        "backgroundColor": "#303030",
                        "color": "white",
                        "padding": "6px",
                        "fontSize": "12px",
                        "minWidth": "90px",
                        "width": "90px",
                        "maxWidth": "240px",
                        "whiteSpace": "normal",
                    },
                    style_header={"backgroundColor": "#404040", "fontWeight": "bold"},
                    filter_action="native",
                    sort_action="native",
                    page_action="native",
                    page_size=20,
                )

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
            )

    def _create_signal_table(self, df: pd.DataFrame):
        """创建最近信号表格"""
        if df.empty:
            return html.P("无数据")

        recent = df.tail(20)[["close", "signal", "signal_score", "rsi"]].copy()
        recent = recent.reset_index()
        recent.columns = ["时间", "价格", "信号", "强度", "RSI"]
        recent["时间"] = pd.to_datetime(recent["时间"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        recent["价格"] = recent["价格"].map("${:,.2f}".format)
        recent["强度"] = recent["强度"].map("{:.1f}".format)
        recent["RSI"] = recent["RSI"].map("{:.1f}".format)

        return dash_table.DataTable(
            data=recent.to_dict("records"),
            columns=[{"name": c, "id": c} for c in recent.columns],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "backgroundColor": "#303030", "color": "white"},
            style_header={"backgroundColor": "#404040", "fontWeight": "bold"},
            style_data_conditional=[
                {"if": {"filter_query": '{信号} = "BUY" || {信号} = "STRONG_BUY"'}, "backgroundColor": "rgba(38, 166, 154, 0.3)"},
                {"if": {"filter_query": '{信号} = "SELL" || {信号} = "STRONG_SELL"'}, "backgroundColor": "rgba(239, 83, 80, 0.3)"},
            ],
            page_size=10,
        )

    def _create_trader_table(self, traders_df: pd.DataFrame):
        """创建交易员详情表格"""
        if traders_df.empty:
            return html.P("无数据")

        display = traders_df.head(30).copy()
        cols_to_show = ["exchange", "nickname", "trader_id", "roi", "pnl", "win_rate", "trade_count"]
        available_cols = [c for c in cols_to_show if c in display.columns]
        display = display[available_cols]

        col_map = {
            "exchange": "交易所",
            "nickname": "昵称",
            "trader_id": "ID",
            "roi": "ROI%",
            "pnl": "盈亏",
            "win_rate": "胜率",
            "trade_count": "交易次数",
        }
        display.columns = [col_map.get(c, c) for c in display.columns]

        return dash_table.DataTable(
            data=display.to_dict("records"),
            columns=[{"name": c, "id": c} for c in display.columns],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "backgroundColor": "#303030", "color": "white"},
            style_header={"backgroundColor": "#404040", "fontWeight": "bold"},
            sort_action="native",
            page_size=15,
        )

    def save_charts(self, charts: dict, output_dir: str = None):
        """保存图表为 HTML 文件"""
        if output_dir is None:
            output_dir = os.path.join(config.DATA_DIR, "charts")
        os.makedirs(output_dir, exist_ok=True)

        for name, fig in charts.items():
            path = os.path.join(output_dir, f"{name}.html")
            fig.write_html(path)
            print(f"[VIS] 图表已保存: {path}")
