from fastapi import FastAPI
from pydantic import BaseModel
import pandas as pd

app = FastAPI(title="Reporting Service")

class ReportRequest(BaseModel):
    evaluationResults: dict  # {"flatness": {...}, "position": {...}}

@app.post("/report")
def generate_report(request: ReportRequest):
    df = pd.DataFrame.from_dict(request.evaluationResults, orient="index")
    summary = df.to_dict(orient="records")
    return {"report": summary}

@app.get("/health")
def health():
    return {"status": "ok"}