"""Descarga de "frames" XBRL y construcción de la tabla por empresa.

Un frame de la SEC devuelve un único concepto contable para TODAS las empresas
que lo reportaron en un periodo dado. Aquí:

  1. Para cada métrica lógica probamos sus etiquetas candidatas en orden.
  2. Fusionamos por CIK quedándonos con el primer valor disponible.
  3. Anotamos qué etiqueta aportó cada dato (columna ``<metric>__tag``) para
     que la falta de homogeneización sea visible y auditable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd

from .client import SECClient
from .metrics import METRICS, Metric


def period_string(year: int, quarter: int | None, kind: str) -> str:
    """Construye el identificador de periodo que usa la API de frames.

    - duration anual:      CY2023
    - duration trimestral: CY2023Q2
    - instant anual:       CY2023Q4I   (foto a fin de ejercicio)
    - instant trimestral:  CY2023Q2I
    """
    if kind == "instant":
        q = quarter if quarter is not None else 4
        return f"CY{year}Q{q}I"
    # duration
    if quarter is None:
        return f"CY{year}"
    return f"CY{year}Q{quarter}"


def fetch_metric(
    client: SECClient,
    metric: Metric,
    year: int,
    quarter: int | None,
) -> pd.DataFrame:
    """Devuelve un DataFrame con columnas: cik, entityName, value, tag, end.

    Una fila por empresa. Vacío si ningún tag tiene frame para ese periodo.
    """
    period = period_string(year, quarter, metric.kind)
    seen: dict[int, dict] = {}

    for tag in metric.tags:
        frame = client.get_frame(metric.taxonomy, tag, metric.unit, period)
        if not frame or "data" not in frame:
            continue
        for row in frame["data"]:
            cik = int(row["cik"])
            if cik in seen:
                continue  # ya cubierto por una etiqueta de mayor prioridad
            seen[cik] = {
                "cik": cik,
                "entityName": row.get("entityName", ""),
                "value": row.get("val"),
                "tag": tag,
                "end": row.get("end", ""),
            }

    if not seen:
        return pd.DataFrame(columns=["cik", "entityName", "value", "tag", "end"])
    return pd.DataFrame(list(seen.values()))


def build_dataset(
    client: SECClient,
    metric_keys: Iterable[str],
    year: int,
    quarter: int | None = None,
    *,
    include_yoy: bool = False,
    progress: Callable[[float, str], None] | None = None,
) -> pd.DataFrame:
    """Tabla ancha: una fila por empresa, una columna por métrica.

    Columnas resultantes por métrica ``k``:
      - ``k``         valor de la métrica
      - ``k__tag``    etiqueta US-GAAP de la que procede (auditoría)
    Más, si ``include_yoy`` y la métrica admite crecimiento:
      - ``k__yoy``    crecimiento interanual en %
    """
    keys = [k for k in metric_keys if k in METRICS]
    base: pd.DataFrame | None = None
    names: dict[int, str] = {}

    total_steps = len(keys) * (2 if include_yoy else 1)
    step = 0

    def _tick(msg: str) -> None:
        nonlocal step
        step += 1
        if progress and total_steps:
            progress(min(step / total_steps, 1.0), msg)

    for key in keys:
        metric = METRICS[key]
        df = fetch_metric(client, metric, year, quarter)
        _tick(f"Descargando {metric.label}…")

        for cik, name in zip(df["cik"], df["entityName"]):
            names.setdefault(int(cik), name)

        cur = df[["cik", "value", "tag"]].rename(
            columns={"value": key, "tag": f"{key}__tag"}
        )

        if include_yoy and metric.growth:
            prev = fetch_metric(client, metric, year - 1, quarter)
            _tick(f"Crecimiento {metric.label}…")
            prev = prev[["cik", "value"]].rename(columns={"value": f"{key}__prev"})
            cur = cur.merge(prev, on="cik", how="left")
            denom = cur[f"{key}__prev"].abs()
            cur[f"{key}__yoy"] = (
                (cur[key] - cur[f"{key}__prev"]) / denom.where(denom != 0) * 100
            )
            cur = cur.drop(columns=[f"{key}__prev"])

        base = cur if base is None else base.merge(cur, on="cik", how="outer")

    if base is None or base.empty:
        return pd.DataFrame()

    base.insert(1, "entityName", base["cik"].map(names))
    return base


def attach_tickers(df: pd.DataFrame, client: SECClient) -> pd.DataFrame:
    """Añade columna ``ticker`` mapeando por CIK (best-effort)."""
    if df.empty or "cik" not in df.columns:
        return df
    mapping = client.get_company_tickers()
    tickers = {cik: info["ticker"] for cik, info in mapping.items()}
    out = df.copy()
    out.insert(1, "ticker", out["cik"].map(tickers).fillna(""))
    return out
