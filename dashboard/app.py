from __future__ import annotations

from pathlib import Path
from typing import cast

from dash import ALL, MATCH, Dash, Input, Output, State, dcc, html, ctx, no_update

from collector.live_store import LiveTelemetryStore, live_telemetry_snapshot
from collector.live_health import LiveDeviceState, evaluate_live_health
from collector.live_session_store import LiveSessionStore, LiveSessionState, live_session_snapshot
from dashboard.figures import SignalKind, build_ecg_figure, build_gsr_figure, build_metric_figure, build_temperature_figure
from dashboard.formatting import (
    device_status, 
    format_event_rows, 
    format_optional_number, 
    format_timestamp, 
    newest_received_at, 
    format_session_duration,
    format_session_started,
    live_session_status,
    live_session_result,
    ecg_sensor_status,
    gsr_sensor_status,
    pulse_sensor_status
)
from shared.models import metrics_message

REFRESH_INTERVAL_MS = 200
GSR_STALE_TIMEOUT_S = 1.0

DEFAULT_ECG_VIEW_WINDOW_S = 10
DEFAULT_GSR_VIEW_WINDOW_S = 60
DEFAULT_TREND_VIEW_WINDOW_S = 120

DEFAULT_VISIBLE_PANELS = [
    "ecg",
    "gsr",
    "ecg-bpm",
    "optical-bpm",
    "spo2",
    "temp-object",
    "temp-ambient",
    # "events",
]

TREND_PANEL_ORDER = (
    "ecg-bpm",
    "optical-bpm",
    "spo2",
    "temp-object",
    "temp-ambient",
    "temperature",
)

TREND_PANEL_KEYS = set(TREND_PANEL_ORDER)

def _ecg_bpm_val(message: metrics_message,) -> int | None:
    if message.leads_off or message.ecg_bpm <= 0:
        return None
    
    return message.ecg_bpm

def _optical_bpm_val(message: metrics_message) -> int | None:
    if (not message.max_finger or message.max_bpm <= 0):
        return None
    
    return message.max_bpm

def _spo2_val(message: metrics_message) -> float | None:
    if not message.max_finger or not message.spo2_valid:
        return None
    
    return message.spo2

def _object_temp_val(message: metrics_message,) -> float | None:
    return message.temp_obj

def _ambient_temp_val(message: metrics_message,) -> float | None:
    return message.temp_amb

def _gsr_stream_is_stale(snapshot: live_telemetry_snapshot,) -> bool:
    if not snapshot.gsr_samples:
        return False

    newest_sample = snapshot.gsr_samples[-1]

    age_s = (snapshot.generated_at_utc - newest_sample.received_at_utc).total_seconds()

    return age_s > GSR_STALE_TIMEOUT_S

def create_dashboard_app(live_store: LiveTelemetryStore, session_store: LiveSessionStore) -> Dash:
    assets_dir = (Path(__file__).resolve().parent / "assets")

    css_path = assets_dir / "dashboard.css"

    if not css_path.is_file():
        raise FileNotFoundError(f"Dashboard stylesheet not found: {css_path}")

    app = Dash(__name__, assets_folder=str(assets_dir), title="BioSense Dashboard", update_title="",)

    app.layout = _build_layout(live_store=live_store, session_store=session_store)

    _register_callbacks(app=app, live_store=live_store, session_store=session_store)

    return app

def _build_layout(live_store: LiveTelemetryStore, session_store: LiveSessionStore) -> html.Div:
    snapshot = live_store.snapshot()
    
    session_snapshot = session_store.snapshot()
    
    session_text, session_class = live_session_status(session_snapshot)
    
    result_text, result_class = live_session_result(session_snapshot)

    return html.Div(
            className="dashboard-shell",
            children=[
                dcc.Interval(id="dashboard-refresh", interval=REFRESH_INTERVAL_MS, n_intervals=0,),
                dcc.Store(id="trend-panel-order", storage_type="local", data=[]),
                html.Header(
                        className="dashboard-header",
                        children=[
                            html.Div(children=[
                                    html.H1("BioSense", className="dashboard-title"),
                                    html.P("Live biomedical telemetry", className="dashboard-subtitle"),
                                ]
                            ),
                            html.Div( className="header-statuses", children=[
                                    _status_group(label="Device", value_id="device-status"),
                                    _status_group(label="Session", value_id="session-status"),
                                    _battery_status(),
                                ],
                            )
                        ]
                ),
                html.Div(
                    className="last-update-row",
                    children=[
                        html.Span("Last telemetry:"),
                        html.Strong(id="last-update", children=format_timestamp(newest_received_at(snapshot)))
                    ]
                ),
                html.Section(
                    id="session-summary",
                    className=_session_summary_class(session_snapshot),
                    children=[
                        _session_summary_item(
                            label="Duration",
                            value_id="session-duration",
                            value=format_session_duration(session_snapshot),
                        ),
                        _session_summary_item(
                            label="Started",
                            value_id="session-started",
                            value=format_session_started(session_snapshot),
                        ),
                        _session_summary_item(
                            label="Last session",
                            value_id="session-last-result",
                            value=result_text,
                            value_class=result_class,
                            item_id="session-last-result-item",
                            style=_last_session_item_style(session_snapshot),
                        ),
                    ]
                ),
                html.Details(
                    className="view-settings",
                    children=[
                        html.Summary(
                            "View settings",
                            className="view-settings-summary",
                        ),
                        html.Div(
                            className="view-settings-body",
                            children=[
                                html.P(
                                    "Choose telemetry panels to display.",
                                    className="panel-description",
                                ),
                                dcc.Checklist(
                                    id="visible-panels",
                                    options=[
                                        {
                                            "label": "Live ECG",
                                            "value": "ecg",
                                        },
                                        {
                                            "label": "Live GSR",
                                            "value": "gsr",
                                        },
                                        {
                                            "label": "ECG heart rate",
                                            "value": "ecg-bpm",
                                        },
                                        {
                                            "label": "Optical heart rate",
                                            "value": "optical-bpm"
                                        },
                                        {
                                            "label": "SpO₂",
                                            "value": "spo2"
                                        },
                                        {
                                            "label": "Object temperature",
                                            "value": "temp-object"
                                        },
                                        {
                                            "label": "Ambient temperature",
                                            "value": "temp-ambient"
                                        },
                                        {
                                            "label": "Temperature comparison",
                                            "value": "temperature"
                                        },
                                        {
                                            "label": "Events",
                                            "value": "events"
                                        },
                                    ],
                                    value=DEFAULT_VISIBLE_PANELS,
                                    inline=True,
                                    className="panel-selector",
                                    persistence=True,
                                    persistence_type="local"
                                ),
                            ]
                        ),
                    ]
                ),
                html.Main(
                    children=[
                        html.Section(
                            className="sensor-overview-grid",
                            children=[
                                _sensor_overview_card(
                                    title="ECG",
                                    status_id="sensor-ecg-status",
                                    values=(
                                        ("Heart rate", "metric-bpm"),
                                    ),
                                ),
                                _sensor_overview_card(
                                    title="GSR",
                                    status_id="sensor-gsr-status",
                                    values=(
                                        ("Conductance", "metric-gsr"),
                                    ),
                                ),
                                _sensor_overview_card(
                                    title="Pulse oximeter",
                                    status_id="sensor-pulse-status",
                                    values=(
                                        ("Heart rate", "metric-max-bpm"),
                                        ("SpO₂", "metric-spo2")
                                    )
                                ),
                                _sensor_overview_card(
                                    title="Object temperature",
                                    status_id=None,
                                    values=(
                                        ("Temperature", "metric-temp"),
                                    )
                                ),
                                _sensor_overview_card(
                                    title="Ambient temperature",
                                    status_id=None,
                                    values=(
                                        ("Temperature", "metric-temp-ambient"),
                                    )
                                ),
                            ]
                        ),
                        html.Section(id="ecg-panel", className="panel ecg-panel", children=[
                                html.Div(
                                    className="panel-toolbar", 
                                    children=[
                                        html.Div(
                                            children=[
                                                html.H2("Live ECG"),
                                                html.P(f"Rolling {live_store.ecg_window_s:g}-second window", className="panel-description"),
                                            ]
                                        ),
                                        html.Div(
                                            className="panel-controls",
                                            children=[
                                                dcc.RadioItems(id="ecg-signal-kind", options=[
                                                        {"label": "Filtered", "value": "filtered",},
                                                        {"label": "Raw", "value": "raw"},
                                                    ],
                                                    value="filtered", inline=True, className="signal-selector",
                                                ),
                                                _window_control(
                                                    panel_key="ecg",
                                                    min_s=5,
                                                    max_s=30,
                                                    step_s=1,
                                                    value_s=DEFAULT_ECG_VIEW_WINDOW_S,
                                                    marks={
                                                        5: "5 s",
                                                        10: "10 s",
                                                        20: "20 s",
                                                        30: "30 s",
                                                    }
                                                )
                                            ],
                                        ),
                                    ]
                                ),
                                dcc.Graph(id="ecg-graph", figure=build_ecg_figure(snapshot, window_s=DEFAULT_ECG_VIEW_WINDOW_S), config={"displaylogo": False, "responsive": True, "scrollZoom": True})
                            ]
                        ),
                        html.Section(
                            id="gsr-panel",
                            className="panel trend-panel",
                            children=[
                                html.Div(
                                    className="panel-toolbar",
                                    children=[
                                        html.Div(
                                            children=[
                                                html.H2("Live skin conductance"),
                                                html.P(f"Rolling {live_store.gsr_window_s:g}-second window"),
                                            ],
                                        ),
                                        _window_control(
                                            panel_key="gsr",
                                            min_s=10,
                                            max_s=300,
                                            step_s=5,
                                            value_s=DEFAULT_GSR_VIEW_WINDOW_S,
                                            marks={
                                                10: "10 s",
                                                # 30: "30 s",
                                                60: "1 min",
                                                180: "3 min",
                                                300: "5 min",
                                            }
                                        ),
                                    ]
                                ),
                                dcc.Graph(
                                    id="gsr-graph",
                                    figure=build_gsr_figure(
                                        snapshot,
                                        window_s=DEFAULT_GSR_VIEW_WINDOW_S
                                    ),
                                    config={
                                        "displaylogo": False,
                                        "responsive": True,
                                        "scrollZoom": True,
                                    }
                                ),
                            ]
                        ),
                        html.Section(
                            id="trend-grid",
                            className="trend-grid",
                            children=[
                                _trend_panel(
                                    panel_key="ecg-bpm",
                                    panel_id="ecg-bpm-panel",
                                    title="ECG heart rate",
                                    desc=("Heart rate calculated from ECG R-peaks"),
                                    graph_id="ecg-bpm-graph",
                                    figure=build_metric_figure(snapshot=snapshot, title="ECG heart rate", y_axis_title="Heart rate [bpm]", value_getter=_ecg_bpm_val, window_s=DEFAULT_TREND_VIEW_WINDOW_S),
                                    window_default_s=DEFAULT_TREND_VIEW_WINDOW_S,
                                ),
                                _trend_panel(
                                    panel_key="optical-bpm",
                                    panel_id="optical-bpm-panel",
                                    title="Optical heart rate",
                                    desc=("Heart rate reported by the optical sensor"),
                                    graph_id="optical-bpm-graph",
                                    figure=build_metric_figure(snapshot=snapshot, title="Optical heart rate", y_axis_title="Heart rate [bpm]", value_getter=_optical_bpm_val, window_s=DEFAULT_TREND_VIEW_WINDOW_S),
                                    window_default_s=DEFAULT_TREND_VIEW_WINDOW_S,
                                ),
                                _trend_panel(
                                    panel_key="spo2",
                                    panel_id="spo2-panel",
                                    title="Blood oxygen saturation",
                                    desc=("SpO₂ reported by the optical sensor"),
                                    graph_id="spo2-graph",
                                    figure=build_metric_figure(snapshot=snapshot, title="SpO₂", y_axis_title="SpO₂ [%]", value_getter=_spo2_val, window_s=DEFAULT_TREND_VIEW_WINDOW_S),
                                    window_default_s=DEFAULT_TREND_VIEW_WINDOW_S,
                                ),
                                _trend_panel(
                                  panel_key="temp-object",
                                  panel_id="temp-object-panel",
                                  title="Object temperature",
                                  desc="Measured object temperature",
                                  graph_id="temp-object-graph",
                                  figure=build_metric_figure(
                                        snapshot=snapshot,
                                        title="Object temperature",
                                        y_axis_title="Temperature [°C]",
                                        value_getter=_object_temp_val,
                                        window_s=DEFAULT_TREND_VIEW_WINDOW_S
                                  ),
                                  window_default_s=DEFAULT_TREND_VIEW_WINDOW_S,
                                ),
                                _trend_panel(
                                    panel_key="temp-ambient",
                                    panel_id="temp-ambient-panel",
                                    title="Ambient temperature",
                                    desc="Measured ambient temperature",
                                    graph_id="temp-ambient-graph",
                                    figure=build_metric_figure(
                                        snapshot=snapshot,
                                        title="Ambient temperature",
                                        y_axis_title="Temperature [°C]",
                                        value_getter=_ambient_temp_val,
                                        window_s=DEFAULT_TREND_VIEW_WINDOW_S
                                    ),
                                    window_default_s=DEFAULT_TREND_VIEW_WINDOW_S,
                                ),
                                _trend_panel(
                                    panel_key="temperature",
                                    panel_id="temperature-panel",
                                    title="Temperature comparison",
                                    desc="Object and ambient temperature",
                                    graph_id="temperature-graph",
                                    figure=build_temperature_figure(snapshot, window_s=DEFAULT_TREND_VIEW_WINDOW_S),
                                    window_default_s=DEFAULT_TREND_VIEW_WINDOW_S,
                                ),
                            ],
                        ),
                        html.Section(
                            id="events-panel",
                            className="panel", 
                            children=[
                                html.Div(
                                    className="panel-toolbar",
                                    children=[
                                        html.Div(
                                            children=[
                                                html.H2("Recent events"),
                                                html.P("Newest events are shown first", className="panel-description")
                                            ]
                                        )
                                    ]
                                ),
                                html.Div(
                                    id="event-list",
                                    className="event-list",
                                    children=_event_children(format_event_rows(snapshot))
                                )
                            ]

                        )
                    ]
                )
            ]
        )


def _register_callbacks(app: Dash, live_store: LiveTelemetryStore, session_store: LiveSessionStore) -> None:
    @app.callback(
        Output("ecg-graph", "figure"),
        Output("gsr-graph", "figure"),
        Output("ecg-bpm-graph", "figure"),
        Output("optical-bpm-graph", "figure"),
        Output("spo2-graph", "figure"),
        Output("temp-object-graph", "figure"),
        Output("temp-ambient-graph", "figure"),
        Output("temperature-graph", "figure"),
        Output("device-status", "children"),
        Output("device-status", "className"),
        Output("battery-percent", "children"),
        Output("battery-voltage", "children"),
        Output("battery-fill", "style"),
        Output("sensor-ecg-status", "children"),
        Output("sensor-ecg-status", "className"),
        Output("sensor-gsr-status", "children"),
        Output("sensor-gsr-status", "className"),
        Output("sensor-pulse-status", "children"),
        Output("sensor-pulse-status", "className"),
        Output("last-update", "children"),
        Output("metric-bpm", "children"),
        Output("metric-max-bpm", "children"),
        Output("metric-gsr", "children"),
        Output("metric-spo2", "children"),
        Output("metric-temp", "children"),
        Output("metric-temp-ambient", "children"),
        Output("event-list", "children"),
        Input("dashboard-refresh", "n_intervals"),
        Input("ecg-signal-kind", "value"),
        Input(
            {"type": "window-slider", "panel": "ecg"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "gsr"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "ecg-bpm"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "optical-bpm"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "spo2"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "temp-object"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "temp-ambient"},
            "value",
        ),
        Input(
            {"type": "window-slider", "panel": "temperature"},
            "value",
        ),
        
    )

    def refresh_dashboard(
        _n_intervals: int, 
        signal_kind: str, 
        ecg_win_s: int, 
        gsr_win_s: int,
        ecg_bpm_win_s: int,
        optical_bpm_win_s: int,
        spo2_win_s: int,
        temp_object_win_s: int,
        temp_ambient_win_s: int,
        temperature_win_s: int,
    ) -> tuple[object, ...]:
        snapshot = live_store.snapshot()

        health = evaluate_live_health(snapshot)

        selected_signal = cast(SignalKind, signal_kind)

        device_text, device_class = device_status(snapshot)

        ecg_status_text, ecg_status_class = ecg_sensor_status(snapshot)

        gsr_status_text, gsr_status_class = gsr_sensor_status(snapshot)

        pulse_status_text, pulse_status_class = pulse_sensor_status(snapshot)

        metrics = snapshot.metrics
        
        if metrics is None:
            battery_percent = "-"
            battery_voltage = "-"
            battery_fill = {"width": "0%"}
        elif health.device_state is LiveDeviceState.OFFLINE:
            battery_percent = "OFFLINE"
            battery_voltage = "-"
            battery_fill = {"width": "0%"}
            
            percent = max(0, min(100, metrics.batt_percent))
            battery_fill = {"width": f"{percent}%"}
        elif health.metrics_stale:
            percent = max(0, min(100, metrics.batt_percent))
            battery_percent = "STALE"
            battery_voltage = f"{metrics.batt_v:.2f} V"
            battery_fill = {"width": f"{percent}%"}
        else:
            percent = max(0, min(100, metrics.batt_percent))
            battery_percent = f"{percent}%"
            battery_voltage = f"{metrics.batt_v:.2f} V"
            battery_fill = {"width": f"{percent}%"}

        if metrics is None:
            bpm = "-"
            max_bpm = "-"
            gsr = "-"
            spo2 = "-"
            temperature = "-"
            ambient_temperature = "-"
        elif (health.device_state is LiveDeviceState.OFFLINE):
            bpm = "OFFLINE"
            max_bpm = "OFFLINE"
            gsr = "OFFLINE"
            spo2 = "OFFLINE"
            temperature = "OFFLINE"
            ambient_temperature = "OFFLINE"
        elif health.metrics_stale:
            bpm = "STALE"
            max_bpm = "STALE"
            gsr = "STALE"
            spo2 = "STALE"
            temperature = "STALE"
            ambient_temperature = "STALE"
        else:
            bpm = (format_optional_number(metrics.ecg_bpm, suffix=" bpm") if (not metrics.leads_off and metrics.ecg_bpm > 0) else "-")
            max_bpm = (format_optional_number(metrics.max_bpm, suffix=" bpm") if (metrics.max_finger and metrics.max_bpm > 0) else "-")
            gsr = (format_optional_number(metrics.gsr_conductance_us, suffix=" µS", precision=2,) if metrics.gsr_valid else "-")
            spo2 = (format_optional_number(metrics.spo2, suffix=" %", precision=1) if metrics.spo2_valid else "-")
            temperature = format_optional_number(metrics.temp_obj, suffix=" °C", precision=1)
            ambient_temperature = format_optional_number(metrics.temp_amb, suffix=" °C", precision=1)

        ecg_is_stale = (bool(snapshot.ecg_samples) and health.ecg_stale or health.device_state is LiveDeviceState.OFFLINE)

        gsr_is_stale = (health.device_state is LiveDeviceState.OFFLINE or _gsr_stream_is_stale(snapshot))

        metrics_are_stale = (health.metrics_stale or (health.device_state is LiveDeviceState.OFFLINE))
        
        return (
            build_ecg_figure(snapshot=snapshot, signal_kind=selected_signal, is_stale=ecg_is_stale, window_s=ecg_win_s),
            build_gsr_figure(snapshot=snapshot, is_stale=gsr_is_stale, window_s=gsr_win_s),
            build_metric_figure(snapshot, title="ECG heart rate", y_axis_title="Heart rate [bpm]", value_getter=_ecg_bpm_val, is_stale=metrics_are_stale, window_s=ecg_bpm_win_s),
            build_metric_figure(snapshot, title="Optical heart rate", y_axis_title="Heart rate [bpm]", value_getter=_optical_bpm_val, is_stale=metrics_are_stale, window_s=optical_bpm_win_s),
            build_metric_figure(snapshot, title="SpO₂", y_axis_title="SpO₂ [%]", value_getter=_spo2_val, is_stale=metrics_are_stale, window_s=spo2_win_s),
            build_metric_figure(snapshot, title="Object temperature", y_axis_title="Temperature [°C]", value_getter=_object_temp_val, is_stale=metrics_are_stale, window_s=temp_object_win_s),
            build_metric_figure(snapshot, title="Ambient temperature", y_axis_title="Temperature [°C]", value_getter=_ambient_temp_val, is_stale=metrics_are_stale, window_s=temp_ambient_win_s),
            build_temperature_figure(snapshot, is_stale=metrics_are_stale, window_s=temperature_win_s),
            device_text,
            device_class,
            battery_percent,
            battery_voltage,
            battery_fill,
            ecg_status_text,
            ecg_status_class,
            gsr_status_text,
            gsr_status_class,
            pulse_status_text,
            pulse_status_class,
            format_timestamp(newest_received_at(snapshot)),
            bpm,
            max_bpm,
            gsr,
            spo2,
            temperature,
            ambient_temperature,
            _event_children(format_event_rows(snapshot))
        )
    
    @app.callback(
        Output("trend-panel-order", "data"),
        Output("ecg-panel", "style"),
        Output("gsr-panel", "style"),
        Output("trend-grid", "style"),
        Output("ecg-bpm-panel", "style"),
        Output("optical-bpm-panel", "style"),
        Output("spo2-panel", "style"),
        Output("temp-object-panel", "style"),
        Output("temp-ambient-panel", "style"),
        Output("temperature-panel", "style"),
        Output("events-panel", "style"),
        Input("visible-panels", "value"),
        Input(
            {
                "type": "trend-order-button",
                "panel": ALL,
                "direction": ALL
            },
            "n_clicks",
        ),
        State("trend-panel-order", "data",)
    )
    def update_panel_visibility(selected_panels: list[str] | None, _move_clicks: list[int], stored_order: list[str] | None) -> tuple[object, ...]:
            trend_order = _update_trend_order(selected_panels, stored_order)
            
            triggered_id = ctx.triggered_id
            
            if isinstance(triggered_id, dict) and triggered_id.get("type") == "trend-order-button":
                trend_order = _move_trend_panel(trend_order, panel_key = str(triggered_id["panel"]), direction=str(triggered_id["direction"]))
            styles = _panel_vis_styles(selected_panels, trend_order)
            return (trend_order, *styles)
        
    @app.callback(
        Output(
            {
                "type": "window-slider",
                "panel": MATCH,
            },
            "value",
        ),
        Output(
            {
                "type": "window-input",
                "panel": MATCH,
            },
            "value",
        ),
        Input(
            {
                "type": "window-slider",
                "panel": MATCH,
            },
            "value",
        ),
        Input(
            {
                "type": "window-input",
                "panel": MATCH,
            },
            "value",
        ),
        State(
            {
                "type": "window-slider",
                "panel": MATCH,
            },
            "min",
        ),
        State(
            {
                "type": "window-slider",
                "panel": MATCH,
            },
            "max",
        ),
        prevent_initial_call=True,
    )
    def sync_window_control(slider_val: int, input_val: int | None, min_val: int, max_val: int) -> tuple[object, object]:
        triggered_id = ctx.triggered_id
        
        if not isinstance(triggered_id, dict):
            return no_update, no_update
        
        if triggered_id["type"] == "window-slider":
            return slider_val, slider_val
        
        if input_val is None:
            return no_update, no_update
        
        value = max(min_val, min(max_val, input_val))
        
        return value, value
    
    @app.callback(
        Output("session-status", "children"),
        Output("session-status", "className"),
        Output("session-summary", "className"),
        Output("session-duration", "children"),
        Output("session-started", "children"),
        Output("session-last-result", "children"),
        Output("session-last-result", "className"),
        Output("session-last-result-item", "style"),
        Input("dashboard-refresh", "n_intervals")  
    )
    def refresh_session(_n_intervals: int) -> tuple[object, ...]:
        snapshot = session_store.snapshot()
        
        state_text, state_class = live_session_status(snapshot)
        result_text, result_class = live_session_result(snapshot)
        
        return (
            state_text,
            state_class,
            _session_summary_class(snapshot),
            format_session_duration(snapshot),
            format_session_started(snapshot),
            result_text,
            result_class,
            _last_session_item_style(snapshot),
        )
            
def _update_trend_order(selected_panels: list[str] | None, stored_order: list[str] | None) -> list[str]:
    selected = set(selected_panels or [])
    
    order = [panel_key for panel_key in (stored_order or []) if (panel_key in selected and panel_key in TREND_PANEL_KEYS)]
    
    for panel_key in TREND_PANEL_ORDER:
        if panel_key in selected and panel_key not in order:
            order.append(panel_key)
            
    return order

def _panel_vis_styles(selected_panels: list[str] | None, trend_order: list[str] | None = None) -> tuple[dict[str, str], ...]:
    selected = set(selected_panels or [])
    
    def panel_style(panel_key: str) -> dict[str, str]:
        if panel_key in selected:
            return {}
        
        return {"display": "none"}
    
    selected_trends = (_update_trend_order(selected_panels, trend_order))
    
    visible_chart_count = (int("ecg" in selected) + int("gsr" in selected) + len(selected_trends))
    
    if not selected_trends:
        trend_grid_style = {"display": "none"}
    elif visible_chart_count < 4:
        trend_grid_style = {"gridTemplateColumns": "1fr",}
    else:
        trend_grid_style = {}
        
    ternd_panel_styles: dict[str, dict[str, str]] = {}
    
    for panel_key in TREND_PANEL_ORDER:
        if panel_key not in selected:
            ternd_panel_styles[panel_key] = {"display": "none"}
            
    for idx, panel_k in enumerate(selected_trends):
        ternd_panel_styles[panel_k] = {"order": str(idx)}
        
    if visible_chart_count >= 4 and len(selected_trends) % 2 == 1:
        last_visible_trend = selected_trends[-1]
        
        ternd_panel_styles[last_visible_trend]["gridColumn"] = "1 / -1"
    
    return (
        panel_style("ecg"),
        panel_style("gsr"),
        trend_grid_style,
        ternd_panel_styles["ecg-bpm"],
        ternd_panel_styles["optical-bpm"],
        ternd_panel_styles["spo2"],
        ternd_panel_styles["temp-object"],
        ternd_panel_styles["temp-ambient"],
        ternd_panel_styles["temperature"],
        panel_style("events")
    )

def _metric_card(title: str, value_id: str,) -> html.Div:
    return html.Div(
        className="metric-card",
        children=[
            html.Span(
                title,
                className="metric-label",
            ),
            html.Strong(
                "-",
                id=value_id,
                className="metric-value",
            )
        ]
    )
    
def _sensor_overview_card(*, title: str, status_id: str | None, values: tuple[tuple[str, str], ...]) -> html.Div:
    header_children: list[object] = [
        html.Span(title, className="sensor-overview-title")
    ]
    
    if status_id is not None:
        header_children.append(
            html.Span(
                "NO DATA",
                id=status_id,
                className=("status-badge status-unknown")
            )
        )
        
    return html.Div(
        className="sensor-overview-card",
        children=[
            html.Div(
                className="sensor-overview-header",
                children=header_children,
            ),
            html.Div(
                className="sensor-overview-values",
                children=[
                    html.Div(
                        className="sensor-overview-metric",
                        children=[
                            html.Span(
                                label,
                                className="sensor-overview-metric-label"
                            ),
                            html.Strong(
                                "-",
                                id=value_id,
                                className="sensor-overview-value"
                            )
                        ]
                    )
                    for label, value_id in values
                ]
            )
        ]
    )
    
def _session_summary_class(snapshot: live_session_snapshot) -> str:
    if snapshot.state in (LiveSessionState.ACTIVE, LiveSessionState.FINALIZING):
        return "session-summary session-summary-current"
    
    if snapshot.last_integrity_complete is not None:
        return "session-summary session-summary-last"
    
    return "session-summary session-summary-hidden"

def _last_session_item_style(snapshot: live_session_snapshot) -> dict[str, str]:
    if snapshot.state is LiveSessionState.INACTIVE and snapshot.last_integrity_complete is not None:
        return {}
    
    return {"display": "none"}
    
def _session_summary_item(*, label: str, value_id: str, value: str, value_class: str = "session-summary-value", item_id: str | None = None, style: dict[str, str] | None = None) -> html.Div:
    props: dict[str, object] = {
        "className": "session-summary-item",
        "style": style,
    }
    
    if item_id is not None:
        props["id"] = item_id
    
    return html.Div(
        children=[
            html.Span(
                label,
                className="status-label",
            ),
            html.Strong(
                value,
                id=value_id,
                className=value_class
            ),
        ],
        **props,
    )

def _sensor_status_card(title: str, value_id: str) -> html.Div:
    return html.Div(
        className="sensor-status-card",
        children=[
            html.Span(title, className="status-label",),
            html.Span("NO DATA", id=value_id, className=("status-badge status-unknown"))
        ]
    )
    
def _window_control(
    *,
    panel_key: str,
    min_s: int,
    max_s: int,
    step_s: int,
    value_s: int,
    marks: dict[int, str],
) -> html.Div:
    return html.Div(
        className="window-control",
        children=[
            html.Div(
                className="window-control-header",
                children=[
                    html.Span("Time window", className="window-control-header"),
                    html.Div(
                        className="window-input-group",
                        children=[
                            dcc.Input(
                                id={
                                    "type": "window-input",
                                    "panel": panel_key,
                                },
                                type="number",
                                min=min_s,
                                max=max_s,
                                step=step_s,
                                value=value_s,
                                debounce=True,
                                className="window-input",
                            ),
                            html.Span("s", className="window-input-unit"),
                        ]
                    )
                ]
            ),
            dcc.Slider(
                id={
                    "type": "window-slider",
                    "panel": panel_key,
                },
                min=min_s,
                max=max_s,
                step=step_s,
                value=value_s,
                marks=marks,
                persistence=True,
                persistence_type="local",
            )
        ]
    )

def _trend_panel(*, panel_key: str, panel_id: str, title: str, desc: str, graph_id: str, figure: object, window_default_s: int) -> html.Section:
    return html.Section(
        id=panel_id,
        className="panel trend-card",
        children=[
            html.Div(
              className="panel-toolbar",
              children=[
                html.Div(
                    children=[
                        html.H2(title),
                        html.P(
                            desc,
                            className="panel-description",
                        ),
                    ]
                ),
                _window_control(
                  panel_key=panel_key,
                  min_s=10,
                  max_s=600,
                  step_s=10,
                  value_s=window_default_s,
                  marks={
                    10: "10 s",
                    # 60: "1 min",
                    120: "2 min",
                    300: "5 min",
                    600: "10 min",
                  },
                ),
                html.Div(
                    className="trend-order-controls",
                    children=[
                        html.Button(
                            "←",
                            id={
                                "type": "trend-order-button",
                                "panel": panel_key,
                                "direction": "left",
                            },
                            n_clicks=0,
                            className="trend-order-button",
                            title="Move panel earlier"
                        ),
                        html.Button(
                            "→",
                            id={
                                "type": "trend-order-button",
                                "panel": panel_key,
                                "direction": "right",
                            },
                            n_clicks=0,
                            className="trend-order-button",
                            title="Move panel later"
                        ),
                    ]
                ),
              ]  
            ),
            dcc.Graph(
                id=graph_id,
                figure=figure,
                className="trend-graph",
                config={
                    "displaylogo": False,
                    "responsive": True,
                    "scrollZoom": True,
                },
            )
        ]
    )

def _move_trend_panel(trend_order: list[str], panel_key: str, direction: str) -> list[str]:
    updated_order = list(trend_order)
    
    if panel_key not in updated_order:
        return updated_order
    
    current_idx = updated_order.index(panel_key)
    
    if direction == "left":
        target_idx = current_idx -1
    elif direction == "right":
        target_idx = current_idx + 1
    else:
        raise ValueError("Direction must be 'left' or 'right'")
    
    if target_idx < 0 or target_idx >= len(updated_order):
        return updated_order
    
    updated_order[current_idx], updated_order[target_idx] = (updated_order[target_idx], updated_order[current_idx])
    
    return updated_order

def _status_group(label: str, value_id: str) -> html.Div:
    return html.Div(
        className="status-group",
        children=[
            html.Span(
                label,
                className="status-label",
            ),
            html.Span(
                "UNKNOWN",
                id=value_id,
                className="status-badge status-unknown"
            )
        ]
    )

def _event_children(rows: list[dict[str, str]]) -> list[html.Div]:
    if not rows:
        return [
            html.Div(
                "No events received",
                className="empty-state"
            )
        ]

    return [
        html.Div(
            className="event-row",
            children=[
                html.Span(
                    row["time"],
                    className="event-time",
                ),
                html.Strong(
                    row["event"],
                    className="event-name"
                ),
                html.Span(
                    f"sample {row['sample']}",
                    className="event-sample"
                )
            ]
        )
        for row in rows
    ]
    
def _battery_status() -> html.Div:
    return html.Div(
        className="battery-status",
        children=[
            html.Span(
                "Battery",
                className="status-label",
            ),
            html.Div(
                className="battery-value-row",
                children=[
                    html.Strong(
                        "-",
                        id="battery-percent",
                        className="battery-percent",
                    ),
                    html.Span(
                        "-",
                        id="battery-voltage",
                        className="battery-voltage"
                    )
                ]
            ),
            html.Div(
                className="battery-track",
                children=[
                    html.Div(
                        id="battery-fill",
                        className="battery-fill",
                        style={"width": "0%"}
                    )
                ]
            )
        ]
    )