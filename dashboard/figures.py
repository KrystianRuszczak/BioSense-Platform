from __future__ import annotations

from typing import Literal
from collections.abc import Callable
import plotly.graph_objects as go

from shared.models import metrics_message
from collector.live_store import live_telemetry_snapshot

SignalKind = Literal["filtered", "raw"]
MetricValueGetter = Callable[[metrics_message], int | float | None]

def build_gsr_figure(snapshot: live_telemetry_snapshot, is_stale: bool = False,) -> go.Figure:
    samples = snapshot.gsr_samples
    figure = go.Figure()

    if not samples:
        figure.add_annotation(
            text="Waiting for GSR data",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 17},
        )

        _configure_gsr_layout(figure)

        return figure

    latest_sample = samples[-1].sample

    device_sample_rate_hz = (samples[-1].sample_rate * samples[-1].sample_step)

    x_values = [(sample.sample - latest_sample) / device_sample_rate_hz for sample in samples]
    y_values: list[float | None] = [(sample.conductance_us if sample.valid else None) for sample in samples]

    has_valid_value = any(value is not None for value in y_values)

    if not has_valid_value:
        figure.add_annotation(
            text="No valid GSR data",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 17},
        )
    else:
        figure.add_trace(
            go.Scattergl(
                x=x_values,
                y=y_values,
                mode="lines+markers",
                name="Skin conductance",
                marker={"size": 4},
                connectgaps=False,
                hovertemplate="Time: %{x:.2f} s<br>Conductance: %{y:.3f} µS<extra></extra>"
            )
        )

    _configure_gsr_layout(figure)

    if is_stale and has_valid_value:
        figure.update_traces(opacity=0.35)

        figure.add_annotation(
            text="GSR SIGNAL LOST",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 18,
                  "color": "#9a3412"},
            bgcolor="rgba(255, 237, 213, 0.92)",
            bordercolor="#fdba74",
            borderwidth=1,
            borderpad=10,
        )

    return figure


def build_ecg_figure(snapshot: live_telemetry_snapshot, signal_kind: SignalKind = "filtered", is_stale: bool = False) -> go.Figure:
    if signal_kind not in ("filtered", "raw"):
        raise ValueError("signal_kind must be 'filtered' or 'raw'")

    samples = snapshot.ecg_samples
    figure = go.Figure()

    if not samples:
        figure.add_annotation(
            text="Waiting for ECG data",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 18},
        )

        _configure_ecg_layout(figure=figure, signal_kind=signal_kind)

        return figure

    latest_sample = samples[-1].sample

    x_values = [(sample.sample - latest_sample) / sample.sample_rate for sample in samples]

    if signal_kind == "filtered":
        y_values = [sample.filtered for sample in samples]
        signal_name = "Filtered ECG"
    else:
        y_values = [sample.raw for sample in samples]
        signal_name = "Raw ECG"

    figure.add_trace(
        go.Scattergl(
            x=x_values,
            y=y_values,
            mode="lines",
            name=signal_name,
            hovertemplate=(
                "Time: %{x:.3f} s<br>Value: %{y}<extra></extra>"
            )
        )
    )

    rpeak_x: list[float] = []
    rpeak_y: list[int] = []

    for x_value, y_value, sample in zip(x_values, y_values, samples, strict=True,):
        if sample.rpeak:
            rpeak_x.append(x_value)
            rpeak_y.append(y_value)

    if rpeak_x:
        figure.add_trace(
            go.Scattergl(
                x=rpeak_x,
                y=rpeak_y,
                mode="markers",
                name="R-peak",
                marker={
                    "size": 8,
                    "symbol": "circle-open"
                },
                hovertemplate=(
                    "R-peak <br>Time: %{x:.3f} s<br>Value: %{y}<extra></extra>"
                )
            )
        )

    _configure_ecg_layout(figure=figure, signal_kind=signal_kind)

    if is_stale:
        figure.update_traces(opacity=0.35)

        figure.add_annotation(
            text="ECG SIGNAL LOST",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 20, "color": "#9a3412"},
            bgcolor=("rgba(255, 237, 213, 0.92)"),
            bordercolor="#fdba74",
            borderwidth=1,
            borderpad=10
        )

    return figure

def build_metric_figure(snapshot: live_telemetry_snapshot, *, title: str, y_axis_title: str, value_getter: MetricValueGetter, is_stale: bool = False) -> go.Figure:
    figure = go.Figure()

    x_values: list[object] = []
    y_values: list[int | float | None] = []

    has_valid_value = False

    for message in snapshot.metrics_history:
        value = value_getter(message)

        x_values.append(message.received_at_utc)
        y_values.append(value)

        if value is not None:
            has_valid_value = True

    if not x_values:
        figure.add_annotation(
            text=(f"Waiting for {title} data"),
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 17},
        )
    elif not has_valid_value:
        figure.add_annotation(
            text=(f"No valid {title} data"),
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 17},
        )
    else:
        figure.add_trace(
            go.Scattergl(
                x=x_values,
                y=y_values,
                mode="lines+markers",
                name=title,
                marker={"size": 5},
                connectgaps=False,
                hovertemplate=("Time: %{x|%H:%M:%S}<br>Value: %{y}<extra></extra>"),
            ),
        )

    figure.update_layout(
        margin={
            "l":60,
            "r":20,
            "t":25,
            "b":50
        },
        height=320,
        hovermode="x unified",
        showlegend=False,
        uirevision=f"biosense-live-{title}",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={
            "title": "Time",
            "tickformat": "%H:%M:%S",
            "showgrid": True,
            "zeroline": False,
        },
        yaxis={
            "title": y_axis_title,
            "showgrid": True,
            "zeroline": False,
        },
    )

    if is_stale and has_valid_value:
        figure.update_traces(opacity=0.35)

        figure.add_annotation(
            text="DATA STALE",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={
                "size": 18,
                "color": "#9a3412",
            },
            bgcolor="rgba(255, 237, 213, 0.92)",
            bordercolor="#fdba74",
            borderwidth=1,
            borderpad=10,
        )

    return figure

def _configure_ecg_layout(figure: go.Figure, signal_kind: SignalKind) -> None:
    title =("Filtered ECG" if signal_kind == "filtered" else "Raw ECG")

    figure.update_layout(
        title={"text": title, "x": 0.02, "xanchor": "left"},
        margin={"l": 55, "r": 20, "t": 55, "b": 45,},
        height=430,
        hovermode="x unified",
        showlegend=True,
        uirevision="biosense-live-ecg",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1.0,
        },
        xaxis={
            "title": "Time relative to latest sample [s]",
            "showgrid": True,
            "zeroline": False
        },
        yaxis={
            "title": "ADC value",
            "showgrid": True,
            "zeroline": False,
        },
    )

def _configure_gsr_layout(figure: go.Figure,) -> None:
    figure.update_layout(
        margin={
            "l": 60,
            "r": 20,
            "t": 25,
            "b": 50,
        },
        height=320,
        hovermode="x unified",
        showlegend=False,
        uirevision="biosense-live-gsr",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={
            "title": "Time relative to latest sample [s]",
            "showgrid": True,
            "zeroline": False,
        },
        yaxis={
            "title": "Conductance [µS]",
            "showgrid": True,
            "zeroline": False,
        },
    )