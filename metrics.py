"""Definición de métricas lógicas y su mapeo a etiquetas US-GAAP.

El problema central: la SEC NO homogeneiza los estados financieros. Una misma
magnitud (p. ej. "ingresos") puede aparecer bajo etiquetas distintas según la
empresa y el año:

    Revenues
    RevenueFromContractWithCustomerExcludingAssessedTax
    RevenueFromContractWithCustomerIncludingAssessedTax
    SalesRevenueNet
    ...

Aquí definimos, para cada métrica "lógica", una lista PRIORIZADA de etiquetas
candidatas. Al construir el dataset se prueba cada etiqueta en orden y, para
cada empresa, se toma el primer valor disponible (registrando qué etiqueta lo
aportó, para que el usuario vea de dónde sale cada dato).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Metric:
    key: str                      # identificador interno y nombre de columna base
    label: str                    # nombre legible (ES)
    kind: str                     # "duration" (flujo: P&G, cash flow) | "instant" (balance)
    unit: str                     # unidad XBRL: "USD", "USD-per-shares", "shares"
    tags: list[str]               # etiquetas US-GAAP candidatas, en orden de prioridad
    taxonomy: str = "us-gaap"
    growth: bool = False          # ¿tiene sentido calcular crecimiento interanual?
    description: str = ""


# --------------------------------------------------------------------------- #
# Catálogo de métricas base (las que se descargan vía frames)
# --------------------------------------------------------------------------- #
METRICS: dict[str, Metric] = {m.key: m for m in [
    # ---- Cuenta de resultados (duration) ----
    Metric(
        "revenue", "Ingresos", "duration", "USD",
        ["Revenues",
         "RevenueFromContractWithCustomerExcludingAssessedTax",
         "RevenueFromContractWithCustomerIncludingAssessedTax",
         "SalesRevenueNet",
         "SalesRevenueGoodsNet"],
        growth=True,
        description="Cifra de negocio / ventas netas.",
    ),
    Metric(
        "cost_of_revenue", "Coste de ventas", "duration", "USD",
        ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
        description="Coste de los bienes/servicios vendidos.",
    ),
    Metric(
        "gross_profit", "Beneficio bruto", "duration", "USD",
        ["GrossProfit"],
    ),
    Metric(
        "operating_income", "Resultado de explotación", "duration", "USD",
        ["OperatingIncomeLoss"],
        growth=True,
    ),
    Metric(
        "net_income", "Beneficio neto", "duration", "USD",
        ["NetIncomeLoss", "ProfitLoss"],
        growth=True,
        description="Resultado neto del periodo.",
    ),
    Metric(
        "rd_expense", "Gasto en I+D", "duration", "USD",
        ["ResearchAndDevelopmentExpense"],
    ),
    Metric(
        "operating_cash_flow", "Flujo de caja operativo", "duration", "USD",
        ["NetCashProvidedByUsedInOperatingActivities",
         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
        growth=True,
    ),
    Metric(
        "eps_diluted", "BPA diluido", "duration", "USD-per-shares",
        ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
        description="Beneficio por acción diluido.",
    ),

    # ---- Balance (instant) ----
    Metric(
        "assets", "Activo total", "instant", "USD",
        ["Assets"],
    ),
    Metric(
        "current_assets", "Activo corriente", "instant", "USD",
        ["AssetsCurrent"],
    ),
    Metric(
        "liabilities", "Pasivo total", "instant", "USD",
        ["Liabilities"],
    ),
    Metric(
        "current_liabilities", "Pasivo corriente", "instant", "USD",
        ["LiabilitiesCurrent"],
    ),
    Metric(
        "equity", "Patrimonio neto", "instant", "USD",
        ["StockholdersEquity",
         "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    ),
    Metric(
        "cash", "Efectivo y equivalentes", "instant", "USD",
        ["CashAndCashEquivalentsAtCarryingValue",
         "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    ),
    Metric(
        "long_term_debt", "Deuda a largo plazo", "instant", "USD",
        ["LongTermDebtNoncurrent", "LongTermDebt"],
    ),
]}


# --------------------------------------------------------------------------- #
# Ratios derivados (se calculan a partir de las métricas base, no se descargan)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Ratio:
    key: str
    label: str
    numerator: str        # key de métrica
    denominator: str      # key de métrica
    as_percent: bool = False
    requires: tuple[str, ...] = field(default=())  # métricas necesarias

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", (self.numerator, self.denominator))


RATIOS: dict[str, Ratio] = {r.key: r for r in [
    Ratio("gross_margin", "Margen bruto %", "gross_profit", "revenue", as_percent=True),
    Ratio("operating_margin", "Margen operativo %", "operating_income", "revenue", as_percent=True),
    Ratio("net_margin", "Margen neto %", "net_income", "revenue", as_percent=True),
    Ratio("roa", "ROA % (rent. activos)", "net_income", "assets", as_percent=True),
    Ratio("roe", "ROE % (rent. patrimonio)", "net_income", "equity", as_percent=True),
    Ratio("current_ratio", "Ratio de liquidez", "current_assets", "current_liabilities"),
    Ratio("debt_to_equity", "Deuda / Patrimonio", "long_term_debt", "equity"),
]}


def metrics_needed_for(ratio_keys: list[str]) -> set[str]:
    """Métricas base necesarias para calcular los ratios indicados."""
    needed: set[str] = set()
    for rk in ratio_keys:
        ratio = RATIOS.get(rk)
        if ratio:
            needed.update(ratio.requires)
    return needed
