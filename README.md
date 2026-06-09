# Cribado de estados financieros · SEC EDGAR

App web (Streamlit) para **filtrar el universo de empresas que reportan a la SEC**
por criterios financieros, usando la API de *frames* XBRL de EDGAR. Exporta los
resultados a **CSV / Excel**.

Los datos vienen directamente de EDGAR y **no están homogeneizados**: distintas
empresas etiquetan la misma magnitud de formas diferentes, y hay errores de
etiquetado en origen. La app está diseñada para que eso sea **visible y
auditable**, no para ocultarlo.

## Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Uso

```powershell
streamlit run app.py
```

Se abre en el navegador. En la barra lateral:

1. **Email de contacto** — obligatorio. La SEC exige identificarse en la cabecera
   `User-Agent`; usa un email real.
2. **Año / periodo** — anual o un trimestre concreto.
3. **Métricas** y **ratios** a descargar/calcular.
4. **Crecimiento interanual (YoY)** — opcional, descarga también el año anterior.
5. Pulsa **Cargar datos de la SEC**.

Después, en el panel principal, añade **filtros** (columna + operador + valor),
revisa la tabla y **descarga** en CSV o Excel.

> La primera carga tarda unos segundos (descarga los *frames*); las siguientes
> usan la caché en disco (`.cache/`, validez 12 h).

## Cómo se maneja la falta de homogeneización

- **Sinónimos de etiquetas.** Cada magnitud lógica (p. ej. *Ingresos*) se mapea a
  una lista priorizada de etiquetas US-GAAP candidatas
  (`Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, …). Para
  cada empresa se toma el primer valor disponible. Ver
  [`sec_screener/metrics.py`](sec_screener/metrics.py).
- **Auditoría.** La columna `<metrica>__tag` indica de qué etiqueta concreta salió
  cada valor. Actívala con la casilla *"Mostrar columnas de auditoría"*.
- **Periodos calendario.** Los *frames* alinean al año natural (`CY`). Empresas con
  ejercicio fiscal no-calendario se aproximan al trimestre natural más cercano, lo
  que puede mezclar contextos entre métricas.
- **Errores en origen.** Verás márgenes imposibles o valores absurdos por filings
  mal etiquetados. No se corrigen solos: usa filtros de cordura (p. ej.
  *Margen neto `between` -100 y 100*) y la columna de auditoría.

## Estructura

```
app.py                    App Streamlit (interfaz + filtros + descargas)
sec_screener/
  client.py               Cliente HTTP de la SEC (User-Agent, rate-limit 10 req/s, caché)
  metrics.py              Catálogo de métricas y su mapeo a etiquetas US-GAAP; ratios
  frames.py               Descarga de frames XBRL y fusión por empresa
  screen.py               Cálculo de ratios y aplicación de filtros
```

## Notas sobre las APIs de la SEC

- *Frames*: `https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{periodo}.json`
- Tickers: `https://www.sec.gov/files/company_tickers.json`
- Requisitos: `User-Agent` con contacto real y máximo 10 peticiones/segundo.
  Detalles: <https://www.sec.gov/os/accessing-edgar-data>
