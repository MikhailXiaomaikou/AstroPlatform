"""Astro ecosystem integration — SAMP, VOTable, Jupyter export, ADQL query."""

import io
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user
from app.models.database import get_db
from app.models.schemas import PipelineRun, PipelineTemplateDB, User
from app.services.ai_tools import augment_adql_payload, build_adql_result_set, store_adql_result_set
from app.storage import download_fits, upload_fits

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integration", tags=["integration"])


# ══════════════════════════════════════
# SAMP Protocol
# ══════════════════════════════════════

class SAMPStatus(BaseModel):
    connected: bool
    hub_url: str | None = None
    registered_clients: list[str] = []

@router.get("/samp/status")
async def samp_status(_user: User | None = Depends(get_optional_user)):
    """Check if a SAMP hub is available."""
    try:
        from astropy.samp import SAMPIntegratedClient
        client = SAMPIntegratedClient()
        client.connect()
        clients = list(client.get_registered_clients())
        hub_url = str(client.hub.hub_url) if hasattr(client, 'hub') else None
        client.disconnect()
        return SAMPStatus(connected=True, hub_url=hub_url, registered_clients=clients)
    except Exception:
        return SAMPStatus(connected=False)


class SAMPSendRequest(BaseModel):
    fits_path: str
    message_type: str = "table.load.fits"  # or "image.load.fits"

@router.post("/samp/send")
async def samp_send(req: SAMPSendRequest, _user: User = Depends(get_current_user)):
    """Send a FITS file to connected SAMP clients (DS9, Aladin, TOPCAT)."""
    from pathlib import Path

    from app.config import settings

    # H10: enforce that fits_path stays within the storage root.  `exists()`
    # alone is bypassable with `../../../etc/passwd`; resolve then verify the
    # resolved path is a descendant of local_storage_dir.
    storage_root = Path(settings.local_storage_dir).resolve()
    full_path = (Path(settings.local_storage_dir) / req.fits_path).resolve()
    try:
        full_path.relative_to(storage_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fits_path: outside storage root")
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="FITS file not found")

    file_url = f"file://{full_path}"

    try:
        from astropy.samp import SAMPIntegratedClient
        client = SAMPIntegratedClient()
        client.connect()

        params = {"url": file_url, "name": req.fits_path}
        message = {"samp.mtype": req.message_type, "samp.params": params}
        client.notify_all(message)
        client.disconnect()

        return {"sent": True, "url": file_url, "message_type": req.message_type}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"SAMP send failed: {e}")


class SAMPReceiver:
    """Bidirectional SAMP client that can receive messages from DS9/TOPCAT/Aladin."""

    _instance = None
    _received: list[dict] = []
    _client = None
    _connected = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def connect_and_subscribe(self):
        """Connect to SAMP hub and subscribe to common MTypes."""
        if self._connected:
            return {"status": "already_connected"}
        try:
            from astropy.samp import SAMPIntegratedClient
            self._client = SAMPIntegratedClient(name="StandardAstro")
            self._client.connect()

            # Subscribe to MTypes
            mtypes = [
                "table.load.fits", "table.load.votable",
                "image.load.fits", "coord.pointAt.sky",
                "table.highlight.row",
            ]
            for mtype in mtypes:
                self._client.bind_receive_notification(mtype, self._on_notification)
                self._client.bind_receive_call(mtype, self._on_call)

            self._connected = True
            return {"status": "connected", "subscribed_mtypes": mtypes}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _on_notification(self, private_key, sender_id, mtype, params, extra):
        self._received.append({
            "sender": sender_id, "mtype": mtype,
            "params": {k: str(v) for k, v in (params or {}).items()},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._received) > 50:
            self._received.pop(0)

    def _on_call(self, private_key, sender_id, msg_id, mtype, params, extra):
        self._on_notification(private_key, sender_id, mtype, params, extra)
        # Acknowledge the call
        if self._client:
            self._client.reply(msg_id, {"samp.status": "samp.ok", "samp.result": {}})

    def disconnect(self):
        if self._client and self._connected:
            try:
                self._client.disconnect()
            except Exception as e:
                logger.debug("SAMP client disconnect failed: %s", e)
            self._connected = False
        return {"status": "disconnected"}

    def get_received(self) -> list[dict]:
        return list(self._received)

    @property
    def is_connected(self) -> bool:
        return self._connected


@router.post("/samp/subscribe")
async def samp_subscribe(_user: User = Depends(get_current_user)):
    """Connect to SAMP hub and subscribe to receive messages."""
    receiver = SAMPReceiver.get_instance()
    return receiver.connect_and_subscribe()


@router.get("/samp/received")
async def samp_received(_user: User | None = Depends(get_optional_user)):
    """List messages received via SAMP from external clients."""
    receiver = SAMPReceiver.get_instance()
    return {"messages": receiver.get_received(), "connected": receiver.is_connected}


@router.post("/samp/unsubscribe")
async def samp_unsubscribe(_user: User = Depends(get_current_user)):
    """Disconnect from SAMP hub."""
    receiver = SAMPReceiver.get_instance()
    return receiver.disconnect()


# ══════════════════════════════════════
# VOTable Support
# ══════════════════════════════════════

@router.get("/votable/convert")
async def convert_to_votable(
    fits_path: str = Query(..., description="Storage path to FITS file"),
    _user: User = Depends(get_current_user),
):
    """Convert a FITS table to VOTable format."""
    from astropy.io import fits
    from astropy.table import Table

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    hdul = fits.open(io.BytesIO(raw))

    table = None
    for hdu in hdul:
        if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU)):
            table = Table.read(hdu)
            break
    hdul.close()

    if table is None:
        raise HTTPException(status_code=400, detail="No table data in FITS file")

    # D4.2 — annotate the VOTable with IVOA UCDs + units so it is
    # self-describing in TOPCAT / Aladin / STILTS.  Write to an
    # intermediate BytesIO, parse, annotate, re-serialise.
    import io as _io
    buf = _io.BytesIO()
    table.write(buf, format="votable", overwrite=True)
    try:
        from astropy.io.votable import parse as _vo_parse
        from app.services.vo_standards import apply_votable_metadata
        buf.seek(0)
        vot = _vo_parse(buf)
        n_annotated = apply_votable_metadata(vot)
        buf = _io.BytesIO()
        vot.to_xml(buf)
        logger.debug("VOTable: annotated %d fields with UCD/unit", n_annotated)
    except Exception as exc:
        logger.debug("VOTable UCD annotation failed, using raw export: %s", exc)
        buf = _io.BytesIO()
        table.write(buf, format="votable", overwrite=True)
    buf.seek(0)

    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type="application/x-votable+xml",
        headers={"Content-Disposition": f"attachment; filename={fits_path.replace('/', '_')}.xml"},
    )


@router.post("/votable/upload")
async def upload_votable(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """Upload a VOTable file and convert to FITS for browsing."""
    from astropy.io.votable import parse as parse_votable

    content = await file.read()
    try:
        votable = parse_votable(io.BytesIO(content))
        table = votable.get_first_table().to_table()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid VOTable: {e}")

    # Convert to FITS
    fits_buf = io.BytesIO()
    table.write(fits_buf, format='fits', overwrite=True)
    fits_buf.seek(0)

    path = f"votable_imports/{uuid.uuid4()}.fits"
    upload_fits(path, fits_buf.read())

    return {
        "path": path,
        "rows": len(table),
        "columns": list(table.colnames),
        "filename": file.filename,
    }


# ══════════════════════════════════════
# Jupyter Notebook Export
# ══════════════════════════════════════

class JupyterExportRequest(BaseModel):
    template_id: str | None = None
    run_id: str | None = None

@router.post("/jupyter/export")
async def export_jupyter(
    req: JupyterExportRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Export a pipeline template or run as a Jupyter notebook."""
    dag = None
    title = "Astro Pipeline"

    if req.template_id:
        result = await db.execute(
            select(PipelineTemplateDB).where(PipelineTemplateDB.id == uuid.UUID(req.template_id))
        )
        tpl = result.scalar_one_or_none()
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")
        dag = tpl.dag
        title = tpl.name
    elif req.run_id:
        result = await db.execute(
            select(PipelineRun).where(PipelineRun.id == uuid.UUID(req.run_id))
        )
        run = result.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        dag = run.dag
        title = f"Pipeline Run {req.run_id[:8]}"
    else:
        raise HTTPException(status_code=400, detail="Provide template_id or run_id")

    notebook = _dag_to_notebook(dag, title)

    from fastapi.responses import Response
    return Response(
        content=json.dumps(notebook, indent=2),
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": f"attachment; filename={title.replace(' ', '_')}.ipynb"},
    )


def _dag_to_notebook(dag: dict, title: str) -> dict:
    """Convert a pipeline DAG to a Jupyter notebook."""
    cells = []

    # Title cell
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [f"# {title}\n", "\n", "Auto-generated from Standard Astro pipeline.\n"]
    })

    # Imports cell
    cells.append({
        "cell_type": "code",
        "metadata": {},
        "source": [
            "import numpy as np\n",
            "from astropy.io import fits\n",
            "from astropy.table import Table\n",
            "from astropy.coordinates import SkyCoord\n",
            "import astropy.units as u\n",
            "import matplotlib.pyplot as plt\n",
            "%matplotlib inline\n",
        ],
        "outputs": [],
        "execution_count": None,
    })

    # Topologically sorted node cells
    from app.pipeline.engine import topological_sort
    try:
        levels = topological_sort(dag)
    except Exception:
        levels = [[n["id"]] for n in dag.get("nodes", [])]

    node_map = {n["id"]: n for n in dag.get("nodes", [])}

    for level in levels:
        for node_id in level:
            node = node_map.get(node_id, {})
            node_type = node.get("type", "Unknown")
            params = node.get("data", {}).get("params", {})
            label = node.get("data", {}).get("label", node_type)

            # Markdown header
            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"## {label} ({node_type})\n", f"\nParameters: `{json.dumps(params)}`\n"]
            })

            # Code cell based on node type
            code = _node_type_to_code(node_type, params, node_id)
            cells.append({
                "cell_type": "code",
                "metadata": {},
                "source": code,
                "outputs": [],
                "execution_count": None,
            })

    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": cells,
    }


def _node_type_to_code(node_type: str, params: dict, node_id: str) -> list[str]:
    """Generate Python code for a pipeline node."""
    if node_type == "LoadData":
        path = params.get("fits_path", "path/to/your/file.fits")
        return [
            "# Load FITS data\n",
            f"hdul = fits.open('{path}')\n",
            "hdul.info()\n",
            f"data_{node_id} = Table.read(hdul[1]) if len(hdul) > 1 else hdul[0].data\n",
        ]
    elif node_type == "Denoise":
        sigma = params.get("sigma", 3.0)
        return [
            "from astropy.stats import sigma_clip\n",
            "\n",
            f"sigma_thresh = {sigma}\n",
            "# Apply sigma clipping\n",
            "clipped = sigma_clip(flux, sigma=sigma_thresh, maxiters=5)\n",
            "mask = clipped.mask\n",
            "clean_flux = np.where(mask, np.interp(np.arange(len(flux)), np.where(~mask)[0], flux[~mask]), flux)\n",
        ]
    elif node_type == "SpectralFit":
        model = params.get("model", "gaussian")
        return [
            "from scipy.optimize import curve_fit\n",
            "\n",
            f"def {model}(x, amp, center, width):\n",
            "    return amp * np.exp(-0.5 * ((x - center) / width)**2)\n" if model == "gaussian" else
            "    return amp * width**2 / ((x - center)**2 + width**2)\n",
            "\n",
            f"popt, pcov = curve_fit({model}, wavelength, flux, maxfev=10000)\n",
            "print(f'Fit parameters: {dict(zip([\"amp\", \"center\", \"width\"], popt))}')\n",
            "plt.plot(wavelength, flux, label='Data')\n",
            f"plt.plot(wavelength, {model}(wavelength, *popt), '--', label='Fit')\n",
            "plt.legend()\n",
            "plt.show()\n",
        ]
    elif node_type == "RedshiftEstimate":
        return [
            "# Redshift estimation via emission line matching\n",
            "rest_lines = {'H-alpha': 6563, 'H-beta': 4861, '[OIII]': 5007, '[OII]': 3727}\n",
            "# Find peaks in spectrum\n",
            "from scipy.signal import find_peaks\n",
            "peaks, _ = find_peaks(flux, height=np.median(flux) + 2*np.std(flux))\n",
            "print(f'Found {len(peaks)} peaks at wavelengths: {wavelength[peaks]}')\n",
        ]
    elif node_type == "Plot":
        plot_type = params.get("plot_type", "spectrum")
        return [
            "fig, ax = plt.subplots(figsize=(12, 5))\n",
            "ax.plot(wavelength, flux, lw=0.8)\n" if plot_type == "spectrum" else
            "ax.scatter(x_data, y_data, s=4, alpha=0.6)\n",
            "ax.set_xlabel('Wavelength')\n",
            "ax.set_ylabel('Flux')\n",
            "ax.set_title('" + params.get("title", "Plot") + "')\n",
            "plt.tight_layout()\n",
            "plt.show()\n",
        ]
    else:
        return [f"# {node_type} node — implement as needed\n", f"params = {json.dumps(params)}\n"]


# ══════════════════════════════════════
# ADQL Query
# ══════════════════════════════════════

class ADQLRequest(BaseModel):
    query: str
    service: str = "gaia"  # "gaia", "vizier", "cadc", "simbad"

# I3/B-S1: Multi-mirror support for each TAP service.  Each service
# resolves to an ordered list of URLs — the first is the primary, the
# rest are fallback mirrors tried in order on HTTP 5xx / timeout.
# VizieR famously has CDS France (Strasbourg) returning 503 during
# scheduled maintenance windows; the international mirrors stay up.
ADQL_SERVICE_MIRRORS: dict[str, list[str]] = {
    "gaia": [
        "https://gea.esac.esa.int/tap-server/tap",  # ESA Madrid (single, very stable)
    ],
    "vizier": [
        "https://tapvizier.cds.unistra.fr/TAPVizieR/tap",  # Primary CDS France
        "https://tapvizier.u-strasbg.fr/TAPVizieR/tap",    # Strasbourg backup
        "http://vizier.china-vo.org/TAPVizieR/tap",        # China-VO mirror
        "https://vizier.iucaa.in/TAPVizieR/tap",           # IUCAA India mirror
    ],
    "cadc": [
        "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus",
    ],
    "simbad": [
        "https://simbad.cds.unistra.fr/simbad/sim-tap",
        "https://simbad.u-strasbg.fr/simbad/sim-tap",
    ],
}

# Backward-compatible single-URL view of the mirrors.  Anything that
# imports ADQL_SERVICES sees only the primary URL — same as before I3.
ADQL_SERVICES = {k: v[0] for k, v in ADQL_SERVICE_MIRRORS.items()}


def _launch_on_mirrors(query: str, service: str, async_mode: bool, progress_callback=None):
    """I3/B-S1: try each mirror for the service in order.  First success
    wins; on any failure (timeout / 5xx / connection error) skip to the
    next mirror.  The error from the LAST attempted mirror is re-raised
    when nothing succeeds, so the caller's existing exception classifier
    in execute_adql_query still works.

    NOTE: this is a sync function intended for use inside
    `loop.run_in_executor`, just like the original `_run_query_sync` /
    `_run_query_async` it replaces.  The outer asyncio.wait_for budget
    in execute_adql_query covers the *total* time across all mirrors —
    we don't add a per-mirror timeout here because individual TAP
    requests already have astroquery's internal HTTP timeout.
    """
    from astroquery.utils.tap.core import TapPlus

    mirrors = ADQL_SERVICE_MIRRORS.get(service, [])
    if not mirrors:
        raise ValueError(f"No TAP mirrors configured for service: {service}")

    last_err: Exception | None = None
    tried: list[str] = []
    mode = "async" if async_mode else "sync"
    for index, url in enumerate(mirrors, start=1):
        tried.append(url)
        try:
            if progress_callback:
                progress_callback({
                    "stage": "mirror_attempt",
                    "service": service,
                    "mode": mode,
                    "mirror_index": index,
                    "mirror_count": len(mirrors),
                    "mirror_url": url,
                    "message": f"Trying {service} TAP mirror {index}/{len(mirrors)} ({mode})",
                })
            tap = TapPlus(url=url)
            if async_mode:
                job = tap.launch_job_async(query)
            else:
                job = tap.launch_job(query)
            table = job.get_results()
            if progress_callback:
                progress_callback({
                    "stage": "mirror_success",
                    "service": service,
                    "mode": mode,
                    "mirror_index": index,
                    "mirror_count": len(mirrors),
                    "mirror_url": url,
                    "message": f"{service} TAP mirror {index}/{len(mirrors)} succeeded",
                })
            return table
        except Exception as e:
            # L17 + PART Y Batch 4 audit: distinguish 4xx (permanent, do not try next
            # mirror) from 5xx / network errors (transient, rotate to next mirror).
            # 404 = table does not exist / 400 = SQL syntax error; the next mirror
            # would return the same error and waste wall-clock time.
            # Only retry on 5xx / timeout / connection errors.
            #
            # PART Y Batch 4: prefer the real status_code attribute from
            # httpx exceptions; fall back to keyword string matching only
            # when no httpx response is available. The old "404 in str(e)"
            # path could mis-classify error messages like "row 400 of ..."
            # as a permanent 4xx and skip valid mirrors.
            err_str = str(e).lower()
            status_code = None
            try:
                import httpx
                if isinstance(e, httpx.HTTPStatusError):
                    status_code = e.response.status_code
            except Exception as inner:
                logger.debug("httpx status_code probe failed: %s", inner)

            if status_code is not None:
                is_permanent = (400 <= status_code < 500) and status_code not in (408, 429)
            else:
                # Fallback: keyword hints (avoids numeric-string false positives
                # like "row 400 of ..." being matched as HTTP 400).
                is_permanent = any(
                    hint in err_str
                    for hint in (
                        "syntax", "parse", "unknown column", "unknown table",
                        "bad request", "forbidden",
                    )
                )
                # Conservative: also accept "HTTP <code>" / "status <code>"
                # phrasings used by astroquery wrappers.
                for code in ("400", "401", "403", "404", "422"):
                    if (
                        f"http {code}" in err_str
                        or f"status {code}" in err_str
                        or f"http/{code}" in err_str
                        or f"({code})" in err_str
                    ):
                        is_permanent = True
                        break
                if "408" in err_str or "429" in err_str or "timeout" in err_str:
                    is_permanent = False
            if is_permanent:
                if progress_callback:
                    progress_callback({
                        "stage": "mirror_permanent_error",
                        "service": service,
                        "mode": mode,
                        "mirror_index": index,
                        "mirror_count": len(mirrors),
                        "mirror_url": url,
                        "error": str(e)[:500],
                        "message": f"{service} TAP mirror {index}/{len(mirrors)} returned a permanent query error",
                    })
                logger.info(
                    "TAP mirror %s returned permanent error (%s: %s); NOT "
                    "trying other mirrors — raising immediately",
                    url, type(e).__name__, str(e)[:200],
                )
                raise
            last_err = e
            if progress_callback:
                progress_callback({
                    "stage": "mirror_transient_error",
                    "service": service,
                    "mode": mode,
                    "mirror_index": index,
                    "mirror_count": len(mirrors),
                    "mirror_url": url,
                    "error": str(e)[:500],
                    "message": f"{service} TAP mirror {index}/{len(mirrors)} failed; trying next mirror",
                })
            logger.info(
                "TAP mirror %s failed with transient error (%s: %s); "
                "trying next mirror",
                url, type(e).__name__, str(e)[:200],
            )
            continue

    # All mirrors failed — re-raise the last error with a hint about
    # which URLs were tried so logs make the failure mode obvious.
    # K3: 'Tried: {tried}' previously inserted the list via f-string -> repr,
    # producing "Tried: ['url1', 'url2']" with brackets and single-quotes, which
    # looks messy. Changed to plain comma-separated. Error messages are read
    # directly by users; clean formatting matters more than anything else here.
    msg = (
        f"All {service} TAP mirrors unavailable after {len(tried)} attempts. "
        f"Tried: {', '.join(tried)}. Last error: {last_err}"
    )
    if last_err is not None:
        raise type(last_err)(msg) from last_err
    raise ConnectionError(msg)

async def execute_adql_query(
    req: ADQLRequest,
    progress_callback=None,
    *,
    sync_timeout_s: float = 30.0,
    async_timeout_s: float = 300.0,
) -> dict:
    """Core ADQL query execution (callable from AI tools without Request)."""
    import asyncio

    if req.service not in ADQL_SERVICES:
        raise HTTPException(status_code=400, detail=f"Unknown service: {req.service}. Available: {list(ADQL_SERVICES.keys())}")

    # H0.2: normalize whitespace.  The Paper 4 reviewer hit
    # "Cannot parse query SELECT TOP 5 ... FROM g" where TAP truncated
    # at a newline mid-identifier.  Pydantic JSON decoding correctly
    # converts \n escape sequences to real newlines, but some TAP
    # servers tokenise on newlines as terminators (or AI-generated
    # queries paste a stray "\n" as literal).  Collapse all whitespace
    # to single spaces before dispatch — ADQL is whitespace-agnostic
    # so this is always safe for legitimate queries.
    req.query = " ".join(req.query.replace("\n", " ").replace("\r", " ").replace("\t", " ").split())

    # Sanitize: block dangerous operations
    query_upper = req.query.upper().strip()
    for forbidden in ("DROP", "DELETE", "INSERT", "UPDATE", "CREATE", "ALTER"):
        if forbidden in query_upper.split():
            raise HTTPException(status_code=400, detail=f"Forbidden keyword: {forbidden}")

    # H0.1: estimate whether the query is "big" (expected to exceed the
    # sync 60 s TAP cutoff).  Gaia / VizieR / CADC all support async TAP
    # (PHASE=RUN polling); we use launch_job_async for big ones and keep
    # the fast sync path for small queries.
    import re as _adql_re
    query_lower = req.query.lower()
    _top_match = _adql_re.search(r"\btop\s+(\d+)\b", query_lower)
    _top_n = int(_top_match.group(1)) if _top_match else None
    _circle_match = _adql_re.search(r"circle\s*\(\s*'icrs'\s*,\s*[\d.+-]+\s*,\s*[\d.+-]+\s*,\s*([\d.]+)\s*\)", query_lower)
    _cone_radius = float(_circle_match.group(1)) if _circle_match else None
    # Heuristics for "probably big":
    #   - No TOP or TOP > 5000
    #   - Cone radius > 1°
    #   - Explicit JOIN (multi-table can be slow)
    _looks_big = (
        (_top_n is None or _top_n > 5000)
        or (_cone_radius is not None and _cone_radius > 1.0)
        or (" join " in query_lower)
    )

    attempt_log: list[dict] = []

    async def _emit_progress(event: dict) -> None:
        entry = {k: v for k, v in event.items() if v is not None}
        attempt_log.append(entry)
        if progress_callback:
            try:
                await progress_callback(entry)
            except Exception:
                logger.debug("ADQL progress callback failed", exc_info=True)

    async def _call_progress_callback(entry: dict) -> None:
        if not progress_callback:
            return
        try:
            await progress_callback(entry)
        except Exception:
            logger.debug("ADQL progress callback failed", exc_info=True)

    try:
        loop = asyncio.get_running_loop()

        def _record_progress_from_thread(event: dict) -> None:
            entry = {k: v for k, v in event.items() if v is not None}
            attempt_log.append(entry)
            if progress_callback:
                loop.call_soon_threadsafe(
                    lambda e=entry: asyncio.create_task(_call_progress_callback(e))
                )

        def _run_query_sync():
            # I3/B-S1: iterate through mirrors instead of hitting only
            # the primary URL.  When CDS France 503s the international
            # VizieR mirrors usually still answer.
            return _launch_on_mirrors(
                req.query,
                req.service,
                async_mode=False,
                progress_callback=_record_progress_from_thread,
            )

        def _run_query_async():
            # H0.1: TAP async mode — submits PHASE=RUN + polls.
            # I3: also iterates mirrors.  astroquery's launch_job_async
            # blocks the thread until the remote job finishes, so we
            # still put it in the executor; the outer 5-min budget in
            # asyncio.wait_for covers all mirrors collectively.
            return _launch_on_mirrors(
                req.query,
                req.service,
                async_mode=True,
                progress_callback=_record_progress_from_thread,
            )

        # Sync first unless we think it'll be big.  H0.1 (post-review):
        # tightened sync to 30s — healthy Gaia/VizieR responds in <5s, and
        # a slow sync is usually a sign async will do better.  Long-task
        # callers can explicitly raise async_timeout_s.
        try:
            if _looks_big:
                await _emit_progress({
                    "stage": "async_start",
                    "service": req.service,
                    "mode": "async",
                    "message": f"ADQL query looks broad; using async TAP directly ({int(async_timeout_s)}s budget)",
                    "async_timeout_seconds": int(async_timeout_s),
                })
                logger.info(
                    "ADQL: query flagged big (top=%s, radius=%s, join=%s); using async TAP",
                    _top_n, _cone_radius, " join " in query_lower,
                )
                table = await asyncio.wait_for(
                    loop.run_in_executor(None, _run_query_async),
                    timeout=async_timeout_s,
                )
            else:
                await _emit_progress({
                    "stage": "sync_start",
                    "service": req.service,
                    "mode": "sync",
                    "message": f"Starting ADQL sync TAP probe ({int(sync_timeout_s)}s budget)",
                    "sync_timeout_seconds": int(sync_timeout_s),
                })
                table = await asyncio.wait_for(
                    loop.run_in_executor(None, _run_query_sync),
                    timeout=sync_timeout_s,
                )
        except asyncio.TimeoutError as timeout_err:
            # Sync timed out on a query we thought was small.  Fall back
            # to async and give it the configured async budget.
            if not _looks_big:
                await _emit_progress({
                    "stage": "sync_timeout_async_fallback",
                    "service": req.service,
                    "mode": "async",
                    "error": str(timeout_err)[:500],
                    "message": "Sync TAP probe timed out; switching to async TAP",
                })
                logger.info("ADQL sync timed out, retrying with async TAP (PHASE=RUN)...")
                try:
                    table = await asyncio.wait_for(
                        loop.run_in_executor(None, _run_query_async),
                        timeout=async_timeout_s,
                    )
                except Exception as async_err:
                    raise HTTPException(
                        status_code=408,
                        detail=(
                            f"ADQL query timed out even after async TAP fallback "
                            f"({int(async_timeout_s)}s). Reduce TOP, shrink cone radius, or use "
                            f"run_adql with explicit smaller scope. Inner error: {async_err}"
                        ),
                    ) from async_err
            else:
                raise HTTPException(
                        status_code=408,
                        detail=(
                        f"ADQL async TAP timed out after {int(async_timeout_s)}s. Query is too "
                        f"large — reduce TOP to <5000 or shrink cone radius "
                        f"to <0.5 deg. Original error: {timeout_err}"
                    ),
                ) from timeout_err
        except Exception as first_err:
            err_str = str(first_err).lower()
            if any(hint in err_str for hint in ("timeout", "connection", "503", "502", "reset")):
                await _emit_progress({
                    "stage": "transient_error_async_retry",
                    "service": req.service,
                    "mode": "async",
                    "error": str(first_err)[:500],
                    "message": "ADQL attempt hit a transient service error; retrying with async TAP",
                })
                logger.warning("ADQL first attempt failed (%s), retrying with async...", first_err)
                import asyncio as _aio
                await _aio.sleep(1.0)
                try:
                    table = await asyncio.wait_for(
                        loop.run_in_executor(None, _run_query_async),
                        timeout=async_timeout_s,
                    )
                except Exception as retry_err:
                    raise HTTPException(
                        status_code=502,
                        detail=f"ADQL service unavailable after retry: {retry_err}",
                    ) from retry_err
            else:
                raise

        # Convert to JSON-serializable format (lowercase column names for consistency)
        columns = [c.lower() for c in table.colnames]
        data = {}
        import numpy as np
        for col, orig_col in zip(columns, table.colnames):
            arr = table[orig_col]
            try:
                # Check if column is masked and get the mask
                mask = None
                if hasattr(arr, "mask"):
                    mask = arr.mask
                if hasattr(arr, "filled"):
                    # L16 (audit 2026-04-20): fill masked float columns with np.nan
                    # rather than 0 — semantically cleaner and the isnan branch below
                    # can catch it without relying on the mask array to reach None.
                    # Integer masked columns are still tracked via mask[idx]
                    # (filled(0) is necessary for integer masked arrays because
                    # np.nan cannot be assigned to an int array).
                    try:
                        is_float_dtype = np.issubdtype(arr.dtype, np.floating)
                    except Exception:
                        is_float_dtype = False
                    fill_value = np.nan if is_float_dtype else 0
                    arr = arr.filled(fill_value)
                vals = []
                for idx, v in enumerate(arr):
                    # Use mask to detect missing values
                    is_masked = mask is not None and (mask[idx] if hasattr(mask, '__getitem__') else mask)
                    if is_masked or v is None:
                        vals.append(None)
                    elif isinstance(v, (np.integer, int)):
                        vals.append(int(v))
                    elif isinstance(v, (np.floating, float)):
                        fv = float(v)
                        if np.isnan(fv) or np.isinf(fv) or abs(fv) > 1e18:
                            vals.append(None)
                        else:
                            vals.append(fv)
                    else:
                        vals.append(str(v))
                data[col] = vals
            except Exception:
                data[col] = [str(v) for v in arr]

        augmented_columns, augmented_data, _rows = augment_adql_payload(
            columns,
            data,
            len(table),
            limit=len(table),
        )
        result_set = build_adql_result_set(
            service=req.service,
            query=req.query,
            columns=augmented_columns,
            data=augmented_data,
            row_count=len(table),
        )
        store_adql_result_set(None, result_set)

        return {
            "columns": augmented_columns,
            "data": augmented_data,
            "row_count": len(table),
            "service": req.service,
            "attempt_log": attempt_log,
        }
    except HTTPException:
        raise
    except Exception as e:
        err_msg = str(e)
        err_lower = err_msg.lower()
        # TAP services return 400 for bad queries (syntax errors, unknown columns/tables)
        if any(hint in err_lower for hint in ("syntax", "parse", "unknown column", "unknown table", "not found", "400", "bad request", "adql")):
            raise HTTPException(status_code=400, detail=f"ADQL query error: {err_msg}")
        raise HTTPException(status_code=502, detail=f"ADQL service error: {err_msg}")


# The public POST /adql/query and GET /adql/services routes were REMOVED
# 2026-06-11: their frontend (the ADQL page) was cut in the M3 trim, leaving
# the query route an unauthenticated dead endpoint (audit-deferred item).
# `execute_adql_query` above stays — the chat tools import and call it
# directly; its DDL/DML guard is covered at function level in
# tests/test_security.py::TestInputValidation.
