"""Cliente HTTP para las APIs de la SEC (data.sec.gov y sec.gov).

Cumple los dos requisitos obligatorios de la SEC:
  1. Cabecera ``User-Agent`` con un contacto real (nombre + email).
  2. No superar 10 peticiones por segundo.

Además cachea las respuestas en disco, porque los "frames" XBRL son
ficheros grandes que solo cambian unas pocas veces al día.

Documentación: https://www.sec.gov/os/accessing-edgar-data
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import requests

# Límite de la SEC: 10 req/s. Dejamos margen con ~8 req/s.
_MIN_INTERVAL = 0.125

# Cuánto tiempo consideramos válida una respuesta cacheada (segundos).
_DEFAULT_TTL = 60 * 60 * 12  # 12 horas


class SECError(RuntimeError):
    """Error al hablar con la SEC (red, 4xx/5xx, JSON inválido)."""


class SECClient:
    """Cliente con rate-limit, reintentos y caché en disco.

    Parameters
    ----------
    contact:
        Email/identificación que la SEC exige en el ``User-Agent``. Si no se
        indica un ``user_agent`` completo, se construye uno a partir de esto.
    user_agent:
        Cabecera ``User-Agent`` completa. Tiene prioridad sobre ``contact``.
    cache_dir:
        Carpeta donde guardar la caché en disco. ``None`` desactiva la caché.
    cache_ttl:
        Validez de la caché en segundos.
    """

    BASE = "https://data.sec.gov"

    def __init__(
        self,
        contact: str | None = None,
        *,
        user_agent: str | None = None,
        cache_dir: str | Path | None = ".cache",
        cache_ttl: int = _DEFAULT_TTL,
    ) -> None:
        if user_agent:
            self.user_agent = user_agent
        elif contact:
            self.user_agent = f"SEC Financial Screener ({contact})"
        else:
            raise ValueError(
                "La SEC exige un contacto. Pasa contact='tu@email.com' o un user_agent completo."
            )

        self.cache_ttl = cache_ttl
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            }
        )
        self._lock = threading.Lock()
        self._last_request = 0.0

    # ------------------------------------------------------------------ #
    # Caché en disco
    # ------------------------------------------------------------------ #
    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path | None) -> Any | None:
        if not path or not path.exists():
            return None
        if (time.time() - path.stat().st_mtime) > self.cache_ttl:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, path: Path | None, data: Any) -> None:
        if not path:
            return
        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass  # la caché es best-effort; no abortamos por no poder escribir

    # ------------------------------------------------------------------ #
    # Petición HTTP con rate-limit y reintentos
    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.time() - self._last_request
            if elapsed < _MIN_INTERVAL:
                time.sleep(_MIN_INTERVAL - elapsed)
            self._last_request = time.time()

    def get_json(self, url: str, *, use_cache: bool = True, retries: int = 3) -> Any:
        """GET con JSON de retorno. Devuelve ``None`` si el recurso no existe (404)."""
        cache_path = self._cache_path(url) if use_cache else None
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached

        last_exc: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self._session.get(url, timeout=30)
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 404:
                return None  # concepto/empresa sin datos para ese frame
            if resp.status_code == 429:  # rate limited
                time.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise SECError(f"HTTP {resp.status_code} en {url}")

            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                raise SECError(f"Respuesta no-JSON en {url}") from exc

            self._write_cache(cache_path, data)
            return data

        raise SECError(f"No se pudo obtener {url} tras {retries} intentos") from last_exc

    # ------------------------------------------------------------------ #
    # Endpoints concretos
    # ------------------------------------------------------------------ #
    def get_frame(self, taxonomy: str, tag: str, unit: str, period: str) -> Any:
        """Frame XBRL: un concepto para TODAS las empresas en un periodo.

        Ej.: get_frame("us-gaap", "Revenues", "USD", "CY2023").
        Devuelve ``None`` si ese concepto no tiene frame en ese periodo.
        """
        url = f"{self.BASE}/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
        return self.get_json(url)

    def get_company_tickers(self) -> dict[int, dict[str, str]]:
        """Mapa CIK -> {ticker, title} desde sec.gov/files/company_tickers.json."""
        url = "https://www.sec.gov/files/company_tickers.json"
        raw = self.get_json(url)
        out: dict[int, dict[str, str]] = {}
        if not raw:
            return out
        for entry in raw.values():
            cik = int(entry["cik_str"])
            # Puede haber varios tickers por CIK; nos quedamos con el primero.
            out.setdefault(cik, {"ticker": entry.get("ticker", ""), "title": entry.get("title", "")})
        return out
