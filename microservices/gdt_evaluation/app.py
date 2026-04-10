from fastapi import FastAPI
from pydantic import BaseModel
import numpy as np
from utils.evaluation import evaluate_features

app = FastAPI(title="GD&T Evaluation Service")

class EvaluationRequest(BaseModel):
    alignedPoints: list   # [[x,y,z], ...]
    nominalFeatures: dict # {"plane":[[x,y,z],...], "circle":[[x,y,z],...]}
    tolerances: dict      # {"flatness":0.05, "position":0.1}

@app.post("/evaluate")
def evaluate(request: EvaluationRequest):
    aligned = np.array(request.alignedPoints)
    results = evaluate_features(aligned, request.nominalFeatures, request.tolerances)
    return {"evaluationResults": results}

@app.get("/health")
def health():
    return {"status": "ok"}