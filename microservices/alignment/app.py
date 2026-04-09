# microservices/alignment/app.py
from fastapi import FastAPI, Request   
from pydantic import BaseModel
import numpy as np
import open3d as o3d
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alignment-service")

class Point(BaseModel):
    x: float
    y: float
    z: float

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logger.info(f"{request.method} {request.url} status={response.status_code} duration={duration:.3f}s")
    return response

class AlignmentRequest(BaseModel):
    compensatedPoints: list[Point]
    nominalPoints: list[Point]

@app.post("/alignPoints")
def align_points(req: AlignmentRequest):
    try:
        logger.info(f"Received alignment request with {len(req.compensatedPoints)} compensated points and {len(req.nominalPoints)} nominal points")
        compensated = np.array([[p.x, p.y, p.z] for p in req.compensatedPoints])
        nominal = np.array([[p.x, p.y, p.z] for p in req.nominalPoints])

        src = o3d.geometry.PointCloud()
        src.points = o3d.utility.Vector3dVector(compensated)

        tgt = o3d.geometry.PointCloud()
        tgt.points = o3d.utility.Vector3dVector(nominal)

        src = src.voxel_down_sample(voxel_size=0.5)
        tgt = tgt.voxel_down_sample(voxel_size=0.5)

        logger.info("Starting ICP alignment...")
        
        start = time.time()

        threshold = 2.0
        trans_init = np.eye(4)
        criteria = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=50)
        reg_p2p = o3d.pipelines.registration.registration_icp(
            src, tgt, threshold, trans_init,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            criteria
        )

        logger.info(f"ICP took {time.time() - start:.2f}s")


        aligned_src = src.transform(reg_p2p.transformation)
        aligned_points = np.asarray(aligned_src.points)
        logger.info(f"ICP completed with fitness={reg_p2p.fitness:.4f} and rmse={reg_p2p.inlier_rmse:.4f}")

        return {
            "transformationMatrix": reg_p2p.transformation.tolist(),
            "fitness": reg_p2p.fitness,
            "rmse": reg_p2p.inlier_rmse,
            "alignedPoints": [
                {"x": float(x), "y": float(y), "z": float(z)} for x, y, z in aligned_points
            ]
        }
    except Exception as e:
        logger.error(f"Alignment failed: {e}")
        return {"error": str(e)}

