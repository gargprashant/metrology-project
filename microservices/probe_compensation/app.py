from fastapi import FastAPI, Request   
from pydantic import BaseModel
from typing import List
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alignment-service")

app = FastAPI(title="Probe Compensation Service")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logger.info(f"{request.method} {request.url} status={response.status_code} duration={duration:.3f}s")
    return response

# ---------- Request/Response Models ----------

class Point(BaseModel):
    x: float
    y: float
    z: float
    normal: List[float]

class ProbeRequest(BaseModel):
    cmmId: str
    probeDiameter: float
    points: List[Point]

class CompensatedPoint(BaseModel):
    x: float
    y: float
    z: float

class ProbeResponse(BaseModel):
    compensatedPoints: List[CompensatedPoint]

# ---------- Core Logic ----------

@app.post("/compensateProbe", response_model=ProbeResponse)
def compensate_probe(request: ProbeRequest):
    compensated = []
    radius = request.probeDiameter / 2.0

    for pt in request.points:
        nx, ny, nz = pt.normal
        # Normalize normal vector
        norm_len = (nx**2 + ny**2 + nz**2) ** 0.5
        nx, ny, nz = nx/norm_len, ny/norm_len, nz/norm_len

        # Apply probe compensation
        cx = pt.x - radius * nx
        cy = pt.y - radius * ny
        cz = pt.z - radius * nz

        compensated.append(CompensatedPoint(x=cx, y=cy, z=cz))

    return ProbeResponse(compensatedPoints=compensated)