from __future__ import annotations

from pathlib import Path
from typing import cast

from dash import Dash, Input, Output, dcc, html

from collector.live_store import LiveTelemetryStore, live_telemetry_snapshot
from collector.live_health import LiveDeviceState, evaluate_live_health
from dashboard.figures import SignalKind, build_ecg_figure, build_gsr_figure
from dashboard.formatting import (
    device_status, 
    format_event_rows, 
    format_optional_number, 
    format_timestamp, 
    newest_received_at, 
    session_status,
    ecg_sensor_status,
    gsr_sensor_status,
    pulse_sensor_status

)

REFRESH_INTERVAL_MS = 200
GSR_STALE_TIMEOUT_S = 1.0

def _gsr_stream_is_stale(snapshot: live_telemetry_snapshot,) -> bool:
    if not snapshot.gsr_samples:
        return False

    newest_sample = snapshot.gsr_samples[-1]

    age_s = (snapshot.generated_at_utc - newest_sample.received_at_utc).total_seconds()

    return age_s > GSR_STALE_TIMEOUT_S

def create_dashboard_app(live_store: LiveTelemetryStore) -> Dash:
    assets_dir = (Path(__file__).resolve().parent / "assets")

    css_path = assets_dir / "dashboard.css"

    if not css_path.is_file():
        raise FileNotFoundError(f"Dashboard stylesheet not found: {css_path}")

    app = Dash(__name__, assets_folder=str(assets_dir), title="BioSense Dashboard", update_title="",)

    app.layout = _build_layout(live_store=live_store)

    _register_callbacks(app=app, live_store=live_store)

    return app

def _build_layout(live_store: LiveTelemetryStore,) -> html.Div:
    snapshot = live_store.snapshot()

    return html.Div(
            className="dashboard-shell",
            children=[
                dcc.Interval(id="dashboard-refresh", interval=REFRESH_INTERVAL_MS, n_intervals=0,),
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
                html.Main(
                    children=[
                        html.Section(className="metric-grid", children=[
                                _metric_card(title="ECG BPM", value_id="metric-bpm"),
                                _metric_card(title="Optical BPM", value_id="metric-max-bpm"),
                                _metric_card(title="Skin conductance", value_id="metric-gsr"),
                                _metric_card(title="SpO₂", value_id="metric-spo2"),
                                _metric_card(title="Object temperature", value_id="metric-temp"),
                                _metric_card(title="Ambient temperature", value_id="metric-temp_ambient"),
                                _metric_card(title="Supply voltage", value_id="metric-battery"),
                            ]
                        ),
                        html.Section(
                            className="sensor-status-grid",
                            children=[
                                _sensor_status_card(title="ECG", value_id="sensor-ecg-status"),
                                _sensor_status_card(title="GSR", value_id="sensor-gsr-status"),
                                _sensor_status_card(title="Pulse oximeter", value_id="sensor-pulse-status"),
                            ]
                        ),
                        html.Section(className="panel ecg-panel", children=[
                                html.Div(
                                    className="panel-toolbar", 
                                    children=[
                                        html.Div(
                                            children=[
                                                html.H2("Live ECG"),
                                                html.P(f"Rolling {live_store.ecg_window_s:g}-second window", className="panel-description"),
                                            ]
                                        ),
                                        dcc.RadioItems(id="ecg-signal-kind", options=[
                                                {"label": "Filtered", "value": "filtered",},
                                                {"label": "Raw", "value": "raw"},
                                            ],
                                            value="filtered", inline=True, className="signal-selector",
                                        )
                                    ]
                                ),
                                dcc.Graph(id="ecg-graph", figure=build_ecg_figure(snapshot), config={"displaylogo": False, "responsive": True, "scrollZoom": True})
                            ]
                        ),
                        html.Section(
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
                                        )
                                    ]
                                ),
                                dcc.Graph(
                                    id="gsr-graph",
                                    figure=build_gsr_figure(
                                        snapshot,
                                    ),
                                    config={
                                        "displaylogo": False,
                                        "responsive": True,
                                        "scrollZoom": True,
                                    }
                                )
                            ]
                        ),
                        html.Section(
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


def _register_callbacks(app: Dash, live_store: LiveTelemetryStore) -> None:
    @app.callback(
        Output("ecg-graph", "figure"),
        Output("gsr-graph", "figure"),
        Output("device-status", "children"),
        Output("device-status", "className"),
        Output("session-status", "children"),
        Output("session-status", "className"),
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
        Output("metric-battery", "children"),
        Output("event-list", "children"),
        Input("dashboard-refresh", "n_intervals"),
        Input("ecg-signal-kind", "value")
    )

    def refresh_dashboard(_n_intervals: int, signal_kind: str) -> tuple[object, ...]:
        snapshot = live_store.snapshot()

        health = evaluate_live_health(snapshot)

        selected_signal = cast(SignalKind, signal_kind)

        device_text, device_class = device_status(snapshot)

        session_text, session_class = session_status(snapshot)

        ecg_status_text, ecg_status_class = ecg_sensor_status(snapshot)

        gsr_status_text, gsr_status_class = gsr_sensor_status(snapshot)

        pulse_status_text, pulse_status_class = pulse_sensor_status(snapshot)

        metrics = snapshot.metrics

        if metrics is None:
            bpm = "-"
            max_bpm = "-"
            gsr = "-"
            spo2 = "-"
            temperature = "-"
            ambient_temperature = "-"
            supply_voltage = "-"
        elif (health.device_state is LiveDeviceState.OFFLINE):
            bpm = "OFFLINE"
            max_bpm = "OFFLINE"
            gsr = "OFFLINE"
            spo2 = "OFFLINE"
            temperature = "OFFLINE"
            ambient_temperature = "OFFLINE"
            supply_voltage = "OFFLINE"
        elif health.metrics_stale:
            bpm = "STALE"
            max_bpm = "STALE"
            gsr = "STALE"
            spo2 = "STALE"
            temperature = "STALE"
            ambient_temperature = "STALE"
            supply_voltage = "STALE"
        else:
            bpm = (format_optional_number(metrics.ecg_bpm, suffix=" bpm") if (not metrics.leads_off and metrics.ecg_bpm > 0) else "-")
            max_bpm = (format_optional_number(metrics.max_bpm, suffix=" bpm") if (metrics.max_finger and metrics.max_bpm > 0) else "-")
            gsr = (format_optional_number(metrics.gsr_conductance_us, suffix=" µS", precision=2,) if metrics.gsr_valid else "-")
            spo2 = (format_optional_number(metrics.spo2, suffix=" %", precision=1) if metrics.spo2_valid else "-")
            temperature = format_optional_number(metrics.temp_obj, suffix=" °C", precision=1)
            ambient_temperature = format_optional_number(metrics.temp_amb, suffix=" °C", precision=1)
            supply_voltage = (f"{metrics.batt_v:.2f} V")

        ecg_is_stale = (bool(snapshot.ecg_samples) and health.ecg_stale or health.device_state is LiveDeviceState.OFFLINE)

        gsr_is_stale = (health.device_state is LiveDeviceState.OFFLINE or _gsr_stream_is_stale(snapshot))

        return (
            build_ecg_figure(snapshot=snapshot, signal_kind=selected_signal, is_stale=ecg_is_stale),
            build_gsr_figure(snapshot=snapshot, is_stale=gsr_is_stale),
            device_text,
            device_class,
            session_text,
            session_class,
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
            supply_voltage,
            _event_children(format_event_rows(snapshot))
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

def _sensor_status_card(title: str, value_id: str) -> html.Div:
    return html.Div(
        className="sensor-status-card",
        children=[
            html.Span(title, className="status-label",),
            html.Span("NO DATA", id=value_id, className=("status-badge status-unknown"))
        ]
    )

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