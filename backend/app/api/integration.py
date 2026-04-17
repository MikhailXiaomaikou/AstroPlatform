"""Astro ecosystem integration — SAMP, VOTable, Jupyter export, ADQL query."""

import io
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_current_user, get_optional_user
from app.models.database import get_db
from app.models.schemas import PipelineRun, PipelineTemplateDB, User
from app.rate_limit import limiter
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
            except Exception:
                pass
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

    buf = io.BytesIO()
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
    from astropy.table import Table as AstropyTable

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

ADQL_SERVICES = {
    "gaia": "https://gea.esac.esa.int/tap-server/tap",
    "vizier": "https://tapvizier.cds.unistra.fr/TAPVizieR/tap",
    "cadc": "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus",
    "simbad": "https://simbad.cds.unistra.fr/simbad/sim-tap",
}

async def execute_adql_query(req: ADQLRequest) -> dict:
    """Core ADQL query execution (callable from AI tools without Request)."""
    import asyncio

    if req.service not in ADQL_SERVICES:
        raise HTTPException(status_code=400, detail=f"Unknown service: {req.service}. Available: {list(ADQL_SERVICES.keys())}")

    # Sanitize: block dangerous operations
    query_upper = req.query.upper().strip()
    for forbidden in ("DROP", "DELETE", "INSERT", "UPDATE", "CREATE", "ALTER"):
        if forbidden in query_upper.split():
            raise HTTPException(status_code=400, detail=f"Forbidden keyword: {forbidden}")

    try:
        from astroquery.utils.tap.core import TapPlus

        loop = asyncio.get_running_loop()

        def _run_query():
            tap = TapPlus(url=ADQL_SERVICES[req.service])
            job = tap.launch_job(req.query)
            return job.get_results()

        # Retry once on transient TAP failures (connection resets, 503s)
        try:
            table = await loop.run_in_executor(None, _run_query)
        except Exception as first_err:
            err_str = str(first_err).lower()
            if any(hint in err_str for hint in ("timeout", "connection", "503", "502", "reset")):
                logger.warning("ADQL first attempt failed (%s), retrying...", first_err)
                import asyncio as _aio
                await _aio.sleep(1.0)
                table = await loop.run_in_executor(None, _run_query)
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
                    arr = arr.filled(0)  # fill with 0, we use mask to detect nulls
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


@router.post("/adql/query")
@limiter.limit("20/minute")
async def adql_query(request: Request, req: ADQLRequest):
    """Execute an ADQL query against a TAP service (HTTP endpoint)."""
    return await execute_adql_query(req)


@router.get("/adql/services")
async def list_adql_services():
    """List available ADQL/TAP services."""
    return [
        {"id": "gaia", "name": "Gaia Archive", "url": ADQL_SERVICES["gaia"], "description": "ESA Gaia mission data"},
        {"id": "vizier", "name": "VizieR TAP", "url": ADQL_SERVICES["vizier"], "description": "CDS VizieR catalog service"},
        {"id": "cadc", "name": "CADC", "url": ADQL_SERVICES["cadc"], "description": "Canadian Astronomy Data Centre"},
    ]
