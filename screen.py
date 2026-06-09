"""Cálculo de ratios derivados y aplicación de filtros de cribado."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from metrics import RATIOS


def add_ratios(df: pd.DataFrame, ratio_keys: list[str]) -> pd.DataFrame:
    """Añade columnas de ratio calculadas a partir de las métricas base.

    Si falta alguna métrica necesaria, la columna queda como NaN.
    """
    out = df.copy()
    for rk in ratio_keys:
        ratio = RATIOS.get(rk)
        if not ratio:
            continue
        if ratio.numerator not in out.columns or ratio.denominator not in out.columns:
            out[rk] = np.nan
            continue
        num = pd.to_numeric(out[ratio.numerator], errors="coerce")
        den = pd.to_numeric(out[ratio.denominator], errors="coerce")
        val = num / den.where(den != 0)
        if ratio.as_percent:
            val = val * 100
        out[rk] = val
    return out


# --------------------------------------------------------------------------- #
# Filtros
# --------------------------------------------------------------------------- #
@dataclass
class Filter:
    column: str
    op: str            # ">=", ">", "<=", "<", "==", "!=", "between"
    value: float
    value2: float | None = None  # para "between"

    def mask(self, df: pd.DataFrame) -> pd.Series:
        if self.column not in df.columns:
            # columna inexistente -> no descarta a nadie
            return pd.Series(True, index=df.index)
        col = pd.to_numeric(df[self.column], errors="coerce")
        ops = {
            ">=": col >= self.value,
            ">": col > self.value,
            "<=": col <= self.value,
            "<": col < self.value,
            "==": col == self.value,
            "!=": col != self.value,
        }
        if self.op == "between" and self.value2 is not None:
            lo, hi = sorted((self.value, self.value2))
            m = (col >= lo) & (col <= hi)
        else:
            m = ops.get(self.op)
            if m is None:
                raise ValueError(f"Operador no soportado: {self.op}")
        # Las filas con NaN en la columna filtrada se descartan (no cumplen).
        return m.fillna(False)


def apply_filters(
    df: pd.DataFrame,
    filters: list[Filter],
    *,
    drop_incomplete: bool = False,
) -> pd.DataFrame:
    """Aplica una conjunción (AND) de filtros.

    Con ``drop_incomplete=True`` exige además que las columnas filtradas no
    sean NaN (ya implícito en Filter.mask, pero útil documentarlo).
    """
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    for f in filters:
        mask &= f.mask(df)
    return df[mask].copy()
